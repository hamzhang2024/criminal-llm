#!/bin/bash
# 清理编译缓存和产物

cd "$(git rev-parse --show-toplevel)"

echo "清理前大小: $(du -sh . | cut -f1)"

# PyInstaller 缓存
rm -rf backend/build backend/dist backend/__pycache__
echo "✓ 删除 PyInstaller 缓存"

# Rust 编译缓存
rm -rf frontend/src-tauri/target
echo "✓ 删除 Rust target/"

# Tauri 资源副本
rm -rf frontend/src-tauri/resources
echo "✓ 删除 Tauri resources/"

# Python 缓存
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
echo "✓ 删除 Python __pycache__"

# macOS 系统文件
find . -name ".DS_Store" -delete 2>/dev/null
echo "✓ 删除 .DS_Store"

echo "清理后大小: $(du -sh . | cut -f1)"
