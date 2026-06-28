use tauri::State;
use serde_json::{json, Value};
use std::path::Path;
use crate::db::AppDb;
use crate::worker;

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
                                    // 递归计数文件（匹配 Python rglob 行为）
                                    let file_count = count_files_recursive(&sub_path, &["pdf", "md"]);

                                    if let Value::Object(ref mut obj) = meta {
                                        obj.insert("file_count".to_string(), json!(file_count));

                                        // 确定状态
                                        let md = sub_path.join("md");
                                        let processed = sub_path.join("processed");
                                        let original = sub_path.join("original");

                                        let status = if count_files_recursive(&md, &["md"]) > 0 {
                                            "md_ready"
                                        } else if count_files_recursive(&processed, &["pdf"]) > 0 {
                                            "processed"
                                        } else if count_files_recursive(&original, &["pdf"]) > 0 {
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

// 递归计数文件（匹配 Python rglob(*) 行为）
fn count_files_recursive(dir: &Path, extensions: &[&str]) -> u64 {
    let mut count = 0;
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                count += count_files_recursive(&path, extensions);
            } else {
                let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
                if extensions.contains(&ext) {
                    count += 1;
                }
            }
        }
    }
    count
}

// 以下是旧的 HTTP 代理命令，已重命名为纯 Rust 实现
// 保留原始符号名作为别名，等前端迁移完成后删除

#[tauri::command]
pub async fn health_check() -> Result<Value, String> {
    health().await
}

/// 获取案件详情（文件系统）
#[tauri::command]
pub async fn get_case_files(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir();
    let case_base_dir = data_dir.join("cases").join(&case_id);
    let mut files: Vec<Value> = Vec::new();

    if case_base_dir.exists() {
        if let Ok(entries) = std::fs::read_dir(&case_base_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() {
                    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                    files.push(json!({"name": name, "path": path.to_string_lossy()}));
                }
            }
        }
    }
    Ok(json!({"case_id": case_id, "files": files, "total": files.len()}))
}

/// 获取按步骤的文件列表（匹配 case.json 中的 step 字段）
#[tauri::command]
pub async fn get_step_files(case_id: String, step: u32, db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir();
    let cases_dir = data_dir.join("cases").join(&case_id);
    let mut step_files: Vec<Value> = Vec::new();

    if cases_dir.exists() {
        scan_case_dirs(&cases_dir, |meta, sub_path| {
            if meta.get("step").and_then(|v| v.as_u64()).unwrap_or(0) == step as u64 {
                let files: Vec<String> = std::fs::read_dir(sub_path)
                    .map(|entries| entries.flatten()
                        .filter_map(|e| e.path().extension().and_then(|ext| ext.to_str().map(|s| s.to_string())))
                        .collect())
                    .unwrap_or_default();
                step_files.push(json!({"path": sub_path.to_string_lossy(), "files": files}));
            }
        });
    }
    Ok(json!({"case_id": case_id, "step": step, "files": step_files}))
}

/// 辅助：扫描案件子目录并调用回调
fn scan_case_dirs<F>(cases_dir: &std::path::Path, mut callback: F)
where F: FnMut(&Value, &std::path::Path)
{
    if let Ok(sub_dirs) = std::fs::read_dir(cases_dir) {
        for sub_entry in sub_dirs.flatten() {
            let sub_path = sub_entry.path();
            if !sub_path.is_dir() { continue; }
            let meta_file = sub_path.join("case.json");
            if meta_file.exists() {
                if let Ok(content) = std::fs::read_to_string(&meta_file) {
                    if let Ok(meta) = serde_json::from_str::<Value>(&content) {
                        callback(&meta, &sub_path);
                    }
                }
            }
        }
    }
}

#[tauri::command]
pub async fn batch_process(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    call_python("convert_to_md", json!({"case_id": case_id}), db).await
}

#[tauri::command]
pub async fn get_split_suggestion(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    call_python("split_suggestion", json!({"case_id": case_id}), db).await
}

#[tauri::command]
pub async fn execute_analysis(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    call_python("analyze_case", json!({"case_id": case_id}), db).await
}

#[tauri::command]
pub async fn chat_analysis(case_id: String, query: String, db: State<'_, AppDb>) -> Result<Value, String> {
    call_python("chat_analysis", json!({"case_id": case_id, "query": query}), db).await
}

