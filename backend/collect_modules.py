#!/usr/bin/env python3
"""
打包前模块收集脚本

扫描 backend/ 下所有本地 .py 模块，自动生成 PyInstaller hiddenimports 列表，
根治手动维护 spec 导致的运行时 ModuleNotFoundError。

用法：
    python3 collect_modules.py            # 打印模块名列表（空格分隔）
    python3 collect_modules.py --json     # 打印 JSON 数组

被 spec 调用（见 criminal-llm.spec），无需手动运行。
"""

import sys
from pathlib import Path

# 这些模块不打包：入口（由 Analysis scripts 指定）、死代码、工具脚本
_EXCLUDE_MODULES = {
    "main",              # 入口，由 Analysis(['main.py']) 处理
    "check_md_quality",  # 独立工具脚本，非运行时依赖
    "case_splitter",     # 死代码（无任何引用）
    "ocr_acceleration",  # 死代码（无任何引用）
    "collect_modules",   # 本脚本自身，打包辅助工具
    "new_feature",      # 临时测试：模拟遗漏
    "new_feature",      # 临时测试：模拟遗漏
}


def collect_local_modules(backend_dir: Path) -> list[str]:
    """收集 backend/ 下所有应打包的本地模块名。"""
    modules = []
    for py in sorted(backend_dir.glob("*.py")):
        name = py.stem
        if name.startswith("_") and name not in ("_bootstrap", "_stdio_guard"):
            # 跳过 __init__ 等，但保留 _bootstrap（bootstrap）、_stdio_guard（stdio 兜底，被 main.py import）
            continue
        if name in _EXCLUDE_MODULES:
            continue
        modules.append(name)
    return modules


def main() -> int:
    backend_dir = Path(__file__).resolve().parent
    modules = collect_local_modules(backend_dir)

    if "--json" in sys.argv:
        import json
        print(json.dumps(modules, ensure_ascii=False, indent=2))
    else:
        print(" ".join(modules))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
