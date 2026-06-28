/// Python worker JSON-RPC 桥接
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Command, Stdio};

static WORKER_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

/// 调用 Python worker 处理计算任务
pub fn call_worker(
    python_exe: &str,
    worker_script: &str,
    data_dir: &str,
    method: &str,
    params: Value,
) -> Result<Value, String> {
    let req_id = WORKER_COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    let request = json!({
        "id": req_id.to_string(),
        "method": method,
        "params": params,
        "data_dir": data_dir,
    });

    let request_str = serde_json::to_string(&request)
        .map_err(|e| format!("JSON序列化失败: {}", e))?;

    let mut child = Command::new(python_exe)
        .arg(worker_script)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("启动 Python worker 失败: {}", e))?;

    // 发送请求
    if let Some(ref mut stdin) = child.stdin {
        stdin.write_all(request_str.as_bytes()).map_err(|e| format!("写入 stdin 失败: {}", e))?;
        stdin.write_all(b"\n").map_err(|e| format!("写入换行失败: {}", e))?;
    }

    // 读取响应
    let mut response_str = String::new();
    if let Some(ref mut stdout) = child.stdout {
        BufReader::new(stdout).read_line(&mut response_str)
            .map_err(|e| format!("读取 stdout 失败: {}", e))?;
    }

    // 读取 stderr
    let mut stderr_str = String::new();
    if let Some(ref mut stderr) = child.stderr {
        Read::read_to_string(&mut BufReader::new(stderr), &mut stderr_str).ok();
    }

    let status = child.wait().map_err(|e| format!("等待 worker 退出失败: {}", e))?;

    if !status.success() {
        return Err(format!("Python worker 退出码 {}: {}", status.code().unwrap_or(-1), stderr_str.trim()));
    }

    // 解析响应
    let response: Value = serde_json::from_str(&response_str)
        .map_err(|e| format!("解析 worker 响应失败: {} (内容: {:.200})", e, response_str))?;

    let resp_id = response.get("id").and_then(|v| v.as_str()).unwrap_or("");
    if resp_id != req_id.to_string() {
        return Err(format!("Worker 响应 ID 不匹配"));
    }

    if let Some(err) = response.get("error").and_then(|v| v.as_str()) {
        return Err(format!("Worker 错误: {}", err));
    }

    response.get("result").cloned()
        .ok_or_else(|| "Worker 返回空结果".to_string())
}
