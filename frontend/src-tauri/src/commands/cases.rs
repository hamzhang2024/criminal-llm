use tauri::State;
use serde_json::{json, Value};
use std::path::Path;
use crate::db::AppDb;

/// GET /health — 直接返回（不走 HTTP）
#[tauri::command]
pub async fn health() -> Result<Value, String> {
    Ok(json!({
        "status": "ok",
        "message": "Tauri backend is running"
    }))
}

/// GET /config — 读取配置状态（隐藏敏感 token）
#[tauri::command]
pub async fn get_config(db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir();
    let config_path = data_dir.join("criminal-llm-config.json");

    let config = if config_path.exists() {
        let content = std::fs::read_to_string(&config_path)
            .map_err(|e| format!("读取配置失败: {}", e))?;
        serde_json::from_str::<Value>(&content)
            .map_err(|e| format!("配置格式错误: {}", e))?
    } else {
        json!({})
    };

    // 返回配置状态（token 只返回布尔值）
    let status = json!({
        "mineru_token": !config.get("mineru_token").and_then(|v| v.as_str()).unwrap_or("").is_empty(),
        "paddleocr_token": !config.get("paddleocr_token").and_then(|v| v.as_str()).unwrap_or("").is_empty(),
        "mineru_mode": config.get("mineru_mode").and_then(|v| v.as_str()).unwrap_or("cloud"),
        "mineru_local_url": config.get("mineru_local_url").and_then(|v| v.as_str()).unwrap_or(""),
        "pdf_engine": config.get("pdf_engine").and_then(|v| v.as_str()).unwrap_or("mineru"),
        "llm_model": config.get("llm_model").and_then(|v| v.as_str()).unwrap_or(""),
        "llm_base_url": config.get("llm_base_url").and_then(|v| v.as_str()).unwrap_or(""),
        "llm_api_key": !config.get("llm_api_key").and_then(|v| v.as_str()).unwrap_or("").is_empty(),
        "evidence_concurrency": config.get("evidence_concurrency").and_then(|v| v.as_u64()).unwrap_or(3),
        "model_context_limit": config.get("model_context_limit").and_then(|v| v.as_u64()),
        "yuandian_token": !config.get("yuandian_token").and_then(|v| v.as_str()).unwrap_or("").is_empty(),
    });

    Ok(status)
}

/// PUT /config — 保存配置（合并）
#[tauri::command]
pub async fn set_config(payload: Value, db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir();
    let config_path = data_dir.join("criminal-llm-config.json");

    // 确保目录存在
    if let Some(parent) = config_path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("创建目录失败: {}", e))?;
    }

    // 读取已有配置
    let existing = if config_path.exists() {
        let content = std::fs::read_to_string(&config_path)
            .unwrap_or_default();
        serde_json::from_str::<Value>(&content)
            .unwrap_or(json!({}))
    } else {
        json!({})
    };

    // 合并
    let mut merged = existing;
    if let (Value::Object(ref mut merged_obj), Value::Object(payload_obj)) = (&mut merged, &payload) {
        for (k, v) in payload_obj {
            merged_obj.insert(k.clone(), v.clone());
        }
    }

    // 保存
    let json_str = serde_json::to_string_pretty(&merged)
        .map_err(|e| format!("JSON 序列化失败: {}", e))?;
    std::fs::write(&config_path, json_str)
        .map_err(|e| format!("保存配置失败: {}", e))?;

    Ok(merged)
}

/// GET /data-dir — 返回数据目录
#[tauri::command]
pub async fn get_data_dir(db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir();
    Ok(json!({
        "data_dir": data_dir.to_string_lossy(),
        "exists": data_dir.exists(),
    }))
}

/// GET /cases — 列出所有案件
#[tauri::command]
pub async fn list_cases(db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir();
    let cases_dir = data_dir.join("cases");
    let mut cases: Vec<Value> = Vec::new();

    if cases_dir.exists() {
        if let Ok(case_dirs) = std::fs::read_dir(&cases_dir) {
            for case_entry in case_dirs.flatten() {
                let case_dir = case_entry.path();
                if !case_dir.is_dir() { continue; }

                if let Ok(sub_dirs) = std::fs::read_dir(&case_dir) {
                    for sub_entry in sub_dirs.flatten() {
                        let sub_path = sub_entry.path();
                        if !sub_path.is_dir() { continue; }

                        let metadata_file = sub_path.join("case.json");
                        if metadata_file.exists() {
                            if let Ok(content) = std::fs::read_to_string(&metadata_file) {
                                if let Ok(mut meta) = serde_json::from_str::<Value>(&content) {
                                    // 计数文件
                                    let mut file_count = 0u64;
                                    if let Ok(entries) = std::fs::read_dir(&sub_path) {
                                        for entry in entries.flatten() {
                                            let path = entry.path();
                                            let ext = path.extension()
                                                .and_then(|e| e.to_str())
                                                .unwrap_or("");
                                            if ext == "pdf" || ext == "md" {
                                                file_count += 1;
                                            }
                                        }
                                    }

                                    if let Value::Object(ref mut obj) = meta {
                                        obj.insert("file_count".to_string(), json!(file_count));

                                        // 确定状态
                                        let md = sub_path.join("md");
                                        let processed = sub_path.join("processed");
                                        let original = sub_path.join("original");

                                        let status = if has_entries(&md) {
                                            "md_ready"
                                        } else if has_entries(&processed) {
                                            "processed"
                                        } else if has_entries(&original) {
                                            "uploaded"
                                        } else {
                                            "new"
                                        };
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

    Ok(json!({
        "cases": cases,
        "total": cases.len()
    }))
}

/// 检查目录是否有条目
fn has_entries(dir: &Path) -> bool {
    dir.exists() && std::fs::read_dir(dir).map_or(false, |mut d| d.next().is_some())
}

// 以下是旧的 HTTP 代理命令，已重命名为纯 Rust 实现
// 保留原始符号名作为别名，等前端迁移完成后删除

#[tauri::command]
pub async fn health_check() -> Result<Value, String> {
    health().await
}

#[tauri::command]
pub async fn batch_process() -> Result<Value, String> {
    Err("待实现".to_string())
}

#[tauri::command]
pub async fn get_split_suggestion() -> Result<Value, String> {
    Err("待实现".to_string())
}

#[tauri::command]
pub async fn execute_analysis() -> Result<Value, String> {
    Err("待实现".to_string())
}

#[tauri::command]
pub async fn chat_analysis() -> Result<Value, String> {
    Err("待实现".to_string())
}

#[tauri::command]
pub async fn convert_to_md() -> Result<Value, String> {
    Err("待实现".to_string())
}

#[tauri::command]
pub async fn delete_case() -> Result<Value, String> {
    Err("待实现".to_string())
}

#[tauri::command]
pub async fn get_case_files() -> Result<Value, String> {
    Err("待实现".to_string())
}

#[tauri::command]
pub async fn get_step_files() -> Result<Value, String> {
    Err("待实现".to_string())
}
