#!/bin/bash
# 同步使用说明书：docs/ → frontend/public/
# 修改说明书后运行此脚本：./scripts/sync-manual.sh

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cp "$ROOT/docs/user-manual.html" "$ROOT/frontend/public/user-manual.html"
echo "✓ 使用说明书已同步到 frontend/public/"
