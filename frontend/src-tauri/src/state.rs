use reqwest::Client;
use serde::Serialize;
use std::sync::Mutex;
use tauri::Manager;

/// 共享 HTTP 客户端
pub struct BackendClient(pub Client);

/// 后端进程 PID
pub struct BackendPid(pub Mutex<Option<u32>>);

/// 后端实际端口（从 backend.port 文件读取）
pub struct BackendPort(pub Mutex<u16>);

/// 电源管理：caffeinate 进程
pub struct CaffeinateProcess(pub Mutex<Option<u32>>);

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

/// 启动 caffeinate 阻止系统休眠（macOS 全局）
pub fn start_caffeinate() -> Option<u32> {
    #[cfg(target_os = "macos")]
    {
        let child = std::process::Command::new("caffeinate")
            .arg("-d")
            .arg("-i")
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn();
        match child {
            Ok(c) => {
                eprintln!("[POWER] 已阻止系统休眠（应用运行期间）");
                Some(c.id())
            }
            Err(e) => {
                eprintln!("[WARN] caffeinate 启动失败: {}", e);
                None
            }
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        None
    }
}

/// 停止 caffeinate
pub fn stop_caffeinate(pid: u32) {
    #[cfg(unix)]
    unsafe {
        libc::kill(pid as i32, libc::SIGTERM);
    }
    #[cfg(windows)]
    {
        let _ = std::process::Command::new("taskkill")
            .args(["/F", "/PID", &pid.to_string()])
            .output();
    }
    eprintln!("[POWER] 已恢复系统休眠");
}

/// 强制终止后端进程（SIGKILL，整个进程组）
pub fn kill_backend_process(app: &tauri::AppHandle) {
    let pid_state = app.state::<BackendPid>();
    let maybe_pid = pid_state.0.lock().unwrap().take();
    if let Some(pid) = maybe_pid {
        eprintln!("[STOP] 关闭后端 PID: {}", pid);
        #[cfg(unix)]
        unsafe {
            libc::kill(-(pid as i32), libc::SIGKILL);
            libc::kill(pid as i32, libc::SIGKILL);
        }
        #[cfg(windows)]
        {
            let _ = std::process::Command::new("taskkill")
                .args(["/F", "/T", "/PID", &pid.to_string()])
                .output();
        }
        eprintln!("[OK] 后端已退出");
    } else {
        // 兜底：BackendPid 为空（如崩溃后状态丢失）但 8080 可能仍被遗留后端占用，
        // 按打包路径特征匹配清理，不误伤其他进程
        #[cfg(unix)]
        {
            let _ = std::process::Command::new("pkill")
                .args(["-f", "resources/backend/criminal-llm"])
                .output();
        }
        #[cfg(windows)]
        {
            let _ = std::process::Command::new("taskkill")
                .args(["/F", "/IM", "criminal-llm.exe"])
                .output();
        }
    }
}
