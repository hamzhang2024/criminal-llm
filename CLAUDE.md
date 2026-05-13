# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Criminal LLM 开发手册

> 本文档是 Claude Code 的开发指南，包含项目全貌、技术栈、变更历史和开发规范。

---

## 📋 项目概述

**Criminal LLM** — 刑事案卷智能分析桌面应用

### 技术栈
| 层级 | 技术 |
|------|------|
| **桌面框架** | Tauri 2.x + React + TypeScript |
| **前端** | React 18 + TypeScript + Vite 5.4 |
| **后端** | FastAPI (Python 3.13) |
| **模型** | bailian/qwen3.6-plus（阿里云百炼） |
| **UI 风格** | macOS 原生风格设计系统 |
| **认证服务** | FastAPI + SQLite + JWT（远程部署） |
| **前端地址** | http://localhost:5173 |
| **后端地址** | http://localhost:8080 |
| **认证服务器** | http://118.196.83.43:8000 |
| **项目路径** | /Users/zhanghan/.openclaw/workspace/criminal-llm/ |
| **认证服务路径** | /Users/zhanghan/.openclaw/workspace/criminal-llm-auth/ |

### 核心业务逻辑
- **辩护理论**：三阶层犯罪论体系
  - 构成要件符合性 → 违法性 → 有责性
- **辩护分析方法**：法条构成要件拆解分析法
  - 提出问题 → 套入法条 → 是否符合构成要件 → 本罪/无罪/他罪

---

## 🏗️ 项目架构

### 目录结构
```
criminal-llm/
├── backend/
│   ├── main.py                 # FastAPI 主入口
│   ├── case_manager.py         # 案件管理 API（CRUD、文件扫描、导入、证据提取）
│   ├── process_api.py          # PDF 处理 API（去水印、OCR）
│   ├── pdf_to_md.py            # PDF → Markdown 转换模块（MinerU 云端）
│   ├── analyzer_api.py         # 案卷分析 API（拆分、证据识别）
│   ├── llm_client.py           # LLM 客户端（百炼 API）
│   ├── legal_knowledge.py      # 法律知识库（内置刑法/刑诉法 + 三阶层理论）
│   ├── legal_db/               # 内置法律文件目录
│   │   ├── criminal_law.md     # 刑法全文
│   │   └── criminal_procedure_law.md  # 刑诉法全文
│   ├── background_tasks.py     # 后台任务管理（PDF 转 MD 异步执行 + 进度持久化）
│   ├── power_manager.py        # macOS 电源管理（caffeinate 防止系统休眠）
│   ├── ocr_acceleration.py     # OCR 加速模块
│   ├── check_md_quality.py     # MD 质量检查工具
│   ├── stage_api.py            # 分析阶段状态管理 API（5阶段 + 5A/5B/53子阶段）
│   ├── pipeline_api.py         # 流水线处理 API
│   ├── analysis_pipeline.py    # 分析流水线编排（步骤 0-5 串联）
│   ├── analysis_engine.py      # 分析引擎（三阶层分析核心）
│   ├── case_splitter.py        # 文书拆分模块
│   ├── pdf_processor.py        # PDF 处理核心（水印移除、OCR）
│   ├── legal_search.py         # 法律条文联网搜索
│   ├── legal_kb_api.py         # 法律知识库 CRUD API
│   ├── legal_knowledge.py      # 法律知识库（内置刑法/刑诉法 + 三阶层理论）
│   ├── config_manager.py       # 配置管理（JSON 持久化）
│   ├── config.py               # 全局配置
│   └── watermark_remover.py    # 水印移除模块
├── frontend/
│   ├── src/
│   │   ├── App.tsx             # 路由入口（含 AuthGate 认证门禁）
│   │   ├── components/
│   │   │   ├── MacOSLayout.tsx   # macOS 风格设计系统
│   │   │   ├── MacOSDialog.tsx   # 模态对话框（showAlert/showConfirm）
│   │   │   ├── PdfViewer.tsx     # PDF 查看器（pdf.js CDN + 懒加载 + 批注）
│   │   │   ├── StickyNoteOverlay.tsx # 区域便签批注
│   │   │   ├── MermaidRenderer.tsx   # Mermaid 图表
│   │   │   └── case-detail/      # （空目录，预留）
│   │   ├── api/
│   │   │   ├── index.ts         # API 客户端（含认证方法 + localStorage token 管理）
│   │   │   └── types.ts         # 类型定义
│   │   ├── pages/
│   │   │   ├── HomePage.tsx      # 首页：案件列表（toolbar 显示登录用户头像+邮箱）
│   │   │   ├── LoginPage.tsx     # 登录页面（认证门禁）
│   │   │   ├── CaseDetailPage.tsx # 案件详情：流水线处理（主页面）
│   │   │   ├── AnalyzePage.tsx   # 案卷分析页面
│   │   │   ├── ReportPage.tsx    # 辩护报告页面
│   │   │   ├── ConvertPage.tsx   # PDF 转 MD 页面
│   │   │   ├── ProcessPage.tsx   # PDF 处理页面
│   │   │   ├── SettingsPage.tsx  # 设置页面（含账号管理 + 退出登录）
│   │   │   └── ManualPage.tsx    # 使用说明书页面
│   │   └── styles/              # 全局样式
│   └── src-tauri/              # Tauri 桌面壳（Rust 后端骨架，阶段 1 实施中）
│       ├── Cargo.toml          # Rust 依赖（reqwest, tokio, Tauri plugins）
│       ├── src/main.rs         # Tauri 入口
│       ├── src/lib.rs          # Tauri commands 库
│       └── tauri.conf.json     # Tauri 配置（窗口、构建、bundle）
├── docs/
│   └── user-manual.html        # 使用说明书（HTML 格式）
├── scripts/
│   └── sync-manual.sh          # 说明书同步脚本
└── data/
    ├── cases/                   # 案件数据存储（symlink → ~/.openclaw/workspace/data/cases）
    │   └── case_xxx/
    │       └── 案件_名称_日期/
    │           ├── case.json
    │           ├── original/    # 原始上传的 PDF
    │           ├── processed/   # 去水印后的 PDF
    │           ├── md/          # 转换后的 Markdown
    │           ├── evidence/    # 提取的证据清单
    │           └── analysis/    # 分析报告
    └── cache/                   # 缓存（缩略图等）
```

