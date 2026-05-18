#!/usr/bin/env python3
"""自动递增版本号并同步到 package.json 和 tauri.conf.json"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent

def bump_version():
    pkg = json.loads((ROOT / "package.json").read_text())
    tauri_conf = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text())

    old_version = pkg["version"]
    parts = old_version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    new_version = ".".join(parts)

    pkg["version"] = new_version
    tauri_conf["version"] = new_version

    (ROOT / "package.json").write_text(json.dumps(pkg, indent=2, ensure_ascii=False) + "\n")
    (ROOT / "src-tauri" / "tauri.conf.json").write_text(json.dumps(tauri_conf, indent=2, ensure_ascii=False) + "\n")

    print(f"版本号: {old_version} → {new_version}")

if __name__ == "__main__":
    bump_version()
