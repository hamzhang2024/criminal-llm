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
| **模型** | deepseek-v4-flash（可在设置页配置） |
| **PDF 引擎** | MinerU / PaddleOCR（可切换，默认 MinerU） |
| **UI 风格** | macOS 原生风格设计系统 |
| **认证服务** | FastAPI + SQLite + JWT（远程部署） |
| **前端地址** | http://localhost:5173 |
| **后端地址** | http://localhost:8080 |
| **认证服务器** | http://118.196.83.43:8000 |
| **项目路径** | D:\criminal-llm-win |
| **认证服务路径** | /opt/criminal-llm-auth/（远程服务器） |

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
│   ├── main.py                 # FastAPI 主入口（路由注册、生命周期、SPA 回退）
│   ├── case_manager.py         # 案件管理 API（CRUD、文件扫描、导入、证据提取）
│   ├── process_api.py          # PDF 处理 API（去水印、OCR）
│   ├── pdf_to_md.py            # PDF → Markdown 转换模块（MinerU 云端）
│   ├── mineru_async.py         # MinerU 异步批量转换（asyncio + Semaphore 并发）
│   ├── paddleocr_async.py      # PaddleOCR 异步批量转换（asyncio + Semaphore 并发）
│   ├── paddleocr_remote.py     # PaddleOCR 远程服务客户端（配额查询 + 转换）
│   ├── analyzer_api.py         # 案卷分析 API（拆分、证据识别）
│   ├── evidence_perdoc.py      # 多笔录案卷按份提取（目录清点→按份提取→确定性校验）
│   ├── evidence_summarizer.py  # 证据详细摘要（8栏目浓缩，供单发分析阶段消费）
│   ├── llm_client.py           # LLM 客户端（百炼 API，超时 600s，3 次重试，并发限流，缓存命中率统计）
│   ├── legal_knowledge.py      # 法律知识库（内置刑法/刑诉法 + 三阶层理论）
│   ├── legal_db/               # 内置法律文件目录
│   │   ├── criminal_law.md     # 刑法全文
│   │   └── criminal_procedure_law.md  # 刑诉法全文
│   ├── background_tasks.py     # 后台任务管理（PDF 转 MD 异步执行 + 进度持久化）
│   ├── power_manager.py        # 电源管理（防止系统休眠中断长时间处理）
│   ├── ocr_acceleration.py     # OCR 加速模块
│   ├── check_md_quality.py     # MD 质量检查工具
│   ├── stage_api.py            # 分析阶段状态管理 API（5阶段 + 5A/5B/53子阶段）
│   ├── pipeline_api.py         # 流水线处理 API
│   ├── analysis_pipeline.py    # 分析流水线编排（步骤 0-5 串联）
│   ├── analysis_engine.py      # 分析引擎（三阶层分析核心）
│   ├── case_splitter.py        # 文书拆分模块
│   ├── config_manager.py       # 配置管理（JSON 持久化，DATA_DIR/criminal-llm-config.json）
│   ├── pdf_processor.py        # PDF 处理核心（水印移除、OCR）
│   ├── legal_search.py         # 法律条文联网搜索
│   ├── legal_kb_api.py         # 法律知识库 CRUD API
│   ├── config.py               # 全局配置（DATA_DIR 解析、HOST/PORT、MAX_FILE_SIZE=500MB）
│   └── watermark_remover.py    # 水印移除模块
├── frontend/
│   ├── vite.config.ts          # Vite 配置（代理 /api → http://localhost:8080）
│   ├── src/
│   │   ├── App.tsx             # 路由入口（含 AuthGate 认证门禁）
│   │   ├── components/
│   │   │   ├── MacOSLayout.tsx   # macOS 风格设计系统
│   │   │   ├── MacOSDialog.tsx   # 模态对话框（showAlert/showConfirm）
│   │   │   ├── PdfViewer.tsx     # PDF 查看器（pdf.js CDN + 懒加载 + 批注）
│   │   │   ├── StickyNoteOverlay.tsx # 区域便签批注
│   │   │   ├── MermaidRenderer.tsx   # Mermaid 图表渲染
│   │   │   └── report/           # 报告页专用组件
│   │   │       ├── ReportRenderer.tsx    # Markdown→HTML 转换 + 证据链接注入
│   │   │       ├── ThreeTierCard.tsx     # 三阶层分析卡片组件
│   │   │       ├── EvidenceContrastTable.tsx # 证据对照表组件
│   │   │       └── SectionSkeleton.tsx   # 报告章节骨架
│   │   ├── api/
│   │   │   ├── index.ts         # API 客户端（含认证 + 证据接口用 fetch 绕过 Tauri invoke）
│   │   │   └── types.ts         # 类型定义
│   │   ├── pages/
│   │   │   ├── HomePage.tsx      # 首页：案件列表（toolbar 显示用户头像+邮箱）
│   │   │   ├── LoginPage.tsx     # 登录页面（认证门禁）
│   │   │   ├── RegisterPage.tsx  # 注册页面（跳转远程认证服务器）
│   │   │   ├── ResetPasswordPage.tsx # 密码重置页面
│   │   │   ├── CaseDetailPage.tsx # 案件详情：流水线处理 + 证据提取 + 刷新按钮
│   │   │   ├── AnalyzePage.tsx   # 案卷分析页面
│   │   │   ├── ReportPage.tsx    # 辩护报告页面（3面板 + 9标签 + 批注 + 编辑）
│   │   │   ├── ConvertPage.tsx   # PDF 转 MD 页面
│   │   │   ├── ProcessPage.tsx   # PDF 处理页面
│   │   │   ├── SettingsPage.tsx  # 设置页面（账号管理 + LLM/MinerU 配置 + 退出登录）
│   │   │   └── ManualPage.tsx    # 使用说明书页面
│   │   └── styles/              # 全局样式
│   └── src-tauri/              # Tauri 桌面壳（Rust，阶段 1 实施中）
│       ├── Cargo.toml          # Rust 依赖（reqwest, tokio, Tauri plugins）
│       ├── src/main.rs         # Tauri 入口
│       ├── src/lib.rs          # Tauri commands 库
│       └── tauri.conf.json     # Tauri 配置（窗口、构建、bundle）
├── docs/
│   └── user-manual.html        # 使用说明书（HTML 格式）
├── scripts/
│   └── sync-manual.sh          # 说明书同步脚本
├── tests/
│   ├── playwright.config.ts    # Playwright 配置（timeout 30s）
│   └── frontend-render.spec.ts # E2E 测试用例
└── data/
    ├── cases/                   # 案件数据存储（symlink → 数据目录/cases）
    │   └── case_xxx/
    │       └── 案件_名称_日期/
    │           ├── case.json
    │           ├── original/    # 原始上传的 PDF
    │           ├── processed/   # 去水印后的 PDF
    │           ├── md/          # 转换后的 Markdown
    │           ├── evidence/    # 提取的证据清单（index.json + md 文件）
    │           └── analysis/    # 分析报告（stage_N/output.md + analysis_state.json）
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

