# Changelog

所有版本变动记在这里。格式参考 [Keep a Changelog](https://keepachangelog.com/)。

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