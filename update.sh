#!/usr/bin/env bash
# 从 GitHub 拉取最新代码,复制到 ~/.local/bin/ 并重启 alist-proxy 服务。
#
# 用法:
#   bash update.sh            # 拉 main 分支最新代码
#   bash update.sh --dry-run  # 只显示会做什么,不实际执行
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
SERVICE_NAME="alist-proxy"
BRANCH="${BRANCH:-main}"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

ok()  { echo "[ok] $*"; }
info(){ echo "[..] $*"; }
warn(){ echo "[!] $*"; }
err() { echo "[X] $*" >&2; }

cd "$REPO_DIR"

if [[ $DRY_RUN -eq 1 ]]; then
  info "DRY-RUN,不会真的改东西"
  info "会执行: git pull origin $BRANCH"
  info "会复制到 $BIN_DIR/: alist_proxy.py alist_proxy_index.html _version.py VERSION"
  info "会执行: systemctl --user restart $SERVICE_NAME"
  exit 0
fi

info "当前 commit: $(git rev-parse --short HEAD)"
info "拉取 origin/$BRANCH ..."
git pull --ff-only origin "$BRANCH"
NEW_COMMIT=$(git rev-parse --short HEAD)
ok "更新到: $NEW_COMMIT"

info "备份当前文件到 ~/.local/share/alist_proxy/backup-pre-update/"
mkdir -p "$HOME/.local/share/alist_proxy/backup-pre-update"
cp -p "$BIN_DIR/alist_proxy.py" "$BIN_DIR/alist_proxy_index.html" \
      "$HOME/.local/share/alist_proxy/backup-pre-update/"

info "复制新文件到 $BIN_DIR/"
cp "$REPO_DIR/alist_proxy.py"          "$BIN_DIR/alist_proxy.py"
cp "$REPO_DIR/alist_proxy_index.html"  "$BIN_DIR/alist_proxy_index.html"
cp "$REPO_DIR/_version.py"             "$BIN_DIR/_version.py"
cp "$REPO_DIR/VERSION"                 "$BIN_DIR/VERSION"
chmod 755 "$BIN_DIR/alist_proxy.py"
chmod 644 "$BIN_DIR/alist_proxy_index.html" "$BIN_DIR/_version.py" "$BIN_DIR/VERSION"
ok "文件已就位"

info "重启 $SERVICE_NAME ..."
systemctl --user restart "$SERVICE_NAME"
sleep 2

if systemctl --user is-active --quiet "$SERVICE_NAME"; then
  HEALTH=$(curl -sS http://localhost:8080/__health__ || echo "unreachable")
  ok "服务已运行: $HEALTH"
else
  err "服务启动失败,查看: journalctl --user -u $SERVICE_NAME -n 50"
  exit 1
fi
