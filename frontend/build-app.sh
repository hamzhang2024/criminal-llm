#!/bin/bash
# 一键打包脚本：PyInstaller + 复制后端 + Tauri build
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/../backend"
TAURI_DIR="$SCRIPT_DIR"
RESOURCES_DIR="$TAURI_DIR/src-tauri/resources/backend"

echo "=== 1/3 PyInstaller 打包后端 ==="
cd "$BACKEND_DIR"
rm -rf __pycache__ build dist
pyinstaller criminal-llm.spec --noconfirm

echo "=== 2/3 复制后端到 Tauri resources ==="
rm -rf "$RESOURCES_DIR"
mkdir -p "$RESOURCES_DIR"
cp -R "$BACKEND_DIR/dist/criminal-llm/"* "$RESOURCES_DIR/"

echo "=== 3/3 Tauri build ==="
cd "$TAURI_DIR"
npx tauri build

echo ""
echo "✅ 打包完成！"
ls -lh "$TAURI_DIR/src-tauri/target/release/bundle/dmg/"*.dmg
