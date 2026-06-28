#!/usr/bin/env bash
# One-line installer for the Alist reverse proxy + indexer package.
#
# Usage:
#   curl -fsSL http://YOUR-MIRROR-HOST/alist-proxy-pkg/install-remote.sh | bash
#
# Optional env overrides (must be set BEFORE curl, e.g. with env(1)):
#   ALIST_PROXY_BASE_URL  — base URL where the tarball is hosted
#                           (default: http://YOUR-MIRROR-HOST/alist-proxy-pkg)
#   ALIST_PROXY_BIN_DIR   — override the install destination directory
#                           (default: ~/.local/bin)
#
# Anything after `--` is forwarded to install.sh, e.g.:
#   ... | bash -s -- --non-interactive --no-start

set -e

BASE_URL="${ALIST_PROXY_BASE_URL:-http://YOUR-MIRROR-HOST/alist-proxy-pkg}"
TARBALL="alist-proxy-pkg.tar.gz"

cleanup() { rm -rf "$TMPDIR" 2>/dev/null || true; }
trap cleanup EXIT

TMPDIR="$(mktemp -d -t alist-proxy.XXXXXX)"
cd "$TMPDIR"

echo "[alist-proxy] downloading $TARBALL from $BASE_URL"
if ! curl -fsSL --retry 3 --retry-delay 2 -o "$TARBALL" "$BASE_URL/$TARBALL"; then
  echo "[alist-proxy] ERROR: failed to download $BASE_URL/$TARBALL" >&2
  echo "[alist-proxy] hint: check connectivity to the host, or set ALIST_PROXY_BASE_URL" >&2
  exit 1
fi

echo "[alist-proxy] downloading checksum"
if ! curl -fsSL --retry 3 --retry-delay 2 -o "$TARBALL.sha256" "$BASE_URL/$TARBALL.sha256"; then
  echo "[alist-proxy] WARN: failed to download checksum, skipping verification" >&2
else
  echo "[alist-proxy] verifying sha256"
  if command -v sha256sum >/dev/null 2>&1; then
    if ! sha256sum -c "$TARBALL.sha256"; then
      echo "[alist-proxy] ERROR: sha256 mismatch" >&2
      exit 1
    fi
  elif command -v shasum >/dev/null 2>&1; then
    if ! shasum -a 256 -c "$TARBALL.sha256"; then
      echo "[alist-proxy] ERROR: sha256 mismatch" >&2
      exit 1
    fi
  else
    echo "[alist-proxy] WARN: no sha256sum/shasum found; skipped verification" >&2
  fi
fi

echo "[alist-proxy] extracting"
tar -xzf "$TARBALL"

if [[ ! -d alist-proxy-pkg ]]; then
  echo "[alist-proxy] ERROR: extracted archive does not contain alist-proxy-pkg/" >&2
  exit 1
fi

cd alist-proxy-pkg

echo "[alist-proxy] running install.sh"
bash install.sh "$@"

cat <<'EOF'

[alist-proxy] install completed.
[alist-proxy] next steps:
  1. Edit credentials (if not already set):
       $EDITOR ~/.config/alist-proxy/config
  2. Restart the service (if it was loaded):
       systemctl --user restart alist-proxy.service
  3. Open: http://localhost:8080/__simple__#/
EOF