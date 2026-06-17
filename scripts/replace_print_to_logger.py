#!/usr/bin/env python3
"""
将后端模块中的 print() 替换为 logger 调用。

规则：
1. 跳过 if __name__ == "__main__": 块内的 print（CLI 输出）
2. 跳过 docstring 中的 print
3. 按前缀映射日志级别：
   - [错误]/[ERROR]/[Error] → logger.error
   - [警告]/[WARN]/[Warning] → logger.warning
   - [信息]/[INFO] → logger.info
   - [调试]/[DEBUG] → logger.debug
   - [DELETE]/[删除] → logger.warning
   - 其他 → logger.info
4. 在文件顶部确保有 import logging 和 logger = logging.getLogger(__name__)
"""
import re
import sys
from pathlib import Path


# 前缀到日志级别的映射
LEVEL_PATTERNS = [
    (r'\[(错误|ERROR|Error)\]', 'error'),
    (r'\[(警告|WARN|Warning|DELETE|删除)\]', 'warning'),
    (r'\[(调试|DEBUG|Debug)\]', 'debug'),
    (r'\[(信息|INFO)\]', 'info'),
]


def detect_level(content: str) -> str:
    """根据 print 内容的前缀检测日志级别"""
    for pattern, level in LEVEL_PATTERNS:
        if re.search(pattern, content):
            return level
    # 内容包含失败/错误关键字
    if any(kw in content for kw in ['失败', '异常', '错误', 'error', 'Error', 'traceback']):
        return 'error'
    return 'info'


def is_in_main_block(lines: list, idx: int) -> bool:
    """判断某行是否在 if __name__ == "__main__": 块内"""
    for i in range(idx - 1, -1, -1):
        line = lines[i]
        if 'if __name__' in line and '=="__main__"' in line.replace('"', '"').replace("'", '"'):
            return True
        # 如果遇到顶层非空行（无缩进）且不是注释，说明不在 main 块
        if line and not line[0].isspace() and not line.startswith('#') and not line.startswith('"""') and not line.startswith("'''"):
            return False
    return False


def find_main_block_start(lines: list) -> int:
    """返回 if __name__ == "__main__": 所在行号，找不到返回 -1"""
    for i, line in enumerate(lines):
        if 'if __name__' in line and '__main__' in line:
            return i
    return -1


def ensure_logger_setup(lines: list) -> list:
    """确保文件顶部有 logger 初始化"""
    has_import = any(re.match(r'^import logging\s*$', l) for l in lines)
    has_logger = any(re.match(r'^logger\s*=\s*logging\.getLogger', l) for l in lines)

    if has_import and has_logger:
        return lines

    # 找到插入位置：最后一个 import 语句之后
    last_import = -1
    for i, line in enumerate(lines):
        if (line.startswith('import ') or line.startswith('from ')) and not line.startswith('from __future__'):
            last_import = i
        elif line.strip() == '' and last_import >= 0:
            # 空行可能表示 import 区结束，但继续找
            pass
        elif line and not line[0].isspace() and not line.startswith('#') and not line.startswith('"""') and last_import >= 0:
            break

    if last_import < 0:
        # 文件开头插入
        new_lines = ['import logging', '', 'logger = logging.getLogger(__name__)', ''] + lines
        return new_lines

    # 在 last_import 之后插入
    insert_pos = last_import + 1
    additions = []
    if not has_import:
        additions.append('import logging')
    if not has_logger:
        if additions:
            additions.append('')
        additions.append('logger = logging.getLogger(__name__)')

    # 检查插入点是否需要空行
    if insert_pos < len(lines) and lines[insert_pos].strip() != '':
        additions.append('')

    return lines[:insert_pos] + additions + lines[insert_pos:]


def transform_print_call(line: str) -> str:
    """将单行 print(...) 转换为 logger.xxx(...)"""
    # 匹配 print(f"...") 或 print("...") 或 print('...')
    # 保留缩进
    m = re.match(r'^(\s*)print\((.+)\)\s*$', line)
    if not m:
        return line

    indent = m.group(1)
    content = m.group(2)

    level = detect_level(content)

    # 处理 print("=" * 60) 这种
    if re.match(r'^["\'][\-=*\s]+["\']\s*\*\s*\d+$', content) or re.match(r'^["\'][\-=*]+["\']$', content):
        return f'{indent}logger.info({content})'

    return f'{indent}logger.{level}({content})'


def process_file(path: Path) -> tuple:
    """处理单个文件，返回 (修改数, 是否改动)"""
    text = path.read_text(encoding='utf-8')
    lines = text.split('\n')

    main_start = find_main_block_start(lines)

    # 识别 docstring 范围（简单的三引号块检测）
    in_docstring = False
    docstring_char = None

    changed_count = 0
    new_lines = []
    for i, line in enumerate(lines):
        # 跟踪 docstring 状态
        stripped = line.lstrip()
        if not in_docstring:
            for q in ['"""', "'''"]:
                if stripped.startswith(q):
                    # 检查是否单行 docstring
                    rest = stripped[3:]
                    if q in rest:
                        pass  # 单行，不进入块
                    else:
                        in_docstring = True
                        docstring_char = q
                        break
        else:
            if docstring_char in line:
                in_docstring = False
                docstring_char = None

        # 跳过 docstring 内的 print
        if in_docstring:
            new_lines.append(line)
            continue

        # 跳过 main 块内的 print
        if main_start >= 0 and i > main_start:
            new_lines.append(line)
            continue

        # 替换 print
        if re.match(r'^\s*print\(', line):
            new_line = transform_print_call(line)
            if new_line != line:
                changed_count += 1
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    if changed_count == 0:
        return 0, False

    # 确保 logger 设置
    new_lines = ensure_logger_setup(new_lines)

    new_text = '\n'.join(new_lines)
    if new_text != text:
        path.write_text(new_text, encoding='utf-8')
    return changed_count, True


def main():
    files = [
        'backend/pdf_to_md.py',
        'backend/analysis_pipeline.py',
        'backend/paddleocr_remote.py',
        'backend/process_api.py',
        'backend/mineru_async.py',
        'backend/watermark_remover.py',
        'backend/legal_search.py',
        'backend/ocr_acceleration.py',
        'backend/llm_client.py',
        'backend/power_manager.py',
        'backend/legal_knowledge.py',
        'backend/case_splitter.py',
        'backend/case_manager.py',
    ]

    root = Path(__file__).parent.parent
    total = 0
    for rel in files:
        path = root / rel
        if not path.exists():
            print(f"跳过（不存在）: {rel}")
            continue
        count, changed = process_file(path)
        if changed:
            print(f"✓ {rel}: 替换 {count} 处")
            total += count
        else:
            print(f"- {rel}: 无需修改")
    print(f"\n总计替换: {total} 处")


if __name__ == '__main__':
    main()
