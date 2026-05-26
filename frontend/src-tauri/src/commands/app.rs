use crate::state::{BackendClient, UpdateInfo};
use tauri::{AppHandle, State};

/// 获取当前版本号
#[tauri::command]
pub fn get_app_version(app: AppHandle) -> String {
    app.package_info().version.to_string()
}

/// 检查更新（从认证服务器获取最新版本）
#[tauri::command]
pub async fn check_update(
    app: AppHandle,
    client: State<'_, BackendClient>,
) -> Result<UpdateInfo, String> {
    let current = app.package_info().version.to_string();
    const AUTH_SERVER_URL: &str = "http://118.196.83.43:8000";

    let resp = client
        .0
        .get(format!("{}/api/latest-version", AUTH_SERVER_URL))
        .send()
        .await
        .map_err(|e| format!("检查更新失败: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;

    let latest = body
        .get("version")
        .and_then(|v| v.as_str())
        .unwrap_or("0.0.0");
    let notes = body
        .get("release_notes")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let has_update = latest > current.as_str();
    let download_url = body
        .get("download_url")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    Ok(UpdateInfo {
        has_update,
        current_version: current,
        latest_version: latest.to_string(),
        download_url: download_url.to_string(),
        release_notes: notes.to_string(),
    })
}
