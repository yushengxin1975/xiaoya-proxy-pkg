<#
.SYNOPSIS
  在 Windows 上安装 xiaoya-proxy 反向代理 + 本地索引服务。
.DESCRIPTION
  默认行为:
    1. 检测 Python 3.11+
    2. 把 alist_proxy.py / .html 复制到 %LOCALAPPDATA%\Programs\xiaoya-proxy\
    3. 写配置到 %USERPROFILE%\.config\xiaoya-proxy\config
    4. 下载 NSSM,注册为 Windows Service "xiaoya-proxy"
    5. 启动服务,验证 http://localhost:8080/__health__
.PARAMETER NonInteractive
  非交互模式,需要环境变量 ALIST_URL/ALIST_USER/ALIST_PASS。
.PARAMETER NoStart
  装好但不启动服务。
.PARAMETER ScheduledTask
  改用 schtasks (登录时启动),不下载 NSSM。适合无管理员权限或没网络的场景。
.PARAMETER Port
  监听端口,默认 8080 (也可由环境变量 LISTEN_PORT 覆盖)。
.EXAMPLE
  # 交互式
  powershell -ExecutionPolicy Bypass -File install.ps1

  # 非交互
  $env:ALIST_URL="http://alist:5244"
  $env:ALIST_USER="dav"
  $env:ALIST_PASS="xxx"
  powershell -ExecutionPolicy Bypass -File install.ps1 -NonInteractive

  # 装好不启动
  powershell -ExecutionPolicy Bypass -File install.ps1 -NoStart

  # 无管理员权限 / 离线场景
  powershell -ExecutionPolicy Bypass -File install.ps1 -ScheduledTask
#>
[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$NoStart,
    [switch]$ScheduledTask,
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ---------- 路径常量 ----------
$ScriptDir   = $PSScriptRoot
$RepoRoot    = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$BinDir      = Join-Path $env:LOCALAPPDATA "Programs\xiaoya-proxy"
$ConfigDir   = Join-Path $env:USERPROFILE ".config\xiaoya-proxy"
$ConfigFile  = Join-Path $ConfigDir "config"
$DataDir     = Join-Path $env:LOCALAPPDATA "xiaoya-proxy"
$ServiceName = "xiaoya-proxy"
$TaskName    = "xiaoya-proxy"
$NssmDir     = Join-Path $env:LOCALAPPDATA "Programs\nssm"
$NssmExe     = Join-Path $NssmDir "nssm.exe"

# 端口
if ($Port -eq 0) {
    if ($env:LISTEN_PORT) { $Port = [int]$env:LISTEN_PORT } else { $Port = 8080 }
}

# ---------- 输出 ----------
function Info($m) { Write-Host "[*] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[+] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Err($m)  { Write-Host "[X] $m" -ForegroundColor Red; exit 1 }

# ---------- 1. 检查 Python 3.11+ ----------
Info "查找 Python 3.11+ ..."
$pythonExe = $null
$pythonCmd = $null
$candidates = @(
    @{ Cmd = "python";  Arg = "" },
    @{ Cmd = "py";      Arg = "-3" },
    @{ Cmd = "python3"; Arg = "" },
    @{ Cmd = "py";      Arg = "-3.11" }
)
foreach ($c in $candidates) {
    # 关键:Arg 为空时不能传,否则 Python 会把它当脚本名
    $argList = @()
    if ($c.Arg) { $argList += $c.Arg }
    $argList += "--version"
    $cmdLine = if ($c.Arg) { "$($c.Cmd) $($c.Arg)" } else { $c.Cmd }
    try {
        $v = & $c.Cmd @argList 2>&1
        if ($LASTEXITCODE -ne 0) { continue }
        if ($v -match "Python (\d+)\.(\d+)") {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -ge 3 -and $min -ge 11) {
                $argList2 = @()
                if ($c.Arg) { $argList2 += $c.Arg }
                $argList2 += @("-c", "import sys; print(sys.executable)")
                $exe = (& $c.Cmd @argList2 2>&1).Trim()
                if ($exe -and (Test-Path $exe)) {
                    $pythonExe = (Resolve-Path $exe).Path
                    $pythonCmd = $cmdLine
                    Ok "$cmdLine : $v  ->  $pythonExe"
                    break
                }
            } else {
                Warn "$cmdLine 版本太低 ($maj.$min),需要 3.11+"
            }
        }
    } catch {}
}
if (-not $pythonExe) {
    Err "未找到 Python 3.11+。请从 https://www.python.org/downloads/ 下载,安装时勾 'Add python.exe to PATH'。"
}

# ---------- 2. 读现有配置 / 提示输入 ----------
$AlistUrl  = $env:ALIST_URL
$AlistUser = $env:ALIST_USER
$AlistPass = $env:ALIST_PASS

if (Test-Path $ConfigFile) {
    Info "检测到现有配置: $ConfigFile"
    Get-Content $ConfigFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        if ($line -match '^ALIST_URL="?([^"]*)"?$')       { if (-not $AlistUrl)  { $AlistUrl  = $Matches[1] } }
        elseif ($line -match '^ALIST_USER="?([^"]*)"?$') { if (-not $AlistUser) { $AlistUser = $Matches[1] } }
        elseif ($line -match '^ALIST_PASS="?([^"]*)"?$') { if (-not $AlistPass) { $AlistPass = $Matches[1] } }
    }
    if ($AlistUser -and $AlistPass) {
        Ok "复用现有凭据: $AlistUser @ $AlistUrl"
    }
}

