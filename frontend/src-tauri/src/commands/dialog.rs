use tauri::AppHandle;
use tauri_plugin_dialog::{DialogExt, FilePath, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_notification::NotificationExt;

fn path_to_string(path: &FilePath) -> String {
    path.to_string()
}

/// 打开文件选择对话框
#[tauri::command]
pub fn pick_files(
    app: AppHandle,
    title: Option<String>,
    extensions: Option<Vec<String>>,
) -> Result<Vec<String>, String> {
    let mut builder = app.dialog().file();

    if let Some(t) = title {
        builder = builder.set_title(t);
    }
    if let Some(exts) = extensions {
        let filters: Vec<&str> = exts.iter().map(|s| s.as_str()).collect();
        builder = builder.add_filter("支持的文件类型", &filters);
    }

    match builder.blocking_pick_file() {
        Some(path) => Ok(vec![path_to_string(&path)]),
        None => Ok(vec![]), // 用户取消
    }
}

/// 打开目录选择对话框
#[tauri::command]
pub fn pick_folder(app: AppHandle, title: Option<String>) -> Result<Option<String>, String> {
    let mut builder = app.dialog().file();
    if let Some(t) = title {
        builder = builder.set_title(t);
    }

    match builder.blocking_pick_folder() {
        Some(path) => Ok(Some(path_to_string(&path))),
        None => Ok(None), // 用户取消
    }
}

/// 打开文件多选对话框（用于批量导入）
#[tauri::command]
pub fn pick_multiple(app: AppHandle, title: Option<String>) -> Result<Vec<String>, String> {
    let mut builder = app.dialog().file();
    if let Some(t) = title {
        builder = builder.set_title(t);
    }

    match builder.blocking_pick_files() {
        Some(paths) => Ok(paths.iter().map(path_to_string).collect()),
        None => Ok(vec![]), // 用户取消
    }
}

/// 发送系统通知
#[tauri::command]
pub fn send_notification(app: AppHandle, title: String, body: String) {
    app.notification()
        .builder()
        .title(&title)
        .body(&body)
        .show()
        .unwrap_or_else(|e| eprintln!("[通知] 发送失败: {}", e));
}

/// 显示确认对话框
#[tauri::command]
pub fn show_confirm_dialog(
    app: AppHandle,
    title: String,
    message: String,
    ok_label: Option<String>,
    cancel_label: Option<String>,
) -> Result<bool, String> {
    let builder = app
        .dialog()
        .message(&message)
        .title(title)
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            ok_label.unwrap_or("确认".to_string()),
            cancel_label.unwrap_or("取消".to_string()),
        ));

    Ok(builder.blocking_show())
}
