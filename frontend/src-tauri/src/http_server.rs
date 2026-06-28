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

    let app = Router::new()
        .route("/api/health", get(health_handler))
        .route("/api/config", any(config_handler))
        .route("/api/cases", get(cases_handler))
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

/// GET+PUT /api/config — 根据 HTTP method 分发
async fn config_handler(
    State(state): State<Arc<HttpServerState>>,
    method: Method,
    body: String,
) -> impl IntoResponse {
    let config_path = config::get_config_path(&state.data_dir);

    match method {
        Method::GET => {
            match config::load_config(&config_path).await {
                Ok(cfg) => (StatusCode::OK, Json(cfg)).into_response(),
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