**PDF 转 MD → 证据提取 → 分析全自动串联**：转换完成后自动触发证据提取，提取完成后（若被告人信息已填写）自动启动分析流水线。

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
/register                  → RegisterPage（注册，跳转远程认证服务器）
/reset-password            → ResetPasswordPage（密码重置）
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

### 报告页（ReportPage.tsx）
- 3 面板布局：左侧证据浏览、中间报告内容、右侧对话/修改
- **9 个内容标签**：指控要素、人物关系、事件拆解、法律法规、证据列表、矛盾分析、**控辩对抗**、三阶层分析、完整报告
- 内容来源：5 阶段分析 API（`stage_api.py`）读取 `analysis/stage_N/output.md`
- **控辩对抗标签**（步骤 4.5）：位于矛盾分析与三阶层分析之间，展示红蓝对抗模拟结果（`analysis/04.5-控辩对抗/对抗分析.md`）
- **编辑功能**：顶部工具栏「编辑」按钮进入编辑模式，textarea 直接修改 Markdown 原文，保存到后端磁盘
- **LLM 修改**：右侧「修改报告」标签页通过 LLM 聊天指令修改三阶层报告（调用 `/api/analyze-case/chat/update`）
- 批注功能：支持 PDF 页面批注和内容区域便签批注，保存后自动退出批注模式
- **证据引用超链接**：报告中的"证据NNN"渲染为可点击链接，点击后左栏切换到对应证据（PDF 模式自动切 MD 模式）
- **表格美化**：深蓝表头、隔行变色、hover 高亮、圆角边框、阴影效果
- **全部分析按钮**：步骤 3 面板新增一键执行全部 6 个分析阶段
- 渲染组件：`ReportRenderer.tsx` 负责 Markdown→HTML 转换 + 证据链接注入 + 表格/三阶层卡片/Mermaid 渲染
- **渐进式报告**：步骤 5 拆分为 5a-5f 六个子阶段（案件概述→证据评估→矛盾利用→三阶层辩护→量刑情节→结论建议），每阶段独立保存。报告页轮询 `/api/pipeline/{caseId}/defense-stages` 检测完成状态，已完成的子阶段自动加载并组合展示，带进度条
- **HTML 增强渲染**：`components/report/` 目录下包含 `ReportRenderer`、`ThreeTierCard`、`EvidenceContrastTable`、`SectionSkeleton`，支持 mermaid 内联渲染、矛盾表格红绿高亮、三阶层卡片布局

