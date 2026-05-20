# PyInstaller hook for fitz (pymupdf compatibility layer)
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = collect_submodules('fitz')
datas = collect_data_files('fitz')
