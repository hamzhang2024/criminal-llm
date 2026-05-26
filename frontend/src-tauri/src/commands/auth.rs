use crate::state::{call_auth, AuthResponse, BackendClient};
use tauri::State;

/// 认证服务器地址
const AUTH_SERVER_URL: &str = "http://118.196.83.43:8000";

/// 认证：登录
#[tauri::command]
pub async fn auth_login(
    email: String,
    password: String,
    client: State<'_, BackendClient>,
) -> Result<AuthResponse, String> {
    call_auth(
        &client.0,
        &format!("{}/api/login", AUTH_SERVER_URL),
        &serde_json::json!({ "email": email, "password": password }),
    )
    .await
}

/// 认证：注册
#[tauri::command]
pub async fn auth_register(
    email: String,
    password: String,
    client: State<'_, BackendClient>,
) -> Result<AuthResponse, String> {
    call_auth(
        &client.0,
        &format!("{}/api/register", AUTH_SERVER_URL),
        &serde_json::json!({ "email": email, "password": password }),
    )
    .await
}

/// 认证：重置密码
#[tauri::command]
pub async fn auth_reset_password(
    email: String,
    old_password: String,
    new_password: String,
    client: State<'_, BackendClient>,
) -> Result<AuthResponse, String> {
    call_auth(
        &client.0,
        &format!("{}/api/reset-password", AUTH_SERVER_URL),
        &serde_json::json!({
            "email": email,
            "old_password": old_password,
            "new_password": new_password,
        }),
    )
    .await
}

/// 认证：发送重置验证码
#[tauri::command]
pub async fn auth_send_reset_code(
    email: String,
    client: State<'_, BackendClient>,
) -> Result<AuthResponse, String> {
    call_auth(
        &client.0,
        &format!("{}/api/send-reset-code", AUTH_SERVER_URL),
        &serde_json::json!({ "email": email }),
    )
    .await
}

/// 认证：通过验证码重置密码
#[tauri::command]
pub async fn auth_reset_with_code(
    email: String,
    code: String,
    new_password: String,
    client: State<'_, BackendClient>,
) -> Result<AuthResponse, String> {
    call_auth(
        &client.0,
        &format!("{}/api/reset-with-code", AUTH_SERVER_URL),
        &serde_json::json!({
            "email": email,
            "code": code,
            "new_password": new_password,
        }),
    )
    .await
}

/// 认证：验证 token
#[tauri::command]
pub async fn auth_verify(
    token: String,
    client: State<'_, BackendClient>,
) -> Result<AuthResponse, String> {
    call_auth(
        &client.0,
        &format!("{}/api/verify", AUTH_SERVER_URL),
        &serde_json::json!({ "token": token }),
    )
    .await
}
