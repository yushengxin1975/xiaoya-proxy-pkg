<#
.SYNOPSIS
  卸载 xiaoya-proxy (Windows)。
.DESCRIPTION
  停止并删除 NSSM 服务(或 schtasks 任务)、删除脚本目录。
  默认保留配置和索引(可重装继续用),-Purge 一并删除。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File uninstall.ps1
  powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Purge
#>
[CmdletBinding()]
param(
    [switch]$Purge
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ServiceName = "xiaoya-proxy"
$TaskName    = "xiaoya-proxy"
$BinDir      = Join-Path $env:LOCALAPPDATA "Programs\xiaoya-proxy"
$ConfigDir   = Join-Path $env:USERPROFILE ".config\xiaoya-proxy"
$ConfigFile  = Join-Path $ConfigDir "config"
$DataDir     = Join-Path $env:LOCALAPPDATA "xiaoya-proxy"
$NssmExe     = Join-Path $env:LOCALAPPDATA "Programs\nssm\nssm.exe"

function Info($m) { Write-Host "[*] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[+] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }

Info "停止并删除服务/任务 ..."
# NSSM 服务
if (Test-Path $NssmExe) {
    & $NssmExe stop $ServiceName 2>&1 | Out-Null
    & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null
    Ok "NSSM 服务已移除"
} else {
    # 可能装了 schtasks 模式
    $hasSchtasks = (Get-Command schtasks -ErrorAction SilentlyContinue) -ne $null
    if ($hasSchtasks) {
        $exists = & schtasks /query /tn $TaskName 2>&1
        if ($LASTEXITCODE -eq 0) {
            & schtasks /end /tn $TaskName 2>&1 | Out-Null
            & schtasks /delete /tn $TaskName /f 2>&1 | Out-Null
            Ok "计划任务已删除"
        } else {
            Warn "未发现已注册的服务/任务,跳过"
        }
    } else {
        Warn "未发现 NSSM 或 schtasks,跳过服务清理"
    }
}

Info "删除脚本目录 ..."
if (Test-Path $BinDir) {
    Remove-Item -Recurse -Force $BinDir
    Ok "已删除 $BinDir"
}

if ($Purge) {
    Warn "Purge 模式:删除配置和索引"
    if (Test-Path $ConfigFile) { Remove-Item -Force $ConfigFile; Ok "已删除 $ConfigFile" }
    if (Test-Path $DataDir)    { Remove-Item -Recurse -Force $DataDir; Ok "已删除 $DataDir" }
} else {
    Warn "保留配置: $ConfigFile (可用 install.ps1 重新启用)"
    Warn "保留索引: $DataDir"
}

Write-Host ""
Ok "卸载完成"
