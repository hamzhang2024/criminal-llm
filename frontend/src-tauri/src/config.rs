use serde_json::{json, Value};
use std::path::Path;
use tokio::fs;

/// 配置文件路径（与 Python 一致）
pub fn get_config_path(data_dir: &Path) -> std::path::PathBuf {
    data_dir.join("criminal-llm-config.json")
}

/// 默认配置（与 Python DEFAULTS 一致）
pub fn default_config() -> Value {
    json!({
        "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "llm_model": "qwen3.5-plus",
        "evidence_concurrency": 3,
        "pdf_engine": "mineru",
        "paddleocr_token": "",
        "mineru_mode": "cloud",
        "mineru_local_url": "",
        "yuandian_token": "",
        "model_context_limit": null,
    })
}

/// 读取配置，合并默认值
pub async fn load_config(config_path: &Path) -> Result<Value, Box<dyn std::error::Error>> {
    if config_path.exists() {
        let content = fs::read_to_string(config_path).await?;
        let user_config: Value = serde_json::from_str(&content)?;

        // 合并默认值和用户配置
        let mut result = default_config();
        if let Value::Object(ref mut obj) = result {
            if let Value::Object(user_obj) = user_config {
                for (k, v) in user_obj {
                    obj.insert(k, v);
                }
            }
        }
        Ok(result)
    } else {
        Ok(default_config())
    }
}

/// 保存配置
pub async fn save_config(config_path: &Path, config: &Value) -> Result<(), Box<dyn std::error::Error>> {
    let config_dir = config_path.parent().unwrap_or(Path::new("."));
    fs::create_dir_all(config_dir).await?;

    let json_str = serde_json::to_string_pretty(&config)?;
    fs::write(config_path, json_str).await?;

    Ok(())
}