---

## 🔄 核心业务流程（流水线式）

```
步骤0：上传 PDF → original/（保持原始文件名）
  ↓
步骤1：去水印/密码 → original/ → processed/（调用 process_api.py）
  ↓
步骤2：PDF 转 MD → processed/或original/ → md/（调用 pdf_to_md.py）
  ↓
步骤3：案卷分析 → md/ → analysis/（三阶层理论辩护分析）
```

### 关键设计原则
1. **每步自动加载上一步输出**，不手动添加文件
2. **保持文件名延续性**，不用 UUID 替换原文件名
3. **文件夹即案件**，后端扫描文件夹作为单一数据源
4. **待导入文件夹**：用户手动创建的文件夹可导入为合法案件

---

## 🛠️ 开发规范

### 路由结构
```
/                          → HomePage（案件列表，需认证）
/login                     → LoginPage（认证门禁，未登录时自动显示）
/case/:caseId              → CaseDetailPage（案件详情 + 流水线工作流）
/case/:caseId/report       → ReportPage（辩护分析报告 + 批注）
/process                   → ProcessPage（PDF 处理 - 独立向后兼容）
/convert                   → ConvertPage（PDF 转 MD - 独立向后兼容）
/analyze                   → AnalyzePage（案卷分析 - 独立向后兼容）
/settings                  → SettingsPage（设置 + 账号管理）
/manual                    → ManualPage（使用说明书）
```

### 关键前端组件
| 组件 | 说明 |
|------|------|
| `AuthGate` | 认证门禁（App.tsx 中），检查 token 有效性，无效则显示 LoginPage |
| `LoginPage` | 登录页面，邮箱 + 密码，注册链接跳转远程网站 |
| `MacOSLayout` | macOS 风格设计系统（Titlebar、Sidebar、Toolbar、Button、Card、EmptyState） |
| `MacOSDialog` | macOS 风格模态对话框（alert/confirm 封装，通过 `showAlert`/`showConfirm` 调用） |
| `PdfViewer` | 自定义 PDF 渲染组件，使用 pdf.js CDN 动态加载 + canvas 渲染，支持懒加载（IntersectionObserver）和批注覆盖层 |
| `StickyNoteOverlay` | 区域便签批注覆盖层，支持点击创建/编辑/删除彩色便签 |
| `MermaidRenderer` | Mermaid 图表渲染组件 |

