use crate::state::BackendClient;
use tauri::State;

/// Backend 服务器地址
const BACKEND_URL: &str = "http://127.0.0.1:8080";

/// 证据提取
#[tauri::command]
pub async fn extract_evidence(
    case_id: String,
    client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .post(format!(
            "{}/api/cases/{}/extract-evidence",
            BACKEND_URL, case_id
        ))
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let status = resp.status();
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    if !status.is_success() {
        let msg = body
            .get("detail")
            .and_then(|v| v.as_str())
            .or_else(|| body.get("error").and_then(|v| v.as_str()))
            .unwrap_or("提取失败");
        return Err(msg.to_string());
    }
    Ok(body)
}

/// 获取证据提取状态
#[tauri::command]
pub async fn get_extract_status(
    case_id: String,
    client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .get(format!(
            "{}/api/cases/{}/extract-status",
            BACKEND_URL, case_id
        ))
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    Ok(body)
}

/// 停止证据提取
#[tauri::command]
pub async fn stop_extract(
    case_id: String,
    client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .post(format!(
            "{}/api/cases/{}/stop-extract",
            BACKEND_URL, case_id
        ))
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    Ok(body)
}

/// 获取证据索引
#[tauri::command]
pub async fn get_evidence_index(
    case_id: String,
    client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .get(format!(
            "{}/api/cases/{}/evidence-index",
            BACKEND_URL, case_id
        ))
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    Ok(body)
}
