"""
数据目录初始化 — 在 config.py 和 main.py 之前加载
避免循环依赖：此模块只使用 stdlib
"""
import os
import sys
from pathlib import Path

# 环境变量优先（方便高级用户自定义）
_env_dir = os.environ.get("CRIMINAL_LLM_DATA_DIR")
if _env_dir:
    DATA_DIR = Path(_env_dir)
elif sys.platform == "darwin":
    # macOS：统一使用 ~/Documents/.criminal-llm-data/
    # 无论开发模式还是打包模式，数据目录一致，避免升级后数据"丢失"
    DATA_DIR = Path.home() / "Documents" / ".criminal-llm-data"
elif sys.platform == "win32":
    # Windows：根据用户反馈，数据存储在安装目录下的 data 文件夹
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后：data/ 在安装目录下
        # sys.executable 是 .../Criminal-Case-Analyzer/Criminal-Case-Analyzer.exe
        # 需要往上两级到达安装目录
        DATA_DIR = Path(sys.executable).resolve().parent.parent / "data"
    else:
        # 开发模式：data/ 在项目根目录
        DATA_DIR = Path(__file__).resolve().parent.parent / "data"
else:
    # Linux 等其他平台
    DATA_DIR = Path.home() / ".criminal-llm-data"

# 确保目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
