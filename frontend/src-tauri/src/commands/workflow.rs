use crate::db::AppDb;
use tauri::State;

/// 创建工作流
#[tauri::command]
pub fn create_workflow(
    db: State<'_, AppDb>,
    id: String,
    name: String,
    config: String,
) -> Result<(), String> {
    db.create_workflow(&id, &name, &config)
}

/// 更新工作流状态
#[tauri::command]
pub fn update_workflow_status(
    db: State<'_, AppDb>,
    id: String,
    status: String,
    current_step: i32,
) -> Result<(), String> {
    db.update_workflow_status(&id, &status, current_step)
}

/// 列出所有工作流
#[tauri::command]
pub fn list_workflows(db: State<'_, AppDb>) -> Result<serde_json::Value, String> {
    let workflows = db.list_workflows()?;
    serde_json::to_value(workflows).map_err(|e| format!("序列化失败: {}", e))
}

/// 获取单个工作流
#[tauri::command]
pub fn get_workflow(db: State<'_, AppDb>, id: String) -> Result<Option<serde_json::Value>, String> {
    let workflow = db.get_workflow(&id)?;
    workflow
        .map(|w| serde_json::to_value(w).map_err(|e| format!("序列化失败: {}", e)))
        .transpose()
}

/// 添加步骤
#[tauri::command]
pub fn add_step(
    db: State<'_, AppDb>,
    id: String,
    workflow_id: String,
    step_type: String,
    input: String,
) -> Result<(), String> {
    db.add_step(&id, &workflow_id, &step_type, &input)
}

/// 更新步骤状态
#[tauri::command]
pub fn update_step(
    db: State<'_, AppDb>,
    id: String,
    status: String,
    progress: i32,
    output: Option<String>,
    error: Option<String>,
) -> Result<(), String> {
    db.update_step(&id, &status, progress, output.as_deref(), error.as_deref())
}

/// 获取工作流的步骤
#[tauri::command]
pub fn get_steps(db: State<'_, AppDb>, workflow_id: String) -> Result<serde_json::Value, String> {
    let steps = db.get_steps(&workflow_id)?;
    serde_json::to_value(steps).map_err(|e| format!("序列化失败: {}", e))
}

/// 记录文件
#[tauri::command]
pub fn add_file(
    db: State<'_, AppDb>,
    id: String,
    workflow_id: String,
    original_path: String,
    file_type: String,
) -> Result<(), String> {
    db.add_file(&id, &workflow_id, &original_path, &file_type)
}

/// 更新文件处理路径
#[tauri::command]
pub fn update_file_paths(
    db: State<'_, AppDb>,
    id: String,
    processed_path: Option<String>,
    md_path: Option<String>,
) -> Result<(), String> {
    db.update_file_paths(&id, processed_path.as_deref(), md_path.as_deref())
}

/// 获取工作流的文件
#[tauri::command]
pub fn get_files(db: State<'_, AppDb>, workflow_id: String) -> Result<serde_json::Value, String> {
    let files = db.get_files(&workflow_id)?;
    serde_json::to_value(files).map_err(|e| format!("序列化失败: {}", e))
}

/// 记录操作日志
#[tauri::command]
pub fn log_operation(
    db: State<'_, AppDb>,
    workflow_id: String,
    operation: String,
    detail: String,
) -> Result<(), String> {
    db.log_operation(&workflow_id, &operation, &detail)
}

/// 删除工作流
#[tauri::command]
pub fn delete_workflow(db: State<'_, AppDb>, id: String) -> Result<(), String> {
    db.delete_workflow(&id)
}
