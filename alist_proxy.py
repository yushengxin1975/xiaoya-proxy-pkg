#!/usr/bin/env python3
"""
Alist 阿里云盘 15 分钟链接续期代理

问题:阿里云盘 OpenAPI 返回的播放 URL 只有 15 分钟有效期(x-oss-expires=900),
浏览器/播放器拿到 403 后会卡死,必须刷新页面才能继续。

本代理反向代理整个 Alist 网页,只拦截视频流做续期:
  - 网页/API/静态资源 → 透明转发到 Alist
  - /api/fs/get 响应 → 改写 raw_url 指向本地 /__stream__
  - /__stream__/<path> → 视频流代理,自动刷新 15 分钟链接

用法:
    python3 alist_proxy.py [PORT]          # 默认 8080

访问:
    http://localhost:8080/                 # 完整 Alist 网页(推荐)
    http://localhost:8080/__simple__/      # 简易网页(备选)
    http://localhost:8080/__health__       # 健康检查

零依赖,纯 Python 3 标准库。
"""

import http.server
import socketserver
import urllib.request
import urllib.error
import urllib.parse
import json
import time
import threading
import sys
import os
import logging
import hashlib
import collections
from http import HTTPStatus
from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: F401  # 保留供将来扩展使用

# ============== 配置(从环境变量读取)==============
# 这些值由 install.sh 写入 ~/.config/alist-proxy/config,
# systemd 通过 EnvironmentFile 加载。手动运行时直接 export 也可。
def _env(name, default=""):
    v = os.environ.get(name, "").strip()
    return v if v else default

ALIST_URL    = _env("ALIST_URL",    "http://localhost:5244")
ALIST_USER   = _env("ALIST_USER",   "")
ALIST_PASS   = _env("ALIST_PASS",   "")
LISTEN_HOST  = _env("LISTEN_HOST",  "localhost")    # localhost 比 127.0.0.1 跨域判断更宽松
LISTEN_PORT  = int(_env("LISTEN_PORT", "8080"))
URL_CACHE_TTL = 14 * 60       # URL 缓存 14 分钟(留 1 分钟缓冲,阿里云盘有效期 15 分钟)
URL_REFRESH_MARGIN = 60       # 距过期不足 60 秒时认为 URL 即将失效
MAX_RETRY_ON_403 = 2          # 遇到 403 时最多重试次数
UPSTREAM_TIMEOUT = 30         # 上游请求超时(秒)
CHUNK_SIZE = 64 * 1024        # 流式转发块大小

# 首页 HTML 与脚本同目录
INDEX_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alist_proxy_index.html")

# ============== 日志 ==============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger("alist-proxy")

