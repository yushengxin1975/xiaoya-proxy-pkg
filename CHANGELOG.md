# Changelog

所有版本变动记在这里。格式参考 [Keep a Changelog](https://keepachangelog.com/)。

## [Unreleased]

### Fixed
- **Windows install.ps1 在中文 Windows 上崩溃**: UTF-8 脚本无 BOM 被 GBK 控制台破坏解析 → 给脚本加 UTF-8 BOM
- **Windows install.ps1 在 PS 5.1 `-File` 模式下 "无法识别 Ensure-Nssm 等函数"**: 5.1 不提升后续定义的函数 → 把 `Ensure-Nssm` / `Register-NssmService` / `Register-SchtasksTask` 三个函数定义挪到主逻辑之前
- **第二次 install 时 `Set-Content : 拒绝访问`**: 首次 install 的 `icacls /grant:r :R` 把 config 收紧到只读,二次 install 无法覆盖 → 改成 `:RW`,并在 Set-Content 前先 icacls /grant 恢复当前用户写权限
- **`schtasks /delete` 任务不存在时退出码非零触发脚本退出**: 用 `cmd /c "schtasks /delete ... >nul 2>&1"` 吞掉错误
- **`ValueError: invalid literal for int() with base 10: '"8080"'`**: `alist_proxy.py` 只读环境变量、不读 config 文件;原 wrapper 直接 `set KEY=VALUE` 把 `LISTEN_PORT="8080"` 的引号带进去 → 改用 PowerShell wrapper 解析 config(去掉外层引号)再 export

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