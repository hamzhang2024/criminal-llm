"""
配置管理
"""
import os
import shutil
from datetime import datetime, timedelta

from _bootstrap import DATA_DIR
from dotenv import load_dotenv

# 加载环境变量
load_dotenv(str(DATA_DIR / ".env"))
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
CACHE_DIR = DATA_DIR / "cache"
ENV_FILE = DATA_DIR / ".env"

# 确保目录存在
for d in [UPLOAD_DIR, OUTPUT_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# OpenClaw 配置（已废弃，保留向后兼容）
OPENCLAW_URL = os.getenv("OPENCLAW_URL", "")
OPENCLAW_MODEL = os.getenv("OPENCLAW_MODEL", "")

# 服务配置（桌面应用默认绑定 localhost）
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", 8080))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# PDF 处理配置
THUMBNAIL_WIDTH = int(os.getenv("THUMBNAIL_WIDTH", 400))  # 提高到400px
THUMBNAIL_DPI = int(os.getenv("THUMBNAIL_DPI", 150))       # 提高到150 DPI
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 500 * 1024 * 1024))  # 500MB

# 自动清理配置
AUTO_CLEANUP_DAYS = int(os.getenv("AUTO_CLEANUP_DAYS", 7))  # 默认清理超过7天的文件


def cleanup_old_files(days: int = AUTO_CLEANUP_DAYS) -> dict:
    """
    清理超过指定天数的文件

    Args:
        days: 保留天数，超过此天数的文件将被删除

    Returns:
        清理统计信息
    """
    cutoff_time = datetime.now() - timedelta(days=days)

    stats = {
        "deleted_jobs": [],
        "deleted_files": 0,
        "freed_bytes": 0,
        "errors": []
    }

    # 清理 uploads、output、cache 目录
    for data_dir in [UPLOAD_DIR, OUTPUT_DIR, CACHE_DIR]:
        if not data_dir.exists():
            continue

        for job_dir in data_dir.iterdir():
            if not job_dir.is_dir():
                continue

            try:
                # 获取目录修改时间
                mtime = datetime.fromtimestamp(job_dir.stat().st_mtime)

                if mtime < cutoff_time:
                    # 计算大小
                    dir_size = sum(f.stat().st_size for f in job_dir.rglob("*") if f.is_file())

                    # 删除目录
                    shutil.rmtree(job_dir)

                    stats["deleted_jobs"].append(job_dir.name)
                    stats["deleted_files"] += 1
                    stats["freed_bytes"] += dir_size

            except Exception as e:
                stats["errors"].append(f"{job_dir.name}: {str(e)}")

    # 格式化大小
    if stats["freed_bytes"] > 1024 * 1024 * 1024:
        stats["freed_size"] = f"{stats['freed_bytes'] / (1024*1024*1024):.2f} GB"
    elif stats["freed_bytes"] > 1024 * 1024:
        stats["freed_size"] = f"{stats['freed_bytes'] / (1024*1024):.2f} MB"
    elif stats["freed_bytes"] > 1024:
        stats["freed_size"] = f"{stats['freed_bytes'] / 1024:.2f} KB"
    else:
        stats["freed_size"] = f"{stats['freed_bytes']} bytes"

    return stats
