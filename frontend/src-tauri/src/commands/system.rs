use tauri::AppHandle;

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

/// 强制退出应用
#[tauri::command]
pub fn force_quit(app: AppHandle) {
    app.exit(0);
}
