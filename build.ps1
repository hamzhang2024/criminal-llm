$env:CARGO_TARGET_DIR = "C:\Users\Tammy\.cargo-cargo\target"
$env:PATH = "C:\Users\Tammy\.cargo\bin;C:\Users\Tammy\AppData\Local\Programs\Python\Python313;C:\Users\Tammy\AppData\Local\Programs\Python\Python313\Scripts;" + $env:PATH

# Install ALL missing deps from requirements.txt
Set-Location D:\criminal-llm-win\backend
python -m pip install -r requirements.txt

# Also install additional deps not in requirements.txt
python -m pip install PyMuPDF pypdf pdf2image Pillow requests python-multipart httpx starlette pydantic python-dotenv uvicorn

# Package backend
Set-Location D:\criminal-llm-win\backend
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
python -m PyInstaller --clean criminal-llm.spec
if ($LASTEXITCODE -ne 0) { exit 1 }
Copy-Item -Force dist\criminal-llm.exe ..\frontend\src-tauri\resources\backend\criminal-llm.exe

# Tauri build
Set-Location D:\criminal-llm-win\frontend
npx tauri build
