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
                        // 分两步：用 netstat 找 PID 列表，再逐个 taskkill
                        // （直接用 for /f 循环在 cmd /C 中可能因转义问题不生效）
                        if let Ok(output) = std::process::Command::new("cmd")
                            .args(["/C", "netstat -ano | findstr :8080 | findstr LISTENING"])
                            .output()
                        {
                            let stdout = String::from_utf8_lossy(&output.stdout);
                            for line in stdout.lines() {
                                let parts: Vec<&str> = line.split_whitespace().collect();
                                if let Some(pid_str) = parts.last() {
                                    let pid = pid_str.trim();
                                    if !pid.is_empty() && pid != "0" {
                                        eprintln!("[CLEANUP] 清理旧后端进程 PID: {}", pid);
                                        let _ = std::process::Command::new("taskkill")
                                            .args(["/F", "/PID", pid])
                                            .stdout(std::process::Stdio::null())
                                            .stderr(std::process::Stdio::null())
                                            .output();
                                    }
                                }
                            }
                        }
                        // 等待 Windows 释放端口（TIME_WAIT 过渡期）
                        std::thread::sleep(std::time::Duration::from_secs(2));
                        eprintln!("[CLEANUP] 已清理可能残留的 8080 端口进程");
                    }
                    #[cfg(unix)]
                    {
                        // macOS/Linux：先列 PID 再逐个 kill（比 xargs kill -9 更可靠）
                        if let Ok(output) = std::process::Command::new("sh")
                            .args(["-c", "lsof -ti:8080 2>/dev/null"])
                            .output()
                        {
                            let stdout = String::from_utf8_lossy(&output.stdout);
                            for pid in stdout.lines() {
                                let pid = pid.trim();
                                if !pid.is_empty() {
                                    eprintln!("[CLEANUP] 清理旧后端进程 PID: {}", pid);
                                    let _ = std::process::Command::new("kill")
                                        .args(["-9", pid])
                                        .output();
                                }
                            }
                        }
                        std::thread::sleep(std::time::Duration::from_secs(1));
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

                    // Windows 上隐藏窗口 + 脱离父进程 Job Object
                    // Tauri v2 (WebView2) 会创建 Job Object 管理进程生命周期，
                    // 子进程默认继承该 Job Object。如果 WebView 重启/崩溃，
                    // Job Object 会连带杀死后端 Python 进程。
                    // CREATE_BREAKAWAY_FROM_JOB 使后端独立运行，不受 Tauri 壳影响。
                    #[cfg(target_os = "windows")]
                    {
                        use std::os::windows::process::CommandExt;
                        const CREATE_NO_WINDOW: u32 = 0x08000000;
                        const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x01000000;
                        cmd.creation_flags(CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB);
                    }

                    let mut child = cmd.spawn().map_err(|e| format!("启动后端失败: {}", e))?;

                    let pid = child.id();
                    eprintln!("[OK] 后端 PID: {} (spawned, waiting for health check)", pid);

                    // 诊断：2 秒后检查进程是否还活着
                    std::thread::sleep(std::time::Duration::from_secs(2));
                    match child.try_wait() {
                        Ok(Some(status)) => {
                            eprintln!("[DIAG] 后端已退出！exit code: {:?}", status.code());
                        }
                        Ok(None) => {
                            eprintln!("[DIAG] 后端仍在运行（2s 后存活）");
                        }
                        Err(e) => {
                            eprintln!("[DIAG] try_wait 失败: {}", e);
                        }
                    }

                    // 写入 PID 文件供诊断使用（端口冲突时方便排查）
                    if let Ok(data_dir) = app.path().app_data_dir() {
                        let _ = std::fs::write(data_dir.join("backend.pid"), pid.to_string());
                    }

                    let backend_pid = app.state::<BackendPid>();
                    *backend_pid.0.lock().unwrap() = Some(pid);

                    // 不在此处阻塞等待后端——就绪检测由前端 HomePage 的 waitForBackend 轮询负责
                    // （带 loading 转圈）。历史版本在此同步轮询 health check 最多 60 秒，导致 Tauri
                    // setup 阻塞、窗口迟迟不弹出（mac 启动体感明显变慢）。
                    // 现在 spawn 后立即返回，窗口先弹出，前端继续轮询后端是否就绪。
                    // 后端崩溃的 traceback 仍写入 backend_stderr.log（spawn 前 stderr 已重定向），
                    // 前端超时会显示真实错误原因。
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
