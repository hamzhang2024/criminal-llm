#!/bin/bash
# 一键打包脚本：PyInstaller + 复制后端 + Tauri build
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/../backend"
TAURI_DIR="$SCRIPT_DIR"
RESOURCES_DIR="$TAURI_DIR/src-tauri/resources/backend"

echo "=== 0/4 打包前校验（兼容性 + 模块完整性） ==="
cd "$SCRIPT_DIR/.."
# 运行校验，捕获输出；print 等兼容性警告允许通过，模块完整性问题（ModuleNotFoundError）硬阻断
CHECK_OUTPUT="$(python3 scripts/prebuild_check.py 2>&1)" || true
echo "$CHECK_OUTPUT"
if echo "$CHECK_OUTPUT" | grep -q "ModuleNotFoundError"; then
    echo "❌ 模块完整性检查失败，存在未收集模块，终止打包"
    exit 1
fi
echo "✅ 校验通过（模块完整性 OK）"

echo "=== 1/4 PyInstaller 打包后端 ==="
cd "$BACKEND_DIR"
rm -rf __pycache__ build dist
pyinstaller criminal-llm.spec --noconfirm

echo "=== 2/4 复制后端到 Tauri resources ==="
rm -rf "$RESOURCES_DIR"
mkdir -p "$RESOURCES_DIR"
cp -R "$BACKEND_DIR/dist/criminal-llm/"* "$RESOURCES_DIR/"

echo "=== 3/4 Tauri build ==="
cd "$TAURI_DIR"
npx tauri build

echo ""
echo "=== 4/4 打包完成 ==="
ls -lh "$TAURI_DIR/src-tauri/target/release/bundle/dmg/"*.dmg 2>/dev/null || echo "（无 DMG 产物，可能目标平台不同）"
