#!/usr/bin/env python3
"""
打包前兼容性检查脚本

扫描代码中的潜在兼容性问题：
1. 硬编码路径（/etc/ssl/、/usr/local/ 等 Unix 路径）
2. 外部命令调用（gs、caffeinate、pdftoppm、xdg-open 等）
3. print() 语句（应改用 logger）
4. FileNotFoundError 风险点（subprocess 调用未预检）

用法：
    python scripts/prebuild_check.py

返回：
    0 - 通过
    1 - 发现问题
"""

import re
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent.parent
BACKEND_DIR = ROOT / "backend"

# 检查规则
RULES = {
    "hardcoded_unix_paths": {
        "pattern": r'"/etc/ssl|"\'/etc/ssl|/usr/local/etc',
        "exclude": ["_bootstrap.py"],  # _bootstrap.py 是合法的回退查找
        "message": "硬编码 Unix 路径（Windows 不存在）",
    },
    "external_commands": {
        "pattern": r'subprocess\.(run|Popen|call|check_output)\s*\(\s*\[?\s*"(gs|caffeinate|pdftoppm|xdg-open)"',
        "exclude": ["power_manager.py"],  # power_manager.py 已有 shutil.which 预检
        "message": "外部命令调用未预检（可能 FileNotFoundError）",
    },
    "print_statements": {
        "pattern": r'print\s*\(',
        "exclude": [
            # 允许这些位置的 print（调试输出、临时提示）
            "scripts/",  # 脚本本身
            "main.py",  # 启动提示保留
        ],
        "message": "print() 语句（打包后丢失，应改用 logger）",
    },
}


def check_file(file_path: Path, rule_name: str, rule: dict) -> list:
    """检查单个文件"""
    issues = []
    rel_path = file_path.relative_to(ROOT)

    # 排除检查
    for exc in rule["exclude"]:
        if exc in str(rel_path):
            return issues

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return issues

    pattern = rule["pattern"]
    for match in re.finditer(pattern, content):
        line_num = content[:match.start()].count("\n") + 1
        issues.append({
            "file": str(rel_path),
            "line": line_num,
            "match": match.group(0)[:50],
            "message": rule["message"],
        })

    return issues


def run_checks():
    """运行所有检查"""
    all_issues = []

    # 扫描 backend 目录下的 .py 文件
    py_files = list(BACKEND_DIR.glob("*.py"))
    py_files = [f for f in py_files if "__pycache__" not in str(f)]

    for rule_name, rule in RULES.items():
        for py_file in py_files:
            issues = check_file(py_file, rule_name, rule)
            all_issues.extend(issues)

    return all_issues


def print_report(issues: list):
    """打印报告"""
    if not issues:
        print("✅ 所有检查通过")
        return 0

    print(f"❌ 发现 {len(issues)} 个潜在问题：\n")

    # 按文件分组
    by_file = {}
    for issue in issues:
        file = issue["file"]
        if file not in by_file:
            by_file[file] = []
        by_file[file].append(issue)

    for file, file_issues in by_file.items():
        print(f"📄 {file}")
        for issue in file_issues:
            print(f"   行 {issue['line']}: {issue['match']}")
            print(f"   问题: {issue['message']}")
        print()

    print("建议修复：")
    print("1. 硬编码路径 → 改用 shutil.which() 或 get_ssl_verify()")
    print("2. 外部命令 → 预检 shutil.which() 后再调用")
    print("3. print() → 改用 logging.getLogger(__name__)")

    return 1


def main():
    print("🔍 打包前兼容性检查...\n")
    issues = run_checks()
    exit_code = print_report(issues)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()