"""
刑事案卷 PDF 智能拆分 WebUI - FastAPI 后端

提供 REST API 供前端调用
简化版：移除 pydantic 依赖，使用原生类型
"""
# 初始化环境：加载 DATA_DIR/.env（必须在所有 import 之前）
import os
from _bootstrap import DATA_DIR

# 加载 .env 文件（此时不能 import config，避免循环依赖）
_env_file = DATA_DIR / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()
del _env_file

# 文件日志（--noconsole 模式下 print 被丢弃，日志写入文件）
_log_path = DATA_DIR / "backend.log"
DATA_DIR.mkdir(parents=True, exist_ok=True)  # 确保目录存在
import logging

_handlers = [logging.StreamHandler()]  # 开发模式输出控制台
try:
    _handlers.insert(0, logging.FileHandler(str(_log_path), encoding="utf-8"))
except (PermissionError, OSError):
    # 文件被占用或无权限，降级为纯控制台输出
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_handlers,
)
del _handlers  # _log_path 保留供 /api/logs/backend 端点使用

from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from typing import List, Optional, Dict, Any
import uvicorn
from pathlib import Path

from config import HOST, PORT, PORT_RANGE, DEBUG, CACHE_DIR, UPLOAD_DIR, OUTPUT_DIR, cleanup_old_files
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
from case_search_api import router as case_search_router
from background_tasks import router as bg_task_router
from data_dir_api import router as data_dir_router

# 创建 FastAPI 应用
app = FastAPI(
    title="Criminal PDF WebUI",
    description="刑事案卷 PDF 智能拆分可视化工具",
    version="1.0.0"
)

# CORS 配置（桌面应用，允许所有来源，安全因为仅监听 localhost）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
app.include_router(case_search_router)
app.include_router(bg_task_router)
app.include_router(data_dir_router)

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


@app.get("/api/llm/cache-stats")
async def llm_cache_stats():
    """LLM 用量统计（进程级累计：调用数/输入/输出/缓存命中，供设置页与提取/分析区实时展示）"""
    from llm_client import get_cache_stats
    return get_cache_stats()


@app.get("/api/config")
async def get_config():
    """获取配置状态（不返回实际值）"""
    status = get_config_status()
    # 附加 PaddleOCR 配额信息
    try:
        from paddleocr_remote import get_daily_quota_status
        status["paddleocr_quota"] = get_daily_quota_status()
    except Exception:
        status["paddleocr_quota"] = None
    return status


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


# ========== 多模型配置管理（v1.9.20 新增） ==========

@app.get("/api/config/llm-profiles")
async def get_llm_profiles():
    """获取所有模型配置列表"""
    from config_manager import get_llm_profiles
    return {"profiles": get_llm_profiles()}


@app.post("/api/config/llm-profiles")
async def save_llm_profile(body: Dict[str, Any]):
    """保存或更新模型配置"""
    from config_manager import save_llm_profile
    profile = body.get("profile")
    if not profile or not profile.get("id"):
        return {"success": False, "error": "缺少 profile 或 id"}
    save_llm_profile(profile)
    return {"success": True}


@app.delete("/api/config/llm-profiles/{profile_id}")
async def delete_llm_profile(profile_id: str):
    """删除模型配置（不能删除 default）"""
    from config_manager import delete_llm_profile
    if delete_llm_profile(profile_id):
        return {"success": True}
    return {"success": False, "error": "不能删除默认模型"}


@app.get("/api/config/llm-profile/{purpose}")
async def get_llm_profile(purpose: str):
    """获取指定用途的模型配置"""
    from config_manager import get_llm_profile
    return {"profile": get_llm_profile(purpose)}


@app.post("/api/config/test")
async def test_config(body: Dict[str, Any]):
    """
    测试配置是否可用（后端发起，无 CORS 问题）

    请求体：{"type": "mineru"|"llm", "token"/"api_key"/"base_url"/"model": ...}
    """
    import httpx

    config_type = body.get("type")

    if config_type == "mineru":
        token = body.get("token", "")
        if not token:
            return {"success": False, "error": "Token 不能为空"}
        try:
            # 打包后 certifi 证书路径可能失效，macOS 用系统证书，Windows 用 certifi 内置
            import sys
            if sys.platform == "darwin" and getattr(sys, "frozen", False):
                ssl_verify = "/etc/ssl/cert.pem"
            else:
                ssl_verify = True
            print(f"[MinerU验证] token长度={len(token)}, token前20字符={token[:20]}...")
            async with httpx.AsyncClient(timeout=15, verify=ssl_verify) as client:
                resp = await client.post(
                    "https://mineru.net/api/v4/file-urls/batch",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"files": [{"name": "test.pdf", "data_id": "test"}]},
                )
            print(f"[MinerU验证] 响应状态: {resp.status_code}, 响应体: {resp.text[:300]}")
            if resp.status_code == 200:
                return {"success": True, "message": "Token 验证成功"}
            else:
                msg = resp.json().get("msg", f"HTTP {resp.status_code}")
                return {"success": False, "error": msg}
        except httpx.RequestError as e:
            print(f"[MinerU验证] 网络请求异常: {type(e).__name__}: {e}")
            return {"success": False, "error": f"网络请求失败: {type(e).__name__} - {e}"}
        except Exception as e:
            print(f"[MinerU验证] 未知异常: {type(e).__name__}: {e}")
            return {"success": False, "error": f"验证异常: {type(e).__name__} - {e}"}

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
            # 同上：打包后 macOS 用系统证书
            import sys
            if sys.platform == "darwin" and getattr(sys, "frozen", False):
                ssl_verify = "/etc/ssl/cert.pem"
            else:
                ssl_verify = True
            async with httpx.AsyncClient(timeout=30, verify=ssl_verify) as client:
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

    elif config_type == "paddleocr":
        token = body.get("token", "")
        if not token:
            return {"success": False, "error": "Token 不能为空"}
        try:
            from paddleocr_remote import test_connection
            ok, msg = test_connection(token)
            return {"success": ok, "message": msg}
        except Exception as e:
            return {"success": False, "error": f"验证异常: {e}"}

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


