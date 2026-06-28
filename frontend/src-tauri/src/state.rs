use reqwest::Client;
use serde::Serialize;

/// 共享 HTTP 客户端（用于外部认证服务器）
pub struct BackendClient(pub Client);

/// 认证响应
#[derive(Serialize)]
pub struct AuthResponse {
    pub success: bool,
    pub token: Option<String>,
    pub email: Option<String>,
    pub error: Option<String>,
}

/// 更新信息
#[derive(Serialize)]
pub struct UpdateInfo {
    pub has_update: bool,
    pub current_version: String,
    pub latest_version: String,
    pub download_url: String,
    pub release_notes: String,
}

/// 调用认证服务器，自动处理成功/错误响应
pub async fn call_auth(
    client: &Client,
    endpoint: &str,
    payload: &serde_json::Value,
) -> Result<AuthResponse, String> {
    let resp = client
        .post(endpoint)
        .json(payload)
        .send()
        .await
        .map_err(|e| format!("网络错误: {}", e))?;
    let status = resp.status().as_u16();
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;
    if status >= 300 {
        return Ok(AuthResponse {
            success: false,
            token: None,
            email: None,
            error: body
                .get("detail")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .or_else(|| {
                    body.get("message")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string())
                })
                .or_else(|| Some(format!("服务器错误 ({})", status))),
        });
    }
    Ok(AuthResponse {
        success: body
            .get("success")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        token: body
            .get("token")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string()),
        email: body
            .get("email")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string()),
        error: body
            .get("detail")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string()),
    })
}
