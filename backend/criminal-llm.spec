# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [
    ('legal_db', 'legal_db'),
    ('pdf_to_md.py', '.'),
    ('mineru_async.py', '.'),
    ('paddleocr_async.py', '.'),
    ('paddleocr_remote.py', '.'),
]
binaries = []

# 收集所有主要依赖
tmp_pymupdf = collect_all('PyMuPDF')
datas += tmp_pymupdf[0]; binaries += tmp_pymupdf[1]

tmp_pil = collect_all('PIL')
datas += tmp_pil[0]; binaries += tmp_pil[1]

tmp_pdf2image = collect_all('pdf2image')
datas += tmp_pdf2image[0]; binaries += tmp_pdf2image[1]

# aiohttp (异步 HTTP 客户端，mineru_async 依赖)
tmp_aiohttp = collect_all('aiohttp')
datas += tmp_aiohttp[0]; binaries += tmp_aiohttp[1]

hiddenimports = [
    # 本地模块（后端核心文件）
    '_bootstrap',
    'config',
    'config_manager',
    'case_manager',
    'process_api',
    'pdf_processor',
    'watermark_remover',
    'pdf_to_md',
    'mineru_async',
    'paddleocr_async',
    'paddleocr_remote',
    'analyzer_api',
    'analysis_engine',
    'analysis_pipeline',
    'pipeline_api',
    'pipeline_errors',
    'stage_api',
    'background_tasks',
    'legal_knowledge',
    'legal_search',
    'legal_kb_api',
    'data_dir_api',
    'llm_client',
    'power_manager',
    # 主要依赖
    'requests',
    'fastapi',
    'fastapi.routing',
    'fastapi.dependencies.utils',
    'starlette',
    'starlette.routing',
    'starlette.applications',
    'starlette.middleware',
    'starlette.middleware.cors',
    'starlette.staticfiles',
    'starlette.responses',
    'starlette.requests',
    'starlette.endpoints',
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'python_multipart',
    'pydantic',
    'pydantic.networks',
    'httpx',
    'dotenv',
    'fitz',
    'pdf2image',
    'PIL',
    'pypdf',
    'PIL.Image',
    # aiohttp (异步 HTTP)
    'aiohttp',
    'aiohttp.client',
    'aiohttp.connector',
    'aiohttp.http_writer',
    'aiohttp.http_parser',
    'aiohttp.streams',
    'aiohttp.signals',
    'aiohttp.tracing',
    'aiohttp.payload',
    'aiohttp.multipart',
    # aiohttp 依赖
    'yarl',
    'multidict',
    'attrs',
    'async_timeout',
    # requests 子模块
    'requests.adapters',
    'requests.auth',
    'requests.models',
    'requests.sessions',
    'requests.status_codes',
    'requests.utils',
    # urllib3 (requests 依赖)
    'urllib3',
    'urllib3.util',
    'urllib3.util.retry',
    'urllib3.poolmanager',
    # charset_normalizer / idna / certifi (requests 依赖)
    'charset_normalizer',
    'idna',
    'certifi',
    # annotated_types (pydantic 依赖)
    'annotated_types',
    'pydantic_core',
    'pydantic_core.core_schema',
    'typing_inspection',
    # paddleocr 远程
    'paddleocr_remote',
    # pdf 转换
    'pdf_to_md',
    # MinerU 异步转换
    'mineru_async',
    # PaddleOCR 异步转换
    'paddleocr_async',
]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
