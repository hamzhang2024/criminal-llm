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

import ast
import dis
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
            "severity": "warning",  # 兼容性问题，不阻断打包
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
            "severity": "fatal",
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
            "severity": "fatal",
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
    标准库 + 打包工具，确认剩余第三方都在 requirements.txt。

    扫描范围：
      - backend/*.py（顶层，会被打包的本地模块）
      - backend/utils/*.py、backend/hooks/*.py（PyInstaller 子包收集；
        hooks/ 是 hook 脚本，PyInstaller 自身不计入运行时依赖）
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

    # 顶层模块（collect_modules 收集的）
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

    # 子目录：PyInstaller 作为子包收集的 .py（utils/、hooks/ 等）
    # hooks/ 是 PyInstaller hook 脚本，其 PyInstaller 等 import 不算运行时依赖
    for subdir in ("utils", "hooks"):
        sub_dir = BACKEND_DIR / subdir
        if not sub_dir.is_dir():
            continue
        for py_file in sorted(sub_dir.glob("*.py")):
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

    # 工具/非运行时白名单：打包工具本身不进运行时依赖
    _TOOL_WHITELIST = {
        "PyInstaller",  # hooks/*.py 的 hook 脚本依赖，打包时用，不进运行时
    }

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
    for mod in sorted(imported - local_modules - stdlib - _TOOL_WHITELIST):
        pkg = _IMPORT_PKG_ALIASES.get(mod, mod)
        if _norm_pkg(pkg) not in req_names:
            missing.append(mod if pkg == mod else f"{mod}（包名 {pkg}）")

    if missing:
        issues.append({
            "file": "backend/requirements.txt",
            "line": 0,
            "match": f"import {missing[0].split('（')[0]}",
            "severity": "fatal",
            "message": f"第三方依赖被 import 但未列入 requirements.txt: {', '.join(missing)} —— 打包后会 ModuleNotFoundError",
        })

    return issues


def check_lazy_import_safety() -> list:
    """检查 PEP 562 模块级 __getattr__ lazy import 的安全性。

    背景：v1.6.8 的 aiohttp NameError bug。当时 paddleocr_async.py 用模块级
    ``__getattr__``（PEP 562）做 lazy import，但函数体内裸引用 ``aiohttp``
    （LOAD_GLOBAL）。PEP 562 的 __getattr__ **只在属性访问时触发**，字节码
    LOAD_GLOBAL 不经过它，于是函数运行时直接 NameError。

    本检查静态拦截这类组合：
      1. 模块级 ``def __getattr__`` 存在
      2. __getattr__ 内 return 的字符串字面量（lazy 提供的名字，如 "aiohttp"）
      3. 同模块任意函数的字节码中出现 ``LOAD_GLOBAL <该名字>``

    命中即报致命错误——这种代码运行时必然 NameError。
    参考：v1.6.8 aiohttp bug（已改为顶部 import 修复，本规则防回归）。
    """
    issues = []

    for py_file in sorted(BACKEND_DIR.glob("*.py")):
        rel_path = str(py_file.relative_to(ROOT))
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        # 1. 找模块级 def __getattr__
        getattr_func = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "__getattr__":
                getattr_func = node
                break
        if getattr_func is None:
            continue

        # 2. 提取 __getattr__ 内 lazy 提供的名字：
        #    典型模式 `if name == "aiohttp":` —— 比较的字符串常量就是 lazy 名字。
        #    （return 的是模块对象/值，不是名字本身，所以不能从 Return 提取。）
        lazy_names = set()
        for sub in ast.walk(getattr_func):
            if isinstance(sub, ast.Compare):
                # 形如 name == "X" 或 "X" == name
                for cmp in sub.comparators:
                    if isinstance(cmp, ast.Constant) and isinstance(cmp.value, str):
                        lazy_names.add(cmp.value)
                if isinstance(sub.left, ast.Constant) and isinstance(sub.left.value, str):
                    lazy_names.add(sub.left.value)
        if not lazy_names:
            continue

        # 3. 编译并扫描所有顶层函数的字节码，查 LOAD_GLOBAL <lazy 名字>
        try:
            code_obj = compile(source, str(py_file), "exec")
        except SyntaxError:
            continue

        # 收集 (函数名, 行号, 触发的 lazy 名字)
        hits = _scan_load_global_for_names(code_obj, lazy_names)
        for func_name, line_no, hit_name in hits:
            issues.append({
                "file": rel_path,
                "line": line_no,
                "match": f"{func_name}() 内 LOAD_GLOBAL {hit_name}",
                "severity": "fatal",
                "message": (
                    f"函数 '{func_name}' 裸引用 '{hit_name}'（LOAD_GLOBAL），但该名字"
                    f"仅由模块级 __getattr__ lazy 提供——PEP 562 对 LOAD_GLOBAL 不生效，"
                    f"运行时 NameError。改用顶部 import（参见 v1.6.8 aiohttp bug）。"
                ),
            })

    return issues


def _scan_load_global_for_names(code_obj, target_names: set) -> list:
    """递归扫描 code_obj 及其嵌套 code 常量，找 LOAD_GLOBAL 命中 target_names。

    返回 [(func_name, first_line, hit_name), ...]。func_name 取 co_qualname 或
    co_name；first_line 是该 LOAD_GLOBAL 出现的源码行号。
    """
    hits = []
    seen = set()

    def walk(co, func_label):
        if id(co) in seen:
            return
        seen.add(id(co))
        for instr in dis.get_instructions(co):
            if instr.opname == "LOAD_GLOBAL" and instr.argval in target_names:
                hits.append((func_label, instr.positions.lineno if instr.positions else 0, instr.argval))
        # 递归嵌套函数/类（co_consts 里的 code 对象）
        for const in co.co_consts:
            if hasattr(const, "co_code"):
                nested_label = const.co_qualname if hasattr(const, "co_qualname") else const.co_name
                walk(const, nested_label)

    walk(code_obj, "<module>")
    return hits


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

    # 模块完整性检查（致命）
    all_issues.extend(check_module_integrity())

    # 第三方依赖完整性检查（致命，堵住 sse_starlette 那类第三方遗漏）
    all_issues.extend(check_third_party_deps())

    # PEP 562 lazy import 安全检查（致命，堵住 v1.6.8 aiohttp 类 NameError）
    all_issues.extend(check_lazy_import_safety())

    # 兜底：未标 severity 的视为 warning（向后兼容）
    for issue in all_issues:
        issue.setdefault("severity", "warning")

    return all_issues


def print_report(issues: list):
    """打印报告。

    致命问题（severity=fatal）用 ❌ 标记，会阻断 CI 打包。
    兼容性警告（severity=warning）用 ⚠️ 标记，仅提示不阻断。
    返回 exit code：有 fatal 返回 1，否则 0。
    """
    if not issues:
        print("✅ 所有检查通过")
        return 0

    fatal_issues = [i for i in issues if i.get("severity") == "fatal"]
    warning_issues = [i for i in issues if i.get("severity") != "fatal"]

    if fatal_issues:
        print(f"❌ 发现 {len(fatal_issues)} 个致命问题（阻断打包）：\n")
    else:
        print(f"⚠️ 发现 {len(warning_issues)} 个兼容性警告（不阻断打包）：\n")

    # 按严重性分组打印：致命问题在前
    def _print_group(group, icon):
        by_file = {}
        for issue in group:
            by_file.setdefault(issue["file"], []).append(issue)
        for file, file_issues in by_file.items():
            print(f"📄 {file}")
            for issue in file_issues:
                print(f"   {icon} 行 {issue['line']}: {issue['match']}")
                print(f"      问题: {issue['message']}")
            print()

    if fatal_issues:
        _print_group(fatal_issues, "❌")
    if warning_issues:
        if fatal_issues:
            print(f"⚠️ 兼容性警告（{len(warning_issues)} 个，不阻断）：\n")
        _print_group(warning_issues, "⚠️")

    print("建议修复：")
    print("1. 硬编码路径 → 改用 shutil.which() 或 get_ssl_verify()（warning）")
    print("2. 外部命令 → 预检 shutil.which() 后再调用（warning）")
    print("3. print() → 改用 logging.getLogger(__name__)（warning）")
    print("4. 模块未收集 → 确认 backend/collect_modules.py 的 _EXCLUDE_MODULES 是否误排除（fatal）")
    print("5. 第三方依赖未列入 → 加到 backend/requirements.txt（并确认 spec hiddenimports/collect_all 收集，函数内 import 尤其注意）（fatal）")
    print("6. PEP 562 lazy + 函数内裸引用 → 改用顶部 import（fatal，参见 v1.6.8 aiohttp bug）")

    return 1 if fatal_issues else 0


def main():
    print("🔍 打包前兼容性检查...\n")
    issues = run_checks()
    exit_code = print_report(issues)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
