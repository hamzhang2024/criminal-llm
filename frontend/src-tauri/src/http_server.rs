use axum::{
    extract::{State, Json, Multipart},
    routing::{get, any, post},
    Router,
    response::IntoResponse,
};
use serde_json::{json, Value};
use std::net::SocketAddr;
use std::path::{PathBuf, Path};
use std::sync::Arc;
use tokio::net::TcpListener;
use axum::http::{StatusCode, Method, header};
use tower_http::cors::CorsLayer;
use tokio::io::AsyncWriteExt;

use crate::config;

pub struct HttpServerState {
    pub data_dir: PathBuf,
}

/// 启动 Rust HTTP 服务器
pub async fn start_server(port: u16, data_dir: PathBuf) -> Result<(), Box<dyn std::error::Error>> {
    let state = Arc::new(HttpServerState { data_dir });

    let cors = CorsLayer::new()
        .allow_origin([
            "http://localhost:5173".parse().unwrap(),
            "http://127.0.0.1:5173".parse().unwrap(),
            "http://localhost:8080".parse().unwrap(),
            "http://127.0.0.1:8080".parse().unwrap(),
            "tauri://localhost".parse().unwrap(),
            "https://tauri.localhost".parse().unwrap(),
        ])
        .allow_methods([Method::GET, Method::POST, Method::PUT, Method::DELETE, Method::OPTIONS])
        .allow_headers([header::CONTENT_TYPE, header::AUTHORIZATION]);

    let app = Router::new()
        .route("/api/health", get(health_handler))
        .route("/api/config", any(config_handler))
        .route("/api/cases", get(cases_handler))
        .route("/api/data-dir", get(data_dir_handler))
        .route("/api/upload", post(upload_handler))
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
    Json(json!({"status": "ok", "message": "Rust HTTP server is running"}))
}

/// GET+PUT /api/config
async fn config_handler(
    State(state): State<Arc<HttpServerState>>,
    method: Method,
    body: String,
) -> impl IntoResponse {
    let config_path = config::get_config_path(&state.data_dir);

    match method {
        Method::GET => match config::load_config(&config_path).await {
            Ok(cfg) => {
                let status = json!({
                    "mineru_token": !cfg.get("mineru_token").and_then(|v| v.as_str()).unwrap_or("").is_empty(),
                    "paddleocr_token": !cfg.get("paddleocr_token").and_then(|v| v.as_str()).unwrap_or("").is_empty(),
                    "mineru_mode": cfg.get("mineru_mode").and_then(|v| v.as_str()).unwrap_or("cloud"),
                    "mineru_local_url": cfg.get("mineru_local_url").and_then(|v| v.as_str()).unwrap_or(""),
                    "pdf_engine": cfg.get("pdf_engine").and_then(|v| v.as_str()).unwrap_or("mineru"),
                    "llm_model": cfg.get("llm_model").and_then(|v| v.as_str()).unwrap_or(""),
                    "llm_base_url": cfg.get("llm_base_url").and_then(|v| v.as_str()).unwrap_or(""),
                    "llm_api_key": !cfg.get("llm_api_key").and_then(|v| v.as_str()).unwrap_or("").is_empty(),
                    "evidence_concurrency": cfg.get("evidence_concurrency").and_then(|v| v.as_u64()).unwrap_or(3),
                    "model_context_limit": cfg.get("model_context_limit").and_then(|v| v.as_u64()),
                    "model_context_limit_k": "128k(估)",
                    "model_strategy": "小案件模式",
                    "model_warning": "",
                    "model_small_case_limit": 40_000,
                    "model_is_estimated": true,
                    "user_context_limit": cfg.get("model_context_limit").and_then(|v| v.as_u64()),
                    "yuandian_token": !cfg.get("yuandian_token").and_then(|v| v.as_str()).unwrap_or("").is_empty(),
                });
                (StatusCode::OK, Json(status)).into_response()
            }
            Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": e.to_string()}))).into_response(),
        },
        Method::PUT => {
            let payload: Value = match serde_json::from_str(&body) {
                Ok(v) => v,
                Err(e) => return (StatusCode::BAD_REQUEST, Json(json!({"error": format!("Invalid JSON: {}", e)}))).into_response(),
            };
            let existing = config::load_config(&config_path).await.unwrap_or(config::default_config());
            let mut merged = existing;
            if let (Value::Object(ref mut merged_obj), Value::Object(payload_obj)) = (&mut merged, &payload) {
                for (k, v) in payload_obj {
                    merged_obj.insert(k.clone(), v.clone());
                }
            }
            if let Err(e) = config::save_config(&config_path, &merged).await {
                return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": e.to_string()}))).into_response();
            }
            match config::load_config(&config_path).await {
                Ok(cfg) => (StatusCode::OK, Json(cfg)).into_response(),
                Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": e.to_string()}))).into_response(),
            }
        }
        _ => (StatusCode::METHOD_NOT_ALLOWED, Json(json!({"error": "Only GET and PUT"}))).into_response(),
    }
}

