#!/usr/bin/env python3
"""
自动修复 print() 语句为 logger 调用

对于每个 .py 文件：
1. 检查是否已有 logger 定义
2. 如果没有，在 import 区域后添加 `logger = logging.getLogger(__name__)`
3. 将 print("xxx") 替换为 logger.info("xxx") 或 logger.warning/error
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
BACKEND_DIR = ROOT / "backend"


def fix_file(file_path: Path) -> dict:
    """修复单个文件"""
    rel_path = file_path.relative_to(ROOT)
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return {"file": str(rel_path), "error": str(e)}

    original = content
    lines = content.split("\n")

    # 1. 检查是否已有 logger
    has_logger = bool(re.search(r"logger\s*=\s*logging\.getLogger", content))
    has_logging_import = bool(re.search(r"import logging", content))

    # 2. 统计 print 数量
    print_count = len(re.findall(r"\bprint\s*\(", content))

    if print_count == 0:
        return {"file": str(rel_path), "changes": 0}

    # 3. 替换 print 为 logger
    new_lines = []
    for line in lines:
        # 跳过注释行
        stripped = line.lstrip()
        if stripped.startswith("#"):
            new_lines.append(line)
            continue

        # 替换 print(...)
        match = re.search(r"\bprint\s*\(\s*(f?['\"].*?['\"]|[^)]+)\s*\)", line)
        if match:
            indent = line[:line.index("print")]
            arg = match.group(1)

            # 根据内容判断日志级别
            if "[ERROR]" in arg or "错误" in arg or "失败" in arg:
                level = "error"
            elif "[WARN]" in arg or "警告" in arg:
                level = "warning"
            else:
                level = "info"

            # 保留 f-string 前缀
            new_call = f'{indent}logger.{level}({arg})'
            line = re.sub(r"\bprint\s*\([^)]*\)", new_call, line)

        new_lines.append(line)

    new_content = "\n".join(new_lines)

    # 4. 添加 import logging 和 logger 定义
    if not has_logging_import:
        # 找到最后一个 import 行
        import_idx = 0
        for i, line in enumerate(new_lines):
            if line.startswith("import ") or line.startswith("from "):
                import_idx = i

        new_lines.insert(import_idx + 1, "import logging")
        new_content = "\n".join(new_lines)

    if not has_logger:
        # 找到合适的位置插入 logger 定义（import 之后，代码之前）
        insert_idx = 0
        for i, line in enumerate(new_lines):
            if line.startswith("import ") or line.startswith("from "):
                insert_idx = i
            elif insert_idx > 0 and not line.startswith("import ") and not line.startswith("from ") and line.strip():
                break

        new_lines.insert(insert_idx + 1, "")
        new_lines.insert(insert_idx + 2, "logger = logging.getLogger(__name__)")
        new_content = "\n".join(new_lines)

    changes = len(re.findall(r"\blogger\.(info|warning|error)\(", new_content)) - \
              (len(re.findall(r"\blogger\.(info|warning|error)\(", content)) if has_logger else 0)

    if new_content != original:
        file_path.write_text(new_content, encoding="utf-8")
        return {"file": str(rel_path), "changes": changes, "print_replaced": print_count}

    return {"file": str(rel_path), "changes": 0}


def main():
    print("🔧 自动修复 print() 语句...\n")

    py_files = list(BACKEND_DIR.glob("*.py"))
    py_files = [f for f in py_files if "__pycache__" not in str(f)]

    total_changes = 0
    fixed_files = []

    for py_file in py_files:
        result = fix_file(py_file)
        if result.get("changes", 0) > 0:
            fixed_files.append(result)
            total_changes += result["changes"]
            print(f"✅ {result['file']}: 替换 {result.get('print_replaced', 0)} 个 print()")

    print(f"\n📊 共修复 {len(fixed_files)} 个文件，替换 {total_changes} 处 print() 语句")
    print("⚠️  请手动检查关键逻辑，确保日志级别正确")


if __name__ == "__main__":
    main()