### 多罪名支持

系统支持多罪名案件。创建案件时可在前端填写多个罪名(如"诈骗罪" + "职务侵占罪"),回车添加。

**证据提取**:
- 提取 prompt 中传入 `当前案件指控罪名`,要求 LLM 为每条证据标注关联的罪名(`- **关联罪名**: [罪名1, 罪名2]`)
- `evidence/index.json` 顶层加 `case_charges`,每条证据加 `charges` 字段

**分析流水线**:
- 共享层(stage_1/2/3/51/52, step1/2/3/4a/4b/4d):案件事实分析,多罪名共用
- 罪名层(stage_4/5, step4c/4.5/5a-5f):每个罪名独立分析,结果存 `analysis/{charge}/`
- 存储路径 `stage_N/output.md` → 罪名层改为 `analysis/{charge}/stage_N/output.md`

**法律知识库**:
- `get_dynamic_legal_knowledge(charges)` 接收罪名列表,对每个罪名独立查法条和司法解释
- `get_cross_charge_comparison(charges)` 生成多罪名构成要件对照表(此罪彼罪辨析用)

### 两套分析系统

项目同时存在两套分析 API，功能有重叠但接口不同：

| 系统 | 文件 | 前缀 | 说明 |
|------|------|------|------|
| **5 阶段分析** | `stage_api.py` | `/api/stage-analysis/*` | 新引擎，支持步骤 1-5 + 子阶段 51/52/53 + 步骤 4.5 控辩对抗 |
| **流水线分析** | `pipeline_api.py` | `/api/pipeline/*` | 旧引擎，步骤 1-5 串联 + 断点续传 + 步骤 4.5 控辩对抗 |

两套系统共享 `analysis_pipeline.py` 中的 `AnalysisPipeline` 类作为核心编排器。

### 分析流水线（含断点续传）

分析流水线支持断点续传：每一步完成后将状态持久化到 `analysis/analysis_state.json`，中断后可从最后完成的步骤继续。`AnalysisPipeline` 提供 `_save_analysis_state()`、`_load_analysis_state()`、`_mark_step_done()`、`_mark_substep_done()`、`_get_next_unfinished_step()`、`_get_resume_summary()` 等方法管理状态。步骤 2/3/4 的子步骤通过文件存在性检查自动跳过已完成的，步骤 5 拆分为 5a-5f 六个子阶段。

