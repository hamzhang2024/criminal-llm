"""ZIP 安全解压工具

防止两类攻击：
1. 路径穿越（Zip Slip）：条目名含 ../ 写入解压目录外
2. Zip Bomb：高压缩比或超大解压总量耗尽磁盘/内存
"""
import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

# 单个 ZIP 文件最大体积（500MB）
MAX_ZIP_SIZE = 500 * 1024 * 1024
# 解压后总大小上限（1GB）
MAX_EXTRACTED_SIZE = 1024 * 1024 * 1024
# 单个条目解压后最大体积（200MB）
MAX_MEMBER_SIZE = 200 * 1024 * 1024


def safe_extractall(zf: zipfile.ZipFile, target_dir: Path) -> None:
    """安全解压 ZIP 到 target_dir。

    - 校验 ZIP 文件体积
    - 逐条目检查路径穿越（必须落在 target_dir 内）
    - 累计校验解压总量，防止 Zip Bomb

    Args:
        zf: 已打开的 ZipFile 对象
        target_dir: 解压目标目录（必须已存在）

    Raises:
        ValueError: 路径穿越或体积超限
    """
    target_dir = target_dir.resolve()
    total_size = 0

    for info in zf.infolist():
        # 路径穿越检查：条目解析后必须落在 target_dir 内
        member_path = (target_dir / info.filename).resolve()
        try:
            member_path.relative_to(target_dir)
        except ValueError as exc:
            raise ValueError(f"非法 ZIP 条目路径（疑似路径穿越）: {info.filename}") from exc

        # 单条目体积校验
        if info.file_size > MAX_MEMBER_SIZE:
            raise ValueError(
                f"ZIP 条目过大（{info.file_size} 字节），超过上限 {MAX_MEMBER_SIZE}: {info.filename}"
            )

        # 累计体积校验（防 Zip Bomb）
        total_size += info.file_size
        if total_size > MAX_EXTRACTED_SIZE:
            raise ValueError(
                f"ZIP 解压总量超过上限 {MAX_EXTRACTED_SIZE} 字节（疑似 Zip Bomb）"
            )

        zf.extract(info, target_dir)

    logger.debug(f"[zip] 安全解压完成: {len(zf.infolist())} 个条目, 共 {total_size} 字节")


def safe_extract_zip(zip_path: Path, target_dir: Path) -> None:
    """打开 ZIP 文件并安全解压（含文件体积预校验）。

    Args:
        zip_path: ZIP 文件路径
        target_dir: 解压目标目录

    Raises:
        ValueError: 文件体积超限或解压过程中路径穿越/超量
    """
    zip_size = zip_path.stat().st_size
    if zip_size > MAX_ZIP_SIZE:
        raise ValueError(f"ZIP 文件过大（{zip_size} 字节），超过上限 {MAX_ZIP_SIZE}")

    with zipfile.ZipFile(zip_path) as zf:
        safe_extractall(zf, target_dir)