#[tauri::command]
pub async fn convert_to_md(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    call_python("convert_to_md", json!({"case_id": case_id}), db).await
}

#[tauri::command]
pub async fn delete_case(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir();
    let cases_root = data_dir.join("cases");
    let target = cases_root.join(&case_id);

    // 防路径穿越：确保目标在 cases/ 子树内
    let canonical = target.canonicalize()
        .map_err(|e| format!("案件路径无效: {}", e))?;
    let root_canonical = cases_root.canonicalize().unwrap_or(cases_root.clone());
    if !canonical.starts_with(&root_canonical) {
        return Err("非法案件路径".to_string());
    }

    if canonical.exists() {
        std::fs::remove_dir_all(&canonical)
            .map_err(|e| format!("删除案件失败: {}", e))?;
        Ok(json!({"deleted": true, "case_id": case_id}))
    } else {
        Err(format!("案件 '{}' 不存在", case_id))
    }
}

/// 辅助：调用 Python worker
async fn call_python(method: &str, params: Value, db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir().to_string_lossy().to_string();
    let python = if cfg!(target_os = "windows") { "python.exe" } else { "python3" };

    let worker_script = {
        if let Ok(exe_path) = std::env::current_exe() {
            if let Some(exe_dir) = exe_path.parent() {
                let w = exe_dir.join("worker.py");
                if w.exists() { w.to_string_lossy().to_string() }
                else {
                    let iw = exe_dir.join("_internal").join("worker.py");
                    if iw.exists() { iw.to_string_lossy().to_string() }
                    else { "../backend/worker.py".to_string() }
                }
            } else { "../backend/worker.py".to_string() }
        } else { "../backend/worker.py".to_string() }
    };

    worker::call_worker(python, &worker_script, &data_dir, method, params)
        .map_err(|e| format!("Python worker 调用失败: {}", e))
}

/// 获取指定案件的所有 MD 文件列表
#[tauri::command]
pub async fn get_md_files(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir();
    let cases_dir = data_dir.join("cases").join(&case_id);
    let mut files: Vec<Value> = Vec::new();

    if let Ok(sub_dirs) = std::fs::read_dir(&cases_dir) {
        for sub_entry in sub_dirs.flatten() {
            let sub_path = sub_entry.path();
            if !sub_path.is_dir() { continue; }
            let md_dir = sub_path.join("md");
            if md_dir.exists() {
                if let Ok(entries) = std::fs::read_dir(&md_dir) {
                    for entry in entries.flatten() {
                        let path = entry.path();
                        if path.extension().and_then(|e| e.to_str()).unwrap_or("") == "md" {
                            files.push(json!({
                                "name": path.file_name().and_then(|n| n.to_str()).unwrap_or(""),
                                "path": path.to_string_lossy(),
                                "size": std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0),
                            }));
                        }
                    }
                }
            }
        }
    }
    Ok(json!({"case_id": case_id, "md_files": files, "total": files.len()}))
}

/// 获取指定案件的所有 PDF 文件列表
#[tauri::command]
pub async fn get_pdf_files(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir();
    let cases_dir = data_dir.join("cases").join(&case_id);
    let mut files: Vec<Value> = Vec::new();

    if let Ok(sub_dirs) = std::fs::read_dir(&cases_dir) {
        for sub_entry in sub_dirs.flatten() {
            let sub_path = sub_entry.path();
            if !sub_path.is_dir() { continue; }
            for subdir in &["original", "processed"] {
                let pdf_dir = sub_path.join(subdir);
                if pdf_dir.exists() {
                    if let Ok(entries) = std::fs::read_dir(&pdf_dir) {
                        for entry in entries.flatten() {
                            let path = entry.path();
                            if path.extension().and_then(|e| e.to_str()).unwrap_or("") == "pdf" {
                                files.push(json!({
                                    "name": path.file_name().and_then(|n| n.to_str()).unwrap_or(""),
                                    "path": path.to_string_lossy(),
                                    "type": *subdir,
                                    "size": std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0),
                                }));
                            }
                        }
                    }
                }
            }
        }
    }
    Ok(json!({"case_id": case_id, "pdf_files": files, "total": files.len()}))
}