### 报告页模板
- 报告页 HTML 模板位于 `backend/templates/report.html`，使用 Jinja2 渲染
- 模板中的 `showPdf` 函数切换 PDF 时会自动清空 iframe src 避免缓存
- 前端 `ReportPage.tsx` 的 PDF 切换依赖 `pdfDoc` 引用变化触发重新渲染

### Vite 代理
开发环境 Vite 将 `/api` 请求代理到 `http://localhost:8080`（FastAPI），无需额外 CORS 配置。生产环境需构建后由 Tauri 壳提供。

### 编码规则
1. **写代码必须使用 qwen3.6-plus**（用户明确要求）
2. **前端构建**：`cd frontend && npm run build`
3. **前端类型检查**：`cd frontend && npx tsc --noEmit`
4. **前端开发服务器**：`cd frontend && npm run dev`（默认端口 5173）
5. **后端启动**：`cd backend && python3 main.py`
6. **Tauri 开发**：`cd frontend && npx tauri dev`（前端 Vite dev + Rust 热重载）
7. **Tauri 构建**：`cd frontend && npx tauri build`（打包三平台桌面应用）
8. **测试 API**：`curl -s http://localhost:8080/api/cases/list | jq '.'`
9. **后端无热重载**：修改后端后需手动重启（`kill` 进程再启动）
10. **用中文注释**：代码注释用中文

### 测试
- **E2E 测试**：`npx playwright test`（配置文件 `tests/playwright.config.ts`）
- 测试用例在 `tests/frontend-render.spec.ts`

### 使用说明书更新
当新增/修改功能后，同步更新说明书：

```bash
# 1. 编辑 docs/user-manual.html（HTML 格式，独立完整文档）
# 2. 同步到前端 public 目录（开发环境由 Vite 服务）
./scripts/sync-manual.sh
```

**源文件**：`docs/user-manual.html`（唯一编辑点）
**分发**：`frontend/public/user-manual.html`（由 sync 脚本复制）
**访问**：侧边栏「工具 → 使用说明书」或 `/manual` 路由

### 文件命名规则
| 阶段 | 输入 | 输出 | 规则 |
|------|------|------|------|
| 上传 | `彭帮生讯问笔录.pdf` | `original/彭帮生讯问笔录.pdf` | 保持原名 |
| 去水印 | `彭帮生讯问笔录.pdf` | `processed/彭帮生讯问笔录_去水印.pdf` | 加 _去水印 后缀 |
| 转 MD | `彭帮生讯问笔录_去水印.pdf` | `md/彭帮生讯问笔录_去水印.md` | 同名换扩展名 |

### API 客户端

`frontend/src/api/index.ts` 提供统一 API 接口：

**认证方法**：
- `login(email, password)` → 调用远程 `/api/login`，返回 `{ token, email }`
- `verifyToken(token)` → 调用远程 `/api/verify`，验证 token 有效性
- `getToken/setToken/clearToken` → localStorage token 读写
- `getAuthEmail/setAuthEmail/clearAuthEmail` → localStorage 邮箱读写

**案件管理、文件处理、分析等本地 API** 通过 `api` 对象统一导出。

### API 设计
- 所有 API 接收 JSON body（使用 Pydantic 模型）或 query params
- 错误返回 `{ "success": false, "error": "..." }` 或 HTTPException
- 成功返回 `{ "success": true, ... }` 或 200 OK + 数据

### 后台 API 路由注册（main.py）

后端按功能模块拆分路由，在 `main.py` 统一注册：
| Router | 前缀 | 功能 |
|--------|------|------|
| `case_router` | `/api/cases/*` | 案件 CRUD、文件扫描、导入、证据提取 |
| `process_router` | `/api/process/*` | PDF 处理（去水印、OCR） |
| `analyzer_router` | `/api/analyze/*` | 案卷分析、证据识别 |
| `stage_router` | `/api/stages/*` | 分析阶段状态管理 |
| `pipeline_router` | `/api/pipeline/*` | 流水线处理 |
| `legal_kb_router` | `/api/legal-kb/*` | 法律知识库 CRUD |
| `bg_task_router` | `/api/tasks/*` | 后台任务（PDF 转 MD 进度） |

