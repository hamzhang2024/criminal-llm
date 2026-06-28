"""
刑事案卷 PDF 智能拆分 WebUI - FastAPI 后端

提供 REST API 供前端调用
简化版：移除 pydantic 依赖，使用原生类型
"""
# 初始化环境：加载 DATA_DIR/.env（必须在所有 import 之前）
import asyncio
import os
import re
import socket
import sys
import time
from contextlib import asynccontextmanager

from _bootstrap import DATA_DIR

# 必须在 import logging / uvicorn 之前：PyInstaller --windowed（Windows 无控制台）打包后
# sys.stdout/stderr/stdin 可能为 None，uvicorn 初始化日志 formatter 时调用
# sys.stderr.isatty() 会直接崩溃，导致后端启动即退出、8080 永不监听。
from _stdio_guard import ensure_stdio

ensure_stdio(DATA_DIR)

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

# Windows: 修复 stderr 中文乱码（cmd 默认 GBK 编码，Python logging 输出 UTF-8 导致乱码）
if sys.platform == "win32":
    import io as _io
    try:
        sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass  # 非控制台模式（PyInstaller --windowed）可能无 stderr.buffer，忽略


def is_port_in_use(host: str, port: int) -> bool:
    """检测端口是否被占用（用于启动前检测旧进程是否已释放端口）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return False
        except OSError:
            return True


from pathlib import Path
from typing import Any

import uvicorn
from analyzer_api import router as analyzer_router
from background_tasks import router as bg_task_router
from case_manager import cleanup_trash
from case_manager import router as case_router
from config_manager import get_config_status, load_config, save_config
from data_dir_api import router as data_dir_router
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from legal_kb_api import router as legal_kb_router
from llm_client import close_llm_client
from pdf_processor import create_job, get_processor
from pipeline_api import router as pipeline_router
from process_api import router as process_router
from stage_api import router as stage_router

from config import CACHE_DIR, DEBUG, HOST, MAX_FILE_SIZE, OUTPUT_DIR, PORT, UPLOAD_DIR, cleanup_old_files

# ========== 生命周期 ==========


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理（替代弃用的 @app.on_event）"""
    # === startup ===
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
            logging.info("[CONFIG] 配置文件不存在，使用默认值")
    except Exception as e:
        logging.error(f"[CONFIG] 读取配置失败: {e}")

    # 恢复后台任务状态（轻量：仅读 JSON，保留同步）
    from background_tasks import init_tasks
    init_tasks()

    # 清理任务丢到后台异步执行，不阻塞 Application startup complete
    async def _background_cleanup():
        try:
            logging.info("[CLEANUP] 检查并清理超过 7 天的文件...")
            stats = cleanup_old_files()
            if stats["deleted_files"] > 0:
                logging.info(f"   已清理 {stats['deleted_files']} 个任务，释放 {stats['freed_size']}")
            else:
                logging.info("   无需清理")

            logging.info("[TRASH] 检查回收站...")
            cleaned = cleanup_trash()
            if cleaned:
                logging.info(f"   已彻底删除 {len(cleaned)} 个过期案件")
            else:
                logging.info("   回收站无需清理")
        except Exception as e:
            logging.error(f"[CLEANUP] 后台清理任务异常: {e}")

    asyncio.create_task(_background_cleanup())

    # 空闲预热：startup complete 后后台加载重依赖
    async def _preload_heavy_deps():
        try:
            from pdf_processor import _get_fitz, _get_pdf2image
            from watermark_remover import _get_fitz as _get_wm_fitz
            await asyncio.sleep(2)  # 让 startup complete 先生效，再预热
            _get_fitz(); _get_pdf2image(); _get_wm_fitz()
            logging.info("[预加载] 重依赖后台预热完成（首次转换/OCR 无需等待加载）")
        except Exception as e:
            logging.warning(f"[预加载] 重依赖预热失败（不影响功能，首次用时再加载）: {e}")

    asyncio.create_task(_preload_heavy_deps())

    yield  # === 应用运行中 ===

    # === shutdown ===
    await close_llm_client()
    logging.info("[SHUTDOWN] Criminal PDF WebUI 已关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="Criminal PDF WebUI",
    description="刑事案卷 PDF 智能拆分可视化工具",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置（桌面应用，仅允许 localhost 来源）
# 生产环境应限制为前端实际地址
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    # Tauri 桌面壳生产模式使用的协议
    "tauri://localhost",
    "https://tauri.localhost",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=True,
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
async def update_config(body: dict[str, Any]):
    """保存配置（合并已有配置，保留 llm_base_url / llm_model）"""
    # SSRF 防护：校验用户可控的外部服务 URL
    from utils.url_guard import SSRFError, validate_external_url
    try:
        llm_base_url = (body.get("llm_base_url") or "").strip()
        if llm_base_url:
            validate_external_url(llm_base_url)
        mineru_local_url = (body.get("mineru_local_url") or "").strip()
        if mineru_local_url:
            # 本地 MinerU 服务允许回环地址
            validate_external_url(mineru_local_url, allow_loopback=True)
    except SSRFError as e:
        return {"success": False, "error": f"URL 校验失败: {e}"}

    existing = load_config()
    merged = {**existing, **body}
    save_config(merged)
    # 重载 LLM 客户端配置
    try:
        from llm_client import get_llm_client
        client = get_llm_client()
        client.reload_config()
        logging.info(f"[配置保存] LLM 客户端已重载: baseUrl={client.base_url}, model={client.model}")
    except Exception as e:
        logging.warning(f"[配置保存] LLM 客户端重载失败: {e}")
    return {"success": True, "message": "配置已保存"}


@app.post("/api/config/test")
async def test_config(body: dict[str, Any]):
    """
    测试配置是否可用（后端发起，无 CORS 问题）

    请求体：{"type": "mineru"|"llm", "token"/"api_key"/"base_url"/"model": ...}
    """
    import httpx

    config_type = body.get("type")

    if config_type == "mineru":
        # 支持本地模式
        mode = body.get("mode", "cloud")
        local_url = body.get("local_url", "").strip()
        if local_url.endswith("/"):
            local_url = local_url[:-1]

        if mode == "local":
            # 本地模式：检查服务器是否可访问
            if not local_url:
                return {"success": False, "error": "本地服务器地址不能为空"}
            # SSRF 防护：本地模式仅允许回环地址
            from utils.url_guard import SSRFError, validate_external_url
            try:
                validate_external_url(local_url, allow_loopback=True)
            except SSRFError as e:
                return {"success": False, "error": f"URL 校验失败: {e}"}
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    # 尝试访问根路径或 API 端点
                    resp = await client.get(f"{local_url}/")
                if resp.status_code < 500:
                    return {"success": True, "message": f"本地服务器连接成功 ({local_url})"}
                else:
                    return {"success": False, "error": f"本地服务器返回 HTTP {resp.status_code}"}
            except httpx.ConnectError:
                return {"success": False, "error": f"无法连接到 {local_url}，请检查服务器是否运行"}
            except httpx.TimeoutException:
                return {"success": False, "error": "连接超时，请检查服务器地址"}
            except Exception as e:
                logging.error(f"[MinerU-Local验证] 异常: {type(e).__name__}: {e}")
                return {"success": False, "error": f"连接失败: {str(e)[:50]}"}

        # 云端模式
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
            logging.info(f"[MinerU验证] token长度={len(token)}")
            async with httpx.AsyncClient(timeout=15, verify=ssl_verify) as client:
                resp = await client.post(
                    "https://mineru.net/api/v4/file-urls/batch",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"files": [{"name": "test.pdf", "data_id": "test"}]},
                )
            logging.debug(f"[MinerU验证] 响应状态: {resp.status_code}, 响应体: {resp.text[:300]}")
            if resp.status_code == 200:
                return {"success": True, "message": "Token 验证成功"}
            else:
                msg = resp.json().get("msg", f"HTTP {resp.status_code}")
                return {"success": False, "error": msg}
        except httpx.RequestError as e:
            logging.error(f"[MinerU验证] 网络请求异常: {type(e).__name__}: {e}")
            return {"success": False, "error": "网络请求失败，请检查网络连接"}
        except Exception as e:
            logging.error(f"[MinerU验证] 未知异常: {type(e).__name__}: {e}")
            return {"success": False, "error": "验证失败，请稍后重试"}

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
        # SSRF 防护：LLM 为外部云服务，禁止回环/内网地址
        from utils.url_guard import SSRFError, validate_external_url
        try:
            validate_external_url(base_url)
        except SSRFError as e:
            return {"success": False, "error": f"Base URL 校验失败: {e}"}
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
            logging.error(f"[LLM验证] 异常: {type(e).__name__}: {e}")
            return {"success": False, "error": "网络请求失败，请检查网络连接"}

    elif config_type == "paddleocr":
        token = body.get("token", "")
        if not token:
            return {"success": False, "error": "Token 不能为空"}
        try:
            from paddleocr_remote import test_connection
            ok, msg = test_connection(token)
            return {"success": ok, "message": msg}
        except Exception as e:
            logging.error(f"[PaddleOCR验证] 异常: {type(e).__name__}: {e}")
            return {"success": False, "error": "验证失败，请稍后重试"}

    elif config_type == "yuandian":
        token = body.get("token", "")
        if not token:
            return {"success": False, "error": "Token 不能为空"}
        try:
            import sys
            if sys.platform == "darwin" and getattr(sys, "frozen", False):
                ssl_verify = "/etc/ssl/cert.pem"
            else:
                ssl_verify = True
            async with httpx.AsyncClient(timeout=15, verify=ssl_verify) as client:
                resp = await client.post(
                    "https://open.chineselaw.com/open/rh_ptal_search",
                    headers={
                        "X-API-Key": token,
                        "Accept": "application/json",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json={"ay": ["盗窃罪"], "top_k": 1},
                )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    return {"success": True, "message": "Token 验证成功"}
                else:
                    return {"success": False, "error": data.get("message", "验证失败")}
            else:
                return {"success": False, "error": f"HTTP {resp.status_code}"}
        except httpx.RequestError as e:
            logging.error(f"[元典验证] 网络请求异常: {type(e).__name__}: {e}")
            return {"success": False, "error": "网络请求失败，请检查网络连接"}
        except Exception as e:
            logging.error(f"[元典验证] 异常: {type(e).__name__}: {e}")
            return {"success": False, "error": "验证失败，请稍后重试"}

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

    # 保存文件（净化文件名防路径穿越）
    from utils.path_validator import sanitize_filename
    safe_filename = sanitize_filename(Path(file.filename).name)
    pdf_path = await processor.save_upload(file_content, safe_filename)

    # 获取页数
    total_pages = processor.get_page_count(pdf_path)

    # 生成缩略图
    try:
        thumbnails = processor.generate_thumbnails(pdf_path)
    except RuntimeError as e:
        # poppler 未安装，返回空的缩略图列表
        thumbnails = []
        logging.warning(f"poppler 未安装: {e}")

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


# 日志脱敏正则：遮蔽常见密钥/密码/Token 形式的敏感值
_REDACT_PATTERNS = [
    # Bearer token（优先匹配整段，含后面的 JWT/Token 值，不保留前缀）
    re.compile(r'Bearer\s+[A-Za-z0-9_\-\.+/=]+', re.IGNORECASE),
    # key=value 或 key: value 形式（api_key / token / password / secret / Authorization）
    # 值部分允许空格以覆盖 "Authorization: Bearer xxx" 已被上一条处理后剩余的纯值场景
    re.compile(
        r'((?:api[_-]?key|token|password|passwd|secret|authorization|mineru[_-]?token|paddleocr[_-]?token|llm[_-]?api[_-]?key)\s*[:=]\s*)["\']?[A-Za-z0-9_\-\.+/= ]{6,}',
        re.IGNORECASE,
    ),
]


def _redact_log(text: str) -> str:
    """对日志文本做敏感字段脱敏，保留 key 名只遮蔽值。"""
    redacted = text
    for pattern in _REDACT_PATTERNS:
        redacted = pattern.sub(
            lambda m: (m.group(1) + "***REDACTED***") if m.lastindex else "***REDACTED***",
            redacted,
        )
    return redacted


@app.get("/api/logs/backend/download")
async def download_backend_log():
    """下载完整的后端日志文件（敏感字段已脱敏）"""
    if not _log_path.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")
    # 读取并脱敏后返回，避免日志中残留的密钥/密码/Token 经下载端点泄露
    raw = _log_path.read_text(encoding="utf-8")
    return PlainTextResponse(_redact_log(raw), media_type="text/plain")


@app.get("/api/logs/backend")
async def get_backend_log(lines: int = 500):
    """获取后端日志的最后 N 行（默认 500 行，敏感字段已脱敏）"""
    if not _log_path.exists():
        return {"success": False, "error": "日志文件不存在", "path": str(_log_path)}
    try:
        all_lines = _redact_log(_log_path.read_text(encoding="utf-8")).splitlines()
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
========================================
  Criminal PDF WebUI v1.0.0
  API:  http://{HOST}:{PORT}/api
  Docs: http://{HOST}:{PORT}/docs
========================================
    """)

    # 端口冲突检测：等待旧进程释放端口（最多 10 秒）
    if is_port_in_use(HOST, PORT):
        logging.warning(f"[PORT] 端口 {HOST}:{PORT} 被占用，等待旧进程释放（最多 10 秒）...")
        for i in range(10):
            time.sleep(1)
            if not is_port_in_use(HOST, PORT):
                logging.info(f"[PORT] 端口已释放（等待 {i + 1}s）")
                break
        else:
            logging.error(
                f"[PORT] 端口 {HOST}:{PORT} 10 秒后仍未释放。"
                f"请手动关闭占用进程后重试。"
            )
            sys.exit(1)

    # PyInstaller 模式下直接传递 app 对象（不能通过模块名导入）
    if getattr(sys, 'frozen', False):
        uvicorn.run(app, host=HOST, port=PORT, reload=False)
    else:
        uvicorn.run(
            "main:app",
            host=HOST,
            port=PORT,
            reload=DEBUG
        )