**步骤 4.5（控辩对抗）**：位于步骤 4（案件 Wiki）和步骤 5（辩护意见）之间，通过红蓝对抗模拟生成攻防对照表。子步骤：45a 红方指控 → 45b 蓝方辩护 → 45c 红方反驳 → 45d 蓝方回应 → 45e 法官裁决。中间结果保存到 `analysis/04.5-控辩对抗/` 目录。

已有案件（已完成全部 5 步的）点击"继续分析"时，`_get_next_unfinished_step` 会自动发现 4.5 为下一个未完成步骤。

### Vite 代理
开发环境 Vite 将 `/api` 请求代理到 `http://localhost:8080`（FastAPI），无需额外 CORS 配置。生产环境需构建后由 Tauri 壳提供。

### 编码规则
1. **LLM 模型不强制**：可在设置页自由配置（当前默认 deepseek-v4-flash）
2. **前端构建**：`cd frontend && npm run build`
3. **前端类型检查**：`cd frontend && npx tsc --noEmit`
4. **前端开发服务器**：`cd frontend && npm run dev`（默认端口 5173，host 0.0.0.0）
5. **后端启动**：`cd backend && python main.py`（Windows）或 `python3 main.py`（macOS/Linux，默认 http://127.0.0.1:8080）
6. **Tauri 开发**：`cd frontend && npx tauri dev`（前端 Vite dev + Rust 热重载）
7. **Tauri 构建**：`cd frontend && npx tauri build`（打包三平台桌面应用）
8. **测试 API**：`curl -s http://localhost:8080/api/cases/list | jq '.'`
9. **后端无热重载**：修改后端后需手动重启（Windows: `taskkill /F /IM python.exe` 或关闭终端；macOS: `pkill -f 'uvicorn main:app'`）
10. **用中文注释**：代码注释用中文
11. **版本号管理**：`cd frontend && python3 bump-version.py`（自动递增 package.json + tauri.conf.json 版本号）
12. **图标生成**：`cd frontend && python3 generate-icon.py`（从桌面图片生成多尺寸图标 + icns）
13. **发布流程**：`git tag v1.x.x && git push origin --tags`（触发 GitHub Actions 多平台构建）

### Vite 代理配置
开发环境 `vite.config.ts` 将 `/api` 请求代理到 `http://localhost:8080`：
```ts
proxy: { '/api': { target: 'http://localhost:8080', changeOrigin: true } }
```
生产环境构建后由 Tauri 壳提供后端服务（Rust 启动内嵌的 Python/FastAPI）。

### 测试
- **E2E 测试**：`npx playwright test`（配置文件 `tests/playwright.config.ts`，timeout 30s）
- **运行单个测试**：`npx playwright test frontend-render.spec.ts`
- **运行单个用例**：`npx playwright test -g "具体测试名称"`
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

### 配置文件（config_manager.py）
存储于 `DATA_DIR/criminal-llm-config.json`，字段：
| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `mineru_token` | string | - | MinerU API Token |
| `paddleocr_token` | string | - | PaddleOCR API Token |
| `pdf_engine` | string | `mineru` | PDF 引擎：`mineru` 或 `paddleocr` |
| `llm_api_key` | string | - | LLM API Key |
| `llm_base_url` | string | 百炼 URL | LLM 服务地址 |
| `llm_model` | string | `deepseek-v4-flash` | 模型名称（设置页可改） |
| `evidence_concurrency` | int | `3` | 证据提取并发数（1-50） |