# ============== Alist API 客户端 ==============
class AlistClient:
    def __init__(self):
        self.token = None
        self.token_lock = threading.Lock()

    def login(self):
        body = json.dumps({"username": ALIST_USER, "password": ALIST_PASS}).encode()
        req = urllib.request.Request(
            f"{ALIST_URL}/api/auth/login",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.load(r)
            if data.get("code") == 200:
                with self.token_lock:
                    self.token = data["data"]["token"]
                log.info("Alist 登录成功")
                return True
            log.error(f"登录失败: {data.get('message')}")
        except Exception as e:
            log.error(f"登录异常: {e}")
        return False

    def api_post(self, path, body):
        for attempt in range(2):
            if not self.token and not self.login():
                return None
            data = json.dumps(body).encode()
            req = urllib.request.Request(
                f"{ALIST_URL}{path}",
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": self.token,
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    log.warning("Token 失效,重新登录")
                    with self.token_lock:
                        self.token = None
                    continue
                log.error(f"API {path} 失败: HTTP {e.code}")
                return None
            except Exception as e:
                log.error(f"API {path} 异常: {e}")
                return None
        return None

    def get_raw_url(self, alist_path):
        r = self.api_post("/api/fs/get", {"path": alist_path, "password": ""})
        if r and r.get("code") == 200:
            return r["data"].get("raw_url", "")
        log.error(f"获取 raw_url 失败: {r.get('message') if r else 'no response'}")
        return None

    def list_dir(self, alist_path):
        r = self.api_post("/api/fs/list", {
            "path": alist_path,
            "refresh": False,
            "page": 1,
            "per_page": 500,
            "password": "",
        })
        if r and r.get("code") == 200:
            return r["data"].get("content", []) or []
        return None

    def get_hls_url(self, alist_path, template_id="QHD"):
        """调用 /api/fs/other method=video_preview,获取指定 template_id 的 m3u8 URL"""
        r = self.api_post("/api/fs/other", {
            "path": alist_path,
            "password": "",
            "method": "video_preview",
        })
        if not r or r.get("code") != 200:
            log.error(f"获取 HLS URL 失败: {r.get('message') if r else 'no response'}")
            return None

        data = r.get("data") or {}
        vpi = data.get("video_preview_play_info") or {}
        task_list = vpi.get("live_transcoding_task_list") or []

        # 优先找指定 template_id,找不到就用第一个
        for task in task_list:
            if task.get("template_id") == template_id:
                return task.get("url")
        if task_list:
            return task_list[0].get("url")
        return None


# ============== URL 缓存 ==============
class UrlCache:
    def __init__(self):
        self.cache = {}   # path -> (raw_url, expire_ts)
        self.lock = threading.Lock()

    def get(self, path):
        """返回 (url_or_None, is_fresh)"""
        with self.lock:
            entry = self.cache.get(path)
            if not entry:
                return None, False
            url, expire = entry
            return url, time.time() < expire

    def put(self, path, url):
        with self.lock:
            self.cache[path] = (url, time.time() + URL_CACHE_TTL)

    def invalidate(self, path):
        with self.lock:
            self.cache.pop(path, None)


# ============== 目录索引(后台构建,搜索读本地) ==============
class DirectoryIndexer:
    """后台线程串行扫描 Alist 目录树,生成可搜索的本地索引。
    搜索请求直接读内存中的索引,完全不打上游,保护资源。

    工作流程:
    1. 代理启动时从 ~/.local/share/alist_proxy/index.json 加载已有索引
    2. 后台线程 BFS 扫描:
       - 首次/无 dirs_meta → 全量扫描(BFS 从 / 开始)
       - 后续重建 → 增量:按目录签名剪枝,只重扫变化的子树
    3. 扫描完成后原子写入索引文件
    4. 每 refresh_interval 秒自动重建一次(也可手动触发)

    增量机制:
    - 每个目录记一个 sig = md5(name|size|modified|is_dir ...)
    - 重建时 fetch 目录,对比 sig:
      * 相同 → 跳过整个子树递归(保留上次所有条目)
      * 不同 → 重扫该层,丢弃旧的直接子条目
    - 孤儿清理:任何 entry 的 parent 不在 dirs_meta 中 → 删除
    - 强制全量:force_full=True 忽略 sig,从头开始

    资源保护(都偏保守):
    - max_depth=4:深度限制
    - max_dirs=2000:总目录预算
    - per_page=200:每目录一次最多取 200 项
    - dir_delay=0.5s:每目录完成后停顿(防突发流量)
    """

    # 索引文件路径(必须持久,不能用 /tmp — 会被 systemd 清理且 tmpfs 重启丢失)
    # 用 XDG_DATA_HOME: ~/.local/share/alist_proxy/index.json
    INDEX_PATH = os.path.join(
        os.environ.get("XDG_DATA_HOME",
                       os.path.join(os.path.expanduser("~"), ".local", "share")),
        "alist_proxy", "index.json",
    )
    INDEX_DIR = os.path.dirname(INDEX_PATH)

    def __init__(self, client):
        self.client = client
        # 索引数据
        self.entries = []           # 内存索引:[{name,size,is_dir,modified,path,parent}, ...]
        self.entries_lock = threading.Lock()
        # per-dir 元数据(增量模式用): {path: {"sig": str, "child_dirs": [paths]}}
        self.dirs_meta = {}
        # 元数据
        self.completed_at = 0.0     # 上次完成时间
        self.last_duration = 0.0    # 上次构建耗时
        self.last_error = ""
        self.last_mode = ""         # "full" | "incremental" | ""
        self.last_skipped = 0       # 上次跳过的未变化目录数
        self.last_rescanned = 0     # 上次重扫的目录数
        # 当前构建进度(由 _build_once 写入,get_status 读取)
        self.running = False
        self.dirs_visited = 0
        self.files_collected = 0
        self.bytes_total = 0
        self.current_path = ""
        self.started_at = 0.0
        self.mode = ""              # 当前正在跑的构建模式
        # 状态读写锁
        self.lock = threading.Lock()
        # 调度
        self.thread = None
        self.force_event = threading.Event()
        self.force_full = False     # 下次构建是否强制全量
        # 配置
        self.max_depth = 4
        self.max_dirs = 2000
        self.per_page = 200
        self.dir_delay = 0.5
        self.dir_timeout = 20
        self.refresh_interval = 6 * 3600  # 6 小时自动重建
        # 全量重建安全网:避免增量长期积累陈旧数据(如果上游不更新父目录 mtime)
        self.last_full_at = 0.0         # 上次全量完成时间戳
        self.force_full_after = 24 * 3600  # 超过 24 小时没全量 → 下次强制全量
        # 启动时加载磁盘索引
        self._load_from_disk()

    def _load_from_disk(self):
        """从磁盘加载已有索引(失败不抛异常)"""
        # 确保目录存在(否则重建时 open 会失败)
        try:
            os.makedirs(self.INDEX_DIR, exist_ok=True)
        except Exception as e:
            log.warning(f"创建索引目录失败 {self.INDEX_DIR}: {e}")

        # 兼容旧路径:新位置无索引但 /tmp 下有,迁移过来(避免重新构建 17 分钟)
        legacy_path = "/tmp/alist_proxy_index.json"
        if not os.path.exists(self.INDEX_PATH) and os.path.exists(legacy_path):
            try:
                import shutil
                shutil.copy2(legacy_path, self.INDEX_PATH)
                log.info(f"已迁移旧索引: {legacy_path} → {self.INDEX_PATH}")
            except Exception as e:
                log.warning(f"迁移旧索引失败(忽略): {e}")

        try:
            if not os.path.exists(self.INDEX_PATH):
                log.info(f"无历史索引文件 {self.INDEX_PATH},首次运行将开始构建")
                return
            with open(self.INDEX_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries", [])
            if not isinstance(entries, list):
                raise ValueError("索引格式错误")
            self.entries = entries
            self.completed_at = data.get("built_at", 0.0)
            self.last_duration = data.get("duration", 0.0)
            ts = (time.strftime("%Y-%m-%d %H:%M:%S",
                                time.localtime(self.completed_at))
                  if self.completed_at else "未知")
            self.last_mode = data.get("mode", "")
            # 上次全量重建时间(用于 24h 安全网判断)
            if self.last_mode == "full":
                self.last_full_at = self.completed_at
            # dirs_meta 用于增量重建(老索引可能没有 → 视为空,下次自动全量)
            dm = data.get("dirs_meta", {})
            if isinstance(dm, dict):
                self.dirs_meta = dm
            log.info(f"加载磁盘索引: {len(self.entries)} 条目 / {len(self.dirs_meta)} 目录签名,建于 {ts} ({self.last_mode or 'legacy'})")
        except Exception as e:
            log.warning(f"加载索引失败(忽略): {e}")
            self.entries = []

    def start(self):
        """启动后台重建线程(幂等)"""
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run_loop, name="indexer", daemon=True)
        self.thread.start()
        log.info("索引后台线程已启动")

    def trigger_now(self, force_full=False):
        """立即触发一次重建(异步,不阻塞调用方)。
        force_full=True:忽略已有 dirs_meta,从头开始(用于怀疑索引陈旧时)"""
        self.force_full = bool(force_full)
        self.force_event.set()
        log.info(f"已请求{'全量' if force_full else '增量'}重建索引")

    def get_status(self):
        """返回当前状态(给 UI 轮询)"""
        with self.lock:
            now = time.time()
            elapsed = (now - self.started_at) if (self.running and self.started_at) else 0.0
            age = (now - self.completed_at) if self.completed_at else None
            return {
                "running": self.running,
                "index_size": len(self.entries),
                "dirs_visited": self.dirs_visited,
                "files_collected": self.files_collected,
                "bytes_total": self.bytes_total,
                "current_path": self.current_path,
                "elapsed_sec": round(elapsed, 1),
                "completed_at": self.completed_at,
                "completed_age_sec": round(age, 1) if age is not None else None,
                "completed_human": (time.strftime("%Y-%m-%d %H:%M:%S",
                                                  time.localtime(self.completed_at))
                                    if self.completed_at else None),
                "last_duration_sec": round(self.last_duration, 1),
                "last_error": self.last_error,
                "last_mode": self.last_mode,
                "last_skipped": self.last_skipped,
                "last_rescanned": self.last_rescanned,
                "current_mode": self.mode,
                "last_full_at": self.last_full_at,
                "force_full_after_sec": self.force_full_after,
                "max_depth": self.max_depth,
                "max_dirs": self.max_dirs,
                "index_file": self.INDEX_PATH,
            }

    def search(self, q, parent="/", scope=0, page=1, per_page=50, max_depth=None):
        """搜索本地索引(完全本地,不打上游)"""
        q = (q or "").strip()
        parent = (parent or "/").strip() or "/"
        if not q:
            return {"code": 200, "message": "success",
                    "data": {"content": [], "total": 0}, "source": "index"}

        # 索引为空 → 友好提示
        with self.entries_lock:
            entries = self.entries
            index_size = len(entries)
        if index_size == 0:
            with self.lock:
                running = self.running
                current = self.current_path
                visited = self.dirs_visited
            msg = ("后台正在扫描目录,首次构建可能需要几分钟..." if running
                   else "索引尚未建立,后台线程已启动...")
            return {
                "code": 202,
                "message": msg,
                "data": {"content": [], "total": 0,
                         "progress": {"running": running, "current_path": current,
                                      "dirs_visited": visited}},
                "source": "index-empty",
            }

        ql = q.lower()
        parent_norm = parent.rstrip("/") or "/"
        max_d = max_depth if max_depth is not None else self.max_depth

        results = []
        for entry in entries:
            name = entry.get("name", "")
            is_dir = entry.get("is_dir", False)
            epath = entry.get("path", "")
            eparent = entry.get("parent", "/")

            # scope 过滤:0=全部,1=仅目录,2=仅文件
            if scope == 1 and not is_dir:
                continue
            if scope == 2 and is_dir:
                continue

            # parent 路径过滤(scope != 0)
            if scope != 0 and parent_norm != "/":
                if not (eparent == parent_norm or eparent.startswith(parent_norm + "/")):
                    continue
                if max_d is not None:
                    rel = eparent[len(parent_norm):].strip("/")
                    depth_in_parent = rel.count("/") + 1 if rel else 0
                    if depth_in_parent > max_d:
                        continue

            # 名字匹配(大小写不敏感子串)
            if ql in name.lower():
                results.append({
                    "name": name,
                    "size": entry.get("size", 0),
                    "is_dir": is_dir,
                    "modified": entry.get("modified", ""),
                    "path": epath,
                    "parent": eparent,
                })

        # 排序:目录优先 → modified 倒序 → 名字
        def sort_key(r):
            return (
                0 if r["is_dir"] else 1,
                r["modified"][:19] if r.get("modified") and len(r["modified"]) >= 19 else "",
                r["name"].lower(),
            )
        # modified 倒序
        results.sort(key=sort_key, reverse=False)
        # 重新排序:目录在上,然后文件按 modified 倒序
        dirs_only = [r for r in results if r["is_dir"]]
        files_only = [r for r in results if not r["is_dir"]]
        dirs_only.sort(key=lambda r: r["name"].lower())
        files_only.sort(key=lambda r: r["modified"][:19] if r.get("modified")
                        and len(r["modified"]) >= 19 else "", reverse=True)
        results = dirs_only + files_only

        total = len(results)
        start = max(0, (page - 1) * per_page)
        end = start + per_page
        return {
            "code": 200,
            "message": "success",
            "data": {"content": results[start:end], "total": total},
            "source": "index",
        }

    def _run_loop(self):
        """主循环:启动后立即构建,之后每 refresh_interval 秒或被强制触发时重建"""
        first = True
        while True:
            if not first:
                self.force_event.wait(timeout=self.refresh_interval)
                self.force_event.clear()
            first = False
            force_full = bool(self.force_full)
            self.force_full = False
            try:
                self._build_once(force_full=force_full)
            except Exception as e:
                log.error(f"索引重建异常: {e}")
                with self.lock:
                    self.last_error = str(e)
                    self.running = False

    @staticmethod
    def _compute_sig(items):
        """根据目录条目列表计算稳定签名:md5(name|size|modified|is_dir)"""
        parts = []
        for it in items:
            parts.append("{}|{}|{}|{}".format(
                it.get("name", ""),
                it.get("size", 0),
                it.get("modified", ""),
                1 if it.get("is_dir") else 0,
            ))
        return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()

    def _build_once(self, force_full=False):
        """执行一次扫描(全量或增量),完成后原子写盘并替换内存索引。

        force_full=True  → 从头开始,忽略 dirs_meta
        force_full=False → 若有 dirs_meta 则增量,否则全量

        安全网:如果距离上次全量已超过 force_full_after(默认 24h),
        自动升级为全量,避免长期增量可能漏掉上游不更新 mtime 的情况。
        """
        # 安全网:超过 24h 没全量 → 强制全量(即使有 dirs_meta)
        if not force_full and self.dirs_meta and self.last_full_at > 0:
            if time.time() - self.last_full_at > self.force_full_after:
                log.info(f"已 {time.time() - self.last_full_at:.0f}s 未全量,触发全量重建")
                force_full = True

        has_history = bool(self.dirs_meta) and not force_full
        with self.lock:
            if self.running:
                log.warning("已有构建在运行,跳过本次触发")
                return
            self.running = True
            self.dirs_visited = 0
            self.files_collected = 0
            self.bytes_total = 0
            self.current_path = ""
            self.started_at = time.time()
            self.last_error = ""
            self.mode = "incremental" if has_history else "full"
            self.last_skipped = 0
            self.last_rescanned = 0

        t0 = time.time()
        errors = 0

        if has_history:
            log.info(f"开始增量重建:已索引 {len(self.dirs_meta)} 目录,"
                     f"max_dirs={self.max_dirs}, dir_delay={self.dir_delay}s")
            new_entries, new_dirs_meta, skipped, rescanned = self._walk_incremental()
        else:
            log.info(f"开始全量重建: max_depth={self.max_depth}, "
                     f"max_dirs={self.max_dirs}, dir_delay={self.dir_delay}s")
            new_entries, new_dirs_meta = self._walk_full()
            skipped = 0
            rescanned = 0

        # 孤儿清理:entry 的 parent 必须还在 dirs_meta 中(被显式访问或被祖先"信任")
        known_dirs = set(new_dirs_meta.keys())
        before = len(new_entries)
        new_entries = [e for e in new_entries if e["parent"] in known_dirs]
        dropped = before - len(new_entries)
        if dropped:
            log.info(f"  清理孤儿条目: {dropped} 个")

        duration = time.time() - t0
        total_files = sum(1 for e in new_entries if not e["is_dir"])
        total_size = sum(e["size"] for e in new_entries if not e["is_dir"])

        # 原子写入磁盘
        try:
            tmp_path = self.INDEX_PATH + ".tmp"
            data = {
                "built_at": time.time(),
                "duration": duration,
                "mode": self.mode,
                "total_dirs": len(new_dirs_meta),
                "total_files": total_files,
                "total_size": total_size,
                "skipped": skipped,
                "rescanned": rescanned,
                "max_depth": self.max_depth,
                "entries": new_entries,
                "dirs_meta": new_dirs_meta,
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp_path, self.INDEX_PATH)
        except Exception as e:
            log.error(f"索引写入失败: {e}")
            with self.lock:
                self.last_error = f"写入失败: {e}"
                self.running = False
                self.mode = ""
            return

        with self.entries_lock:
            self.entries = new_entries
        with self.lock:
            self.dirs_meta = new_dirs_meta
            self.completed_at = time.time()
            self.last_duration = duration
            self.last_mode = self.mode
            self.last_skipped = skipped
            self.last_rescanned = rescanned
            self.current_path = ""
            self.running = False
            self.mode = ""
            # 全量完成时记录时间戳,用于 24h 安全网
            if not has_history or force_full:
                self.last_full_at = time.time()

        try:
            size_mb = os.path.getsize(self.INDEX_PATH) / 1024 / 1024
        except OSError:
            size_mb = 0
        log.info(f"索引重建完成 ({'增量' if has_history else '全量'}): "
                 f"{len(new_entries)} 条目 ({len(new_dirs_meta)} 目录签名, "
                 f"{total_files} 文件), 耗时 {duration:.1f}s, "
                 f"文件 {size_mb:.1f}MB, 跳过 {skipped}, 重扫 {rescanned}")

    def _walk_full(self):
        """全量 BFS 扫描:返回 (entries, dirs_meta)"""
        new_entries = []
        new_dirs_meta = {}
        visited = set()
        queue = collections.deque([("/", 0)])

        while queue and len(visited) < self.max_dirs:
            path, depth = queue.popleft()
            if path in visited or depth > self.max_depth:
                continue
            visited.add(path)

            with self.lock:
                self.current_path = path
                self.dirs_visited += 1

            try:
                items = self.client.list_dir(path)
            except Exception as e:
                log.debug(f"  索引: list {path} 异常: {e}")
                continue
            if items is None:
                continue

            sig = self._compute_sig(items)
            child_dirs = []
            for it in items:
                name = it.get("name", "")
                is_dir = it.get("is_dir", False)
                if not name:
                    continue
                full = (path.rstrip("/") + "/" + name) if path != "/" else "/" + name
                entry = {
                    "name": name, "size": it.get("size", 0),
                    "is_dir": is_dir, "modified": it.get("modified", ""),
                    "path": full, "parent": path,
                }
                new_entries.append(entry)
                if is_dir:
                    queue.append((full, depth + 1))
                    child_dirs.append(full)
                else:
                    with self.lock:
                        self.files_collected += 1
                        self.bytes_total += entry["size"]

            new_dirs_meta[path] = {
                "sig": sig,
                "child_dirs": child_dirs,
                "scanned_at": time.time(),
            }
            time.sleep(self.dir_delay)

        return new_entries, new_dirs_meta

    def _walk_incremental(self):
        """增量 BFS:对每个目录 fetch 一次,签名匹配则跳过子树,否则重扫该层。

        返回 (entries, dirs_meta, skipped_count, rescanned_count)
        """
        new_entries = list(self.entries)              # 起点:全部现有条目
        new_dirs_meta = dict(self.dirs_meta)          # 起点:全部现有签名
        visited = set()
        skipped = 0
        rescanned = 0
        queue = collections.deque([("/", 0)])

        while queue and len(visited) < self.max_dirs:
            path, depth = queue.popleft()
            if path in visited or depth > self.max_depth:
                continue
            visited.add(path)

            with self.lock:
                self.current_path = path
                self.dirs_visited += 1

            try:
                items = self.client.list_dir(path)
            except Exception as e:
                log.debug(f"  增量: list {path} 异常: {e}")
                continue
            if items is None:
                continue

            sig = self._compute_sig(items)
            old_meta = self.dirs_meta.get(path)

            # 签名未变 → 跳过子树递归,保留所有旧条目
            if old_meta and old_meta.get("sig") == sig:
                skipped += 1
                new_dirs_meta[path] = {**old_meta, "scanned_at": time.time()}
                time.sleep(self.dir_delay)
                continue

            # 签名变了(或新目录)→ 重扫这一层
            rescanned += 1

            # 丢弃该目录的直接子条目(它们会被下面的循环重新生成)
            new_entries = [e for e in new_entries if e["parent"] != path]

            # 处理当前 items,记录新的子目录路径
            child_dirs = []
            current_child_paths = set()
            for it in items:
                name = it.get("name", "")
                is_dir = it.get("is_dir", False)
                if not name:
                    continue
                full = (path.rstrip("/") + "/" + name) if path != "/" else "/" + name
                entry = {
                    "name": name, "size": it.get("size", 0),
                    "is_dir": is_dir, "modified": it.get("modified", ""),
                    "path": full, "parent": path,
                }
                new_entries.append(entry)
                if is_dir:
                    queue.append((full, depth + 1))
                    child_dirs.append(full)
                    current_child_paths.add(full)
                else:
                    with self.lock:
                        self.files_collected += 1
                        self.bytes_total += entry["size"]

            # 只丢弃真正消失的旧子目录(新 items 里没有的)
            # 仍存在的子目录保留其 dirs_meta,递归访问时签名匹配可以跳过子树
            if old_meta:
                for old_child in old_meta.get("child_dirs", []):
                    if old_child not in current_child_paths:
                        new_dirs_meta.pop(old_child, None)

            new_dirs_meta[path] = {
                "sig": sig,
                "child_dirs": child_dirs,
                "scanned_at": time.time(),
            }
            time.sleep(self.dir_delay)

        return new_entries, new_dirs_meta, skipped, rescanned


# ============== 代理请求处理器 ==============
class ProxyHandler(http.server.BaseHTTPRequestHandler):
    client = AlistClient()
    cache = UrlCache()
    hls_cache = UrlCache()  # 缓存 HLS 转码 URL,key = alist_path__tmpl__template_id
    search = None  # 保留旧接口以防旧 HTML 引用;实际搜索改走 indexer.search
    indexer = DirectoryIndexer(client=client)  # 后台索引,搜索读本地,完全不打上游
    protocol_version = "HTTP/1.1"   # 支持 keep-alive,Range 体验更好

    def log_message(self, format, *args):
        log.info(f"{self.client_address[0]} - {format % args}")

    def end_headers(self):
        """自动加 CORS 头,允许所有 origin(localhost 和 127.0.0.1 互访)"""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Range, If-None-Match, If-Modified-Since")
        self.send_header("Access-Control-Expose-Headers", "Content-Range, Content-Length, Accept-Ranges, ETag, Last-Modified")
        super().end_headers()

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(204)
        self.end_headers()

    # ---------- 路由 ----------
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path_only = parsed.path
        # 同时去前后斜杠,让 /__simple__/ 和 /__simple__ 等价
        path = urllib.parse.unquote(path_only.strip("/"))

        # 特殊端点
        if path == "__health__":
            self._text(200, "OK\n")
            return

        if path == "__simple__":
            self._serve_index()
            return

        if path == "__hls_test__":
            self._serve_file(os.path.join(os.path.dirname(INDEX_HTML_PATH), "hls_test.html"))
            return

        if path == "__api__/list" or path.startswith("__api__/list/"):
            sub = path[len("__api__/list"):].lstrip("/")
            self._handle_api_list(sub)
            return

        # 索引状态端点(GET /__api__/index/status) — UI 轮询显示进度
        if path == "__api__/index/status":
            self._handle_api_index_status()
            return

        if path == "__list__" or path.startswith("__list__/"):
            sub = path[len("__list__"):].lstrip("/")
            self._handle_list(sub)
            return

        # 视频流代理端点:/__stream__/<alist_path>
        if path == "__stream__" or path.startswith("__stream__/"):
            sub = path[len("__stream__"):].lstrip("/")
            self._handle_proxy(sub)
            return

        # HLS 代理端点:/__hls__/<alist_path__tmpl__template_id>/<filename>
        # 例如 /__hls__/每日更新/.../xxx.mp4__tmpl__QHD/media.m3u8
        # 注意:需要保留 query 参数(.ts URL 带签名)
        if path == "__hls__" or path.startswith("__hls__/"):
            # 用原始 self.path(含 query)去掉前缀
            full_path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).strip("/")
            sub = full_path[len("__hls__"):].lstrip("/")
            # 加上 query
            query = urllib.parse.urlsplit(self.path).query
            if query:
                sub = sub + "?" + query
            self._handle_hls_proxy(sub)
            return

        # 拦截 /d/<path> 和 /p/<path>(Alist 下载/播放端点)
        # 这些端点原本返回 302 到 OSS,改为本地视频流代理
        if path == "d" or path.startswith("d/"):
            sub = path[len("d"):].lstrip("/")
            self._handle_proxy(sub)
            return
        if path == "p" or path.startswith("p/"):
            sub = path[len("p"):].lstrip("/")
            self._handle_proxy(sub)
            return

        # 其他所有路径 → 反向代理 Alist(网页/API/静态资源)
        self._reverse_proxy("GET")

    def do_HEAD(self):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path.strip("/"))
        if path == "__health__":
            self._text_head(200, {"Content-Type": "text/plain"})
            return
        if path.startswith("__stream__"):
            sub = path[len("__stream__"):].lstrip("/")
            self._handle_proxy(sub, head_only=True)
            return
        if path.startswith("__hls__/"):
            full_path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).strip("/")
            sub = full_path[len("__hls__"):].lstrip("/")
            query = urllib.parse.urlsplit(self.path).query
            if query:
                sub = sub + "?" + query
            self._handle_hls_proxy(sub, head_only=True)
            return
        # /d/ 和 /p/ 也支持 HEAD
        if path.startswith("d/"):
            sub = path[len("d"):].lstrip("/")
            self._handle_proxy(sub, head_only=True)
            return
        if path.startswith("p/"):
            sub = path[len("p"):].lstrip("/")
            self._handle_proxy(sub, head_only=True)
            return
        if path.startswith("__api__/") or path.startswith("__list__") or path.startswith("__simple"):
            self._text_head(405, {"Content-Type": "text/plain"})
            return
        # 其他 HEAD → 反向代理
        self._reverse_proxy("HEAD")

    def do_POST(self):
        """POST 请求:反向代理到 Alist,拦截 /api/fs/get 改写 raw_url"""
        # 自实现的搜索端点(不走 Alist)
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path.strip("/"))
        if path == "__api__/search":
            self._handle_api_search()
            return
        if path == "__api__/index/start":
            self._handle_api_index_start()
            return
        self._reverse_proxy("POST")

    def do_PUT(self):
        self._reverse_proxy("PUT")

    def do_DELETE(self):
        self._reverse_proxy("DELETE")

    # ---------- 反向代理 Alist ----------
    def _reverse_proxy(self, method):
        """反向代理 Alist 网页和 API,拦截 /api/fs/get 改写 raw_url"""
        target_url = ALIST_URL + self.path
        log.info(f"反向代理 [{method}]: {self.path[:100]}")

        try:
            # 读取请求 body(如果有)
            body = None
            cl = self.headers.get("Content-Length")
            if cl and int(cl) > 0:
                body = self.rfile.read(int(cl))

            # 如果是 /api/fs/get 或 /api/fs/other,从 body 解析 path,缓存供改写响应用
            if self.path.startswith(("/api/fs/get", "/api/fs/other")) and body:
                try:
                    body_json = json.loads(body)
                    self._last_api_path = body_json.get("path", "")
                except Exception:
                    self._last_api_path = None
            else:
                self._last_api_path = None

            req = urllib.request.Request(target_url, method=method, data=body)

            # 转发请求头
            for h in ("Content-Type", "Authorization", "Cookie", "Accept",
                      "Referer", "Origin", "Range", "If-None-Match", "If-Modified-Since"):
                v = self.headers.get(h)
                if v:
                    req.add_header(h, v)
            # 伪装 UA,避免某些后端拒绝
            if not req.has_header("User-Agent"):
                req.add_header("User-Agent", "Mozilla/5.0 alist-proxy")
            req.add_header("Accept-Encoding", "identity")

            # 自定义 opener 禁止跟随重定向(避免 /d/ 端点 302 到内部 IP 死循环)
            class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None  # 不跟随重定向
            opener = urllib.request.build_opener(NoRedirectHandler)

            upstream = None
            try:
                upstream = opener.open(req, timeout=UPSTREAM_TIMEOUT)
            except urllib.error.HTTPError as e:
                # 302/301 会触发 HTTPError(因为禁止了重定向)
                if e.code in (301, 302, 303, 307, 308):
                    # 透传重定向,但改写 Location 中的 Alist URL
                    loc = e.headers.get("Location", "")
                    if loc and ALIST_URL in loc:
                        loc = loc.replace(ALIST_URL, f"http://{LISTEN_HOST}:{LISTEN_PORT}")
                        log.info(f"  重定向改写: {loc[:60]}...")
                    self.send_response(e.code)
                    for k, v in e.headers.items():
                        lk = k.lower()
                        if lk in ("transfer-encoding", "connection", "keep-alive",
                                  "server", "date", "content-length", "content-encoding", "location"):
                            continue
                        self.send_header(k, v)
                    if loc:
                        self.send_header("Location", loc)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                # 其他 HTTP 错误,走下面的错误处理
                raise

            try:
                status = upstream.status
                resp_headers = upstream.headers
                data = upstream.read()

                # 拦截 /api/fs/get 响应,改写 raw_url
                if self.path.startswith("/api/fs/get") and status == 200:
                    data = self._rewrite_fs_get_response(data)

                # 拦截 /api/fs/other 响应,改写 HLS 转码 URL
                if self.path.startswith("/api/fs/other") and status == 200:
                    data = self._rewrite_fs_other_response(data)

                # 转发响应
                self.send_response(status)
                SKIP = {"transfer-encoding", "connection", "keep-alive",
                        "server", "date", "content-length", "content-encoding"}
                for k, v in resp_headers.items():
                    lk = k.lower()
                    if lk in SKIP:
                        continue
                    self.send_header(k, v)

                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except BrokenPipeError:
                    pass
            finally:
                upstream.close()

        except urllib.error.HTTPError as e:
            log.warning(f"  上游 HTTP {e.code}: {e.reason}")
            try:
                err_body = e.read()
            except Exception:
                err_body = b""
            self.send_response(e.code)
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            try:
                self.wfile.write(err_body)
            except BrokenPipeError:
                pass
        except Exception as e:
            log.error(f"  反向代理异常: {e}")
            # /api/* 端点返回 JSON 错误,避免前端 JSON.parse 失败
            # 其他端点(网页/资源)返回纯文本
            if self.path.startswith("/api/"):
                err_msg = str(e) or e.__class__.__name__
                self._json(502, {
                    "code": 502,
                    "message": f"上游 Alist 无响应: {err_msg}",
                    "data": None,
                })
            else:
                self._text(502, f"反向代理失败: {e}\n")

    def _rewrite_fs_get_response(self, data):
        """改写 /api/fs/get 响应的 raw_url,指向本地 /__stream__"""
        try:
            j = json.loads(data)
            if j.get("code") != 200 or "data" not in j:
                return data
            # 从请求体解析 alist path
            # 注意:body 已经被读取,这里从 self 缓存拿不到
            # 用 data 里的 name 字段 + 推断不行,需要 path
            # 解决:在 _reverse_proxy 里缓存 body 和 path
            alist_path = getattr(self, "_last_api_path", None)
            if not alist_path:
                # 尝试从 raw_url 反推不了,直接返回原数据
                return data
            # 改写 raw_url
            encoded = urllib.parse.quote(alist_path.lstrip("/"))
            new_url = f"http://{LISTEN_HOST}:{LISTEN_PORT}/__stream__/{encoded}"
            log.info(f"  改写 raw_url → {new_url[:80]}...")
            j["data"]["raw_url"] = new_url
            return json.dumps(j, ensure_ascii=False).encode("utf-8")
        except Exception as e:
            log.warning(f"  改写 fs/get 响应失败: {e}")
            return data

    def _rewrite_fs_other_response(self, data):
        """改写 /api/fs/other 响应中的 HLS 转码 URL,指向本地 /__hls__"""
        try:
            j = json.loads(data)
            if j.get("code") != 200 or "data" not in j:
                return data

            data_obj = j.get("data") or {}
            vpi = data_obj.get("video_preview_play_info") or {}
            task_list = vpi.get("live_transcoding_task_list") or []

            if not task_list:
                return data

            alist_path = getattr(self, "_last_api_path", "") or ""
            if not alist_path:
                log.warning("  改写 fs/other: alist_path 为空,跳过")
                return data

            # 用 alist_path + template_id 做 cache key,把每个转码模板的 URL 都改写
            rewritten = 0
            for task in task_list:
                url = task.get("url")
                if url and "aliyundrive" in url:
                    template_id = task.get("template_id", "default")
                    cache_key = f"{alist_path}__tmpl__{template_id}"
                    self.hls_cache.put(cache_key, url)
                    # 改写为本地代理 URL(alist_path 以 / 开头,quote 后去掉前导 /)
                    encoded_key = urllib.parse.quote(cache_key.lstrip("/"))
                    new_url = f"http://{LISTEN_HOST}:{LISTEN_PORT}/__hls__/{encoded_key}/media.m3u8"
                    task["url"] = new_url
                    rewritten += 1
                    log.info(f"  改写 HLS [{template_id}] → 本地代理")

            if rewritten > 0:
                return json.dumps(j, ensure_ascii=False).encode("utf-8")
            return data
        except Exception as e:
            log.warning(f"  改写 fs/other 响应失败: {e}")
            return data

    # ---------- 视频代理 ----------
    def _handle_proxy(self, rel_path, head_only=False):
        alist_path = "/" + rel_path
        log.info(f"请求{'[HEAD] ' if head_only else ''}: {alist_path}")

        for attempt in range(MAX_RETRY_ON_403 + 1):
            url, is_fresh = self.cache.get(alist_path)
            if not url or not is_fresh:
                url = self.client.get_raw_url(alist_path)
                if not url:
                    self._text(502, "无法从 Alist 获取文件 URL\n")
                    return
                self.cache.put(alist_path, url)
                log.info(f"  获取新 URL(缓存至 {URL_CACHE_TTL // 60} 分钟后)")

            ok = self._proxy_to(url, alist_path, head_only=head_only)
            if ok:
                return

            # 失败,可能是 URL 过期或网络问题
            if attempt < MAX_RETRY_ON_403:
                log.warning(f"  代理失败,刷新 URL 重试 (attempt {attempt + 1}/{MAX_RETRY_ON_403})")
                self.cache.invalidate(alist_path)
            else:
                self._text(502, "代理失败,已重试多次\n")

    # ---------- HLS 代理 ----------
    def _handle_hls_proxy(self, rel_path, head_only=False):
        """
        处理 HLS 请求。rel_path 格式:<cache_key>/<filename>
        cache_key 是 alist_path__tmpl__template_id
        filename 是 media.m3u8 或 media-xxx.ts(可能带 query)
        """
        # 分离 cache_key 和 filename
        slash_idx = rel_path.rfind("/")
        if slash_idx < 0:
            self._text(400, "无效 HLS 路径\n")
            return
        cache_key_encoded = rel_path[:slash_idx]
        filename_part = rel_path[slash_idx + 1:]

        # 分离 filename 和 query
        if "?" in filename_part:
            filename, query_string = filename_part.split("?", 1)
        else:
            filename = filename_part
            query_string = ""

        # URL 解码 cache_key
        # cache_key 去掉了前导 /,需要加回来
        cache_key = "/" + urllib.parse.unquote(cache_key_encoded)

        log.info(f"HLS 请求{'[HEAD] ' if head_only else ''}: key={cache_key[:60]}... file={filename}")

        for attempt in range(MAX_RETRY_ON_403 + 1):
            m3u8_url, is_fresh = self.hls_cache.get(cache_key)
            if not m3u8_url or not is_fresh:
                if "__tmpl__" in cache_key:
                    alist_path, template_id = cache_key.rsplit("__tmpl__", 1)
                else:
                    alist_path, template_id = cache_key, "QHD"

                m3u8_url = self.client.get_hls_url(alist_path, template_id)
                if not m3u8_url:
                    self._text(502, f"无法获取 HLS URL (path={alist_path}, template={template_id})\n")
                    return
                self.hls_cache.put(cache_key, m3u8_url)
                log.info(f"  获取新 HLS URL(缓存至 {URL_CACHE_TTL // 60} 分钟后)")

            if filename.endswith(".m3u8"):
                ok = self._proxy_m3u8(m3u8_url, cache_key, head_only=head_only)
            else:
                # .ts 文件:从 hls_cache 拿完整 URL(含 query,在 _proxy_m3u8 里缓存)
                ts_cache_key = f"{cache_key}/{filename}"
                ts_url, ts_fresh = self.hls_cache.get(ts_cache_key)
                if not ts_url or not ts_fresh:
                    # .ts URL 不在缓存或已过期,重新下载 m3u8 拿新签名
                    log.info(f"  .ts URL 不在缓存或已过期,重新下载 m3u8 拿新签名")
                    # 强制刷新 m3u8 URL(从 Alist 重新拿)
                    if "__tmpl__" in cache_key:
                        alist_path, template_id = cache_key.rsplit("__tmpl__", 1)
                    else:
                        alist_path, template_id = cache_key, "QHD"
                    fresh_m3u8_url = self.client.get_hls_url(alist_path, template_id)
                    if fresh_m3u8_url:
                        m3u8_url = fresh_m3u8_url
                        self.hls_cache.put(cache_key, m3u8_url)
                    # 重新下载 m3u8 内容(静默模式,只更新 .ts 缓存)
                    self._proxy_m3u8(m3u8_url, cache_key, head_only=False, silent=True)
                    ts_url, ts_fresh = self.hls_cache.get(ts_cache_key)

                if not ts_url:
                    self._text(502, f"无法获取 .ts URL: {filename}\n")
                    return

                ok = self._proxy_to(ts_url, ts_cache_key, head_only=head_only)

            if ok:
                return
            if attempt < MAX_RETRY_ON_403:
                log.warning(f"  HLS 代理失败,刷新 URL 重试 (attempt {attempt + 1}/{MAX_RETRY_ON_403})")
                self.hls_cache.invalidate(cache_key)
                # 同时失效 .ts 缓存,强制下次重新下载 m3u8
                if not filename.endswith(".m3u8"):
                    ts_cache_key = f"{cache_key}/{filename}"
                    self.hls_cache.invalidate(ts_cache_key)
            else:
                self._text(502, "HLS 代理失败,已重试多次\n")

    def _proxy_m3u8(self, m3u8_url, cache_key, head_only=False, silent=False):
        """下载 m3u8,改写 .ts 路径为本地代理,返回给客户端
        silent=True 时只更新 .ts 缓存,不发送响应(用于 .ts 续期)
        """
        try:
            req = urllib.request.Request(m3u8_url, method="GET")
            req.add_header("User-Agent", "Mozilla/5.0 alist-proxy")
            with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as upstream:
                if upstream.status == 403:
                    return False
                content = upstream.read().decode("utf-8")

                # 改写 m3u8 中的 .ts 路径
                # m3u8 里的 .ts 通常是相对路径,如 media-1.ts 或 media-1.ts?xxx
                # 把完整 .ts URL(含 query)存入 hls_cache,key = cache_key/filename
                # 改写为 /__hls__/<cache_key>/<filename>(不含 query,query 在缓存里)
                base_path = f"/__hls__/{urllib.parse.quote(cache_key.lstrip('/'))}"
                # m3u8 URL 的 base(用于拼 .ts 的完整 URL)
                m3u8_base = m3u8_url.rsplit("/", 1)[0]
                m3u8_query = ""
                if "?" in m3u8_url:
                    m3u8_query = m3u8_url.split("?", 1)[1]

                lines = content.split("\n")
                rewritten = []
                for line in lines:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        path_part = stripped.split("?", 1)[0]
                        if path_part.endswith(".ts") or ".ts?" in stripped:
                            # 分离 filename 和 query
                            if "?" in stripped:
                                fname, ts_query = stripped.split("?", 1)
                            else:
                                fname, ts_query = stripped, m3u8_query

                            # 拼完整的 .ts URL(阿里 CDN)
                            if ts_query:
                                full_ts_url = f"{m3u8_base}/{fname}?{ts_query}"
                            else:
                                full_ts_url = f"{m3u8_base}/{fname}"

                            # 存入缓存,key = cache_key/fname
                            ts_cache_key = f"{cache_key}/{fname}"
                            self.hls_cache.put(ts_cache_key, full_ts_url)

                            # 改写为本地 URL(不含 query)
                            rewritten.append(f"{base_path}/{fname}")
                        else:
                            rewritten.append(line)
                    else:
                        rewritten.append(line)
                new_content = "\n".join(rewritten).encode("utf-8")

                if silent:
                    log.info(f"  m3u8 静默更新 .ts 缓存({len(new_content)} 字节)")
                    return True
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(new_content)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                if not head_only:
                    try:
                        self.wfile.write(new_content)
                    except BrokenPipeError:
                        pass
                log.info(f"  m3u8 改写完成({len(new_content)} 字节)")
                return True
        except urllib.error.HTTPError as e:
            if e.code == 403:
                return False
            log.error(f"  m3u8 下载失败: HTTP {e.code}")
            if not silent:
                self._text(502, f"m3u8 下载失败: HTTP {e.code}\n")
            return True
        except Exception as e:
            log.error(f"  m3u8 代理异常: {e}")
            if not silent:
                self._text(500, f"m3u8 代理异常: {e}\n")
            return True

    def _proxy_to(self, raw_url, alist_path, head_only=False):
        """转发请求到 raw_url。返回 True 表示已处理(无论成功失败),False 表示需要刷新 URL 重试"""
        try:
            req = urllib.request.Request(raw_url, method="GET")
            # 转发 Range 头(视频 seek 必需,HEAD 和 GET 都需要)
            for h in ("Range", "User-Agent", "Accept"):
                v = self.headers.get(h)
                if v:
                    req.add_header(h, v)
            # 避免上游返回压缩,视频不需要
            req.add_header("Accept-Encoding", "identity")

            with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as upstream:
                status = upstream.status
                log.info(f"  上游: HTTP {status}")

                if status == 403:
                    return False  # URL 过期,需要刷新

                # 转发响应头(过滤会引起问题的头)
                self.send_response(status)
                sent_headers = set()
                # 这些头 Python 会自动加,或会引起浏览器混淆,不转发上游的
                SKIP_HEADERS = {
                    "transfer-encoding", "connection", "keep-alive",
                    "server", "date",                     # Python 自动加
                    "content-disposition",                # 阿里 OSS 强制 attachment,会导致浏览器下载而非播放
                    "x-oss-request-id", "x-oss-server-time",
                    "x-oss-object-type", "x-oss-hash-func",
                    "x-oss-hash-value", "x-oss-hash-crc64ecma",
                    "x-oss-storage-class",               # OSS 内部头,无需暴露
                }
                for k, v in upstream.headers.items():
                    lk = k.lower()
                    if lk in SKIP_HEADERS:
                        continue
                    self.send_header(k, v)
                    sent_headers.add(lk)

                if head_only:
                    self.end_headers()
                    log.info(f"  HEAD 完成")
                    return True

                # 如果没有 Content-Length,用 chunked(由 protocol_version HTTP/1.1 处理)
                if "content-length" not in sent_headers and "content-range" not in sent_headers:
                    self.send_header("Transfer-Encoding", "chunked")

                self.end_headers()

                # 流式转发 body
                total = 0
                try:
                    while True:
                        chunk = upstream.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        total += len(chunk)
                except BrokenPipeError:
                    log.info(f"  客户端断开,已转发 {total} 字节")
                except ConnectionResetError:
                    log.info(f"  连接重置,已转发 {total} 字节")
                log.info(f"  转发完成: {total} 字节")
                return True

        except urllib.error.HTTPError as e:
            log.warning(f"  上游 HTTP {e.code}: {e.reason}")
            if e.code == 403:
                return False
            try:
                self.send_response(e.code)
                self.end_headers()
                self.wfile.write(e.read()[:512])
            except Exception:
                pass
            return True
        except urllib.error.URLError as e:
            log.error(f"  上游连接失败: {e.reason}")
            try:
                self._text(502, f"上游连接失败: {e.reason}\n")
            except Exception:
                pass
            return True
        except Exception as e:
            log.error(f"  代理异常: {e}")
            try:
                self._text(500, f"代理异常: {e}\n")
            except Exception:
                pass
            return True

    # ---------- Web 首页 ----------
    def _serve_file(self, filepath):
        """服务一个静态文件"""
        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except Exception as e:
            self._text(404, f"文件不存在: {e}\n")
            return
        ext = os.path.splitext(filepath)[1].lower()
        ct = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
              ".css": "text/css", ".json": "application/json"}.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def _serve_index(self):
        try:
            with open(INDEX_HTML_PATH, "rb") as f:
                html = f.read()
        except Exception as e:
            log.error(f"读取首页 HTML 失败: {e}")
            self._text(500, f"首页 HTML 读取失败: {e}\n")
            return
        # 注入 Alist token(替换页面里的占位符)
        token = self.client.token or ""
        if token:
            html = html.replace(b"__ALIST_TOKEN__", token.encode("utf-8"))
            log.info(f"  注入 token 到简易网页(长度 {len(token)})")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(html)
        except BrokenPipeError:
            pass

    # ---------- JSON API ----------
    def _handle_api_list(self, rel_path):
        alist_path = "/" + rel_path if rel_path else "/"
        items = self.client.list_dir(alist_path)
        if items is None:
            self._json(500, {"code": 500, "message": "无法列出目录", "data": None})
            return
        # 精简返回字段
        data = [
            {
                "name": it["name"],
                "size": it.get("size", 0),
                "is_dir": it["is_dir"],
                "modified": it.get("modified", ""),
            }
            for it in items
        ]
        self._json(200, {"code": 200, "message": "success", "data": data})

    def _handle_api_search(self):
        """全局搜索 API(POST /__api__/search)
        直接读本地索引,完全不打上游,保护资源。
        索引未建好时返回 code=202 + progress,前端据此显示"索引中"提示。
        """
        try:
            cl = self.headers.get("Content-Length")
            if not cl or int(cl) == 0:
                self._json(400, {"code": 400, "message": "缺少请求体", "data": None})
                return
            body = self.rfile.read(int(cl))
            params = json.loads(body)
        except Exception as e:
            self._json(400, {"code": 400, "message": f"请求体解析失败: {e}", "data": None})
            return

        q = (params.get("q") or params.get("keywords") or "").strip()
        parent = (params.get("parent") or "/").strip() or "/"
        scope = int(params.get("scope", 0))
        page = max(1, int(params.get("page", 1)))
        per_page = min(200, max(1, int(params.get("per_page", 50))))
        max_depth = params.get("max_depth")
        if max_depth is not None:
            try:
                max_depth = min(8, max(0, int(max_depth)))
            except (TypeError, ValueError):
                max_depth = None

        t0 = time.time()
        result = self.indexer.search(q, parent, scope, page, per_page, max_depth)
        elapsed = time.time() - t0
        total = result.get("data", {}).get("total", 0)
        source = result.get("source", "index")
        log.info(f"搜索 '{q}' parent={parent} scope={scope} page={page}: "
                 f"命中 {total},耗时 {elapsed * 1000:.0f}ms,来源={source}")
        self._json(200, result)

    def _handle_api_index_status(self):
        """索引状态 API(GET /__api__/index/status)"""
        status = self.indexer.get_status()
        self._json(200, {"code": 200, "message": "success", "data": status})

    def _handle_api_index_start(self):
        """手动触发重建索引(POST /__api__/index/start[?force_full=true])"""
        parsed = urllib.parse.urlsplit(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        force_full = qs.get("force_full", ["false"])[0].lower() in ("1", "true", "yes")
        self.indexer.trigger_now(force_full=force_full)
        self._json(200, {
            "code": 200,
            "message": f"已请求{'全量' if force_full else '增量'}重建索引,详见 /__api__/index/status",
            "data": self.indexer.get_status(),
        })

    # ---------- 目录浏览(纯文本,保留兼容) ----------
    def _handle_list(self, rel_path):
        alist_path = "/" + rel_path if rel_path else "/"
        items = self.client.list_dir(alist_path)
        if items is None:
            self._text(500, "无法列出目录\n")
            return

        lines = [f"目录: {alist_path}", f"共 {len(items)} 项", ""]
        playable = []
        for item in items:
            name = item["name"]
            if item["is_dir"]:
                lines.append(f"  [DIR]  {name}/")
            else:
                size_mb = item["size"] / 1024 / 1024
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                is_video = ext in ("mp4", "mkv", "ts", "iso", "mov", "avi", "m2ts", "webm", "flv")
                marker = "  [可播放]" if is_video else ""
                lines.append(f"         {name}  ({size_mb:.1f} MB){marker}")
                if is_video:
                    playable.append(name)

        if playable:
            lines.append("")
            lines.append("=== 可播放 URL ===")
            base = f"http://{LISTEN_HOST}:{LISTEN_PORT}"
            for name in playable:
                full = f"{rel_path}/{name}"
                encoded = urllib.parse.quote(full)
                lines.append(f"  {base}/{encoded}")

        # 子目录链接
        dirs = [i["name"] for i in items if i["is_dir"]]
        if dirs:
            lines.append("")
            lines.append("=== 子目录 ===")
            for d in dirs:
                full = f"{rel_path}/{d}" if rel_path else d
                encoded = urllib.parse.quote(full)
                lines.append(f"  http://{LISTEN_HOST}:{LISTEN_PORT}/__list__/{encoded}")

        self._text(200, "\n".join(lines) + "\n")

    # ---------- 辅助 ----------
    def _text(self, status, body):
        body_b = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body_b)))
        self.end_headers()
        try:
            self.wfile.write(body_b)
        except BrokenPipeError:
            pass

    def _json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _text_head(self, status, headers):
        """HEAD 响应:只有头部"""
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    # 配置校验:必须填 ALIST_USER 和 ALIST_PASS(来自 ~/.config/alist-proxy/config)
    if not ALIST_USER or not ALIST_PASS:
        print("=" * 60, file=sys.stderr)
        print("❌ 缺少 Alist 凭据", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"   ALIST_URL  = {ALIST_URL}", file=sys.stderr)
        print(f"   ALIST_USER = {ALIST_USER!r}", file=sys.stderr)
        print(f"   ALIST_PASS = {'(已设置)' if ALIST_PASS else '(空)'}", file=sys.stderr)
        print("", file=sys.stderr)
        print("请编辑配置文件:", file=sys.stderr)
        print("    ~/.config/alist-proxy/config", file=sys.stderr)
        print("", file=sys.stderr)
        print("或设置环境变量后重试:", file=sys.stderr)
        print("    export ALIST_USER='your_user'", file=sys.stderr)
        print("    export ALIST_PASS='your_password'", file=sys.stderr)
        print("", file=sys.stderr)
        print("首次安装请运行 install.sh。", file=sys.stderr)
        sys.exit(1)

    port = LISTEN_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"无效端口: {sys.argv[1]}", file=sys.stderr)
            sys.exit(1)

    # 打印生效配置(密码脱敏)
    log.info(f"生效配置: ALIST_URL={ALIST_URL} ALIST_USER={ALIST_USER} "
             f"LISTEN={LISTEN_HOST}:{port}")

    # 启动时尝试登录(失败也不退出,改为懒登录:请求时再试)
    if not ProxyHandler.client.login():
        log.warning("启动登录失败,服务将延迟登录(请求时再试)")

    # 启动后台索引线程(幂等):已有磁盘索引立即可用,后台慢慢刷新
    ProxyHandler.indexer.start()

    server = ThreadingHTTPServer((LISTEN_HOST, port), ProxyHandler)
    log.info(f"代理服务启动: http://{LISTEN_HOST}:{port}")
    log.info(f"  Web 首页: http://localhost:{port}/")
    log.info(f"  目录 API: http://localhost:{port}/__api__/list/<路径>")
    log.info(f"  全局搜索: http://localhost:{port}/__api__/search")
    log.info(f"  索引状态: http://localhost:{port}/__api__/index/status")
    log.info(f"  触发重建: POST http://localhost:{port}/__api__/index/start")
    log.info(f"  健康检查: http://localhost:{port}/__health__")
    log.info(f"  按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("停止中...")
        server.shutdown()


if __name__ == "__main__":
    main()
