#!/usr/bin/env bash
# publish.sh — 一键发版到官网 casefix.cn
#
# 流程:校验环境 → 下载 GitHub Release 安装包 → scp 到官网 uploads →
#       从 git log 自动生成更新日志写入 release_notes.json → 验证
#
# 用法:
#   ./scripts/publish.sh            # 发布最新 git tag
#   ./scripts/publish.sh 1.6.4      # 发布指定版本
#
# 前置:
#   - gh CLI 已登录(gh auth status)
#   - SSH 免密到 root@118.196.83.43 已配(ssh-copy-id)
#   - GitHub 上对应 tag 的 Release 已由 CI 构建完成

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
REMOTE="hamzhang2024/criminal-llm"
CASEFIX="http://www.casefix.cn"
SSH_HOST="root@118.196.83.43"
AUTH_DIR="/opt/criminal-llm-auth"

cd "$REPO_DIR"

# ---- 1. 确定版本号 ----
VER="${1:-$(git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//')}"
if [ -z "${VER:-}" ]; then
  echo "❌ 无法确定版本号。用法: $0 <版本号,如 1.6.4>"
  exit 1
fi
echo "📦 发布版本: v$VER"

# ---- 2. 前置检查 ----
command -v gh >/dev/null       || { echo "❌ 需要 gh CLI(https://cli.github.com)"; exit 1; }
command -v python3 >/dev/null  || { echo "❌ 需要 python3"; exit 1; }
gh release view "v$VER" --repo "$REMOTE" >/dev/null 2>&1 \
  || { echo "❌ GitHub 上还没有 v$VER 的 Release。先 git tag v$VER && git push --tags,等 CI 构建完成再跑本脚本。"; exit 1; }
ssh -o BatchMode=yes -o ConnectTimeout=8 "$SSH_HOST" true 2>/dev/null \
  || { echo "❌ SSH 免密未通。先执行: ssh-copy-id $SSH_HOST"; exit 1; }

# ---- 3. 下载 Release 安装包 ----
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "⬇️  下载安装包..."
gh release download "v$VER" --repo "$REMOTE" --dir "$TMP" \
  --pattern '*.msi' --pattern '*.dmg' --pattern '*.deb' --clobber
ls -1 "$TMP" | sed 's/^/   /'

# ---- 4. scp 直传到官网 uploads(绕过 upload API 的 .deb 白名单限制)----
echo "⬆️  上传到 $CASEFIX ..."
for f in "$TMP"/*.msi "$TMP"/*.dmg "$TMP"/*.deb; do
  [ -f "$f" ] || continue
  scp -q "$f" "$SSH_HOST:$AUTH_DIR/data/uploads/" && echo "   ✓ $(basename "$f")"
done

# ---- 5. 从 git log 生成更新日志,写入 release_notes.json ----
PREV=$(git describe --tags --abbrev=0 "v${VER}^" 2>/dev/null | sed 's/^v//' || true)
RANGE="${PREV:+v$PREV..}v$VER"
NOTES_B64=$(git log "$RANGE" --pretty=format:'%s' 2>/dev/null \
  | grep -viE '版本号|bump|merge' \
  | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()], ensure_ascii=False))" \
  | base64 | tr -d '\n')
TODAY=$(date +%Y-%m-%d)

echo "📝 写入更新日志(从 $RANGE 的提交生成)..."
ssh -o BatchMode=yes "$SSH_HOST" "VER='$VER' TODAY='$TODAY' NOTES_B64='$NOTES_B64' python3 -" <<'PY'
import os, json, base64
ver, today = os.environ['VER'], os.environ['TODAY']
notes = json.loads(base64.b64decode(os.environ['NOTES_B64']).decode())
p = "/opt/criminal-llm-auth/data/release_notes.json"
d = json.load(open(p, encoding='utf-8'))
if any(v['version'] == ver for v in d['versions']):
    print(f"   - v{ver} 日志已存在,跳过")
else:
    d['versions'].insert(0, {"version": ver, "date": today, "notes": notes})
    json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f"   ✓ v{ver} 写入 {len(notes)} 条更新日志")
PY

# ---- 6. 验证 ----
echo "🔍 验证线上..."
curl -s "$CASEFIX/api/latest-version" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('   线上版本:', d.get('version'))
n = d.get('release_notes', '') or ''
print('   更新日志:', (n[:70] + '...') if len(n) > 70 else n)
for dl in d.get('downloads', []):
    print('  ', dl['platform'], '→ $CASEFIX' + dl['url'])
"
echo "🎉 v$VER 已发布到 $CASEFIX"