/// GET /api/cases
async fn cases_handler(State(state): State<Arc<HttpServerState>>) -> impl IntoResponse {
    let cases_dir = state.data_dir.join("cases");
    let mut cases: Vec<Value> = Vec::new();

    if cases_dir.exists() {
        if let Ok(mut case_dirs) = tokio::fs::read_dir(&cases_dir).await {
            while let Ok(Some(case_entry)) = case_dirs.next_entry().await {
                let case_dir = case_entry.path();
                if !case_dir.is_dir() { continue; }
                if let Ok(mut sub_dirs) = tokio::fs::read_dir(&case_dir).await {
                    while let Ok(Some(sub_entry)) = sub_dirs.next_entry().await {
                        let sub_path = sub_entry.path();
                        if !sub_path.is_dir() { continue; }
                        let metadata_file = sub_path.join("case.json");
                        if metadata_file.exists() {
                            if let Ok(content) = tokio::fs::read_to_string(&metadata_file).await {
                                if let Ok(mut meta) = serde_json::from_str::<Value>(&content) {
                                    let mut file_count = 0u64;
                                    if let Ok(mut entries) = tokio::fs::read_dir(&sub_path).await {
    while let Ok(Some(entry)) = entries.next_entry().await {
        let path = entry.path();
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
        if ext == "pdf" || ext == "md" { file_count += 1; }
    }
}
                                    if let Value::Object(ref mut obj) = meta {
                                        obj.insert("file_count".to_string(), json!(file_count));
                                        let md = sub_path.join("md");
                                        let processed = sub_path.join("processed");
                                        let original = sub_path.join("original");
                                        let status = if has_entries(&md).await { "md_ready" }
                                        else if has_entries(&processed).await { "processed" }
                                        else if has_entries(&original).await { "uploaded" }
                                        else { "new" };
                                        obj.insert("status".to_string(), json!(status));
                                    }
                                    cases.push(meta);
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    (StatusCode::OK, Json(json!({ "cases": cases, "total": cases.len() }))).into_response()
}

/// GET /api/data-dir
async fn data_dir_handler(State(state): State<Arc<HttpServerState>>) -> impl IntoResponse {
    (StatusCode::OK, Json(json!({
        "data_dir": state.data_dir.to_string_lossy(),
        "exists": state.data_dir.exists(),
    }))).into_response()
}

/// POST /api/upload
async fn upload_handler(
    State(state): State<Arc<HttpServerState>>,
    mut multipart: Multipart,
) -> impl IntoResponse {
    let upload_dir = state.data_dir.join("uploads");
    let _ = tokio::fs::create_dir_all(&upload_dir).await;
    let mut saved_files: Vec<Value> = Vec::new();

    while let Ok(Some(mut field)) = multipart.next_field().await {
        let filename = field.file_name().map(sanitize_filename).unwrap_or_else(|| "unknown".to_string());
        let file_path = upload_dir.join(&filename);

        let mut file = match tokio::fs::File::create(&file_path).await {
            Ok(f) => f,
            Err(e) => return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": format!("Failed to create file: {}", e)}))).into_response(),
        };
        let mut total = 0u64;
        while let Ok(Some(chunk)) = field.chunk().await {
            if let Err(e) = file.write_all(&chunk).await {
                return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": format!("Write failed: {}", e)}))).into_response();
            }
            total += chunk.len() as u64;
        }
        saved_files.push(json!({"filename": filename, "size": total}));
    }
    (StatusCode::OK, Json(json!({"success": true, "files": saved_files, "count": saved_files.len()}))).into_response()
}

/// 净化文件名防路径穿越
fn sanitize_filename(name: &str) -> String {
    Path::new(name).file_name().and_then(|n| n.to_str()).unwrap_or("unknown").to_string()
}

/// 检查目录是否有条目（异步）
async fn has_entries(dir: &Path) -> bool {
    if let Ok(mut entries) = tokio::fs::read_dir(dir).await {
        entries.next_entry().await.ok().flatten().is_some()
    } else {
        false
    }
}
