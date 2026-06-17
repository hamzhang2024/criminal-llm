# PyInstaller hook for fitz (pymupdf compatibility layer)
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules('fitz')
datas = collect_data_files('fitz')