if (-not $AlistUser -or -not $AlistPass) {
    if ($NonInteractive) {
        Err "缺少 ALIST_USER/ALIST_PASS,非交互模式需要环境变量"
    }
    Write-Host ""
    Write-Host "首次安装:请输入 Alist 连接信息" -ForegroundColor White
    Write-Host "================================="
    if (-not $AlistUrl) {
        $input = Read-Host "Alist URL [默认 http://localhost:5244]"
        $AlistUrl = if ($input) { $input } else { "http://localhost:5244" }
    }
    if (-not $AlistUser) {
        $AlistUser = Read-Host "Alist 用户名"
        if (-not $AlistUser) { Err "用户名不能为空" }
    }
    if (-not $AlistPass) {
        $secure = Read-Host "Alist 密码" -AsSecureString
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $AlistPass = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) | Out-Null
    }
    $portInput = Read-Host "监听端口 [默认 $Port]"
    if ($portInput) { $Port = [int]$portInput }
}

# ---------- 3. 写配置 ----------
Info "写入配置 ..."
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
New-Item -ItemType Directory -Force -Path $DataDir   | Out-Null
@"
# xiaoya-proxy 配置 (由 install.ps1 生成,手动修改后需重启服务)
# ALIST_PASS 为明文,请确保本机账户安全

ALIST_URL="$AlistUrl"
ALIST_USER="$AlistUser"
ALIST_PASS="$AlistPass"
LISTEN_HOST="localhost"
LISTEN_PORT="$Port"
"@ | Set-Content -Path $ConfigFile -Encoding UTF8

# 收紧权限:仅当前用户可读
try { icacls $ConfigFile /inheritance:r /grant:r "${env:USERNAME}:R" 2>&1 | Out-Null } catch {}
Ok "配置: $ConfigFile"

# ---------- 4. 复制脚本 ----------
Info "复制脚本到 $BinDir ..."
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
Copy-Item -Path (Join-Path $RepoRoot "alist_proxy.py")         -Destination (Join-Path $BinDir "alist_proxy.py")         -Force
Copy-Item -Path (Join-Path $RepoRoot "alist_proxy_index.html") -Destination (Join-Path $BinDir "alist_proxy_index.html") -Force
Copy-Item -Path (Join-Path $RepoRoot "_version.py")            -Destination (Join-Path $BinDir "_version.py")            -Force
Copy-Item -Path (Join-Path $RepoRoot "VERSION")                -Destination (Join-Path $BinDir "VERSION")                -Force
Ok "脚本已就位"

