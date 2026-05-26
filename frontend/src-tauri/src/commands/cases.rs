use crate::state::BackendClient;
use tauri::State;

/// Backend 服务器地址
const BACKEND_URL: &str = "http://localhost:8080";

/// 健康检查
#[tauri::command]
pub async fn health_check(client: State<'_, BackendClient>) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .get(format!("{}/api/health", BACKEND_URL))
        .send()
        .await
        .map_err(|e| format!("后端未启动: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    Ok(body)
}

/// 列出所有案件
#[tauri::command]
pub async fn list_cases(client: State<'_, BackendClient>) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .get(format!("{}/api/cases/list", BACKEND_URL))
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    Ok(body)
}

/// 获取案件的文件列表
#[tauri::command]
pub async fn get_case_files(
    case_id: String,
    client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .get(format!("{}/api/cases/{}/files", BACKEND_URL, case_id))
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    Ok(body)
}

/// 获取步骤文件
#[tauri::command]
pub async fn get_step_files(
    case_id: String,
    step: u32,
    client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .get(format!(
            "{}/api/cases/{}/step-files/{}",
            BACKEND_URL, case_id, step
        ))
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    Ok(body)
}

/// 批量处理（step=1:PDF处理, step=2:拆分, step=3:转MD）
#[tauri::command]
pub async fn batch_process(
    case_id: String,
    step: u32,
    file_names: Vec<String>,
    options: serde_json::Value,
    client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
    let mut body = serde_json::Map::new();
    body.insert("step".to_string(), serde_json::json!(step));
    body.insert("file_names".to_string(), serde_json::json!(file_names));
    if let Some(obj) = options.as_object() {
        for (k, v) in obj {
            body.insert(k.clone(), v.clone());
        }
    }
    let resp = client
        .0
        .post(format!(
            "{}/api/cases/{}/batch-process",
            BACKEND_URL, case_id
        ))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let status = resp.status();
    let result: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    if !status.is_success() {
        let msg = result
            .get("error")
            .and_then(|v| v.as_str())
            .or_else(|| result.get("detail").and_then(|v| v.as_str()))
            .unwrap_or("处理失败");
        return Err(msg.to_string());
    }
    Ok(result)
}

/// 获取拆分建议
#[tauri::command]
pub async fn get_split_suggestion(
    case_id: String,
    file_name: String,
    client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .post(format!(
            "{}/api/cases/{}/split-suggest",
            BACKEND_URL, case_id
        ))
        .json(&serde_json::json!({ "file_name": file_name }))
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    Ok(body)
}

/// 执行案卷分析
#[tauri::command]
pub async fn execute_analysis(
    case_id: String,
    defendant: String,
    client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .post(format!(
            "{}/api/cases/{}/batch-process",
            BACKEND_URL, case_id
        ))
        .json(&serde_json::json!({
            "step": 4,
            "defendant": defendant,
        }))
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    Ok(body)
}

/// 对话分析
#[tauri::command]
pub async fn chat_analysis(
    case_id: String,
    message: String,
    history: Vec<serde_json::Value>,
    client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .post(format!("{}/api/analyze-case/chat/{}", BACKEND_URL, case_id))
        .json(&serde_json::json!({
            "message": message,
            "history": history,
            "use_ai": true,
        }))
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    Ok(body)
}

/// 转换 PDF 为 MD
#[tauri::command]
pub async fn convert_to_md(
    case_id: String,
    file_name: String,
    client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .post(format!(
            "{}/api/cases/{}/convert-to-md",
            BACKEND_URL, case_id
        ))
        .json(&serde_json::json!({ "file_name": file_name }))
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    Ok(body)
}

/// 删除案件
#[tauri::command]
pub async fn delete_case(
    case_id: String,
    client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
    let resp = client
        .0
        .delete(format!("{}/api/cases/{}", BACKEND_URL, case_id))
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    Ok(body)
}
