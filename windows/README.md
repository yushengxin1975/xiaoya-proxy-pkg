# xiaoya-proxy Windows 版

在 Windows 上安装 `xiaoya-proxy` 反向代理 + 本地索引服务。
核心 `alist_proxy.py` 是纯 Python 标准库,无需任何第三方依赖。

## 系统要求

- **Windows 10 1809+** 或 **Windows 11**(需要 `schtasks` / `NSSM` 支持)
- **Python 3.11+** —— 从 [python.org](https://www.python.org/downloads/) 下载安装,务必勾选 **Add python.exe to PATH**
- 管理员权限 **不需要**(NSSM 默认以当前用户注册)
- 网络可达上游 Alist

## 安装步骤

### 1. 获取项目文件

任选一种方式:

```powershell
# 方式 A: git clone(推荐,后续可用 git pull 升级)
git clone https://github.com/yushengxin1975/xiaoya-proxy-pkg.git
cd xiaoya-proxy-pkg\windows

# 方式 B: 下载 zip
# https://github.com/yushengxin1975/xiaoya-proxy-pkg/archive/refs/heads/main.zip
# 解压后进入 xiaoya-proxy-pkg-main\windows\
```

### 2. 运行安装脚本

**交互模式**(首次安装,会提示输入 Alist URL / 用户名 / 密码):

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**非交互模式**(脚本/CI 场景):

```powershell
$env:ALIST_URL = "http://your-alist-host:5244"
$env:ALIST_USER = "your-user"
$env:ALIST_PASS = "your-pass"
powershell -ExecutionPolicy Bypass -File .\install.ps1 -NonInteractive
```

装好后会:
- 复制脚本到 `%LOCALAPPDATA%\Programs\xiaoya-proxy\`
- 写配置到 `%USERPROFILE%\.config\xiaoya-proxy\config`
- 下载 NSSM 并注册 Windows Service `xiaoya-proxy`
- 启动服务,验证 `http://localhost:8080/__health__`

**可选参数**

| 参数 | 说明 |
|---|---|
| `-NonInteractive` | 非交互模式,需要 env 变量 |
| `-NoStart` | 装好但不启动 |
| `-ScheduledTask` | 改用 schtasks(登录时启动),跳过下载 NSSM |
| `-Port 9090` | 改监听端口(默认 8080) |

### 3. 访问

打开 <http://localhost:8080/__simple__/>

## 文件分布

| 路径 | 作用 |
|---|---|
| `%LOCALAPPDATA%\Programs\xiaoya-proxy\alist_proxy.py` | 主程序 |
| `%LOCALAPPDATA%\Programs\xiaoya-proxy\alist_proxy_index.html` | 网页 UI |
| `%LOCALAPPDATA%\Programs\xiaoya-proxy\_version.py` / `VERSION` | 版本号 |
| `%USERPROFILE%\.config\xiaoya-proxy\config` | 配置(URL/凭据/端口) |
| `%LOCALAPPDATA%\xiaoya-proxy\` | 索引 + 服务日志 |

## 常用命令

```powershell
# 服务管理(NSSM 模式)
nssm start   xiaoya-proxy
nssm stop    xiaoya-proxy
nssm restart xiaoya-proxy
nssm status  xiaoya-proxy
nssm edit    xiaoya-proxy   # 改环境变量/密码(改 config 后用)

# 实时日志
Get-Content $env:LOCALAPPDATA\xiaoya-proxy\service.log -Wait

# 索引重建
Invoke-RestMethod -Method POST -Uri http://localhost:8080/__api__/index/start
Invoke-RestMethod -Method POST -Uri "http://localhost:8080/__api__/index/start?force_full=true"
Invoke-RestMethod -Uri http://localhost:8080/__api__/index/status | ConvertFrom-Json
```

## 改密码后

```powershell
# 方法 1: 改 config 文件,然后 nssm restart
notepad $env:USERPROFILE\.config\xiaoya-proxy\config
nssm restart xiaoya-proxy

# 方法 2: 用 nssm edit 改环境变量
nssm edit xiaoya-proxy   # 弹出 GUI,改 Environment 里的 ALIST_*
```

## 升级

```powershell
# 仓库根目录
cd ..\
git pull

# 重装(覆盖文件 + 重启服务)
cd windows
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

服务中断约 1-2 秒。

## 卸载

```powershell
# 保留配置和索引(可重装继续用)
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1

# 完全清理
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1 -Purge
```

## 故障排查

**服务起不来:**
```powershell
Get-Content $env:LOCALAPPDATA\xiaoya-proxy\service.err.log
# 或
nssm status xiaoya-proxy
nssm edit xiaoya-proxy   # 看 AppEnvironmentExtra 等配置
```

**Python 找不到:**
```powershell
python --version     # 应该是 3.11+
py -3.11 --version   # 或这样
where python         # 确认 PATH
```

**端口被占:**
```powershell
# 查谁占了 8080
Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
# 重装改端口
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Port 9090
```

**PowerShell 报 "无法加载文件,因为在此系统上禁止运行脚本":**
```powershell
# 用 -ExecutionPolicy Bypass 绕过(推荐,只对当前命令生效)
powershell -ExecutionPolicy Bypass -File .\install.ps1
# 或一次性放宽当前用户策略
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## 设计说明

- **服务/任务** 默认用 NSSM(NSSM 会把任何 exe 包成标准 Windows Service,支持自动重启、日志轮转)。无管理员权限或不想下载 NSSM 时,加 `-ScheduledTask` 切到 `schtasks onlogon`,功能一样,只是登录后才启动。
- **配置** 写在文件里(权限 600 等价:仅当前用户 R),不写到注册表或服务环境变量里。NSSM 启动时把 config 内容读出来设到进程 env。
- **更新机制** 沿用 Linux 版:本地有 git 仓库,`git pull` 拿到新代码,`install.ps1` 把新文件覆盖到安装目录 + restart 服务。中断 < 2 秒。
- **无第三方 Python 依赖** —— 跟 Linux 版一样,只用标准库。NSSM 是独立的 ~600KB exe,不污染 Python 环境。
