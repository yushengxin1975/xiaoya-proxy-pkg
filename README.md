# xiaoya-proxy — 小雅原生网页代理

本项目是**小雅原生网页**的本地代理,主要解决原生网页在小雅转存视频上的三个体验问题:

## 🎯 解决的核心问题

### 1. 原生网页播放视频 15 分钟后卡住
小雅转存到阿里云盘的视频走 Alist 的转码接口 (`video_preview`),**签名 URL TTL 只有 15 分钟**。原生播放器在播放到 ~14 分钟时,下一个 `.ts` 请求会拿到 502,需要刷新页面才能继续。

本代理接管 `/__hls__/` 通道后:
- 自动在 URL 临过期 (<3 分钟) 时 schedule bg 续签
- `.ts` URL 失效时自动用 peek() 兜底旧签名
- 即使上游偶发 502,代理返回 `Retry-After: 3`,浏览器静默重试

### 2. 原生网页不能对整个目录排序
小雅原生 UI 只能对**当前页**(一页 ~50 个文件)排序,翻页后又乱。本代理提供 `__simple__/` 简化版 UI,**全目录一次性展示**,支持按文件名/大小/修改时间排序,且对**所有子目录递归生效**。

### 3. 原生网页不显示字幕轨道,不支持单独字幕文件
小雅原生播放器有 3 个槽位:①内置字幕 ②同名字幕 ③外挂字幕。但:
- 阿里云盘转码返回的视频**不包含字幕声明**,原生播放器看不到字幕选项
- 同目录字幕(`.srt` / `.ass` / `.vtt`)原生不会自动加载
- 外挂字幕需要手动上传

本代理 `PROXY_FALLBACK_JS` 自动:
- 通过代理调用 `video_preview` 拿字幕轨 → 注入 `<track>`
- 扫描视频同目录的 `.srt/.ass/.vtt/.sub/.ssa` 文件自动加载
- 支持外部字幕(代理注入)

## 🎬 附加功能(超出上述 3 点的进阶)

### 本地 .ts 段缓存(防风控)
- 阿里云盘有时会对转码请求做**风控**(返回 500 NotFound.File),原生播放器直接报"无法播放"
- 代理把 `.ts` 段缓存到本地磁盘,200GB LRU
- 后台 worker 自动拉取正在播放的视频到本地
- 上游断流时自动回退本地缓存

### 缓存进度可视化
- 播放器右下角浮动面板,实时显示磁盘缓存段数(如 `▓ 256/1185 段 (21.6%)`)
- 直观看到"本地有多少段可用"

### 真实 EXTINF 修复音频变调
- 降级 m3u8 用真实段时长,避免 PTS 漂移导致的音频变调/怪声

## 🚀 部署(推荐用 AI Client 协助)

部署较繁琐,推荐用 **opencode** / Cursor / Claude Code / Cline 等 AI 客户端协助,只需一句:

> "帮我部署这个 xiaoya-proxy 项目。地址是 https://github.com/yushengxin1975/xiaoya-proxy-pkg"

AI 会自动:
- 拉取项目
- 询问 Alist URL / 用户名 / 密码
- 调用 `windows/install.ps1`(Windows)或 `bash install.sh`(Linux)
- 验证 `http://localhost:8080/__health__`

### 手动部署(Windows)

