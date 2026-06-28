use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension, Result};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Mutex;

/// 工作流状态（预留，供未来工作流引擎使用）
#[allow(dead_code)]
#[derive(Debug, Serialize, Deserialize)]
pub enum WorkflowStatus {
    Draft,
    Running,
    Completed,
    Failed,
}

impl std::fmt::Display for WorkflowStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WorkflowStatus::Draft => write!(f, "draft"),
            WorkflowStatus::Running => write!(f, "running"),
            WorkflowStatus::Completed => write!(f, "completed"),
            WorkflowStatus::Failed => write!(f, "failed"),
        }
    }
}

/// 步骤类型（预留，供未来工作流引擎使用）
#[allow(dead_code)]
#[derive(Debug, Serialize, Deserialize)]
pub enum StepType {
    Watermark,
    Split,
    Convert,
    Analyze,
}

impl std::fmt::Display for StepType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StepType::Watermark => write!(f, "watermark"),
            StepType::Split => write!(f, "split"),
            StepType::Convert => write!(f, "convert"),
            StepType::Analyze => write!(f, "analyze"),
        }
    }
}

/// 步骤状态（预留，供未来工作流引擎使用）
#[allow(dead_code)]
#[derive(Debug, Serialize, Deserialize)]
pub enum StepStatus {
    Pending,
    Running,
    Done,
    Skipped,
    Error,
}

impl std::fmt::Display for StepStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StepStatus::Pending => write!(f, "pending"),
            StepStatus::Running => write!(f, "running"),
            StepStatus::Done => write!(f, "done"),
            StepStatus::Skipped => write!(f, "skipped"),
            StepStatus::Error => write!(f, "error"),
        }
    }
}

/// 工作流记录
#[derive(Debug, Serialize, Deserialize)]
pub struct Workflow {
    pub id: String,
    pub name: String,
    pub created_at: String,
    pub updated_at: String,
    pub status: String,
    pub current_step: i32,
    pub config: String, // JSON
}

/// 步骤记录
#[derive(Debug, Serialize, Deserialize)]
pub struct WorkflowStep {
    pub id: String,
    pub workflow_id: String,
    pub step_type: String,
    pub status: String,
    pub input: String,          // JSON
    pub output: Option<String>, // JSON
    pub progress: i32,
    pub error: Option<String>,
    pub started_at: Option<String>,
    pub finished_at: Option<String>,
}

/// 文件记录
#[derive(Debug, Serialize, Deserialize)]
pub struct CaseFile {
    pub id: String,
    pub workflow_id: String,
    pub original_path: String,
    pub processed_path: Option<String>,
    pub md_path: Option<String>,
    pub file_type: String,
    pub created_at: String,
}

/// 数据库包装器
pub struct AppDb {
    conn: Mutex<Connection>,
    data_dir: PathBuf,
}

