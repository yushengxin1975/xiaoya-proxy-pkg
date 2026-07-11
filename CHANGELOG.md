# Changelog

所有版本变动记在这里。格式参考 [Keep a Changelog](https://keepachangelog.com/)。

## [Unreleased]

## [0.3.1] - 2026-07-11

### Added
- **虚拟根目录挂载点**:通过 `EXTRA_ROOT_LINKS="显示名|/path"` 配置,把 Alist 其他 storage 上的路径在代理页根目录以"🔗"虚拟文件夹的形式露出。Alist 端建议把这些 storage 挂到独立子路径(如 `/my_aliyun`),避免和小雅分享库在 Alist 自身根视图撞车。
  - 端点:`GET /__api__/extra_roots`
  - 前端在根目录列表顶部插入虚拟条目 + 一条"小雅分享库"分隔线,点击直接跳到挂载路径
  - 用途:把个人阿里云盘转存目录、小雅分享库之外的存储等统一汇聚到代理页根

## [0.3.0] - 2026-07-11

### Added
- **播放历史功能**(代理页"📜 历史"按钮):
  - 新增 `PlayHistoryStore` 持久化类,存储于 `~/.local/share/alist_proxy/history.json`,上限 200 条
  - 自动记录触发点:`/__stream__/<path>` 首次 Range 命中 + `/__hls__/<path>/media.m3u8` 命中(seek 时的后续 Range 不重复计数)
  - 60 秒内同路径去重,再次播放只 +1 计数并刷新时间
  - 端点:`GET /__api__/history`(列表)、`POST /__api__/history/record`(前端兜底)、`POST /__api__/history/clear`(全清)、`DELETE /__api__/history?path=`(删单条)
  - 前端面板:列表 + 累计播放次数 + "X 分钟前"相对时间 + 单条删除 + 一键清空 + 点击直达播放

## [Unreleased]

### Fixed
- **Windows install.ps1 在中文 Windows 上崩溃**: UTF-8 脚本无 BOM 被 GBK 控制台破坏解析 → 给脚本加 UTF-8 BOM
- **Windows install.ps1 在 PS 5.1 `-File` 模式下 "无法识别 Ensure-Nssm 等函数"**: 5.1 不提升后续定义的函数 → 把 `Ensure-Nssm` / `Register-NssmService` / `Register-SchtasksTask` 三个函数定义挪到主逻辑之前
- **第二次 install 时 `Set-Content : 拒绝访问`**: 首次 install 的 `icacls /grant:r :R` 把 config 收紧到只读,二次 install 无法覆盖 → 改成 `:RW`,并在 Set-Content 前先 icacls /grant 恢复当前用户写权限
- **`schtasks /delete` 任务不存在时退出码非零触发脚本退出**: 用 `cmd /c "schtasks /delete ... >nul 2>&1"` 吞掉错误
- **`ValueError: invalid literal for int() with base 10: '"8080"'`**: `alist_proxy.py` 只读环境变量、不读 config 文件;原 wrapper 直接 `set KEY=VALUE` 把 `LISTEN_PORT="8080"` 的引号带进去 → 改用 PowerShell wrapper 解析 config(去掉外层引号)再 export
- **ScheduledTask 后备模式登录后弹 cmd 窗口**: PowerShell wrapper 跑 `python.exe` 会创建 console,任务计划程序又给 wrapper bat 起一个新 cmd 窗口 → wrapper 改调 `pythonw.exe`(无 console);同时给任务设 `Hidden=true`,任务计划程序登录触发时不再显示 wrapper 窗口

### Added
- **非管理员 Windows 用户的安装路径**: NSSM 服务和 `schtasks /create` 都需要管理员,普通用户(Win10/11 默认账户)装不上 → 增加 PowerShell `Register-ScheduledTask` 后备路径(`LogonType=Interactive` + `RunLevel=Limited`),自动检测并降级
- **`run_proxy.bat` + `run_proxy.ps1` wrapper**: PowerShell 解析 config 设环境变量后再启 python,避免 cmd `for /f` + `^"` 引号剥离的兼容问题

## [0.2.0] - 2026-06-28

### Added
- **HLS URL 续签**: `get_hls_url` 加指数退避重试(2s/4s/8s),容忍 Alist 偶发 `NotFound.File`
- **后台异步续签**: 同步重试用尽后,丢后台线程每 5s 试一次,最长约 30 分钟
- **小雅页面兜底脚本** `PROXY_FALLBACK_JS`: 注入到小雅页 HTML,`MutationObserver` 监听 NotFound 错误文案,触发后切到代理 `/__hls__/` 通道续播
- **字幕基础设施**:
  - `__subtitle__/<base64>` 端点代理阿里云盘 VTT,加 CORS 头
  - m3u8 注入 `EXT-X-MEDIA TYPE=SUBTITLES` 声明,让 hls.js / ArtPlayer 暴露字幕选择
- **小雅页面字幕切换 UI**: 浮动面板 + 透明背景 + 文字阴影,Kodi 风格
- **`/__api__/version`** 端点返回运行时版本
- **`/__health__`** 响应附带版本号
- **Windows 原生安装器** `windows/install.ps1`:
  - 探测 Python 3.11+ (支持 `python` / `py -3` / `python3` / `py -3.11`)
  - 默认下载 NSSM 注册为 Windows Service `xiaoya-proxy`
  - NSSM 不可用时自动降级到 `schtasks onlogon`(无需管理员)
  - `-NonInteractive` / `-NoStart` / `-ScheduledTask` / `-Port` 参数
- **Linux 一键升级** `update.sh`:`git pull` + 文件覆盖 + `systemctl restart`,支持 `--dry-run`

### Fixed
- 搜索结果里点击目录时的页面竞态(`exitSearch` 双触 `route()` 导致页面停在旧路径),`exitSearch(restoreView)` 新增参数,目录点击传 `false` 跳过恢复路由

## [0.1.0] - 2026-06-26

### Added
- 初始发布:Alist 反向代理 + 本地目录索引
- 15 分钟 URL 自动续期
- 简易页面 `__simple__/`(目录浏览 + 全局搜索 + 浏览器内 HLS 播放)
- 一键安装脚本 `install.sh` + `install-remote.sh`