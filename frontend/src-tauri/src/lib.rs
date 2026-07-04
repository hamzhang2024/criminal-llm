use reqwest::Client;
use std::sync::Mutex;
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

mod commands;
mod db;
mod state;

use db::AppDb;
use state::{start_caffeinate, BackendClient, BackendPid, BackendPort, CaffeinateProcess};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(BackendClient(Client::new()))
        .manage(BackendPid(Mutex::new(None)))
        .manage(BackendPort(Mutex::new(8080)))
        .manage(CaffeinateProcess(Mutex::new(start_caffeinate())))
        .invoke_handler(tauri::generate_handler![
            // App
            commands::get_app_version,
            commands::check_update,
            // Auth
            commands::auth_login,
            commands::auth_register,
            commands::auth_reset_password,
            commands::auth_send_reset_code,
            commands::auth_reset_with_code,
            commands::auth_verify,
            // Cases
            commands::health_check,
            commands::list_cases,
            commands::get_case_files,
            commands::get_step_files,
            commands::batch_process,
            commands::get_split_suggestion,
            commands::execute_analysis,
            commands::chat_analysis,
            commands::convert_to_md,
            commands::delete_case,
            // Evidence
            commands::extract_evidence,
            commands::get_extract_status,
            commands::stop_extract,
            commands::get_evidence_index,
            // System
            commands::open_file,
            commands::open_url,
            commands::force_quit,
            commands::get_backend_port,
            // Dialog & Notification
            commands::pick_files,
            commands::pick_folder,
            commands::pick_multiple,
            commands::send_notification,
            commands::show_confirm_dialog,
            // Workflow (SQLite)
            commands::create_workflow,
            commands::update_workflow_status,
            commands::list_workflows,
            commands::get_workflow,
            commands::add_step,
            commands::update_step,
            commands::get_steps,
            commands::add_file,
            commands::update_file_paths,
            commands::get_files,
            commands::log_operation,
            commands::delete_workflow,
        ])
        .setup(|app| {
            // 初始化本地数据库
            let db_path = app
                .path()
                .app_data_dir()
                .map_err(|e| format!("获取数据目录失败: {}", e))?
                .join("criminal-llm.db");
            eprintln!("[DB] 数据库路径: {:?}", db_path);
            let db = AppDb::new(db_path).map_err(|e| format!("初始化数据库失败: {}", e))?;
            app.manage(db);

            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // 生产模式：启动 Python 后端
            if !cfg!(debug_assertions) {
                let resource_path = app
                    .path()
                    .resolve("resources/backend", tauri::path::BaseDirectory::Resource)
                    .map_err(|e| format!("无法解析资源路径: {}", e))?;

                #[cfg(target_os = "windows")]
                let backend_exe = resource_path.join("criminal-llm.exe");
                #[cfg(not(target_os = "windows"))]
                let backend_exe = resource_path.join("criminal-llm");

                if !backend_exe.exists() {
                    eprintln!("警告: 后端可执行文件不存在: {:?}", backend_exe);
                } else {
                    eprintln!("[START] 启动后端: {:?}", backend_exe);

                    // 设置工作目录为后端所在目录（确保能找到 legal_db 等资源）
                    let backend_dir = backend_exe.parent().unwrap().to_path_buf();

                    let mut cmd = std::process::Command::new(&backend_exe);
                    cmd.current_dir(&backend_dir);
                    // stdio 重定向到文件（诊断：后端 uvicorn 输出与崩溃 traceback 可见）
                    // main.py 的 _stdio_guard 假设"Rust 已把 stderr 重定向到文件"，
                    // 之前设 null 违背该假设 → ensure_stdio 不兜底 → traceback 全丢。这里兑现。
                    let data_dir = app.path().app_data_dir()
                        .map_err(|e| format!("获取数据目录失败: {}", e))?;
                    let stderr_file = std::fs::OpenOptions::new()
                        .create(true).write(true).truncate(true)
                        .open(data_dir.join("backend_stderr.log"))
                        .map_err(|e| format!("打开 backend_stderr.log 失败: {e}"))?;
                    let stdout_file = std::fs::OpenOptions::new()
                        .create(true).write(true).truncate(true)
                        .open(data_dir.join("backend_stdout.log"))
                        .map_err(|e| format!("打开 backend_stdout.log 失败: {e}"))?;
                    cmd.stdout(std::process::Stdio::from(stdout_file));
                    cmd.stderr(std::process::Stdio::from(stderr_file));

                    // Windows 上隐藏窗口
                    #[cfg(target_os = "windows")]
                    {
                        use std::os::windows::process::CommandExt;
                        const CREATE_NO_WINDOW: u32 = 0x08000000;
                        cmd.creation_flags(CREATE_NO_WINDOW);
                    }

                    let child = cmd.spawn().map_err(|e| format!("启动后端失败: {}", e))?;

                    let pid = child.id();
                    eprintln!("[OK] 后端 PID: {}", pid);

                    let backend_pid = app.state::<BackendPid>();
                    *backend_pid.0.lock().unwrap() = Some(pid);

                    // 不再同步等待后端就绪：PyInstaller 冷启动可能 ~2 分钟，
                    // 同步阻塞会导致窗口迟迟不出现。改为 setup 立即返回创建 webview，
                    // 由前端 waitForBackend 探测 8080 就绪（超时 180s，余量充足）。
                    // 后端进程独立运行，崩溃/就绪状态均记入 backend_stderr.log。
                }
            }

            // 拦截外部链接，用系统浏览器打开
            let url = if cfg!(debug_assertions) {
                WebviewUrl::External("http://localhost:5173".parse().unwrap())
            } else {
                WebviewUrl::App("index.html".into())
            };

            let _webview = WebviewWindowBuilder::new(app, "main", url)
                .title("刑事案卷分析系统")
                .inner_size(1280.0, 800.0)
                .min_inner_size(800.0, 600.0)
                .center()
                .resizable(true)
                .on_navigation(move |url| {
                    let scheme = url.scheme();
                    let is_local = scheme == "tauri"
                        || scheme == "file"
                        || scheme == "asset"
                        || scheme == "http"
                        || scheme == "https"
                        || url.host_str() == Some("localhost")
                        || url.host_str() == Some("127.0.0.1");
                    if is_local {
                        return true;
                    }
                    #[cfg(target_os = "macos")]
                    {
                        if let Err(e) = std::process::Command::new("open")
                            .arg(url.as_str())
                            .output()
                        {
                            eprintln!("打开外部链接失败: {}", e);
                        }
                    }
                    #[cfg(target_os = "windows")]
                    {
                        if let Err(e) = std::process::Command::new("explorer")
                            .arg(url.as_str())
                            .output()
                        {
                            eprintln!("打开外部链接失败: {}", e);
                        }
                    }
                    false
                })
                .build()?;

            Ok(())
        })
        .on_window_event(|window, event| match event {
            tauri::WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close();
                let _ = window.emit("close-requested", ());
            }
            // 拖拽文件到窗口
            tauri::WindowEvent::DragDrop(tauri::DragDropEvent::Drop { paths, position }) => {
                let file_paths: Vec<String> = paths
                    .iter()
                    .filter(|p| p.extension().is_some_and(|ext| ext == "pdf"))
                    .map(|p| p.to_string_lossy().to_string())
                    .collect();
                if !file_paths.is_empty() {
                    eprintln!("[拖拽] 收到 {} 个 PDF 文件", file_paths.len());
                    let _ = window.emit(
                        "files-dropped",
                        serde_json::json!({
                            "paths": file_paths,
                            "x": position.x,
                            "y": position.y,
                        }),
                    );
                }
            }
            _ => {}
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
