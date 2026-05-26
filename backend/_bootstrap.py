"""
数据目录初始化 — 在 config.py 和 main.py 之前加载
避免循环依赖：此模块只使用 stdlib
"""
import os
import sys
from pathlib import Path

if sys.platform == "darwin":
    # macOS
    if getattr(sys, "frozen", False):
        # 打包后：sys.executable → {app}.app/Contents/MacOS/{name}
        # 上溯 2 级 → {app}.app/Contents
        DATA_DIR = Path(sys.executable).resolve().parent.parent.parent / "data"
    else:
        # 开发模式：文稿目录（隐藏）
        DATA_DIR = Path.home() / "Documents" / ".criminal-llm-data"
elif sys.platform == "win32":
    if getattr(sys, "frozen", False):
        # 打包后：data/ 在安装目录下
        # sys.executable → {安装目录}/resources/backend/criminal-llm.exe
        # 上溯 3 级 → {安装目录}
        DATA_DIR = Path(sys.executable).resolve().parent.parent.parent / "data"
    else:
        # 开发模式：data/ 在项目根目录下
        # __file__ → {项目目录}/backend/_bootstrap.py
        # 上溯 1 级 → {项目目录}
        DATA_DIR = Path(__file__).resolve().parent.parent / "data"
else:
    # Linux 等其他平台
    DATA_DIR = Path.home() / ".criminal-llm-data"

# 确保目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
