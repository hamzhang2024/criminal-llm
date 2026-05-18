use std::collections::HashMap;
use std::sync::Mutex;
use tauri::{Manager, State, WebviewWindowBuilder, WebviewUrl};
use reqwest::Client;
use serde_json;
use serde::Serialize;

/// Backend 服务器地址
const BACKEND_URL: &str = "http://localhost:8080";

/// 认证服务器地址
const AUTH_SERVER_URL: &str = "http://118.196.83.43:8000";

/// 本地开发服务器地址（开发模式用）
#[allow(dead_code)]
const LOCAL_DEV_URL: &str = "http://localhost:5173";

/// 共享 HTTP 客户端
struct BackendClient(pub Client);

/// 后端进程 PID
struct BackendPid(Mutex<Option<u32>>);

/// 电源管理：caffeinate 进程
struct CaffeinateProcess(Mutex<Option<u32>>);

/// 调用认证服务器，自动处理成功/错误响应
#[allow(dead_code)]
async fn call_auth(
  client: &Client,
  endpoint: &str,
  payload: &serde_json::Value,
) -> Result<AuthResponse, String> {
  let resp = client
    .post(endpoint)
    .json(payload)
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let status = resp.status().as_u16();
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  if status >= 300 {
    return Ok(AuthResponse {
      success: false,
      token: None,
      email: None,
      error: body.get("detail").and_then(|v| v.as_str()).map(|s| s.to_string())
        .or_else(|| body.get("message").and_then(|v| v.as_str()).map(|s| s.to_string()))
        .or_else(|| Some(format!("服务器错误 ({})", status))),
    });
  }
  Ok(AuthResponse {
    success: body.get("success").and_then(|v| v.as_bool()).unwrap_or(false),
    token: body.get("token").and_then(|v| v.as_str()).map(|s| s.to_string()),
    email: body.get("email").and_then(|v| v.as_str()).map(|s| s.to_string()),
    error: body.get("detail").and_then(|v| v.as_str()).map(|s| s.to_string()),
  })
}

/// 获取当前版本号
#[tauri::command]
fn get_app_version(app: tauri::AppHandle) -> String {
  app.package_info().version.to_string()
}

/// 检查更新（从认证服务器获取最新版本）
#[derive(Serialize)]
struct UpdateInfo {
  has_update: bool,
  current_version: String,
  latest_version: String,
  download_url: String,
  release_notes: String,
}

