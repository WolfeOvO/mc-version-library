#!/usr/bin/env bash
# 最小构建 + 部署示例。按需改 SITE_DIR / SITE_URL，不要提交真实域名。
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-build}"            # build | sync
SITE_DIR="${SITE_DIR:-./dist-deploy}"
SITE_URL="${SITE_URL:-https://example.com}"
export SITE_DIR SITE_URL

if [ "$MODE" = "sync" ]; then
  echo "== sync"
  python3 scripts/sync.py --only all
fi

echo "== generate pages"
python3 scripts/gen_mkdocs.py

echo "== mkdocs build"
mkdocs build --clean

echo "== sitemap ($SITE_URL)"
python3 scripts/make_sitemap.py

if [ -n "${DEPLOY:-}" ]; then
  echo "== rsync -> $SITE_DIR"
  mkdir -p "$SITE_DIR"
  rsync -a --delete site/ "$SITE_DIR/"
fi

echo "done: site/  (设置 DEPLOY=1 且 SITE_DIR=... 才会同步)"
