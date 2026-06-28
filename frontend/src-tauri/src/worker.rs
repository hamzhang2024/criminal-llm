/// Python worker JSON-RPC 桥接
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};

static WORKER_COUNTER: AtomicU64 = AtomicU64::new(1);

/// 子进程守卫 — 在 drop 时强制 kill
struct ChildGuard(Option<Child>);
impl ChildGuard {
    fn disarm(&mut self) -> Child {
        self.0.take().unwrap()
    }
}
impl Drop for ChildGuard {
    fn drop(&mut self) {
        if let Some(ref mut child) = self.0 {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// 调用 Python worker（带 600 秒超时 + 进程守卫）
pub fn call_worker(
    python_exe: &str,
    worker_script: &str,
    data_dir: &str,
    method: &str,
    params: Value,
) -> Result<Value, String> {
    let req_id = WORKER_COUNTER.fetch_add(1, Ordering::SeqCst);
    let request = json!({
        "id": req_id.to_string(),
        "method": method,
        "params": params,
        "data_dir": data_dir,
    });

    let request_str = serde_json::to_string(&request)
        .map_err(|e| format!("JSON序列化失败: {}", e))?;

    let child = Command::new(python_exe)
        .arg(worker_script)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("启动 Python worker 失败: {}", e))?;

    // Guard: 确保子进程在任何错误路径被 kill
    let mut guard = ChildGuard(Some(child));
    let child_ref = guard.0.as_mut().unwrap();

    // 发送请求
    if let Some(ref mut stdin) = child_ref.stdin {
        stdin.write_all(request_str.as_bytes()).map_err(|e| {
            format!("写入 stdin 失败: {}", e)
        })?;
        stdin.write_all(b"\n").map_err(|e| {
            format!("写入换行失败: {}", e)
        })?;
    }

    // 读取 stdout（带超时）
    let mut response_str = String::new();
    if let Some(stdout) = child_ref.stdout.take() {
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            let mut buf = String::new();
            match reader.read_line(&mut buf) {
                Ok(_) => { let _ = tx.send(Ok(buf)); }
                Err(e) => { let _ = tx.send(Err(format!("读取 stdout 失败: {}", e))); }
            }
        });

        match rx.recv_timeout(std::time::Duration::from_secs(600)) {
            Ok(Ok(s)) => response_str = s,
            Ok(Err(e)) => return Err(e),
            Err(_) => return Err("Python worker 响应超时（600 秒）".to_string()),
        }
    }

    // 读取 stderr
    let mut stderr_str = String::new();
    if let Some(ref mut stderr) = child_ref.stderr {
        Read::read_to_string(stderr, &mut stderr_str).ok();
    }

    // 等待子进程退出
    let child = guard.disarm();
    let output = child.wait_with_output()
        .map_err(|e| format!("等待 worker 退出失败: {}", e))?;

    if !output.status.success() {
        let stderr_preview = String::from_utf8_lossy(&output.stderr);
        return Err(format!("Python worker 退出码 {}: {:.500}",
            output.status.code().unwrap_or(-1), stderr_preview.trim()));
    }

    // 解析 JSON 响应
    let response: Value = serde_json::from_str(&response_str)
        .map_err(|e| format!("解析 worker 响应失败: {} (内容: {:.200})", e, response_str))?;

    let resp_id = response.get("id").and_then(|v| v.as_str()).unwrap_or("");
    if resp_id != req_id.to_string() {
        return Err("Worker 响应 ID 不匹配".to_string());
    }

    if let Some(err) = response.get("error").and_then(|v| v.as_str()) {
        return Err(format!("Worker 错误: {}", err));
    }

    response.get("result").cloned()
        .ok_or_else(|| "Worker 返回空结果".to_string())
}