# ---------- 5. 注册为服务或任务 ----------
$usedMode = $null  # 记录最终用的模式,后面输出提示用

if (-not $NoStart) {
    if ($ScheduledTask) {
        # ---- schtasks 路径(用户显式选) ----
        Register-SchtasksTask -PythonExe $pythonExe -BinDir $BinDir -TaskName $TaskName
        $usedMode = "schtasks"
    } else {
        # ---- 默认:NSSM,失败自动降级到 schtasks ----
        $nssmOk = Ensure-Nssm
        if ($nssmOk) {
            try {
                Register-NssmService -NssmExe $NssmExe -ServiceName $ServiceName `
                    -PythonExe $pythonExe -BinDir $BinDir -DataDir $DataDir `
                    -AlistUrl $AlistUrl -AlistUser $AlistUser -AlistPass $AlistPass -Port $Port
                $usedMode = "nssm"
            } catch {
                Warn "NSSM 注册失败: $($_.Exception.Message)"
                Warn "降级到 schtasks 模式 ..."
                Register-SchtasksTask -PythonExe $pythonExe -BinDir $BinDir -TaskName $TaskName
                $usedMode = "schtasks"
            }
        } else {
            Warn "NSSM 不可用,降级到 schtasks 模式"
            Register-SchtasksTask -PythonExe $pythonExe -BinDir $BinDir -TaskName $TaskName
            $usedMode = "schtasks"
        }
    }

    # ---------- 6. 验证 ----------
    Info "等待服务启动 ..."
    $ok = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:$Port/__health__" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch {}
    }
    if ($ok) {
        Ok "服务已运行: http://localhost:$Port"
    } else {
        Warn "服务未能响应 http://localhost:$Port/__health__"
        Warn "查看日志: $DataDir\service.log"
        Warn "或查看任务计划程序历史"
    }
}

# ---------- 完成 ----------
Write-Host ""
Write-Host "================================" -ForegroundColor White
if ($usedMode) {
    Ok "安装完成 ($usedMode 模式)"
} else {
    Ok "安装完成 (未启动服务)"
}
Write-Host "================================" -ForegroundColor White
Write-Host "  访问:     http://localhost:$Port/__simple__/"
Write-Host "  配置:     $ConfigFile"
Write-Host "  数据:     $DataDir"
Write-Host "  脚本:     $BinDir"
Write-Host ""
Write-Host "  常用命令:"
if ($usedMode -eq "schtasks") {
    Write-Host "    schtasks /run   /tn $TaskName         # 启动"
    Write-Host "    schtasks /end   /tn $TaskName         # 停止"
    Write-Host "    schtasks /delete /tn $TaskName /f      # 删除任务"
    Write-Host "    Get-EventLog -LogName Application -Newest 20   # 应用日志"
} elseif ($usedMode -eq "nssm") {
    Write-Host "    nssm start   $ServiceName             # 启动"
    Write-Host "    nssm stop    $ServiceName             # 停止"
    Write-Host "    nssm restart $ServiceName             # 重启"
    Write-Host "    nssm status  $ServiceName             # 状态"
    Write-Host "    nssm edit    $ServiceName             # 编辑(改密码/配置后用)"
    Write-Host "    Get-Content $DataDir\service.log -Wait  # 实时日志"
}
if (-not $usedMode) {
    Write-Host "  (服务未启动,重新运行 install.ps1 不带 -NoStart 即可启动)"
}
Write-Host ""
Write-Host "  卸载: powershell -ExecutionPolicy Bypass -File uninstall.ps1 [-Purge]"

# ============================================================================
# 函数定义
# ============================================================================