### Tauri 桌面客户端（阶段 1 实施中）

Tauri 2.x 桌面壳已搭建，Rust 后端骨架（axum HTTP 服务器 + SQLite + JWT 认证）按计划逐步替换 Python 后端。

**Rust 依赖**：`reqwest`（HTTP 客户端）、`tokio`（异步运行时）、`tauri-plugin-fs/dialog/shell`（系统能力）
**开发模式**：`npx tauri dev` 同时启动 Vite 前端 + Tauri Rust 后端
**构建**：`npx tauri build` 生成 `.app` / `.exe` / `.deb`

### 认证系统

**远程认证服务器**（`criminal-llm-auth/`）：
- 部署地址：`http://118.196.83.43:8000`
- 技术栈：FastAPI + SQLite + JWT（SHA-256 + Salt 密码哈希）
- Token 有效期：720 小时（30 天）
- 页面：首页（`/`）、登录页（`/login`）、注册页（`/register`）
- API：`POST /api/register`、`POST /api/login`、`POST /api/verify`

**桌面客户端认证门禁**：
- `App.tsx` 中的 `AuthGate` 组件：启动时检查 localStorage 中的 token
- 无 token 或验证失败 → 显示 `LoginPage`
- 验证通过 → 显示主应用
- Token 和邮箱存储在 localStorage（`auth_token`、`auth_email`）
- 认证服务器地址在 `api/index.ts` 中硬编码，可通过 `localStorage.auth_server_url` 覆盖（开发调试用）
- 首页 toolbar 显示用户头像（渐变色圆形 + 邮箱首字母）和完整邮箱，点击跳转设置页
- 设置页顶部"账号"卡片：显示当前邮箱 + "退出登录"按钮

**认证服务器管理**：
```bash
# 启动（服务器上）
cd /opt/criminal-llm-auth
source venv/bin/activate
export JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### 后台任务
- PDF 转 MD 使用 `background_tasks.py` 的 `ThreadPoolExecutor(max_workers=1)` 异步执行
- 任务状态持久化到 `~/.openclaw/criminal-llm-tasks.json`，重启可恢复
- 前端轮询 `/api/tasks/{case_id}/convert-status` 获取进度
- 前端刷新页面后自动恢复当前步骤和转换进度（localStorage + 任务状态检查）
- 证据提取支持断点续传：已处理的 MD 文件跳过，部分处理的文件会清理后重新提取
- 转换/提取期间自动阻止 macOS 休眠（`power_manager.py`，使用 `caffeinate -d -i`）

### 证据提取
- **PDF 转 MD 完成后自动触发证据提取**，无需用户手动点击。流程：
  1. 用户点击"转换" → PDF 转 MD
  2. 转换完成 → 自动开始证据提取
  3. 提取完成 → 自动开始分析（如果被告人信息已填写）
- 手动"提取证据"按钮保留，用于重新提取/错误恢复
- 采用 **3 并发提取**（`asyncio.gather` + `Semaphore(3)`）
- 起诉书/起诉意见书优先串行处理（依赖前一步输出）
- 非起诉书文件并发处理，按卷号排序后分配连续证据编号
- 提取时自动识别文书边界，逐笔提取犯罪事实

### 知识库
- **内置**：刑法全文 + 刑诉法全文（`backend/legal_db/`）
- **硬编码**：三阶层理论、证据审查要点（`backend/legal_knowledge.py`）
- **动态**：LLM 联网搜索从 `flk.npc.gov.cn` 获取司法解释

---

## ⚠️ 已知问题

1. **MinerU 配额** — 每日 1000 页免费，超额后需排队；转换超时设为 1 小时（3600 秒）
2. **案件目录嵌套** — 案件目录结构为 `data/cases/{case_id}/案件_名称_日期/`，`find_case_path()` 会扫描子目录匹配

---

## 📌 重要提醒

- **不要修改用户数据**：`data/cases/` 里的案件数据不要随意删除
- **保持向后兼容**：已有的 API 端点不要破坏
- **测试后提交**：修改后确认 `npm run build` 通过
- **用中文注释**：代码注释用中文
