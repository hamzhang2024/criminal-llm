#!/usr/bin/env python3
"""
打包前验证脚本 — 检查所有依赖和资源是否正确配置

使用方法：
    python check_build.py

检查项：
1. 前端 node_modules 是否完整
2. 后端 Python 依赖是否安装
3. PyInstaller spec 文件是否包含所有模块
4. 资源文件是否存在
5. Tauri 配置是否正确
"""

import os
import sys
import subprocess
from pathlib import Path

# 颜色输出
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
RESET = '\033[0m'

def print_status(name, status, detail=''):
    """打印状态"""
    color = GREEN if status == 'OK' else RED if status == 'FAIL' else YELLOW
    print(f"  {name}: {color}{status}{RESET} {detail}")

def check_command(cmd, name):
    """检查命令是否可用"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return True, result.stdout.strip().split('\n')[0] if result.stdout else ''
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, ''

def main():
    project_root = Path(__file__).parent.parent
    backend_dir = project_root / 'backend'
    frontend_dir = project_root / 'frontend'

    print(f"\n{'='*60}")
    print("Criminal-LLM 打包验证")
    print(f"{'='*60}\n")

    errors = []

    # 1. 检查命令
    print("【命令检查】")
    for cmd, name in [
        (['node', '--version'], 'Node.js'),
        (['npm', '--version'], 'npm'),
        (['python3', '--version'], 'Python'),
        (['pyinstaller', '--version'], 'PyInstaller'),
    ]:
        ok, version = check_command(cmd, name)
        print_status(name, 'OK' if ok else 'FAIL', version)
        if not ok:
            errors.append(f"{name} 未安装")

    # 2. 检查后端依赖
    print("\n【后端 Python 依赖】")
    backend_modules = [
        'fastapi', 'uvicorn', 'pydantic', 'httpx', 'aiohttp',
        'fitz',  # PyMuPDF
        'PIL',  # Pillow
        'pdf2image', 'requests', 'dotenv',
    ]
    for mod in backend_modules:
        try:
            __import__(mod)
            print_status(mod, 'OK')
        except ImportError:
            print_status(mod, 'FAIL', '未安装')
            errors.append(f"Python 模块 {mod} 未安装")

    # 3. 检查后端文件
    print("\n【后端模块文件】")
    backend_files = [
        'main.py', '_bootstrap.py', 'config.py', 'config_manager.py',
        'case_manager.py', 'process_api.py', 'pdf_processor.py',
        'watermark_remover.py', 'pdf_to_md.py', 'mineru_async.py',
        'paddleocr_async.py', 'analyzer_api.py', 'analysis_engine.py',
        'analysis_pipeline.py', 'pipeline_api.py', 'stage_api.py',
        'background_tasks.py', 'legal_knowledge.py', 'llm_client.py',
    ]
    for f in backend_files:
        path = backend_dir / f
        print_status(f, 'OK' if path.exists() else 'FAIL')
        if not path.exists():
            errors.append(f"后端文件 {f} 不存在")

    # 4. 检查资源文件
    print("\n【资源文件】")
    resources = [
        backend_dir / 'legal_db' / 'criminal_law.md',
        backend_dir / 'legal_db' / 'criminal_procedure_law.md',
    ]
    for r in resources:
        print_status(r.name, 'OK' if r.exists() else 'FAIL', str(r.parent.relative_to(project_root)))
        if not r.exists():
            errors.append(f"资源文件 {r} 不存在")

    # 5. 检查前端依赖
    print("\n【前端依赖】")
    node_modules = frontend_dir / 'node_modules'
    if node_modules.exists():
        print_status('node_modules', 'OK')
        # 检查关键依赖
        key_deps = ['react', 'react-dom', 'lucide-react', 'marked', 'dompurify']
        for dep in key_deps:
            dep_path = node_modules / dep
            print_status(f"  {dep}", 'OK' if dep_path.exists() else 'FAIL')
            if not dep_path.exists():
                errors.append(f"前端依赖 {dep} 未安装")
    else:
        print_status('node_modules', 'FAIL', '请先运行 npm install')
        errors.append('node_modules 不存在')

    # 6. 检查 Tauri 配置
    print("\n【Tauri 配置】")
    tauri_conf = frontend_dir / 'src-tauri' / 'tauri.conf.json'
    if tauri_conf.exists():
        print_status('tauri.conf.json', 'OK')
        # 检查图标
        icons_dir = frontend_dir / 'src-tauri' / 'icons'
        icons = ['icon.ico', 'icon.icns', 'icon.png']
        for icon in icons:
            icon_path = icons_dir / icon
            print_status(f"  {icon}", 'OK' if icon_path.exists() else 'WARN', '可选')
    else:
        print_status('tauri.conf.json', 'FAIL')
        errors.append('tauri.conf.json 不存在')

    # 7. 检查 PyInstaller spec
    print("\n【PyInstaller 配置】")
    spec_file = backend_dir / 'criminal-llm.spec'
    if spec_file.exists():
        print_status('criminal-llm.spec', 'OK')
        # 检查关键模块是否在 hiddenimports 中
        content = spec_file.read_text()
        required_modules = ['case_manager', 'process_api', 'llm_client', 'legal_knowledge']
        for mod in required_modules:
            if f"'{mod}'" in content or f'"{mod}"' in content:
                print_status(f"  {mod} in hiddenimports", 'OK')
            else:
                print_status(f"  {mod} in hiddenimports", 'WARN', '可能遗漏')
    else:
        print_status('criminal-llm.spec', 'FAIL')
        errors.append('criminal-llm.spec 不存在')

    # 8. 检查前端构建
    print("\n【前端构建检查】")
    dist_dir = frontend_dir / 'dist'
    if dist_dir.exists():
        print_status('dist/', 'OK', '已构建')
    else:
        print_status('dist/', 'WARN', '未构建，请运行 npm run build')

    # 总结
    print(f"\n{'='*60}")
    if errors:
        print(f"{RED}发现 {len(errors)} 个问题：{RESET}")
        for e in errors:
            print(f"  - {e}")
        print(f"\n请修复以上问题后再打包。")
        return 1
    else:
        print(f"{GREEN}所有检查通过，可以打包！{RESET}")
        print(f"\n打包命令：")
        print(f"  前端: cd frontend && npm run build")
        print(f"  后端: cd backend && pyinstaller criminal-llm.spec --noconfirm")
        print(f"  Tauri: cd frontend && npx tauri build")
        return 0

if __name__ == '__main__':
    sys.exit(main())
