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

                    // 启动前先杀掉可能残留的旧后端进程（占 8080 端口）
                    // 场景：上次应用异常退出（崩溃/任务管理器杀），后端进程没被清理，
                    // 残留进程占用 8080 导致新后端启动失败卡住（Windows 常见）
                    #[cfg(windows)]
                    {
                        // 用 taskkill 杀占 8080 的进程（比 PowerShell 更可靠且不弹窗）
                        let _ = std::process::Command::new("cmd")
                            .args(["/C", "for /f \"tokens=5\" %a in ('netstat -ano ^| findstr :8080 ^| findstr LISTENING') do taskkill /F /PID %a 2>nul"])
                            .stdout(std::process::Stdio::null())
                            .stderr(std::process::Stdio::null())
                            .output();
                        eprintln!("[CLEANUP] 已清理可能残留的 8080 端口进程");
                    }
                    #[cfg(unix)]
                    {
                        let _ = std::process::Command::new("sh")
                            .args(["-c", "lsof -ti:8080 | xargs kill -9 2>/dev/null"])
                            .output();
                    }

                    // 设置工作目录为后端所在目录（确保能找到 legal_db 等资源）
                    let backend_dir = backend_exe.parent().unwrap().to_path_buf();

                    // 将后端 stderr 重定向到文件（后端崩溃时 traceback 会留下，
                    // 否则 Stdio::null() 会丢失所有错误信息，无法排查启动失败）
                    let stderr_log = backend_dir.join("backend_stderr.log");

                    let mut cmd = std::process::Command::new(&backend_exe);
                    cmd.current_dir(&backend_dir);
                    cmd.stdout(std::process::Stdio::null());

                    // 尝试重定向 stderr 到文件；失败则丢弃（退回原行为）
                    match std::fs::File::create(&stderr_log) {
                        Ok(f) => {
                            cmd.stderr(std::process::Stdio::from(f));
                        }
                        Err(e) => {
                            eprintln!("[WARN] 无法创建 stderr 日志: {}, 错误: {}", stderr_log.display(), e);
                            cmd.stderr(std::process::Stdio::null());
                        }
                    }

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

                    // 等待后端就绪
                    let client = reqwest::blocking::Client::builder()
                        .timeout(std::time::Duration::from_secs(5))
                        .build()
                        .unwrap();

                    let max_attempts = 120; // 120 次 * 500ms = 60 秒（Windows PyInstaller 首次启动较慢）
                    let mut backend_ready = false;

                    for i in 1..=max_attempts {
                        match client.get("http://127.0.0.1:8080/api/health").send() {
                            Ok(res) if res.status().is_success() => {
                                eprintln!("[OK] 后端已就绪（{}秒）", i / 2);
                                backend_ready = true;
                                break;
                            }
                            Ok(res) => {
                                eprintln!("[WARN] 后端响应非 200: {} (尝试 {}/{})", res.status(), i, max_attempts);
                            }
                            Err(e) => {
                                if i % 10 == 0 {
                                    eprintln!("[WAIT] 后端未就绪，继续等待... ({}/{}) 错误: {}", i, max_attempts, e);
                                }
                            }
                        }
                        std::thread::sleep(std::time::Duration::from_millis(500));
                    }

                    if !backend_ready {
                        eprintln!("[ERROR] 后端启动超时（30秒），请检查后端日志");
                        // 读取 stderr 日志，把实际错误信息留下供排查
                        let stderr_log = backend_dir.join("backend_stderr.log");
                        if stderr_log.exists() {
                            match std::fs::read_to_string(&stderr_log) {
                                Ok(content) if !content.is_empty() => {
                                    eprintln!("[BACKEND STDERR] 后端错误输出:\n{}", content);
                                }
                                _ => {
                                    eprintln!("[BACKEND STDERR] 日志为空（后端可能被防火墙拦截或端口被占用）");
                                }
                            }
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
