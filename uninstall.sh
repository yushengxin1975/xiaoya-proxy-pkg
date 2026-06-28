#!/usr/bin/env bash
# 卸载 Alist 反向代理
# 用法: bash uninstall.sh [--purge]  # --purge 同时删除配置和索引
set -euo pipefail

if [[ -t 1 ]]; then
    RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; RST=$'\e[0m'
else
    RED=""; GRN=""; YLW=""; RST=""
fi
info()  { echo "[信息] $*"; }
ok()    { echo "${GRN}[完成]${RST} $*"; }
warn()  { echo "${YLW}[警告]${RST} $*"; }

PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        *) echo "未知参数: $arg"; exit 1 ;;
    esac
done

SERVICE_FILE="$HOME/.config/systemd/user/alist-proxy.service"
SCRIPT_FILE="$HOME/.local/bin/alist_proxy.py"
HTML_FILE="$HOME/.local/bin/alist_proxy_index.html"
CONFIG_FILE="$HOME/.config/alist-proxy/config"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/alist_proxy"

# 停止 + 禁用 systemd 服务
if command -v systemctl >/dev/null 2>&1; then
    if systemctl --user is-active --quiet alist-proxy.service 2>/dev/null; then
        info "停止服务..."
        systemctl --user stop alist-proxy.service
    fi
    if systemctl --user is-enabled --quiet alist-proxy.service 2>/dev/null; then
        systemctl --user disable alist-proxy.service
    fi
    if [[ -f "$SERVICE_FILE" ]]; then
        rm -f "$SERVICE_FILE"
        systemctl --user daemon-reload
        ok "已移除 systemd 单元"
    fi
fi

# 删除脚本和 HTML
[[ -f "$SCRIPT_FILE" ]] && rm -f "$SCRIPT_FILE" && info "已删除 $SCRIPT_FILE"
[[ -f "$HTML_FILE" ]]   && rm -f "$HTML_FILE"   && info "已删除 $HTML_FILE"

# --purge 才删配置和数据
if [[ $PURGE -eq 1 ]]; then
    [[ -f "$CONFIG_FILE" ]] && rm -f "$CONFIG_FILE" && info "已删除配置 $CONFIG_FILE"
    [[ -d "$DATA_DIR" ]] && rm -rf "$DATA_DIR" && info "已删除索引 $DATA_DIR"
else
    warn "保留配置: $CONFIG_FILE"
    warn "保留索引: $DATA_DIR(重新 install 可继续使用)"
    warn "  想彻底清理请用: bash uninstall.sh --purge"
fi

echo ""
ok "卸载完成"
