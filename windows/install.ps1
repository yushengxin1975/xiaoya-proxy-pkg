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
  $env:ALIST_USER="your_user"
  $env:ALIST_PASS="your_pass"
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

# ---------- 函数定义(PowerShell 5.1 -File 模式不提升后续函数,故提前) ----------
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
    & $NssmExe stop $ServiceName 2>&1 | Out-Null
    & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null
    & $NssmExe install $ServiceName $PythonExe (Join-Path $BinDir "alist_proxy.py") | Out-Null
    & $NssmExe set $ServiceName AppDirectory $BinDir | Out-Null
    & $NssmExe set $ServiceName DisplayName "xiaoya-proxy reverse proxy" | Out-Null
    & $NssmExe set $ServiceName Description "Alist reverse proxy with 15-min URL auto-refresh and local indexer" | Out-Null
    $envMulti = (@(
        "ALIST_URL=$AlistUrl",
        "ALIST_USER=$AlistUser",
        "ALIST_PASS=$AlistPass",
        "LISTEN_HOST=localhost",
        "LISTEN_PORT=$Port"
    )) -join "`0"
    & $NssmExe set $ServiceName AppEnvironmentExtra $envMulti | Out-Null
    & $NssmExe set $ServiceName ExitActions Restart | Out-Null
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
    # delete 不存在时也非 0,容忍(用 cmd /c 吞掉)
    cmd /c "schtasks /delete /tn $TaskName /f >nul 2>&1" | Out-Null
    & schtasks /create /tn $TaskName /tr $tr /sc onlogon /rl limited /f 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "schtasks /create 退出码 $LASTEXITCODE"
    }
    Ok "已创建计划任务: $TaskName"
    & schtasks /run /tn $TaskName 2>&1 | Out-Null
}

function Register-PowerShellScheduledTask {
    param(
        [string]$PythonExe,
        [string]$BinDir,
        [string]$TaskName
    )
    Info "用 PowerShell Register-ScheduledTask 注册登录启动任务(非管理员方案)..."
    # 写一个 wrapper 批处理,把环境变量从 config 文件带进去,然后跑 python
    # 用 PowerShell 解析 config(set 不会带引号,无 BOM),比 cmd for /f 稳
    $exe = Join-Path $BinDir "alist_proxy.py"
    $wrapperBat = Join-Path $BinDir "run_proxy.bat"
    $wrapperPs1 = Join-Path $BinDir "run_proxy.ps1"
    $cfg = $ConfigFile
    # 优先用 pythonw.exe(无 console,不弹 cmd 窗口);python.org 安装包默认自带
    # 找不到(eg. 系统 Python 或精简 embed)再降级到 python.exe
    $runtimeExe = $PythonExe
    $pythonwExe = $PythonExe -replace '\\python\.exe$', '\pythonw.exe'
    if ($pythonwExe -ne $PythonExe -and (Test-Path $pythonwExe)) {
        $runtimeExe = $pythonwExe
        Ok "使用 pythonw.exe 运行(无控制台窗口)"
    } else {
        Warn "pythonw.exe 不存在,降级到 python.exe (会有黑色 cmd 窗口闪现)"
    }
    $batContent = @"
@echo off
REM 由 install.ps1 生成:从 config 读凭据并启动 alist_proxy(用 PowerShell 处理 quoting/特殊字符)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$wrapperPs1"
"@
    $psContent = @'
# 由 install.ps1 生成:从 config 读凭据并启动 alist_proxy
$cfg = '__CFG__'
if (-not (Test-Path $cfg)) { Write-Error "config not found: $cfg"; exit 1 }
Get-Content $cfg | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return }
    if ($line -match '^(ALIST_[A-Z]+|LISTEN_[A-Z]+)="?([^"]*)"?$') {
        Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2]
    }
}
& '__PYEXE__' '__EXE__'
'@
    $psContent = $psContent.Replace('__CFG__', $cfg).Replace('__PYEXE__', $runtimeExe).Replace('__EXE__', $exe)
    # 无 BOM 写 bat 和 ps1(避免 cmd / PowerShell 因 BOM 报错)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($wrapperBat, $batContent, $utf8NoBom)
    [System.IO.File]::WriteAllText($wrapperPs1, $psContent, $utf8NoBom)
    try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
    $action = New-ScheduledTaskAction -Execute $wrapperBat -WorkingDirectory $BinDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    # Hidden=true 让任务计划程序在登录触发时不显示 wrapper 窗口(后台静默运行)
    $settings.Hidden = $true
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Alist reverse proxy with 15-min URL auto-refresh and local indexer" -Force | Out-Null
    Ok "已创建 PowerShell 计划任务: $TaskName (wrapper: $wrapperBat)"
    Start-ScheduledTask -TaskName $TaskName | Out-Null
}

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
# 如已有配置文件且 ACL 被收紧,先恢复当前用户完全访问(便于覆盖写)
if (Test-Path $ConfigFile) {
    try { icacls $ConfigFile /grant "${env:USERNAME}:F" 2>&1 | Out-Null } catch {}
}
@"
# xiaoya-proxy 配置 (由 install.ps1 生成,手动修改后需重启服务)
# ALIST_PASS 为明文,请确保本机账户安全

