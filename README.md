# Alist 反向代理 + 本地索引

一个运行在你 Chromebook 上的 Python 小服务,给上游 Alist 做反向代理。专为资源受限的上游设计:

- **15 分钟 URL 自动续期**:阿里云盘等存储的临时链接过期不再卡死,自动续签
- **本地全局索引**:后台慢慢爬目录存到本地,搜索不打上游
- **增量重建**:目录无变化时几乎零开销(只查 1 个目录),24h 自动全量兜底
- **一键安装**:整个项目只有一个 `install.sh`,在另一台机器上 30 秒搞定

## 系统要求

- Python 3.11+(Chromebook 开启 Linux 子系统后通常已有 `python3`)
- Linux + systemd(Crostini 默认满足)
- 网络可达上游 Alist

无任何第三方依赖,只用 Python 标准库。

## 一键安装

```bash
# 1. 把整个项目目录传到 Chromebook(USB / 云盘 / git clone 都行)
cd alist-proxy-pkg

# 2. 运行安装脚本(交互式填 Alist URL / 用户名 / 密码)
bash install.sh
```

非交互模式(脚本/CI 场景):

```bash
ALIST_URL=http://alist.example.com:5244 \
ALIST_USER=myuser \
ALIST_PASS=mypass \
bash install.sh --non-interactive
```

装好后访问: <http://localhost:8080/__simple__/>

### 从远端镜像一键安装

项目同时发布在一个固定 URL 上,可以在一台全新的 Chromebook / Linux 机器上 **只跑一行 curl** 完成安装:

```bash
curl -fsSL http://YOUR-MIRROR-HOST/alist-proxy-pkg/install-remote.sh \
  | ALIST_URL='http://your-alist-host:5678' \
    ALIST_USER='your-user' ALIST_PASS='your-pass' \
    bash -s -- --non-interactive
```

> **安全提醒:** 上面是占位符,不要把真实凭据直接写进任何文档、截图或提交记录。本机执行时,真实 `ALIST_USER`/`ALIST_PASS` 通过环境变量在本地传入,不会出现在服务器日志或镜像包内。

它会:

1. 从 `YOUR-MIRROR-HOST` 下载最新 tarball + sha256
2. 校验 sha256(失败立即中止,不会装损坏的包)
3. 解压到临时目录
4. 调用项目自带的 `install.sh` 完成安装

装好之后:

```bash
systemctl --user enable --now alist-proxy
```

打开 <http://localhost:8080/__simple__/>。

可选环境变量(必须在 `curl` 之前用 `env` 传,不要写在管道后面):

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ALIST_PROXY_BASE_URL` | `http://YOUR-MIRROR-HOST/alist-proxy-pkg` | tarball / sha256 所在的基础 URL |
| `ALIST_URL` | — | 上游 Alist 地址(必填,否则 install.sh 会进交互模式) |
| `ALIST_USER` / `ALIST_PASS` | — | 上游 Alist 凭据(必填) |

例:把镜像换到另一个 URL,只需:

```bash
ALIST_PROXY_BASE_URL=https://my-mirror.example.com/alist-proxy-pkg \
curl -fsSL "$ALIST_PROXY_BASE_URL/install-remote.sh" \
  | ALIST_URL=... ALIST_USER=... ALIST_PASS=... bash -s -- --non-interactive
```

> **注意:** `install-remote.sh` 里没有把任何凭据硬编码进去 — 凭据只通过管道前的 env 变量传递,不会被记录到服务器访问日志。代价是 curl 命令比较长。如果你接受"凭据以明文形式托管在 YOUR-MIRROR-HOST"的代价,可以让我把默认值写到 `install-remote.sh` 里,这样 curl 命令就只剩一行纯 `curl | bash`。

## 卸载

```bash
bash uninstall.sh          # 保留配置和索引,可重装继续用
bash uninstall.sh --purge  # 完全清理
```

## 常用命令

```bash
systemctl --user status alist-proxy      # 看状态
systemctl --user restart alist-proxy     # 重启
systemctl --user stop alist-proxy        # 停止
journalctl --user -u alist-proxy -f      # 实时日志

# 索引重建
curl -X POST http://localhost:8080/__api__/index/start              # 增量重建
curl -X POST "http://localhost:8080/__api__/index/start?force_full=true"  # 强制全量

# 看索引状态
curl http://localhost:8080/__api__/index/status | python3 -m json.tool
```

## 配置文件

路径:`~/.config/alist-proxy/config`(权限 600,只有你的用户能读)

```ini
ALIST_URL=http://alist.example.com:5244
ALIST_USER=myuser
ALIST_PASS=mypassword
LISTEN_HOST=localhost
LISTEN_PORT=8080
```

修改后重启生效:`systemctl --user restart alist-proxy`

## 文件分布

| 路径 | 作用 |
|---|---|
| `~/.local/bin/alist_proxy.py` | 主程序 |
| `~/.local/bin/alist_proxy_index.html` | 网页 UI(打包进 HTML) |
| `~/.config/alist-proxy/config` | 配置(URL/凭据/端口) |
| `~/.config/systemd/user/alist-proxy.service` | systemd 单元 |
| `~/.local/share/alist_proxy/index.json` | 本地索引(后台构建,持久) |

## 全局搜索是怎么工作的

1. 代理启动时从磁盘加载已有索引(若有,立即可用)
2. 后台线程用"礼貌延迟"(0.5s/目录)爬上游目录,缓存在本地
3. 用户搜索时,代理**完全不打上游**,只读本地索引文件,几乎瞬时
4. 后续重建走"增量":按目录签名剪枝,只重扫变化的子树
5. 每 24 小时自动全量扫描一次(防止上游不更新父目录 mtime 时漏掉变化)

上限:`max_depth=4`、`max_dirs=2000`(`max_depth=4` 的扫描约 17 分钟封顶)。

## 从其它设备访问

默认监听 `localhost`(只本机能访问)。要让同网络其它设备也能看,把 `LISTEN_HOST` 改为 `0.0.0.0`:

```ini
LISTEN_HOST=0.0.0.0
LISTEN_PORT=8080
```

然后通过 `http://<chromebook-ip>:8080/__simple__/` 访问。

## 故障排查

**服务起不来:**
```bash
journalctl --user -u alist-proxy -n 50
```

**搜索结果陈旧:**
- 等 24 小时(下次自动全量),或
- 手动:`curl -X POST http://localhost:8080/__api__/index/start?force_full=true`

**上游 Alist 慢/卡:** 完全没事 — 搜索读本地索引,跟上游状态无关。浏览页面才会受上游影响。

**Python 版本太低:** Chromebook 默认 Linux 容器有时是 3.9,需要装 3.11+:
```bash
sudo apt install python3.11
# 或用 pyenv
```

## 设计原则

- **零第三方依赖**:只有 Python 标准库,任何 Linux 都能跑
- **零额外内存**:空闲时只占 ~30MB RSS
- **保护上游**:浏览器打开页面时上游不会被打爆
- **无状态代理**:停掉服务不影响已缓存的播放会话(URL 缓存只在内存)