#[tauri::command]
async fn check_update(app: tauri::AppHandle, client: State<'_, BackendClient>) -> Result<UpdateInfo, String> {
  let current = app.package_info().version.to_string();

  let resp = client.0.get(format!("{}/api/latest-version", AUTH_SERVER_URL))
    .send().await
    .map_err(|e| format!("检查更新失败: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;

  let latest = body.get("version").and_then(|v| v.as_str()).unwrap_or("0.0.0");
  let notes = body.get("release_notes").and_then(|v| v.as_str()).unwrap_or("");
  let has_update = latest > current.as_str();
  let download_url = body.get("download_url").and_then(|v| v.as_str()).unwrap_or("");

  Ok(UpdateInfo {
    has_update,
    current_version: current,
    latest_version: latest.to_string(),
    download_url: download_url.to_string(),
    release_notes: notes.to_string(),
  })
}

/// 健康检查
#[tauri::command]
async fn health_check(client: State<'_, BackendClient>) -> Result<HashMap<String, String>, String> {
  let resp = client.0.get(format!("{}/api/health", BACKEND_URL))
    .send().await
    .map_err(|e| format!("后端未启动: {}", e))?;
  let body: HashMap<String, String> = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(body)
}

/// 列出所有案件
#[tauri::command]
async fn list_cases(client: State<'_, BackendClient>) -> Result<serde_json::Value, String> {
  let resp = client.0.get(format!("{}/api/cases/list", BACKEND_URL))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(body)
}

/// 获取案件的文件列表
#[tauri::command]
async fn get_case_files(case_id: String, client: State<'_, BackendClient>) -> Result<serde_json::Value, String> {
  let resp = client.0.get(format!("{}/api/cases/{}/files", BACKEND_URL, case_id))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(body)
}

/// 获取步骤文件
#[tauri::command]
async fn get_step_files(case_id: String, step: u32, client: State<'_, BackendClient>) -> Result<serde_json::Value, String> {
  let resp = client.0.get(format!("{}/api/cases/{}/step-files/{}", BACKEND_URL, case_id, step))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(body)
}

/// 批量处理（step=1:PDF处理, step=2:拆分, step=3:转MD）
#[tauri::command]
async fn batch_process(
  case_id: String,
  step: u32,
  file_names: Vec<String>,
  options: serde_json::Value,
  client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
  let mut body = serde_json::Map::new();
  body.insert("step".to_string(), serde_json::json!(step));
  body.insert("file_names".to_string(), serde_json::json!(file_names));
  if let Some(obj) = options.as_object() {
    for (k, v) in obj {
      body.insert(k.clone(), v.clone());
    }
  }
  let resp = client.0.post(format!("{}/api/cases/{}/batch-process", BACKEND_URL, case_id))
    .json(&body)
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let result: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(result)
}

/// 获取拆分建议
#[tauri::command]
async fn get_split_suggestion(
  case_id: String,
  file_name: String,
  client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
  let resp = client.0.post(format!("{}/api/cases/{}/split-suggest", BACKEND_URL, case_id))
    .json(&serde_json::json!({ "file_name": file_name }))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(body)
}

/// 执行案卷分析
#[tauri::command]
async fn execute_analysis(
  case_id: String,
  defendant: String,
  client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
  let resp = client.0.post(format!("{}/api/cases/{}/batch-process", BACKEND_URL, case_id))
    .json(&serde_json::json!({
      "step": 4,
      "defendant": defendant,
    }))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(body)
}

/// 对话分析
#[tauri::command]
async fn chat_analysis(
  case_id: String,
  message: String,
  history: Vec<serde_json::Value>,
  client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
  let resp = client.0.post(format!("{}/api/analyze-case/chat/{}", BACKEND_URL, case_id))
    .json(&serde_json::json!({
      "message": message,
      "history": history,
      "use_ai": true,
    }))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(body)
}

/// 打开文件（macOS）
#[tauri::command]
fn open_file(file_path: String) -> Result<bool, String> {
  std::process::Command::new("open")
    .arg(&file_path)
    .output()
    .map_err(|e| format!("无法打开文件: {}", e))?;
  Ok(true)
}

/// 打开 URL（macOS）
#[tauri::command]
fn open_url(url: String) -> Result<bool, String> {
  std::process::Command::new("open")
    .arg(&url)
    .output()
    .map_err(|e| format!("无法打开链接: {}", e))?;
  Ok(true)
}

/// 转换 PDF 为 MD
#[tauri::command]
async fn convert_to_md(
  case_id: String,
  file_name: String,
  client: State<'_, BackendClient>,
) -> Result<serde_json::Value, String> {
  let resp = client.0.post(format!("{}/api/cases/{}/convert-to-md", BACKEND_URL, case_id))
    .json(&serde_json::json!({ "file_name": file_name }))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(body)
}

/// 删除案件
#[tauri::command]
async fn delete_case(case_id: String, client: State<'_, BackendClient>) -> Result<serde_json::Value, String> {
  let resp = client.0.delete(format!("{}/api/cases/{}", BACKEND_URL, case_id))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(body)
}

/// 认证：登录
#[derive(Serialize)]
struct AuthResponse {
  success: bool,
  token: Option<String>,
  email: Option<String>,
  error: Option<String>,
}

#[tauri::command]
async fn auth_login(email: String, password: String, client: State<'_, BackendClient>) -> Result<AuthResponse, String> {
  let resp = client.0.post(format!("{}/api/login", AUTH_SERVER_URL))
    .json(&serde_json::json!({ "email": email, "password": password }))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(AuthResponse {
    success: body.get("success").and_then(|v| v.as_bool()).unwrap_or(false),
    token: body.get("token").and_then(|v| v.as_str()).map(|s| s.to_string()),
    email: body.get("email").and_then(|v| v.as_str()).map(|s| s.to_string()),
    error: body.get("detail").and_then(|v| v.as_str()).map(|s| s.to_string()),
  })
}

/// 认证：注册
#[tauri::command]
async fn auth_register(email: String, password: String, client: State<'_, BackendClient>) -> Result<AuthResponse, String> {
  let resp = client.0.post(format!("{}/api/register", AUTH_SERVER_URL))
    .json(&serde_json::json!({ "email": email, "password": password }))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(AuthResponse {
    success: body.get("success").and_then(|v| v.as_bool()).unwrap_or(false),
    token: None,
    email: None,
    error: body.get("detail").and_then(|v| v.as_str()).map(|s| s.to_string()),
  })
}

/// 认证：重置密码
#[tauri::command]
async fn auth_reset_password(email: String, old_password: String, new_password: String, client: State<'_, BackendClient>) -> Result<AuthResponse, String> {
  let resp = client.0.post(format!("{}/api/reset-password", AUTH_SERVER_URL))
    .json(&serde_json::json!({ "email": email, "old_password": old_password, "new_password": new_password }))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(AuthResponse {
    success: body.get("success").and_then(|v| v.as_bool()).unwrap_or(false),
    token: None,
    email: None,
    error: body.get("detail").and_then(|v| v.as_str()).map(|s| s.to_string()),
  })
}

/// 认证：通过邮箱验证码重置密码
#[tauri::command]
async fn auth_send_reset_code(email: String, client: State<'_, BackendClient>) -> Result<AuthResponse, String> {
  let resp = client.0.post(format!("{}/api/send-reset-code", AUTH_SERVER_URL))
    .json(&serde_json::json!({ "email": email }))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(AuthResponse {
    success: body.get("success").and_then(|v| v.as_bool()).unwrap_or(false),
    token: None,
    email: None,
    error: body.get("detail").and_then(|v| v.as_str()).map(|s| s.to_string()),
  })
}

#[tauri::command]
async fn auth_reset_with_code(email: String, code: String, new_password: String, client: State<'_, BackendClient>) -> Result<AuthResponse, String> {
  let resp = client.0.post(format!("{}/api/reset-with-code", AUTH_SERVER_URL))
    .json(&serde_json::json!({ "email": email, "code": code, "new_password": new_password }))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(AuthResponse {
    success: body.get("success").and_then(|v| v.as_bool()).unwrap_or(false),
    token: None,
    email: None,
    error: body.get("detail").and_then(|v| v.as_str()).map(|s| s.to_string()),
  })
}

/// 认证：验证 token
#[tauri::command]
async fn auth_verify(token: String, client: State<'_, BackendClient>) -> Result<AuthResponse, String> {
  let resp = client.0.post(format!("{}/api/verify", AUTH_SERVER_URL))
    .json(&serde_json::json!({ "token": token }))
    .send().await
    .map_err(|e| format!("网络错误: {}", e))?;
  let body: serde_json::Value = resp.json().await
    .map_err(|e| format!("解析失败: {}", e))?;
  Ok(AuthResponse {
    success: body.get("success").and_then(|v| v.as_bool()).unwrap_or(false),
    token: None,
    email: body.get("email").and_then(|v| v.as_str()).map(|s| s.to_string()),
    error: body.get("detail").and_then(|v| v.as_str()).map(|s| s.to_string()),
  })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .plugin(tauri_plugin_fs::init())
    .plugin(tauri_plugin_shell::init())
    .plugin(tauri_plugin_dialog::init())
    .manage(BackendClient(Client::new()))
    .manage(BackendPid(Mutex::new(None)))
    .manage(CaffeinateProcess(Mutex::new(start_caffeinate())))
    .invoke_handler(tauri::generate_handler![
      get_app_version,
      check_update,
      health_check,
      list_cases,
      get_case_files,
      get_step_files,
      batch_process,
      get_split_suggestion,
      execute_analysis,
      chat_analysis,
      open_file,
      open_url,
      convert_to_md,
      delete_case,
      auth_login,
      auth_register,
      auth_reset_password,
      auth_send_reset_code,
      auth_reset_with_code,
      auth_verify,
    ])
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }

      // 开发模式下不启动后端（用户自行 python3 main.py）
      if !cfg!(debug_assertions) {
        // 生产模式：启动 Python 后端
        let resource_path = app.path()
          .resolve("resources/backend", tauri::path::BaseDirectory::Resource)
          .map_err(|e| format!("无法解析资源路径: {}", e))?;

        let backend_exe = resource_path.join("criminal-llm");

        if !backend_exe.exists() {
          eprintln!("警告: 后端可执行文件不存在: {:?}", backend_exe);
        } else {
          eprintln!("🚀 启动后端: {:?}", backend_exe);

          // 使用 std::process::Command 启动后端
          // 设置数据目录环境变量，确保后端使用正确的路径
          let home = app.path().home_dir()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|_| std::env::var("HOME").unwrap_or_else(|_| "/Users/zhanghan".to_string()));
          let data_dir = format!("{}/Documents/.criminal-llm-data", home);

          let child = std::process::Command::new(&backend_exe)
            .env("CRIMINAL_LLM_DATA_DIR", &data_dir)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| format!("启动后端失败: {}", e))?;

          eprintln!("📁 数据目录: {}", data_dir);

          let pid = child.id();
          eprintln!("✅ 后端 PID: {}", pid);

          // 存储 PID 供后续清理
          let backend_pid = app.state::<BackendPid>();
          *backend_pid.0.lock().unwrap() = Some(pid);

          // 等待后端就绪
          let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .unwrap();

          for i in 1..=30 {
            if client.get("http://localhost:8080/api/health").send().is_ok() {
              eprintln!("✅ 后端已就绪（{}秒）", i);
              break;
            }
            std::thread::sleep(std::time::Duration::from_millis(500));
            if i == 30 {
              eprintln!("⚠️  后端启动超时（30秒）");
            }
          }
        }
      }

      // 拦截外部链接点击，用系统浏览器打开
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
          // 允许本地地址正常加载
          let is_local = url.scheme() == "tauri"
            || url.scheme() == "file"
            || url.host_str() == Some("localhost");
          if is_local {
            return true;
          }
          // 外部链接用系统浏览器打开
          if let Err(e) = std::process::Command::new("open").arg(url.as_str()).output() {
            eprintln!("打开外部链接失败: {}", e);
          }
          false // 阻止在 webview 内导航
        })
        .build()?;

      Ok(())
    })
    .on_window_event(|window, event| {
      if let tauri::WindowEvent::CloseRequested { .. } = event {
        // 关闭窗口时强制终止后端进程及其子进程
        kill_backend_process(window);
        // 停止 caffeinate，恢复系统休眠
        let caffeinate = window.state::<CaffeinateProcess>();
        let maybe_pid = caffeinate.0.lock().unwrap().take();
        if let Some(pid) = maybe_pid {
          stop_caffeinate(pid);
        }
      }
    })
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}