ALIST_URL="$AlistUrl"
ALIST_USER="$AlistUser"
ALIST_PASS="$AlistPass"
LISTEN_HOST="localhost"
LISTEN_PORT="$Port"
"@ | Set-Content -Path $ConfigFile -Encoding UTF8

# 收紧权限:仅当前用户可读写(目录 ACL 已限制其它用户访问)
try { icacls $ConfigFile /inheritance:r /grant "${env:USERNAME}:RW" 2>&1 | Out-Null } catch {}
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
        try {
            Register-SchtasksTask -PythonExe $pythonExe -BinDir $BinDir -TaskName $TaskName
            $usedMode = "schtasks"
        } catch {
            Warn "schtasks 注册失败: $($_.Exception.Message)"
            Warn "降级到 PowerShell Register-ScheduledTask ..."
            Register-PowerShellScheduledTask -PythonExe $pythonExe -BinDir $BinDir -TaskName $TaskName
            $usedMode = "psscheduledtask"
        }
    } else {
        # ---- 默认:NSSM,失败自动降级到 PS Register-ScheduledTask(非管理员) ----
        $nssmOk = Ensure-Nssm
        if ($nssmOk) {
            try {
                Register-NssmService -NssmExe $NssmExe -ServiceName $ServiceName `
                    -PythonExe $pythonExe -BinDir $BinDir -DataDir $DataDir `
                    -AlistUrl $AlistUrl -AlistUser $AlistUser -AlistPass $AlistPass -Port $Port
                $usedMode = "nssm"
            } catch {
                Warn "NSSM 注册失败: $($_.Exception.Message)"
                Warn "降级到 PowerShell Register-ScheduledTask ..."
                Register-PowerShellScheduledTask -PythonExe $pythonExe -BinDir $BinDir -TaskName $TaskName
                $usedMode = "psscheduledtask"
            }
        } else {
            Warn "NSSM 不可用,降级到 PowerShell Register-ScheduledTask ..."
            Register-PowerShellScheduledTask -PythonExe $pythonExe -BinDir $BinDir -TaskName $TaskName
            $usedMode = "psscheduledtask"
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
} elseif ($usedMode -eq "psscheduledtask") {
    Write-Host "    Start-ScheduledTask -TaskName '$TaskName'   # 启动"
    Write-Host "    Stop-ScheduledTask  -TaskName '$TaskName'   # 停止"
    Write-Host "    Get-ScheduledTask   -TaskName '$TaskName'   # 状态"
    Write-Host "    Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false   # 删除"
    Write-Host "    Get-Content $DataDir\stdout.log -Wait  # 实时日志"
}
if (-not $usedMode) {
    Write-Host "  (服务未启动,重新运行 install.ps1 不带 -NoStart 即可启动)"
}
Write-Host ""
Write-Host "  卸载: powershell -ExecutionPolicy Bypass -File uninstall.ps1 [-Purge]"