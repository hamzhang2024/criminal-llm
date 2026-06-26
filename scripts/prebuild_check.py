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
import subprocess
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
            "collect_modules.py",  # 打包辅助脚本，print 为其输出
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


def check_module_integrity() -> list:
    """检查模块完整性：被引用的本地模块是否都在 collect_modules 收集范围内。

    捕获未来新增模块忘记打包导致的运行时 ModuleNotFoundError。
    直接调用 collect_modules.py 拿真实输出，避免与脚本排除规则脱节。
    """
    issues = []

    # 调用 collect_modules.py 拿真实收集结果
    collect_script = BACKEND_DIR / "collect_modules.py"
    result = subprocess.run(
        [sys.executable, str(collect_script)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        issues.append({
            "file": "backend/collect_modules.py",
            "line": 0,
            "match": "collect_modules 执行失败",
            "message": f"collect_modules.py 执行失败: {result.stderr.strip()}",
        })
        return issues

    collected = set(result.stdout.split())

    # 扫描所有 import 引用，确认被引用的本地模块都在收集范围内
    import_re = re.compile(r'^\s*(?:from\s+(\w+)\s+import|import\s+(\w+))', re.MULTILINE)
    referenced = set()
    for py_file in BACKEND_DIR.glob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in import_re.finditer(content):
            mod = m.group(1) or m.group(2)
            referenced.add(mod)

    # 被引用、是本地 .py 文件、但未被 collect_modules 收集的模块
    truly_missing = {
        mod for mod in referenced
        if (BACKEND_DIR / f"{mod}.py").exists() and mod not in collected
    }

    for mod in sorted(truly_missing):
        issues.append({
            "file": f"backend/{mod}.py",
            "line": 0,
            "match": f"import {mod}",
            "message": f"本地模块 '{mod}' 被 import 但未被 collect_modules 收集，打包后会 ModuleNotFoundError",
        })

    return issues


# 第三方包 import 名 → requirements 包名（处理两者不一致的常见情况）
_IMPORT_PKG_ALIASES = {
    "fitz": "PyMuPDF",
    "PIL": "Pillow",
    "dotenv": "python-dotenv",
    "multipart": "python-multipart",
    "python_multipart": "python-multipart",
    "sse_starlette": "sse-starlette",
    "pydantic_core": "pydantic-core",
}


def _norm_pkg(name: str) -> str:
    """规范化包名用于比较（PEP 503：连字符/下划线/点等价，小写）。"""
    return name.lower().replace("-", "_").replace(".", "_")


def check_third_party_deps() -> list:
    """检查第三方依赖完整性：代码 import 的第三方库是否都在 requirements.txt。

    collect_modules / check_module_integrity 只覆盖「本地模块」；第三方依赖（如
    sse_starlette）若漏进 requirements，打包后运行时 ModuleNotFoundError（v1.6.6
    之前的 SSE 500 即此问题）。本函数扫描所有 import（含函数内），排除本地模块 +
    标准库，确认剩余第三方都在 requirements.txt。
    """
    issues = []

    # 只扫描「会被打包」的本地模块（collect_modules 已排除死代码如 ocr_acceleration，
    # 否则死代码里的 onnxruntime/rapidocr 等可选依赖会误报）
    collect_script = BACKEND_DIR / "collect_modules.py"
    result = subprocess.run(
        [sys.executable, str(collect_script)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return issues  # collect_modules 失败由 check_module_integrity 单独报
    scan_modules = set(result.stdout.split())

    import_re = re.compile(r'^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))', re.MULTILINE)
    imported = set()
    for mod in scan_modules:
        py_file = BACKEND_DIR / f"{mod}.py"
        if not py_file.exists():
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in import_re.finditer(content):
            name = m.group(1) or m.group(2)
            if name and not name.startswith("."):
                imported.add(name.split(".")[0])

    # 本地：会被打包的模块 + backend 下的子包目录（utils/、legal_db/ 等）
    local_modules = set(scan_modules)
    for d in BACKEND_DIR.iterdir():
        if d.is_dir() and d.name != "__pycache__":
            local_modules.add(d.name)
    stdlib = set(getattr(sys, "stdlib_module_names", set()))

    req_names = set()
    req_file = BACKEND_DIR / "requirements.txt"
    if req_file.exists():
        for line in req_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pkg = re.split(r"[>=<\[!~;]", line)[0].strip()
                if pkg:
                    req_names.add(_norm_pkg(pkg))

    missing = []
    for mod in sorted(imported - local_modules - stdlib):
        pkg = _IMPORT_PKG_ALIASES.get(mod, mod)
        if _norm_pkg(pkg) not in req_names:
            missing.append(mod if pkg == mod else f"{mod}（包名 {pkg}）")

    if missing:
        issues.append({
            "file": "backend/requirements.txt",
            "line": 0,
            "match": f"import {missing[0].split('（')[0]}",
            "message": f"第三方依赖被 import 但未列入 requirements.txt: {', '.join(missing)} —— 打包后会 ModuleNotFoundError",
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

    # 模块完整性检查
    all_issues.extend(check_module_integrity())

    # 第三方依赖完整性检查（堵住 sse_starlette 那类第三方遗漏）
    all_issues.extend(check_third_party_deps())

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
    print("4. 模块未收集 → 确认 backend/collect_modules.py 的 _EXCLUDE_MODULES 是否误排除")
    print("5. 第三方依赖未列入 → 加到 backend/requirements.txt（并确认 spec hiddenimports/collect_all 收集，函数内 import 尤其注意）")

    return 1


def main():
    print("🔍 打包前兼容性检查...\n")
    issues = run_checks()
    exit_code = print_report(issues)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
