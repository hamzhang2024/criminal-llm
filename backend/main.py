"""
刑事案卷 PDF 智能拆分 WebUI - FastAPI 后端

提供 REST API 供前端调用
简化版：移除 pydantic 依赖，使用原生类型
"""
# 初始化环境：加载 DATA_DIR/.env（必须在所有 import 之前）
import os
import sys
from pathlib import Path

# 复制 DATA_DIR 计算逻辑（此时不能 import config，避免循环依赖）
_is_frozen = getattr(sys, 'frozen', False)
if _is_frozen:
    _data_dir = Path.home() / "Documents" / ".criminal-llm-data"
else:
    _data_dir = Path(__file__).parent.parent / "data"
_env_file = _data_dir / ".env"
del _is_frozen, _data_dir

if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()
del _env_file

from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from typing import List, Optional, Dict, Any
import uvicorn
from pathlib import Path

from config import HOST, PORT, DEBUG, CACHE_DIR, UPLOAD_DIR, OUTPUT_DIR, cleanup_old_files
from config_manager import load_config, save_config, get_config_status
from case_manager import cleanup_trash
from pdf_processor import create_job, get_processor, PDFProcessor
from llm_client import close_llm_client
from analyzer_api import router as analyzer_router
from process_api import router as process_router
from case_manager import router as case_router
from stage_api import router as stage_router
from pipeline_api import router as pipeline_router
from legal_kb_api import router as legal_kb_router
from background_tasks import router as bg_task_router

# 创建 FastAPI 应用
app = FastAPI(
    title="Criminal PDF WebUI",
    description="刑事案卷 PDF 智能拆分可视化工具",
    version="1.0.0"
)

# CORS 配置（桌面应用限定 localhost）
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://localhost(:\d+)?|https?://127\.0\.0\.1(:\d+)?|tauri://localhost|http://tauri\.localhost",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件服务（缩略图）
app.mount("/thumbnails", StaticFiles(directory=CACHE_DIR), name="thumbnails")

# 注册路由
app.include_router(analyzer_router)
app.include_router(process_router)
app.include_router(case_router)
app.include_router(pipeline_router)
app.include_router(stage_router)
app.include_router(legal_kb_router)
app.include_router(bg_task_router)

# 静态文件服务（前端构建产物，生产环境使用）
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"

# 静态资源服务
if frontend_dist.exists():
    # Assets 文件
    assets_dist = frontend_dist / "assets"
    if assets_dist.exists():
        app.mount("/assets", StaticFiles(directory=assets_dist), name="assets")

# 说明书静态服务（生产环境）
docs_dir = Path(__file__).parent.parent / "docs"
if docs_dir.exists():
    app.mount("/docs", StaticFiles(directory=docs_dir), name="docs")


# ========== API 端点 ==========

