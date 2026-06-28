use serde_json::{json, Value};

/// 证据提取（需要 Python worker，Phase 2 实现 JSON-RPC 桥接）
#[tauri::command]
pub async fn extract_evidence(case_id: String) -> Result<Value, String> {
    Err(format!("【待实现】证据提取需要 Python worker 处理 case '{}'。\nPhase 2 将通过 stdin/stdout JSON-RPC 桥接 Python 计算引擎。", case_id))
}

/// 获取证据提取状态（需要 Python worker）
#[tauri::command]
pub async fn get_extract_status(case_id: String) -> Result<Value, String> {
    Ok(json!({"case_id": case_id, "status": "pending", "message": "Phase 2 迁移中"}))
}

/// 停止证据提取（需要 Python worker）
#[tauri::command]
pub async fn stop_extract(_case_id: String) -> Result<Value, String> {
    Err(format!("【待实现】停止提取需要 Python worker。"))
}

/// 获取证据索引（文件系统读取，无需 Python）
#[tauri::command]
pub async fn get_evidence_index(_case_id: String) -> Result<Value, String> {
    Err(format!("【待实现】证据索引读取。"))
}
