use axum::{
    extract::{State, Json},
    routing::{get, any},
    Router,
    response::IntoResponse,
};
use serde_json::{json, Value};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::net::TcpListener;
use axum::http::{StatusCode, Method};
use tower_http::cors::{CorsLayer, Any};

use crate::config;

/// HTTP 服务器状态
pub struct HttpServerState {
    pub data_dir: PathBuf,
}

/// 启动 HTTP 服务器（替代 Python FastAPI）
pub async fn start_server(port: u16, data_dir: PathBuf) -> Result<(), Box<dyn std::error::Error>> {
    let state = Arc::new(HttpServerState {
        data_dir,
    });

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .route("/api/health", get(health_handler))
        .route("/api/config", any(config_handler))
        .route("/api/cases", get(cases_handler))
        .route("/api/data-dir", get(data_dir_handler))
        .layer(cors)
        .with_state(state);

    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let listener = TcpListener::bind(addr).await?;

    eprintln!("[HTTP] 服务器启动在 http://127.0.0.1:{}", port);

    axum::serve(listener, app).await?;

    Ok(())
}

/// GET /api/health
async fn health_handler() -> Json<Value> {
    Json(json!({
        "status": "ok",
        "message": "Rust HTTP server is running"
    }))
}

/// GET /api/config — GET 返回配置状态（隐藏 token 明文）
/// PUT /api/config — 保存配置 JSON body
async fn config_handler(
    State(state): State<Arc<HttpServerState>>,
    method: Method,
    body: String,
) -> impl IntoResponse {
    let config_path = config::get_config_path(&state.data_dir);

    match method {
        Method::GET => {
            match config::load_config(&config_path).await {
                Ok(cfg) => {
                    // 返回配置状态（token 只返回是否配置，不返回明文）
                    let status = json!({
                        "mineru_token": !cfg.get("mineru_token").and_then(|v| v.as_str()).unwrap_or("").is_empty(),
                        "mineru_mode": cfg.get("mineru_mode").and_then(|v| v.as_str()).unwrap_or("cloud"),
                        "mineru_local_url": cfg.get("mineru_local_url").and_then(|v| v.as_str()).unwrap_or(""),
                        "pdf_engine": cfg.get("pdf_engine").and_then(|v| v.as_str()).unwrap_or("mineru"),
                        "llm_model": cfg.get("llm_model").and_then(|v| v.as_str()).unwrap_or(""),
                        "llm_base_url": cfg.get("llm_base_url").and_then(|v| v.as_str()).unwrap_or(""),
                        "llm_api_key": !cfg.get("llm_api_key").and_then(|v| v.as_str()).unwrap_or("").is_empty(),
                        "evidence_concurrency": cfg.get("evidence_concurrency").and_then(|v| v.as_u64()).unwrap_or(3),
                        "model_context_limit": cfg.get("model_context_limit").and_then(|v| v.as_u64()),
                        "yuandian_token": !cfg.get("yuandian_token").and_then(|v| v.as_str()).unwrap_or("").is_empty(),
                    });
                    (StatusCode::OK, Json(status)).into_response()
                }
                Err(e) => (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": e.to_string()})),
                ).into_response(),
            }
        }
        Method::PUT => {
            let payload: Value = match serde_json::from_str(&body) {
                Ok(v) => v,
                Err(e) => return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"error": format!("Invalid JSON: {}", e)})),
                ).into_response(),
            };

            if let Err(e) = config::save_config(&config_path, &payload).await {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": e.to_string()})),
                ).into_response();
            }

            match config::load_config(&config_path).await {
                Ok(cfg) => (StatusCode::OK, Json(cfg)).into_response(),
                Err(e) => (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": e.to_string()})),
                ).into_response(),
            }
        }
        _ => (
            StatusCode::METHOD_NOT_ALLOWED,
            Json(json!({"error": "Only GET and PUT are supported"})),
        ).into_response(),
    }
}

/// GET /api/cases — 列出案件（扫描文件系统）
async fn cases_handler(
    State(state): State<Arc<HttpServerState>>,
) -> impl IntoResponse {
    let cases_dir = state.data_dir.join("cases");
    let mut cases = Vec::new();

    if let Ok(mut dir) = tokio::fs::read_dir(&cases_dir).await {
        while let Ok(Some(entry)) = dir.next_entry().await {
            let name = entry.file_name().to_string_lossy().to_string();
            if entry.path().is_dir() {
                cases.push(json!({
                    "id": name,
                    "name": name,
                    "path": entry.path().to_string_lossy(),
                }));
            }
        }
    }

    (StatusCode::OK, Json(json!({ "cases": cases, "total": cases.len() }))).into_response()
}

/// GET /api/data-dir — 返回数据目录路径
async fn data_dir_handler(
    State(state): State<Arc<HttpServerState>>,
) -> impl IntoResponse {
    let data_dir = state.data_dir.to_string_lossy().to_string();
    (StatusCode::OK, Json(json!({
        "data_dir": data_dir,
        "exists": state.data_dir.exists(),
    }))).into_response()
}