function Ensure-Nssm {
    if (Test-Path $NssmExe) {
        Ok "NSSM 已存在: $NssmExe"
        return $true
    }
    Info "下载 NSSM ..."
    New-Item -ItemType Directory -Force -Path $NssmDir | Out-Null
    $zip = Join-Path $env:TEMP "nssm.zip"
    $nssmUrls = @(
        "https://nssm.cc/release/nssm-2.24.zip",
        "https://github.com/yushengxin1975/nssm-mirror/releases/latest/download/nssm-2.24.zip"
    )
    foreach ($nssmUrl in $nssmUrls) {
        try {
            Info "尝试: $nssmUrl"
            Invoke-WebRequest -Uri $nssmUrl -OutFile $zip -UseBasicParsing -TimeoutSec 30
            Expand-Archive -Path $zip -DestinationPath $env:TEMP -Force
            $src = Get-ChildItem -Path $env:TEMP -Recurse -Filter "nssm.exe" |
                   Where-Object { $_.DirectoryName -like "*win64*" } | Select-Object -First 1
            if ($src) {
                Copy-Item $src.FullName $NssmExe -Force
                Remove-Item $zip -Force
                Ok "NSSM 已下载: $NssmExe"
                return $true
            }
        } catch {
            Warn "下载失败: $($_.Exception.Message)"
        }
    }
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    return $false
}

function Register-NssmService {
    param(
        [string]$NssmExe,
        [string]$ServiceName,
        [string]$PythonExe,
        [string]$BinDir,
        [string]$DataDir,
        [string]$AlistUrl,
        [string]$AlistUser,
        [string]$AlistPass,
        [int]$Port
    )
    # 清理旧服务
    & $NssmExe stop $ServiceName 2>&1 | Out-Null
    & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null

    # 注册
    & $NssmExe install $ServiceName $PythonExe (Join-Path $BinDir "alist_proxy.py") | Out-Null
    & $NssmExe set $ServiceName AppDirectory $BinDir | Out-Null
    & $NssmExe set $ServiceName DisplayName "xiaoya-proxy reverse proxy" | Out-Null
    & $NssmExe set $ServiceName Description "Alist reverse proxy with 15-min URL auto-refresh and local indexer" | Out-Null
    # 环境变量:多字符串用 null (`0) 分隔,内部值再用 | 拆
    $envMulti = (@(
        "ALIST_URL=$AlistUrl",
        "ALIST_USER=$AlistUser",
        "ALIST_PASS=$AlistPass",
        "LISTEN_HOST=localhost",
        "LISTEN_PORT=$Port"
    )) -join "`0"
    & $NssmExe set $ServiceName AppEnvironmentExtra $envMulti | Out-Null
    # 失败时自动重启
    & $NssmExe set $ServiceName ExitActions Restart | Out-Null
    # 日志 + 自动轮转
    & $NssmExe set $ServiceName AppStdout (Join-Path $DataDir "service.log") | Out-Null
    & $NssmExe set $ServiceName AppStderr (Join-Path $DataDir "service.err.log") | Out-Null
    & $NssmExe set $ServiceName AppRotateFiles 1 | Out-Null
    & $NssmExe set $ServiceName AppRotateBytes 10485760 | Out-Null

    Ok "服务已注册: $ServiceName"
    & $NssmExe start $ServiceName | Out-Null
}

function Register-SchtasksTask {
    param(
        [string]$PythonExe,
        [string]$BinDir,
        [string]$TaskName
    )
    Info "用 schtasks 注册登录启动任务 ..."
    $tr = '"' + $PythonExe + '" "' + (Join-Path $BinDir "alist_proxy.py") + '"'
    & schtasks /delete /tn $TaskName /f 2>&1 | Out-Null
    # /rl limited 不需要管理员
    & schtasks /create /tn $TaskName /tr $tr /sc onlogon /rl limited /f 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Err "schtasks 创建失败,查看: schtasks /create /?`n手动:'schtasks /create /tn $TaskName /tr `\"$tr`\" /sc onlogon'"
    }
    Ok "已创建计划任务: $TaskName"
    & schtasks /run /tn $TaskName 2>&1 | Out-Null
}