@app.get("/api/logs/backend/download")
async def download_backend_log():
    """下载完整的后端日志文件"""
    if not _log_path.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")
    return FileResponse(
        str(_log_path),
        media_type="text/plain",
        filename="criminal-llm-backend.log",
    )


@app.get("/api/logs/backend")
async def get_backend_log(lines: int = 500):
    """获取后端日志的最后 N 行（默认 500 行）"""
    if not _log_path.exists():
        return {"success": False, "error": "日志文件不存在", "path": str(_log_path)}
    try:
        all_lines = _log_path.read_text(encoding="utf-8").splitlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return {
            "success": True,
            "total_lines": len(all_lines),
            "returned_lines": len(tail),
            "lines": tail,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


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
    logging.info("[START] Criminal PDF WebUI 启动中...")
    logging.info(f"[DATA] 数据目录: {UPLOAD_DIR.parent}")
    logging.info(f"[API] http://{HOST}:{PORT}/api")

    try:
        from config_manager import CONFIG_PATH, load_config
        logging.info(f"[CONFIG] 配置文件路径: {CONFIG_PATH}")
        if CONFIG_PATH.exists():
            cfg = load_config()
            logging.info(f"[CONFIG] llm_base_url={cfg.get('llm_base_url', '(未设置)')}, llm_model={cfg.get('llm_model', '(未设置)')}")
        else:
            logging.info(f"[CONFIG] 配置文件不存在，使用默认值")
    except Exception as e:
        logging.error(f"[CONFIG] 读取配置失败: {e}")

    from background_tasks import init_tasks
    init_tasks()

    logging.info(f"[CLEANUP] 检查并清理超过 7 天的文件...")
    stats = cleanup_old_files()
    if stats["deleted_files"] > 0:
        logging.info(f"   已清理 {stats['deleted_files']} 个任务，释放 {stats['freed_size']}")
    else:
        logging.info(f"   无需清理")

    logging.info(f"[TRASH] 检查回收站...")
    cleaned = cleanup_trash()
    if cleaned:
        logging.info(f"   已彻底删除 {len(cleaned)} 个过期案件")
    else:
        logging.info(f"   回收站无需清理")


@app.on_event("shutdown")
async def shutdown():
    """应用关闭时清理"""
    await close_llm_client()
    logging.info("[SHUTDOWN] Criminal PDF WebUI 已关闭")


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
    import socket

    # 端口探测：从 BASE_PORT 开始试，直到 PORT_RANGE 个端口
    actual_port = PORT
    for offset in range(PORT_RANGE):
        test_port = PORT + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex((HOST, test_port))
            if result != 0:
                actual_port = test_port
                break

    print(f"""
========================================
  Criminal PDF WebUI v1.0.0
  API:  http://{HOST}:{actual_port}/api
  Docs: http://{HOST}:{actual_port}/docs
========================================
    """)

    # 写端口到文件（供 Rust/Frontend 读取）
    from config import DATA_DIR
    port_file = DATA_DIR / "backend.port"
    port_file.write_text(str(actual_port), encoding="utf-8")

    # 清理旧任务文件中的不完整记录
    tasks_file = DATA_DIR / "criminal-llm-tasks.json"
    if tasks_file.exists():
        try:
            import json
            tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
            # 删除缺少必要字段的任务
            cleaned = {}
            for tid, s in tasks.items():
                if isinstance(s, dict) and s.get("case_id"):
                    cleaned[tid] = s
            tasks_file.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
            logging.info(f"[启动] 清理任务文件: {len(tasks)}→{len(cleaned)} 条")
        except Exception:
            pass

    # PyInstaller 模式下直接传递 app 对象（不能通过模块名导入）
    import sys
    if getattr(sys, 'frozen', False):
        uvicorn.run(app, host=HOST, port=actual_port, reload=False)
    else:
        uvicorn.run(
            "main:app",
            host=HOST,
            port=actual_port,
            reload=DEBUG
        )