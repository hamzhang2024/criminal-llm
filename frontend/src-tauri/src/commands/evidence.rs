use serde_json::{json, Value};
use tauri::State;
use crate::db::AppDb;

/// 证据提取（需要 Python worker，Phase 2 实现 JSON-RPC 桥接）
#[tauri::command]
pub async fn extract_evidence(case_id: String) -> Result<Value, String> {
    Err(format!("【待实现】证据提取需要 Python worker 处理 case '{}'。", case_id))
}

/// 获取证据提取状态
#[tauri::command]
pub async fn get_extract_status(_case_id: String) -> Result<Value, String> {
    Ok(json!({"status": "pending", "message": "Phase 2 migration"}))
}

/// 停止证据提取
#[tauri::command]
pub async fn stop_extract(_case_id: String) -> Result<Value, String> {
    Ok(json!({"stopped": true}))
}

/// 获取证据索引（纯文件系统读取，无需 Python）
#[tauri::command]
pub async fn get_evidence_index(case_id: String, db: State<'_, AppDb>) -> Result<Value, String> {
    let data_dir = db.data_dir();
    let cases_dir = data_dir.join("cases").join(&case_id);

    // 扫描所有 case_xxx/案件_名称/ 子目录
    let mut evidence_files: Vec<Value> = Vec::new();

    if cases_dir.exists() {
        if let Ok(sub_dirs) = std::fs::read_dir(&cases_dir) {
            for sub_entry in sub_dirs.flatten() {
                let sub_path = sub_entry.path();
                if !sub_path.is_dir() { continue; }

                // 检查是否有 evidence/index.json
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

                // 也列出 evidence/ 目录中的 MD 文件
                if evidence_dir.exists() {
                    if let Ok(entries) = std::fs::read_dir(&evidence_dir) {
                        for entry in entries.flatten() {
                            let path = entry.path();
                            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
                            if ext == "md" {
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
