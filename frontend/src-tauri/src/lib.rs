use reqwest::Client;
use std::sync::Mutex;
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

mod commands;
mod db;
mod state;

use db::AppDb;
use state::{start_caffeinate, BackendClient, BackendPid, CaffeinateProcess};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(BackendClient(Client::new()))
        .manage(BackendPid(Mutex::new(None)))
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

                    let mut cmd = std::process::Command::new(&backend_exe);
                    cmd.stdout(std::process::Stdio::null());
                    cmd.stderr(std::process::Stdio::null());

                    let child = cmd.spawn().map_err(|e| format!("启动后端失败: {}", e))?;

                    let pid = child.id();
                    eprintln!("[OK] 后端 PID: {}", pid);

                    let backend_pid = app.state::<BackendPid>();
                    *backend_pid.0.lock().unwrap() = Some(pid);

                    // 等待后端就绪
                    let client = reqwest::blocking::Client::builder()
                        .timeout(std::time::Duration::from_secs(30))
                        .build()
                        .unwrap();

                    for i in 1..=30 {
                        if client
                            .get("http://localhost:8080/api/health")
                            .send()
                            .is_ok()
                        {
                            eprintln!("[OK] 后端已就绪（{}秒）", i);
                            break;
                        }
                        std::thread::sleep(std::time::Duration::from_millis(500));
                        if i == 30 {
                            eprintln!("[WARN] 后端启动超时（30秒）");
                        }
                    }
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