**config.py 全局常量**：
- `DATA_DIR`：优先级 `CRIMINAL_LLM_DATA_DIR` 环境变量 → 平台特定默认目录
  - **macOS**：`~/Documents/.criminal-llm-data/`
  - **Windows**：安装目录下 `data/` 文件夹（用户反馈要求）
  - **Linux**：`~/.criminal-llm-data/`
- `HOST` = `127.0.0.1`, `PORT` = `8080`
- `MAX_FILE_SIZE` = 500MB
- `THUMBNAIL_WIDTH` = 400px, `THUMBNAIL_DPI` = 150

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
| `stage_router` | `/api/stage-analysis/*` | 5 阶段分析（运行、进度、结果、Markdown 读写） |
| `pipeline_router` | `/api/pipeline/*` | 流水线处理（含断点续传 `/resume`、状态查询 `/analysis-state`、防御阶段 `/defense-stages`、`/defense-stage/{name}`） |
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

### 打包与部署

#### CI 自动构建
- `.github/workflows/release.yml`：推送 `v*` 标签或手动触发，GitHub Actions 在三台 runner 上并行构建（Windows + macOS Intel + macOS Apple Silicon），产物自动发布 GitHub Release
- 触发：`git tag v1.0.10 && git push origin --tags` 或在 GitHub Actions 页面手动 Run workflow

#### 本地打包
1. **PyInstaller 打包后端**：`cd backend && pyinstaller criminal-llm.spec --noconfirm`
2. **前端构建**：`cd frontend && npm run build`
3. **Tauri 构建**：`cd frontend && npx tauri build`
   - DMG 打包有中文文件名兼容问题，需用 `hdiutil` 手动创建
4. **清理缓存**：重新打包前清理 `backend/__pycache__/` 和 `backend/build/`

#### 服务器部署
- 下载地址：`root@118.196.83.43:/opt/criminal-llm-auth/data/uploads/`
- 上传 DMG：`scp <dmg-file> root@118.196.83.43:/opt/criminal-llm-auth/data/uploads/`
- 服务管理：认证服务在 `/opt/criminal-llm-auth/`，用 `nohup` 后台运行

### 证据提取关键点
- 证据清单在 `evidence/index.json`，每条记录有 `md_file` 字段指向实际文件
- **后期添加的文件**：index.json 中的 `md_file` 可能带有数字前缀（如 `104_xxx.md`），但实际文件名可能没有前缀
- `_load_evidence_texts()` 会 fallback 匹配无前缀的文件名
- **DeepSeek 缓存命中率优化**：请求采用 system（短角色）+ assistant（固定提取规则）+ user（文件内容）三层结构，固定前缀完全一致，变化的文件内容放在最后，最大化 prompt cache 命中率。每次请求后打印缓存命中率统计
- **重试分层**：`llm_client` 负责网络层重试（3 次，5s→10s→15s），`_extract_single_file_with_tracking` 负责业务层重试（2 次，10s→20s），两者不重叠。网络层重试耗尽后抛出 `LLMRetryExhaustedError`，上层识别后不再重复重试
- **信号量释放**：重试等待在信号量之外执行，其他文件不阻塞
- **卡死检测**：3 分钟无进展自动取消，10 秒轮询，LLM 调用成功后立即更新心跳
- **详细摘要层**：提取完成后自动生成 8 栏目浓缩摘要（`evidence_summarizer.py`），写入 index.json 的 `digest` 字段 + `evidence/summaries/` 缓存；阶段3时间线/阶段52矛盾分析通过 `_load_evidence_texts(prefer_summary=True)` 消费摘要，分批阶段保持全文。起诉书跳过摘要保留原文全文。前端证据预览（报告页 + 案件详情页）支持「摘要 | 全文」切换