```powershell
git clone https://github.com/yushengxin1975/xiaoya-proxy-pkg.git
cd xiaoya-proxy-pkg\windows
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

非交互:
```powershell
$env:ALIST_URL = "http://your-alist-host:5244"
$env:ALIST_USER = "your-user"
$env:ALIST_PASS = "your-pass"
powershell -ExecutionPolicy Bypass -File .\install.ps1 -NonInteractive
```

### 手动部署(Linux / Chromebook Crostini)

```bash
git clone https://github.com/yushengxin1975/xiaoya-proxy-pkg.git
cd xiaoya-proxy-pkg
bash install.sh
```

非交互:
```bash
ALIST_URL=http://alist.example.com:5244 \
ALIST_USER=myuser \
ALIST_PASS=mypass \
bash install.sh --non-interactive
```

## 🎬 使用

1. 浏览器打开 <http://localhost:8080/__simple__/>
2. 浏览视频列表(支持递归全目录排序)
3. 点击视频 → 跳转到小雅原生页面播放
4. 字幕自动显示在播放器选项里
5. 长视频可看完不卡(URL 自动续期 + 本地缓存)

## 📊 系统要求

- **Windows 10 1809+ / Windows 11**:Python 3.11+ (从 [python.org](https://www.python.org/downloads/) 下载,勾 **Add to PATH**)。自动下载 NSSM 注册为 Windows Service。
- **Linux / Chromebook Crostini**:Python 3.11+,systemd(Crostini 默认满足)。
- 无任何第三方依赖,只用 Python 标准库。

## 🔧 常用命令

**Windows (NSSM 服务):**
```powershell
nssm status  xiaoya-proxy    # 看状态
nssm restart xiaoya-proxy    # 重启
nssm edit    xiaoya-proxy    # 改密码/环境变量(GUI)

# 升级
cd xiaoya-proxy-pkg\windows
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**Linux (systemd):**
```bash
systemctl --user status alist-proxy      # 看状态
systemctl --user restart alist-proxy     # 重启
journalctl --user -u alist-proxy -f      # 实时日志

# 升级
cd ~/xiaoya-proxy-pkg && bash update.sh
```

**索引重建(两平台一样):**
```bash
curl -X POST http://localhost:8080/__api__/index/start              # 增量重建
curl -X POST "http://localhost:8080/__api__/index/start?force_full=true"  # 强制全量
curl http://localhost:8080/__api__/cache/stats | python3 -m json.tool   # 缓存状态
```

## 📁 文件分布

**Windows:**
| 路径 | 作用 |
|------|------|
| `%LOCALAPPDATA%\Programs\xiaoya-proxy\alist_proxy.py` | 主程序 |
| `%LOCALAPPDATA%\Programs\xiaoya-proxy\alist_proxy_index.html` | 网页 UI |
| `%USERPROFILE%\.config\xiaoya-proxy\config` | 配置(URL/凭据/端口) |
| `%USERPROFILE%\.cache\alist_proxy\ts_segments\` | .ts 段缓存目录 |

**Linux:**
| 路径 | 作用 |
|------|------|
| `~/.local/bin/alist_proxy.py` | 主程序 |
| `~/.local/bin/alist_proxy_index.html` | 网页 UI |
| `~/.config/alist-proxy/config` | 配置 |
| `~/.cache/alist_proxy/ts_segments/` | .ts 段缓存目录 |

## 🐛 故障排查

**Windows 服务起不来:**
```powershell
Get-Content $env:LOCALAPPDATA\xiaoya-proxy\service.err.log    # 错误日志
Get-Content $env:LOCALAPPDATA\xiaoya-proxy\service.log -Wait  # 实时跟踪
nssm status xiaoya-proxy
nssm edit xiaoya-proxy   # GUI 编辑环境变量/工作目录
```

**Linux 服务起不来:**
```bash
journalctl --user -u alist-proxy -n 50
```

**Python 版本太低:**
- Linux: `sudo apt install python3.11`
- Windows: 重装 Python 选 3.11+ 版本,勾 Add to PATH

## 设计原则

- **零第三方依赖**:只有 Python 标准库
- **保护上游**:浏览页面时上游不会被打爆
- **零额外内存**:空闲时只占 ~30MB RSS
- **平台无关**:同一份 `alist_proxy.py` 在 Linux 和 Windows 上行为完全一致

## 卸载

**Windows:**
```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1          # 保留配置和索引
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1 -Purge  # 完全清理
```

**Linux:**
```bash
bash uninstall.sh          # 保留配置和索引,可重装继续用
bash uninstall.sh --purge  # 完全清理
```