@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/config/concurrency-status")
async def get_concurrency_status():
    """返回当前 LLM 并发状态"""
    from llm_client import get_llm_client
    try:
        llm = get_llm_client()
        if llm.concurrency_controller:
            status = llm.concurrency_controller.get_status()
            return {"success": True, **status}
        return {"success": False, "error": "并发控制器未启用"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/config")
async def get_config():
    """获取配置状态（不返回实际值）"""
    return get_config_status()


@app.put("/api/config")
async def update_config(body: Dict[str, Any]):
    """保存配置（合并已有配置，保留 llm_base_url / llm_model）"""
    existing = load_config()
    merged = {**existing, **body}
    save_config(merged)
    # 重载 LLM 客户端配置
    try:
        from llm_client import get_llm_client
        client = get_llm_client()
        client.reload_config()
        print(f"[配置保存] LLM 客户端已重载: baseUrl={client.base_url}, model={client.model}")
    except Exception as e:
        print(f"[配置保存] LLM 客户端重载失败: {e}")
    return {"success": True, "message": "配置已保存"}


@app.post("/api/config/test")
async def test_config(body: Dict[str, Any]):
    """
    测试配置是否可用（后端发起，无 CORS 问题）

    请求体：{"type": "mineru"|"llm", "token"/"api_key"/"base_url"/"model": ...}
    """
    import httpx
    import ssl
    import certifi

    config_type = body.get("type")

    if config_type == "mineru":
        token = body.get("token", "")
        if not token:
            return {"success": False, "error": "Token 不能为空"}
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            transport = httpx.AsyncHTTPTransport(verify=ssl_ctx)
            async with httpx.AsyncClient(timeout=15, transport=transport) as client:
                resp = await client.post(
                    "https://mineru.net/api/v4/file-urls/batch",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"files": [{"name": "test.pdf", "data_id": "test"}]},
                )
            if resp.status_code == 200:
                return {"success": True, "message": "Token 验证成功"}
            else:
                msg = resp.json().get("msg", f"HTTP {resp.status_code}")
                return {"success": False, "error": msg}
        except Exception as e:
            return {"success": False, "error": f"网络错误: {e}"}

    elif config_type == "llm":
        api_key = body.get("api_key", "")
        base_url = body.get("base_url", "")
        model = body.get("model", "")
        if not api_key:
            return {"success": False, "error": "API Key 不能为空"}
        if not base_url:
            return {"success": False, "error": "Base URL 不能为空"}
        if not model:
            return {"success": False, "error": "模型名称不能为空"}
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            transport = httpx.AsyncHTTPTransport(verify=ssl_ctx)
            async with httpx.AsyncClient(timeout=30, transport=transport) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": model, "messages": [{"role": "user", "content": "你好"}], "max_tokens": 10},
                )
            if resp.status_code == 200:
                return {"success": True, "message": "API Key 验证成功"}
            else:
                err_body = resp.json()
                msg = err_body.get("error", {}).get("message", err_body.get("message", f"HTTP {resp.status_code}"))
                return {"success": False, "error": msg}
        except Exception as e:
            return {"success": False, "error": f"网络错误: {e}"}

    return {"success": False, "error": f"未知的测试类型: {config_type}"}


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """
    上传 PDF 文件

    Returns:
        job_id: 任务 ID
        total_pages: 总页数
        thumbnails: 缩略图列表
    """
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")

    # 检查文件大小
    file_content = await file.read()
    if len(file_content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大，最大支持 {MAX_FILE_SIZE // (1024*1024)}MB"
        )

    # 创建任务
    job_id = create_job()
    processor = get_processor(job_id)

    # 保存文件
    pdf_path = await processor.save_upload(file_content, file.filename)

    # 获取页数
    total_pages = processor.get_page_count(pdf_path)

    # 生成缩略图
    try:
        thumbnails = processor.generate_thumbnails(pdf_path)
    except RuntimeError as e:
        # poppler 未安装，返回空的缩略图列表
        thumbnails = []
        print(f"警告: {e}")

    return {
        "job_id": job_id,
        "filename": file.filename,
        "total_pages": total_pages,
        "thumbnails": thumbnails
    }


@app.get("/api/pages/{job_id}")
async def get_pages(job_id: str):
    """
    获取页面信息
    
    Returns:
        thumbnails: 缩略图列表
    """
    processor = get_processor(job_id)
    
    # 加载已生成的缩略图
    thumbnail_dir = CACHE_DIR / job_id / "thumbnails"
    if not thumbnail_dir.exists():
        raise HTTPException(status_code=404, detail="任务不存在或未处理")
    
    thumbnails = []
    for img in sorted(thumbnail_dir.glob("page_*.png")):
        page_num = int(img.stem.split("_")[1])
        thumbnails.append({
            "page": page_num,
            "url": f"/thumbnails/{job_id}/thumbnails/{img.name}"
        })
    
    return {
        "job_id": job_id,
        "thumbnails": thumbnails
    }



@app.post("/api/cleanup")
async def manual_cleanup(days: int = 7):
    """
    手动清理过期文件
    
    Args:
        days: 清理超过指定天数的文件，默认 7 天
    
    Returns:
        清理统计信息
    """
    stats = cleanup_old_files(days)
    
    return {
        "success": True,
        "deleted_jobs": stats["deleted_jobs"],
        "deleted_count": stats["deleted_files"],
        "freed_size": stats["freed_size"],
        "errors": stats["errors"],
        "message": f"已清理 {stats['deleted_files']} 个任务，释放 {stats['freed_size']}"
    }


@app.get("/api/storage-stats")
async def storage_stats():
    """
    获取存储统计信息
    
    Returns:
        各目录的大小和文件数量
    """
    def get_dir_stats(path: Path) -> dict:
        if not path.exists():
            return {"size": 0, "count": 0}
        
        total_size = 0
        file_count = 0
        
        for f in path.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size
                file_count += 1
        
        # 格式化大小
        if total_size > 1024 * 1024 * 1024:
            size_str = f"{total_size / (1024*1024*1024):.2f} GB"
        elif total_size > 1024 * 1024:
            size_str = f"{total_size / (1024*1024):.2f} MB"
        elif total_size > 1024:
            size_str = f"{total_size / 1024:.2f} KB"
        else:
            size_str = f"{total_size} bytes"
        
        return {"size": total_size, "size_human": size_str, "count": file_count}
    
    return {
        "uploads": get_dir_stats(UPLOAD_DIR),
        "output": get_dir_stats(OUTPUT_DIR),
        "cache": get_dir_stats(CACHE_DIR),
        "total_jobs": len(list(UPLOAD_DIR.iterdir())) if UPLOAD_DIR.exists() else 0
    }


# ========== 生命周期 ==========

@app.on_event("startup")
async def startup():
    """应用启动时初始化"""
    print(f"🚀 Criminal PDF WebUI 启动中...")
    print(f"📂 数据目录: {UPLOAD_DIR.parent}")
    print(f"🔗 API: http://{HOST}:{PORT}/api")

    # 打印配置文件路径和内容（诊断用）
    try:
        from config_manager import CONFIG_PATH, load_config
        print(f"⚙️ 配置文件路径: {CONFIG_PATH}")
        if CONFIG_PATH.exists():
            cfg = load_config()
            print(f"⚙️ 配置内容: llm_base_url={cfg.get('llm_base_url', '(未设置)')}, llm_model={cfg.get('llm_model', '(未设置)')}, llm_api_key={'已配置' if cfg.get('llm_api_key') else '未配置'}, mineru_token={'已配置' if cfg.get('mineru_token') else '未配置'}")
        else:
            print(f"⚙️ 配置文件不存在，使用默认值")
    except Exception as e:
        print(f"⚙️ 读取配置失败: {e}")

    # 初始化后台任务管理器
    from background_tasks import init_tasks
    init_tasks()

    # 自动清理过期文件
    print(f"🧹 检查并清理超过 7 天的文件...")
    stats = cleanup_old_files()
    if stats["deleted_files"] > 0:
        print(f"   ✅ 已清理 {stats['deleted_files']} 个任务，释放 {stats['freed_size']}")
    else:
        print(f"   ✅ 无需清理")

    # 清理回收站中超过 5 天的案件
    print(f"🗑️ 检查回收站...")
    cleaned = cleanup_trash()
    if cleaned:
        print(f"   ✅ 已彻底删除 {len(cleaned)} 个过期案件")
    else:
        print(f"   ✅ 回收站无需清理")


@app.on_event("shutdown")
async def shutdown():
    """应用关闭时清理"""
    await close_llm_client()
    print("👋 Criminal PDF WebUI 已关闭")


# ========== 静态资源回退（favicon 等） ==========
# 必须在 SPA 回退之前，否则会被 index.html 覆盖

@app.get("/favicon.svg")
async def serve_favicon():
    favicon_file = frontend_dist / "favicon.svg"
    if favicon_file.exists():
        return FileResponse(
            favicon_file,
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-cache, no-store"}
        )
    raise HTTPException(status_code=404, detail="Favicon not found")


# ========== SPA 路由回退 ==========
# 注意：必须放在所有 API 路由之后，否则会覆盖 API 路由

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """SPA 路由回退 - 返回 index.html 让 React Router 处理"""
    # 返回 index.html
    index_file = frontend_dist / "index.html"
    if index_file.exists():
        return FileResponse(index_file, media_type="text/html")
    raise HTTPException(status_code=404, detail="前端未构建")


# ========== 启动入口 ==========

if __name__ == "__main__":
    print(f"""
╔════════════════════════════════════════════╗
║   Criminal PDF WebUI v1.0.0               ║
║   刑事案卷 PDF 智能拆分可视化工具            ║
╠════════════════════════════════════════════╣
║   API:  http://{HOST}:{PORT}/api           ║
║   Docs: http://{HOST}:{PORT}/docs          ║
╚════════════════════════════════════════════╝
    """)

    # PyInstaller 模式下直接传递 app 对象（不能通过模块名导入）
    import sys
    if getattr(sys, 'frozen', False):
        uvicorn.run(app, host=HOST, port=PORT, reload=False)
    else:
        uvicorn.run(
            "main:app",
            host=HOST,
            port=PORT,
            reload=DEBUG
        )