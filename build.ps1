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
# onedir 模式：dist\criminal-llm\ 是目录（二进制 + _internal\），先清理旧产物再整目录复制
Remove-Item -Recurse -Force ..\frontend\src-tauri\resources\backend\* -ErrorAction SilentlyContinue
Copy-Item -Recurse -Force dist\criminal-llm\* ..\frontend\src-tauri\resources\backend\

# Tauri build
Set-Location D:\criminal-llm-win\frontend
npx tauri build
