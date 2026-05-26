@echo off
set CARGO_TARGET_DIR=C:\Users\Tammy\.cargo-cargo\target
set PATH=C:\Users\Tammy\AppData\Local\Programs\Python\Python313;C:\Users\Tammy\AppData\Local\Programs\Python\Python313\Scripts;%PATH%

echo === Step 1: Install missing deps ===
cd /d D:\criminal-llm-win\backend
python -m pip install PyMuPDF python-multipart pypdf

echo === Step 2: Package backend ===
rmdir /s /q build dist 2>nul
python -m PyInstaller --clean criminal-llm.spec
if %ERRORLEVEL% NEQ 0 (
    echo PyInstaller failed
    exit /b 1
)
copy /Y dist\criminal-llm.exe ..\frontend\src-tauri\resources\backend\criminal-llm.exe
echo Backend packaged successfully

echo === Step 3: Tauri build ===
cd /d D:\criminal-llm-win\frontend
npx tauri build
echo === DONE ===
