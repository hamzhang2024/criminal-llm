use crate::state::{BackendClient, UpdateInfo};
use tauri::{AppHandle, State};

/// 获取当前版本号
#[tauri::command]
pub fn get_app_version(app: AppHandle) -> String {
    app.package_info().version.to_string()
}

/// 语义化版本比较：a 是否比 b 新（按数字段逐段比较）
/// 字符串字典序在跨位数时会出错（"1.10.0" < "1.9.12"），必须按数值比较
fn version_gt(a: &str, b: &str) -> bool {
    let parse = |v: &str| -> Vec<u64> { v.split('.').map(|x| x.parse().unwrap_or(0)).collect() };
    let (va, vb) = (parse(a), parse(b));
    for i in 0..va.len().max(vb.len()) {
        let (x, y) = (va.get(i).copied().unwrap_or(0), vb.get(i).copied().unwrap_or(0));
        if x != y {
            return x > y;
        }
    }
    false
}

/// 检查更新（从认证服务器获取最新版本）
#[tauri::command]
pub async fn check_update(
    app: AppHandle,
    client: State<'_, BackendClient>,
) -> Result<UpdateInfo, String> {
    let current = app.package_info().version.to_string();
    const AUTH_SERVER_URL: &str = "https://www.casefix.cn";

    let resp = client
        .0
        .get(format!("{}/api/latest-version", AUTH_SERVER_URL))
        .send()
        .await
        .map_err(|e| format!("检查更新失败: {}", e))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("解析失败: {}", e))?;

    let latest = body
        .get("version")
        .and_then(|v| v.as_str())
        .unwrap_or("0.0.0");
    let notes = body
        .get("release_notes")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    // 按数值段比较版本（字典序会让 1.10.0 被误判为比 1.9.x 旧）
    let has_update = version_gt(latest, &current);
    let download_url = body
        .get("download_url")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    Ok(UpdateInfo {
        has_update,
        current_version: current,
        latest_version: latest.to_string(),
        download_url: download_url.to_string(),
        release_notes: notes.to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_gt_numeric_segments() {
        // 跨位数：字典序会错判，数值比较必须为新
        assert!(version_gt("1.10.0", "1.9.12"));
        assert!(version_gt("1.10.0", "1.9.0"));
        assert!(version_gt("2.0.0", "1.99.99"));
        // 常规递增
        assert!(version_gt("1.9.12", "1.9.9"));
        assert!(version_gt("1.9.1", "1.9"));
        // 相等与更旧
        assert!(!version_gt("1.9.9", "1.9.9"));
        assert!(!version_gt("1.9.0", "1.10.0"));
        assert!(!version_gt("1.9", "1.9.1"));
        assert!(!version_gt("0.0.0", "1.0.0"));
        // 非数字段容错（按 0 处理）
        assert!(version_gt("1.9.1", "1.9.x"));
    }
}
