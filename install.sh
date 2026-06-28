#!/usr/bin/env bash
# 一键安装 Alist 反向代理 + 本地索引服务
# 用法:
#   bash install.sh                       # 交互式填入配置
#   bash install.sh --no-start            # 装好但不启动(用于调试)
#   ALIST_USER=foo ALIST_PASS=bar bash install.sh --non-interactive  # 无人值守
set -euo pipefail

# ---------- 颜色输出 ----------
if [[ -t 1 ]]; then
    RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[34m'; RST=$'\e[0m'
else
    RED=""; GRN=""; YLW=""; BLU=""; RST=""
fi
info()  { echo "${BLU}[信息]${RST} $*"; }
ok()    { echo "${GRN}[成功]${RST} $*"; }
warn()  { echo "${YLW}[警告]${RST} $*"; }
err()   { echo "${RED}[错误]${RST} $*" >&2; }

# ---------- 参数 ----------
NO_START=0
NON_INTERACTIVE=0
for arg in "$@"; do
    case "$arg" in
        --no-start) NO_START=1 ;;
        --non-interactive|-y) NON_INTERACTIVE=1 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) err "未知参数: $arg"; exit 1 ;;
    esac
done

# 给关键变量设默认值(防止 set -u 在写入配置文件时报 unbound)
ALIST_URL="${ALIST_URL:-http://localhost:5244}"
ALIST_USER="${ALIST_USER:-}"
ALIST_PASS="${ALIST_PASS:-}"
LISTEN_HOST="${LISTEN_HOST:-localhost}"
LISTEN_PORT="${LISTEN_PORT:-8080}"

# ---------- 前置检查 ----------
info "检查 Python 版本..."
if ! command -v python3 >/dev/null 2>&1; then
    err "找不到 python3。请先安装 Python 3.11+(Chromebook 需先开启 Linux)"
    exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 11 ]]; }; then
    err "需要 Python 3.11+,当前是 $PY_VERSION"
    exit 1
fi
ok "Python $PY_VERSION"

# stdlib 检查(只用标准库)
python3 -c 'import http.server, socketserver, urllib.request, json, threading, hashlib' 2>/dev/null \
    || { err "Python 标准库不完整"; exit 1; }

# ---------- 路径常量 ----------
CONFIG_DIR="$HOME/.config/alist-proxy"
CONFIG_FILE="$CONFIG_DIR/config"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_DIR="$HOME/.config/systemd/user"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/alist_proxy"

# 当前脚本所在目录(项目根)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------- 处理配置 ----------
if [[ -f "$CONFIG_FILE" ]]; then
    info "检测到现有配置: $CONFIG_FILE"
    # 加载已有配置以便复用
    set -a; source "$CONFIG_FILE"; set +a
    if [[ -z "${ALIST_USER:-}" ]] || [[ -z "${ALIST_PASS:-}" ]]; then
        warn "现有配置缺少 ALIST_USER 或 ALIST_PASS,将提示重新输入"
    else
        ok "将使用现有凭据: $ALIST_USER @ $ALIST_URL"
    fi
fi

# 交互式或环境变量补全
if [[ -z "${ALIST_USER:-}" ]] || [[ -z "${ALIST_PASS:-}" ]]; then
    if [[ $NON_INTERACTIVE -eq 1 ]] || [[ ! -t 0 ]]; then
        err "缺少 ALIST_USER/ALIST_PASS,非交互模式需要环境变量"
        err "  用法: ALIST_USER=foo ALIST_PASS=bar bash install.sh --non-interactive"
        exit 1
    fi

    echo ""
    echo "首次安装:请输入 Alist 连接信息"
    echo "─────────────────────────────────"
    read -rp "Alist URL [默认 http://localhost:5244]: " INPUT_URL
    ALIST_URL="${INPUT_URL:-http://localhost:5244}"

    read -rp "Alist 用户名: " ALIST_USER
    if [[ -z "$ALIST_USER" ]]; then err "用户名不能为空"; exit 1; fi

    # 密码输入隐藏
    read -rsp "Alist 密码: " ALIST_PASS
    echo ""
    if [[ -z "$ALIST_PASS" ]]; then err "密码不能为空"; exit 1; fi

    read -rp "监听端口 [默认 8080]: " INPUT_PORT
    LISTEN_PORT="${INPUT_PORT:-8080}"
    LISTEN_HOST="${LISTEN_HOST:-localhost}"
fi

# ---------- 写配置文件 ----------
mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_FILE" <<EOF
# Alist 反向代理配置(由 install.sh 生成,修改后需重启服务生效)
# 手动重启: systemctl --user restart alist-proxy
# 注意: 值都加了引号,确保含 &、空格等特殊字符的密码能被 source 正确解析

ALIST_URL="$ALIST_URL"
ALIST_USER="$ALIST_USER"
ALIST_PASS="$ALIST_PASS"
LISTEN_HOST="${LISTEN_HOST:-localhost}"
LISTEN_PORT="${LISTEN_PORT:-8080}"
EOF
chmod 600 "$CONFIG_FILE"
ok "配置文件: $CONFIG_FILE (权限 600)"

# ---------- 安装脚本 ----------
mkdir -p "$BIN_DIR"
cp "$SCRIPT_DIR/alist_proxy.py" "$BIN_DIR/alist_proxy.py"
cp "$SCRIPT_DIR/alist_proxy_index.html" "$BIN_DIR/alist_proxy_index.html"
chmod 755 "$BIN_DIR/alist_proxy.py"
ok "脚本已安装到 $BIN_DIR/"

# 数据目录(存索引)
mkdir -p "$DATA_DIR"

# ---------- systemd ----------
mkdir -p "$SYSTEMD_DIR"
cp "$SCRIPT_DIR/alist-proxy.service" "$SYSTEMD_DIR/alist-proxy.service"

if command -v systemctl >/dev/null 2>&1; then
    info "重载 systemd..."
    systemctl --user daemon-reload
    if [[ $NO_START -eq 0 ]]; then
        info "启动服务..."
        systemctl --user enable --now alist-proxy.service
        sleep 2
        if systemctl --user is-active --quiet alist-proxy.service; then
            ok "服务已启动"
        else
            warn "服务启动失败,查看日志: journalctl --user -u alist-proxy -n 30"
        fi
    else
        info "已跳过启动(--no-start)"
    fi
else
    warn "找不到 systemctl。请手动运行:"
    echo "    python3 $BIN_DIR/alist_proxy.py"
fi

# ---------- 完成 ----------
LISTEN_PORT_FINAL="${LISTEN_PORT:-8080}"
echo ""
echo "════════════════════════════════════════"
ok "安装完成"
echo "════════════════════════════════════════"
echo ""
echo "  访问地址: http://localhost:${LISTEN_PORT_FINAL}/__simple__/"
echo ""
echo "  常用命令:"
echo "    systemctl --user status alist-proxy      # 状态"
echo "    systemctl --user restart alist-proxy     # 重启"
echo "    systemctl --user stop alist-proxy        # 停止"
echo "    journalctl --user -u alist-proxy -f      # 实时日志"
echo "    bash $SCRIPT_DIR/uninstall.sh            # 卸载"
echo ""
echo "  配置文件: $CONFIG_FILE"
echo "  索引文件: $DATA_DIR/index.json(后台慢慢构建)"
echo ""
echo "  首次访问全局搜索可能需要等几分钟(后台在爬目录)"
