# -*- mode: python ; coding: utf-8 -*-
"""
Criminal LLM PyInstaller 打包配置

本地模块通过 collect_modules.py 自动收集，避免手动维护遗漏导致运行时
ModuleNotFoundError。修改 backend/ 新增模块无需同步本文件。

打包前校验：scripts/prebuild_check.py + backend/collect_modules.py
"""
import subprocess
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

BACKEND_DIR = Path(SPECPATH)  # spec 文件所在目录（即 backend/）


def _collect_local_modules() -> list[str]:
    """调用 collect_modules.py 自动收集本地模块名。"""
    result = subprocess.run(
        [sys.executable, str(BACKEND_DIR / "collect_modules.py")],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


# ---- 数据文件 ----
datas = [
    ('legal_db', 'legal_db'),
    # 单文件模块已在 hiddenimports 中，无需重复列入 datas
]
binaries = []

# ---- 收集带二进制/数据资源的第三方库 ----
# PyMuPDF（含 native lib）
tmp_pymupdf = collect_all('PyMuPDF')
datas += tmp_pymupdf[0]; binaries += tmp_pymupdf[1]

# Pillow
tmp_pil = collect_all('PIL')
datas += tmp_pil[0]; binaries += tmp_pil[1]

# pdf2image（薄封装，保留 collect_all 以防遗漏子模块）
tmp_pdf2image = collect_all('pdf2image')
datas += tmp_pdf2image[0]; binaries += tmp_pdf2image[1]

# aiohttp（异步 HTTP，mineru_async 依赖）
tmp_aiohttp = collect_all('aiohttp')
datas += tmp_aiohttp[0]; binaries += tmp_aiohttp[1]

# ---- hiddenimports ----
hiddenimports = [
    # 本地模块：自动收集，覆盖所有 *_helpers / 子模块
    *_collect_local_modules(),
    # 第三方库：仅列 PyInstaller 静态分析可能漏掉的子模块（其余自动发现）
    'uvicorn.loops.auto',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan.on',
    'python_multipart',
    'pydantic.networks',
    'pydantic_core.core_schema',
    'aiohttp.client',
    'aiohttp.connector',
    'aiohttp.http_writer',
    'aiohttp.http_parser',
    'aiohttp.streams',
    'aiohttp.signals',
    'aiohttp.tracing',
    'aiohttp.payload',
    'aiohttp.multipart',
    'yarl',
    'multidict',
    'async_timeout',
    'urllib3.util.retry',
]

# ---- excludes：排除后端未使用但被传递引入的重依赖，瘦身 ----
excludes = [
    'numpy',          # 后端代码未 import，却被 opencv/PIL 传递引入（上百 MB）
    'cv2',            # opencv，后端未使用
    'opencv',         # 同上
    'PyPDF2',         # 已用 PyMuPDF 替代，未 import
    'pdfplumber',     # 仅注释提及，未实际 import
    'matplotlib',     # 未使用
    'tkinter',        # 无 GUI 需求
    'pytest',         # 测试框架，不应进产物
    'IPython',
    'jupyter',
    'notebook',
]


a = Analysis(
    ['main.py'],
    pathex=[str(BACKEND_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='criminal-llm',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
