use crate::state::{kill_backend_process, stop_caffeinate, BackendPort, CaffeinateProcess};
use tauri::{AppHandle, Manager};

/// 打开文件（跨平台）
#[tauri::command]
pub fn open_file(file_path: String) -> Result<bool, String> {
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&file_path)
            .output()
            .map_err(|e| format!("无法打开文件: {}", e))?;
    }
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .arg("/select,")
            .arg(&file_path)
            .output()
            .map_err(|e| format!("无法打开文件: {}", e))?;
    }
    Ok(true)
}

/// 打开 URL（跨平台）
#[tauri::command]
pub fn open_url(url: String) -> Result<bool, String> {
    #[cfg(target_os = "macos")]
    let cmd = "open";
    #[cfg(target_os = "windows")]
    let cmd = "explorer";
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let cmd = "xdg-open";

    std::process::Command::new(cmd)
        .arg(&url)
        .output()
        .map_err(|e| format!("无法打开链接: {}", e))?;
    Ok(true)
}

/// 获取后端实际端口
#[tauri::command]
pub fn get_backend_port(app: tauri::AppHandle) -> Result<u16, String> {
    let port_state = app.state::<BackendPort>();
    let port = *port_state.0.lock().map_err(|e| format!("锁错误: {}", e))?;
    Ok(port)
}

/// 强制退出应用（确认关闭时使用，绕过 prevent_close）
#[tauri::command]
pub fn force_quit(app: AppHandle) {
    kill_backend_process(&app);

    let caff_state = app.state::<CaffeinateProcess>();
    if let Some(caff_pid) = *caff_state.0.lock().unwrap() {
        stop_caffeinate(caff_pid);
    }

    app.exit(0);
}
