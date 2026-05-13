use std::collections::HashMap;
use tauri::State;
use reqwest::Client;
use serde_json;

/// Backend 服务器地址
const BACKEND_URL: &str = "http://localhost:8080";

/// 共享 HTTP 客户端
struct BackendClient(pub Client);

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .plugin(tauri_plugin_fs::init())
    .plugin(tauri_plugin_shell::init())
    .plugin(tauri_plugin_dialog::init())
    .manage(BackendClient(Client::new()))
    .invoke_handler(tauri::generate_handler![
      health_check,
      list_cases,
      get_case_files,
      get_step_files,
      batch_process,
      get_split_suggestion,
      execute_analysis,
      chat_analysis,
      open_file,
      convert_to_md,
      delete_case,
    ])
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }
      Ok(())
    })
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
