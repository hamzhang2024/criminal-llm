use serde_json::{json, Value};
use tauri::State;
use crate::db::AppDb;
use crate::worker;

/// 证据提取（Python worker JSON-RPC）
#[tauri::command]
pub async fn extract_evidence(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir().to_string_lossy().to_string();
    let python = if cfg!(target_os = "windows") { "python.exe" } else { "python3" };

    worker::call_worker(
        python,
        &worker::get_worker_script(),
        &data_dir,
        "extract_evidence",
        json!({"case_id": case_id}),
    ).map_err(|e| format!("证据提取失败: {}", e))
}

/// 获取证据提取状态（ping worker）
#[tauri::command]
pub async fn get_extract_status(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir().to_string_lossy().to_string();
    let python = if cfg!(target_os = "windows") { "python.exe" } else { "python3" };

    match worker::call_worker(python, &worker::get_worker_script(), &data_dir, "ping", json!({})) {
        Ok(_) => Ok(json!({"case_id": case_id, "status": "running"})),
        Err(e) => Ok(json!({"case_id": case_id, "status": "pending", "error": e})),
    }
}

/// 停止证据提取（向 worker 发送 stop 信号）
#[tauri::command]
pub async fn stop_extract(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir().to_string_lossy().to_string();
    let python = if cfg!(target_os = "windows") { "python.exe" } else { "python3" };

    match worker::call_worker(
        python,
        &worker::get_worker_script(),
        &data_dir,
        "stop_extract",
        json!({"case_id": case_id}),
    ) {
        Ok(_) => Ok(json!({"stopped": true, "case_id": case_id})),
        Err(e) => Ok(json!({"stopped": false, "case_id": case_id, "error": e})),
    }
}

/// 获取证据索引（纯文件系统读取）
#[tauri::command]
pub async fn get_evidence_index(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir();
    let cases_dir = data_dir.join("cases").join(&case_id);
    let mut evidence_files: Vec<Value> = Vec::new();

    if cases_dir.exists() {
        if let Ok(sub_dirs) = std::fs::read_dir(&cases_dir) {
            for sub_entry in sub_dirs.flatten() {
                let sub_path = sub_entry.path();
                if !sub_path.is_dir() { continue; }
                let evidence_dir = sub_path.join("evidence");
                let index_file = evidence_dir.join("index.json");
                if index_file.exists() {
                    if let Ok(content) = std::fs::read_to_string(&index_file) {
                        if let Ok(index_data) = serde_json::from_str::<Value>(&content) {
                            if let Some(entries) = index_data.as_array() {
                                for entry in entries {
                                    evidence_files.push(entry.clone());
                                }
                            }
                        }
                    }
                }
                if evidence_dir.exists() {
                    if let Ok(entries) = std::fs::read_dir(&evidence_dir) {
                        for entry in entries.flatten() {
                            let path = entry.path();
                            if path.extension().and_then(|e| e.to_str()).unwrap_or("") == "md" {
                                let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                                evidence_files.push(json!({"file": name, "path": path.to_string_lossy()}));
                            }
                        }
                    }
                }
            }
        }
    }
    Ok(json!({"case_id": case_id, "evidence": evidence_files, "total": evidence_files.len()}))
}