impl AppDb {
    /// 初始化数据库
    pub fn new(db_path: PathBuf) -> Result<Self, String> {
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("创建数据库目录失败: {}", e))?;
        }

        let conn = Connection::open(&db_path).map_err(|e| format!("打开数据库失败: {}", e))?;

        // 数据目录是 db 文件所在目录
        let data_dir = db_path.parent().unwrap_or(&db_path).to_path_buf();

        let db = AppDb {
            conn: Mutex::new(conn),
            data_dir,
        };

        db.init_tables()?;
        Ok(db)
    }

    /// 获取数据目录
    pub fn data_dir(&self) -> &PathBuf {
        &self.data_dir
    }

    /// 创建表
    fn init_tables(&self) -> Result<(), String> {
        let conn = self.conn.lock().unwrap();
        conn.execute_batch("PRAGMA foreign_keys = ON;")
            .map_err(|e| format!("启用外键约束失败: {}", e))?;
        conn.execute_batch(
            "
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                status TEXT CHECK(status IN ('draft', 'running', 'completed', 'failed')),
                current_step INTEGER DEFAULT 0,
                config TEXT
            );

            CREATE TABLE IF NOT EXISTS workflow_steps (
                id TEXT PRIMARY KEY,
                workflow_id TEXT REFERENCES workflows(id),
                step_type TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                input TEXT,
                output TEXT,
                progress INTEGER DEFAULT 0,
                error TEXT,
                started_at TEXT,
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS case_files (
                id TEXT PRIMARY KEY,
                workflow_id TEXT REFERENCES workflows(id),
                original_path TEXT NOT NULL,
                processed_path TEXT,
                md_path TEXT,
                file_type TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS operations_log (
                id TEXT PRIMARY KEY,
                workflow_id TEXT,
                operation TEXT NOT NULL,
                detail TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            ",
        )
        .map_err(|e| format!("初始化表失败: {}", e))?;

        Ok(())
    }

    /// 创建工作流
    pub fn create_workflow(&self, id: &str, name: &str, config: &str) -> Result<(), String> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO workflows (id, name, status, current_step, config)
             VALUES (?1, ?2, 'draft', 0, ?3)",
            params![id, name, config],
        )
        .map_err(|e| format!("创建工作流失败: {}", e))?;
        Ok(())
    }

    /// 更新工作流状态
    pub fn update_workflow_status(
        &self,
        id: &str,
        status: &str,
        current_step: i32,
    ) -> Result<(), String> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE workflows SET status = ?1, current_step = ?2, updated_at = datetime('now')
             WHERE id = ?3",
            params![status, current_step, id],
        )
        .map_err(|e| format!("更新工作流状态失败: {}", e))?;
        Ok(())
    }

    /// 获取所有工作流
    pub fn list_workflows(&self) -> Result<Vec<Workflow>, String> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn
            .prepare("SELECT id, name, created_at, updated_at, status, current_step, config FROM workflows ORDER BY created_at DESC")
            .map_err(|e| format!("查询工作流失败: {}", e))?;

        let workflows = stmt
            .query_map([], |row| {
                Ok(Workflow {
                    id: row.get(0)?,
                    name: row.get(1)?,
                    created_at: row.get(2)?,
                    updated_at: row.get(3)?,
                    status: row.get(4)?,
                    current_step: row.get(5)?,
                    config: row.get(6)?,
                })
            })
            .map_err(|e| format!("读取工作流失败: {}", e))?;

        workflows
            .collect::<Result<Vec<_>, _>>()
            .map_err(|e| format!("收集工作流失败: {}", e))
    }

    /// 获取单个工作流
    pub fn get_workflow(&self, id: &str) -> Result<Option<Workflow>, String> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn
            .prepare("SELECT id, name, created_at, updated_at, status, current_step, config FROM workflows WHERE id = ?1")
            .map_err(|e| format!("查询工作流失败: {}", e))?;

        stmt.query_row(params![id], |row| {
            Ok(Workflow {
                id: row.get(0)?,
                name: row.get(1)?,
                created_at: row.get(2)?,
                updated_at: row.get(3)?,
                status: row.get(4)?,
                current_step: row.get(5)?,
                config: row.get(6)?,
            })
        })
        .optional()
        .map_err(|e| format!("读取工作流失败: {}", e))
    }

    /// 添加步骤
    pub fn add_step(
        &self,
        id: &str,
        workflow_id: &str,
        step_type: &str,
        input: &str,
    ) -> Result<(), String> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO workflow_steps (id, workflow_id, step_type, status, input)
             VALUES (?1, ?2, ?3, 'pending', ?4)",
            params![id, workflow_id, step_type, input],
        )
        .map_err(|e| format!("添加步骤失败: {}", e))?;
        Ok(())
    }

    /// 更新步骤状态
    pub fn update_step(
        &self,
        id: &str,
        status: &str,
        progress: i32,
        output: Option<&str>,
        error: Option<&str>,
    ) -> Result<(), String> {
        let conn = self.conn.lock().unwrap();
        let now = Utc::now().to_rfc3339();
        conn.execute(
            "UPDATE workflow_steps
             SET status = ?1, progress = ?2, output = ?3, error = ?4,
                 started_at = CASE WHEN ?1 = 'running' AND started_at IS NULL THEN ?5 ELSE started_at END,
                 finished_at = CASE WHEN ?1 IN ('done', 'error') THEN ?5 ELSE finished_at END
             WHERE id = ?6",
            params![status, progress, output, error, now, id],
        )
        .map_err(|e| format!("更新步骤失败: {}", e))?;
        Ok(())
    }

    /// 获取工作流的步骤
    pub fn get_steps(&self, workflow_id: &str) -> Result<Vec<WorkflowStep>, String> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn
            .prepare("SELECT id, workflow_id, step_type, status, input, output, progress, error, started_at, finished_at FROM workflow_steps WHERE workflow_id = ?1 ORDER BY rowid")
            .map_err(|e| format!("查询步骤失败: {}", e))?;

        let steps = stmt
            .query_map(params![workflow_id], |row| {
                Ok(WorkflowStep {
                    id: row.get(0)?,
                    workflow_id: row.get(1)?,
                    step_type: row.get(2)?,
                    status: row.get(3)?,
                    input: row.get(4)?,
                    output: row.get(5)?,
                    progress: row.get(6)?,
                    error: row.get(7)?,
                    started_at: row.get(8)?,
                    finished_at: row.get(9)?,
                })
            })
            .map_err(|e| format!("读取步骤失败: {}", e))?;

        steps
            .collect::<Result<Vec<_>, _>>()
            .map_err(|e| format!("收集步骤失败: {}", e))
    }

    /// 记录文件
    pub fn add_file(
        &self,
        id: &str,
        workflow_id: &str,
        original_path: &str,
        file_type: &str,
    ) -> Result<(), String> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO case_files (id, workflow_id, original_path, file_type)
             VALUES (?1, ?2, ?3, ?4)",
            params![id, workflow_id, original_path, file_type],
        )
        .map_err(|e| format!("记录文件失败: {}", e))?;
        Ok(())
    }

    /// 更新文件处理路径
    pub fn update_file_paths(
        &self,
        id: &str,
        processed_path: Option<&str>,
        md_path: Option<&str>,
    ) -> Result<(), String> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE case_files SET processed_path = ?1, md_path = ?2 WHERE id = ?3",
            params![processed_path, md_path, id],
        )
        .map_err(|e| format!("更新文件路径失败: {}", e))?;
        Ok(())
    }

    /// 获取工作流的文件
    pub fn get_files(&self, workflow_id: &str) -> Result<Vec<CaseFile>, String> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn
            .prepare("SELECT id, workflow_id, original_path, processed_path, md_path, file_type, created_at FROM case_files WHERE workflow_id = ?1")
            .map_err(|e| format!("查询文件失败: {}", e))?;

        let files = stmt
            .query_map(params![workflow_id], |row| {
                Ok(CaseFile {
                    id: row.get(0)?,
                    workflow_id: row.get(1)?,
                    original_path: row.get(2)?,
                    processed_path: row.get(3)?,
                    md_path: row.get(4)?,
                    file_type: row.get(5)?,
                    created_at: row.get(6)?,
                })
            })
            .map_err(|e| format!("读取文件失败: {}", e))?;

        files
            .collect::<Result<Vec<_>, _>>()
            .map_err(|e| format!("收集文件失败: {}", e))
    }

    /// 记录操作日志
    pub fn log_operation(
        &self,
        workflow_id: &str,
        operation: &str,
        detail: &str,
    ) -> Result<(), String> {
        let conn = self.conn.lock().unwrap();
        let id = format!("op_{}", Utc::now().timestamp_millis());
        conn.execute(
            "INSERT INTO operations_log (id, workflow_id, operation, detail)
             VALUES (?1, ?2, ?3, ?4)",
            params![id, workflow_id, operation, detail],
        )
        .map_err(|e| format!("记录操作日志失败: {}", e))?;
        Ok(())
    }

    /// 删除工作流及其关联数据
    pub fn delete_workflow(&self, id: &str) -> Result<(), String> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "DELETE FROM workflow_steps WHERE workflow_id = ?1",
            params![id],
        )
        .map_err(|e| format!("删除步骤失败: {}", e))?;
        conn.execute("DELETE FROM case_files WHERE workflow_id = ?1", params![id])
            .map_err(|e| format!("删除文件记录失败: {}", e))?;
        conn.execute(
            "DELETE FROM operations_log WHERE workflow_id = ?1",
            params![id],
        )
        .map_err(|e| format!("删除操作日志失败: {}", e))?;
        conn.execute("DELETE FROM workflows WHERE id = ?1", params![id])
            .map_err(|e| format!("删除工作流失败: {}", e))?;
        Ok(())
    }
}
