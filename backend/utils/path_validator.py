r"""
路径验证工具 - 防止路径遍历攻击

安全规则：
1. 禁止绝对路径（如 /etc/passwd, C:\Windows\...）
2. 禁止路径跳转（如 ../, ..\）
3. 禁止危险字符（如 ;, |, &, $, ` 等 shell 元字符）
4. 仅允许安全字符：中文、字母、数字、下划线、横杠、点、斜杠
"""
import logging
import re
from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger(__name__)


# 安全文件名正则：允许中文、字母、数字、下划线、横杠、点
SAFE_FILENAME_PATTERN = re.compile(r'^[\w\-\.一-龥]+$')

# 安全路径正则：允许中文、字母、数字、下划线、横杠、点、斜杠、反斜杠
SAFE_PATH_PATTERN = re.compile(r'^[\w\-\.\/\\一-龥]+$')

# 危险扩展名黑名单：可执行脚本和系统命令文件，防止命令注入
DANGEROUS_EXTENSIONS = {
    '.sh', '.bash', '.zsh', '.fish',           # Unix shell
    '.bat', '.cmd', '.ps1', '.psm1',           # Windows 脚本
    '.exe', '.com', '.scr', '.msi',            # 可执行文件
    '.py', '.rb', '.pl', '.php', '.jar',       # 脚本语言
    '.vbs', '.wsf', '.wsh',                    # Windows 宿主脚本
    '.reg', '.dll', '.so', '.dylib',           # 系统库和注册表
    '.app', '.action', '.workflow',            # macOS 自动化
}


def sanitize_filename(name: str) -> str:
    """
    严格文件名校验，防止命令注入

    Args:
        name: 文件名（不含路径）

    Returns:
        校验后的文件名

    Raises:
        HTTPException: 文件名包含非法字符
    """
    if not name:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    # 检查是否包含危险字符
    if not SAFE_FILENAME_PATTERN.match(name):
        logger.warning(f"[安全] 文件名包含非法字符: {name}")
        raise HTTPException(
            status_code=400,
            detail="文件名包含非法字符，仅允许中文、字母、数字、下划线、横杠、点"
        )

    # 检查是否包含路径跳转
    if '..' in name:
        raise HTTPException(status_code=400, detail="文件名不能包含路径跳转")

    # 检查危险扩展名（防止上传可执行脚本）
    name_lower = name.lower()
    for ext in DANGEROUS_EXTENSIONS:
        if name_lower.endswith(ext):
            logger.warning(f"[安全] 检测到危险扩展名 '{ext}': {name}")
            raise HTTPException(
                status_code=400,
                detail=f"不允许上传 {ext} 类型的文件"
            )

    return name


def validate_path(base_dir: Path, user_input: str) -> Path:
    """
    验证路径是否在允许范围内，防止路径遍历攻击

    Args:
        base_dir: 基准目录（安全边界）
        user_input: 用户输入的路径（相对路径）

    Returns:
        验证后的安全路径

    Raises:
        HTTPException: 路径越界或包含非法字符

    使用示例:
        case_path = find_case_path(case_id)
        safe_file = validate_path(case_path / "original", file_name)
    """
    if not user_input:
        raise HTTPException(status_code=400, detail="路径不能为空")

    # 1. 禁止绝对路径
    user_path = Path(user_input)
    if user_path.is_absolute():
        logger.warning(f"[安全] 检测到绝对路径尝试: {user_input}")
        raise HTTPException(status_code=400, detail="不允许绝对路径")

    # 2. 禁止路径跳转（../ 或 ..\）
    if '..' in user_input or '..' in str(user_path):
        logger.warning(f"[安全] 检测到路径跳转尝试: {user_input}")
        raise HTTPException(status_code=400, detail="路径不能包含跳转符号")

    # 3. 禁止危险字符（shell 元字符）
    dangerous_chars = [';', '|', '&', '$', '`', '<', '>', '(', ')', '{', '}', '[', ']']
    for char in dangerous_chars:
        if char in user_input:
            logger.warning(f"[安全] 检测到危险字符 '{char}' 在路径中: {user_input}")
            raise HTTPException(
                status_code=400,
                detail=f"路径包含非法字符 '{char}'"
            )

    # 4. 检查是否符合安全路径模式
    if not SAFE_PATH_PATTERN.match(user_input):
        logger.warning(f"[安全] 路径包含非法字符: {user_input}")
        raise HTTPException(
            status_code=400,
            detail="路径包含非法字符"
        )

    # 5. 计算实际路径并验证是否在基准目录内
    resolved_base = base_dir.resolve()
    resolved_target = (base_dir / user_path).resolve()

    # 使用字符串比较检查是否越界
    # 注意：resolve() 可能改变路径表示形式（如大小写），需要规范化
    base_str = str(resolved_base)
    target_str = str(resolved_target)

    if not target_str.startswith(base_str):
        logger.warning(f"[安全] 路径越界尝试: 基准={base_str}, 目标={target_str}")
        raise HTTPException(status_code=400, detail="路径越界，不允许访问基准目录以外的文件")

    return resolved_target


def validate_case_path(case_id: str) -> Path:
    """
    验证案件 ID 并返回案件路径

    Args:
        case_id: 案件 ID

    Returns:
        案件目录路径

    Raises:
        HTTPException: 案件 ID 无效或案件不存在
    """
    from config import DATA_DIR

    if not case_id:
        raise HTTPException(status_code=400, detail="案件 ID 不能为空")

    # 案件 ID 格式检查（case_xxx）
    if not re.match(r'^case_[a-f0-9]+$', case_id):
        raise HTTPException(status_code=400, detail="案件 ID 格式无效")

    # 查找案件目录
    cases_dir = DATA_DIR / "cases"
    if not cases_dir.exists():
        raise HTTPException(status_code=404, detail="案件目录不存在")

    # 查找匹配的案件文件夹
    for case_folder in cases_dir.iterdir():
        if case_folder.is_dir() and case_folder.name.startswith(case_id):
            return case_folder

    raise HTTPException(status_code=404, detail="案件不存在")
