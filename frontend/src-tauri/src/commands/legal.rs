use serde_json::{json, Value};
use std::path::Path;

/// 列出所有法律知识条目（从 legal_db/ 目录读取）
#[tauri::command]
pub fn list_legal_kb() -> Result<Value, String> {
    let mut files: Vec<Value> = Vec::new();

    // 在 resources/ 目录中找法律文件
    let legal_dirs = vec![
        Path::new("resources/legal_db"),
        Path::new("../backend/legal_db"),
        Path::new("legal_db"),
    ];

    for dir in &legal_dirs {
        if let Ok(entries) = std::fs::read_dir(dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
                if ext == "md" || ext == "json" {
                    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                    files.push(json!({
                        "name": name,
                        "path": path.to_string_lossy(),
                        "size": std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0),
                    }));
                }
            }
        }
    }
    Ok(json!({"files": files, "total": files.len()}))
}

/// 获取单个法律条目内容
#[tauri::command]
pub fn get_legal_item(name: String) -> Result<Value, String> {
    // 防路径穿越：仅允许纯文件名（无路径分隔符）
    let safe_name = std::path::Path::new(&name);
    if safe_name.components().count() != 1 {
        return Err("非法文件名".to_string());
    }

    let legal_dirs = vec![
        "resources/legal_db",
        "../backend/legal_db",
        "legal_db",
    ];

    for dir in &legal_dirs {
        let file_path = std::path::Path::new(dir).join(&name);
        if file_path.exists() {
            let content = std::fs::read_to_string(&file_path)
                .map_err(|e| format!("读取文件失败: {}", e))?;
            return Ok(json!({"name": name, "content": content}));
        }
    }

    Err(format!("法律文件 '{}' 不存在", name))
}