### 后台任务
- PDF 转 MD 使用 `background_tasks.py` 的 `ThreadPoolExecutor(max_workers=10)` 异步并发执行
- 任务状态持久化到 `DATA_DIR/criminal-llm-tasks.json`，重启可恢复
- **双引擎支持**：根据 `pdf_engine` 配置选择 `MinerU` 或 `PaddleOCR`
- MinerU 转换失败自动重试 3 次（15s→30s 退避），全部转换完成后对失败文件再统一重试一轮
- 前端轮询 `/api/tasks/{case_id}/convert-status` 获取进度
- 前端刷新页面后自动恢复当前步骤和转换进度（localStorage + 任务状态检查）
- 证据提取支持断点续传：已处理的 MD 文件跳过，部分处理的文件会清理后重新提取
- 转换/提取期间自动阻止系统休眠（`power_manager.py`）

### 证据提取（详细）
- **PDF 转 MD 完成后自动触发证据提取**，无需用户手动点击。流程：
  1. 用户点击"转换" → PDF 转 MD
  2. 转换完成 → 自动开始证据提取
  3. 提取完成 → 自动开始分析（如果被告人信息已填写）
- 手动"提取证据"按钮保留，用于重新提取/错误恢复
- **"刷新证据"按钮**：提取完成后若轮询失败但后台可能已完成，用户可手动刷新检查
- **轮询容错**：连续 3 次请求失败才停止轮询（非单次失败即停止）
- **并发数可配置**：默认 3，用户可在设置页面调整为 1-50。配置存储在 `DATA_DIR/criminal-llm-config.json` 的 `evidence_concurrency` 字段
- **固定并发 + 内置重试**：`llm_client` 3 次指数退避重试（5s→10s→15s），上层 2 次业务重试（10s→20s），无自适应并发
- 起诉书/起诉意见书优先串行处理（依赖前一步输出）
- 非起诉书文件并发处理，按卷号排序后分配连续证据编号
- 提取时自动识别文书边界，逐笔提取犯罪事实
- **解析容错**：优先尝试 JSON 数组解析，失败后回退到文本标记解析
- **证据接口**：`getEvidenceIndex` 和 `getExtractStatus` 使用 `fetch` 直接调用后端 API，不经过 Tauri invoke（避免参数名 `caseId` vs `case_id` 不匹配问题）

### 知识库
- **内置**：刑法全文 + 刑诉法全文（`backend/legal_db/`）
- **硬编码**：三阶层理论、证据审查要点（`backend/legal_knowledge.py`）
- **动态**：LLM 联网搜索从 `flk.npc.gov.cn` 获取司法解释

---

## ⚠️ 已知问题

1. **MinerU 配额** — 每日 1000 页免费，超额后需排队；转换超时设为 1 小时（3600 秒）
2. **DMG 打包** — `bundle_dmg.sh` 对中文文件名有兼容问题，需用 `hdiutil create` 手动创建
3. ~~**证据提取卡死**~~ — 已修复：600s 超时保护 + 2 次重试 + 3 分钟无进展自动取消
4. **PaddleOCR 配额** — 每日 1000 页免费，通过 `/api/config` 返回 `paddleocr_quota` 状态

---

## 📌 重要提醒

- **不要修改用户数据**：`data/cases/` 里的案件数据不要随意删除
- **数据目录**：
  - macOS：`~/Documents/.criminal-llm-data/`
  - Windows：安装目录下 `data/` 文件夹（用户反馈要求）
  - 环境变量 `CRIMINAL_LLM_DATA_DIR` 可覆盖默认位置
- **保持向后兼容**：已有的 API 端点不要破坏
- **测试后提交**：修改后确认 `npm run build` 通过
- **用中文注释**：代码注释用中文

## 🛠️ 工具脚本

| 脚本 | 路径 | 功能 |
|------|------|------|
| 版本号递增 | `frontend/bump-version.py` | 自动递增 package.json + tauri.conf.json 版本号 |
| 图标生成 | `frontend/generate-icon.py` | 从桌面图片裁剪生成多尺寸 PNG + icns |
| 说明书同步 | `scripts/sync-manual.sh` | 同步 docs/user-manual.html 到前端 public 目录 |