/// 强制终止后端进程（SIGKILL，整个进程组）
fn kill_backend_process(window: &tauri::Window) {
  let pid_state = window.state::<BackendPid>();
  let maybe_pid = pid_state.0.lock().unwrap().take();
  if let Some(pid) = maybe_pid {
    eprintln!("🛑 关闭后端 PID: {}", pid);
    #[cfg(unix)]
    unsafe {
      // 先杀整个进程组（包括子进程），再杀主进程
      // 使用负 PID 向整个进程组发送信号
      libc::kill(-(pid as i32), libc::SIGKILL);
      libc::kill(pid as i32, libc::SIGKILL);
    }
    #[cfg(windows)]
    {
      // Windows 上递归终止进程树
      let _ = std::process::Command::new("taskkill")
        .args(["/F", "/T", "/PID", &pid.to_string()])
        .output();
    }
    eprintln!("✅ 后端已退出");
  }
}

/// 启动 caffeinate 阻止系统休眠（macOS 全局）
fn start_caffeinate() -> Option<u32> {
  #[cfg(target_os = "macos")]
  {
    let child = std::process::Command::new("caffeinate")
      .arg("-d")  // 阻止显示器休眠
      .arg("-i")  // 阻止系统空闲休眠
      .stdin(std::process::Stdio::piped())
      .stdout(std::process::Stdio::null())
      .stderr(std::process::Stdio::null())
      .spawn();
    match child {
      Ok(c) => {
        eprintln!("🔋 已阻止系统休眠（应用运行期间）");
        Some(c.id())
      }
      Err(e) => {
        eprintln!("⚠️ caffeinate 启动失败: {}", e);
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
fn stop_caffeinate(pid: u32) {
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
  eprintln!("🔋 已恢复系统休眠");
}
