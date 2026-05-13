# Criminal PDF Desktop App - 桌面级应用开发手册

> 从 WebUI 到完整桌面工作流：去水印 → 拆分 → PDF转MD → 案卷分析

---

## 一、项目概述

### 1.1 目标

将现有 WebUI（FastAPI + React）重构为**原生桌面应用**，实现刑事案卷处理的**一站式工作流**。

### 1.2 核心工作流

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  ①去水印  │───→│  ②拆分   │───→│ ③PDF转MD │───→│ ④案卷分析 │
│ Watermark │    │  Split   │    │  MD Conv │    │  Analyze │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
     │               │               │               │
  PDF输入      按文书类型分       2方案转换      三阶层辩护分析
  批量处理     可视化拖拽       API/CLI切换    对话式追问
```

### 1.3 用户故事

```
张律师收到一箱纸质案卷扫描件（带水印的 500 页 PDF）
  ↓
① 导入 PDF → 一键去水印 → 得到干净的 PDF
  ↓
② 可视化拆分 → AI 识别文书类型 → 拖拽调整 → 拆分成多个独立文件
  ↓
③ 全部转为 Markdown → 自动缓存 → 下次秒读
  ↓
④ 选择拆分后的证据 → 输入辩护对象 → AI 生成辩护分析报告
  ↓
导出 HTML 报告 → 打印/存档/提交
```

---

## 二、技术选型

### 2.1 方案对比

| 方案 | 开发量 | 体积 | 性能 | 原生体验 | 推荐度 |
|------|--------|------|------|----------|--------|
| **Tauri v2** | 中等 | ~15MB | ⚡极快 | ✅ 好 | ⭐⭐⭐⭐⭐ |
| Electron | 最小 | ~150MB | 一般 | ✅ 好 | ⭐⭐⭐ |
| Native (Swift) | 最大 | ~5MB | ⚡最快 | ✅ 最佳 | ⭐⭐ |
| PyInstaller + PyQt | 大 | ~200MB | 快 | 一般 | ⭐⭐⭐ |

### 2.2 推荐方案：Tauri v2

**理由：**
- ✅ 体积极小（~15MB vs Electron 150MB）
- ✅ 内存占用低（~50MB vs Electron 200MB+）
- ✅ 前端代码 90% 可复用（React + TypeScript）
- ✅ 后端用 Rust（性能）+ 调用 Python 脚本（现有逻辑）
- ✅ macOS 原生窗口、通知、文件系统访问

**架构：**
```
┌─────────────────────────────────────┐
│         Tauri 窗口层 (Rust)          │
│  ├─ 原生菜单栏                        │
│  ├─ 拖拽文件到窗口                    │
│  ├─ 系统通知                         │
│  └─ 本地文件系统访问                  │
├─────────────────────────────────────┤
│         前端 UI (React/TS)           │
│  ├─ 现有组件 90% 复用                 │
│  ├─ Tauri API 替代 fetch 调用         │
│  └─ 路由改为标签页模式               │
├─────────────────────────────────────┤
│         后端逻辑                     │
│  ├─ Python 子进程调用（现有模块）     │
│  ├─ Rust FFI 高性能操作（可选）       │
│  └─ 本地 SQLite 数据库（案件状态）    │
└─────────────────────────────────────┘
```

### 2.3 LLM 配置策略 ⭐重要

**原则：LLM 选择权完全交给用户，参考 Cherry Studio 的多供应商管理模式。**

| 场景 | LLM 配置 | 说明 |
|------|----------|------|
| **开发测试** | 本地 OpenClaw Gateway | 零成本，快速迭代 |
| **生产发布** | 用户自行配置 API | 灵活选择，按需付费 |

**Cherry Studio 风格的多供应商管理：**
```
┌─────────────────────────────────────────────┐
│  🤖 AI 模型管理（Cherry Studio 风格）         │
├─────────────────────────────────────────────┤
│                                             │
│  已添加的供应商：                             │
│  ┌───────────────────────────────────────┐  │
│  │ 🏢 百炼 DashScope              [编辑] │  │
│  │    模型: qwen-plus, qwen-max...      │  │
│  │    状态: ✅ 已启用                    │  │
│  ├───────────────────────────────────────┤  │
│  │ 🏢 智谱 AI                     [编辑] │  │
│  │    模型: glm-4-plus, glm-4...        │  │
│  │    状态: ✅ 已启用                    │  │
│  ├───────────────────────────────────────┤  │
│  │ 🏢 OpenAI                     [编辑] │  │
│  │    模型: gpt-4o, gpt-4o-mini...      │  │
│  │    状态: ❌ API Key 未配置            │  │
│  ├───────────────────────────────────────┤  │
│  │ 🏢 本地 Gateway                [编辑] │  │
│  │    模型: auto (由 Gateway 路由)       │  │
│  │    状态: ✅ 已启用 (localhost)        │  │
│  └───────────────────────────────────────┘  │
│                                             │
│  [+ 添加供应商]                              │
│                                             │
│  常用供应商预设：                             │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐          │
│  │ 百炼│ │ 智谱│ │OpenAI│ │本地 │          │
│  └─────┘ └─────┘ └─────┘ └─────┘          │
│                                             │
└─────────────────────────────────────────────┘
```

**添加供应商流程：**
```
1. 选择预设模板（或自定义）
2. 填写 API Key
3. 点击「测试连接」验证
4. 自动获取可用模型列表
5. 选择默认使用的模型
```

**支持的预设供应商：**
```json
{
  "presets": {
    "bailian": {
      "name": "百炼 DashScope",
      "icon": "🏢",
      "baseURL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "models": ["qwen-plus", "qwen-max", "qwen-turbo"],
      "recommended": "qwen-plus",
      "docUrl": "https://help.aliyun.com/zh/model-studio/"
    },
    "zhipu": {
      "name": "智谱 AI",
      "icon": "🧠",
      "baseURL": "https://open.bigmodel.cn/api/paas/v4",
      "models": ["glm-4-plus", "glm-4", "glm-4-flash"],
      "recommended": "glm-4-plus",
      "docUrl": "https://open.bigmodel.cn/dev/api"
    },
    "openai": {
      "name": "OpenAI",
      "icon": "🔵",
      "baseURL": "https://api.openai.com/v1",
      "models": ["gpt-4o", "gpt-4o-mini", "o1"],
      "recommended": "gpt-4o",
      "docUrl": "https://platform.openai.com/docs"
    },
    "local-gateway": {
      "name": "本地 Gateway",
      "icon": "🏠",
      "baseURL": "http://localhost:18789/v1",
      "models": ["auto"],
      "recommended": "auto",
      "docUrl": null
    },
    "ollama": {
      "name": "Ollama",
      "icon": "🦙",
      "baseURL": "http://localhost:11434/v1",
      "models": [],  // 自动从 /api/tags 获取
      "recommended": "qwen2.5:32b",
      "docUrl": "https://ollama.com"
    },
    "mlxs": {
      "name": "MLX (Apple Silicon)",
      "icon": "🍎",
      "baseURL": "http://localhost:8080/v1",
      "models": [],
      "recommended": "qwen2.5-32b-mlx",
      "docUrl": "https://ml-explore.github.io/mlx/"
    }
  }
}
```

**分析时模型选择（对话界面）：**
```
┌─────────────────────────────────────────────┐
│  📝 案卷分析                                 │
│                                              │
│  供应商: [百炼 DashScope ▼]                  │
│  模型:   [qwen-plus ▼]  [切换]               │
│          └─ qwen-plus (推荐)                 │
│          └─ qwen-max (更智能)                 │
│          └─ qwen-turbo (更快)                │
│                                              │
│  辩护对象: [故意伤害罪 _______________]       │
│  [开始分析]                                   │
└─────────────────────────────────────────────┘
```

### 2.4 PDF→MD 转换策略 ⭐重要

**原则：优先调用 MinerU 云端 API，本地 CLI 作为备选。**

| 优先级 | 方式 | 质量 | 速度 | 依赖 | 适用场景 |
|--------|------|------|------|------|----------|
| 1️⃣ | **MinerU API** | ⭐⭐⭐⭐⭐ | ~1 分钟 | `MINERU_TOKEN` | 首选，免费额度足够 |
| 2️⃣ | **MinerU CLI（本地 GPU）** | ⭐⭐⭐⭐⭐ | ~10-15 秒 | CUDA/MPS + 21GB 模型 | 有显卡，完全离线 |
| 3️⃣ | **MinerU CLI（本地 CPU）** | ⭐⭐⭐⭐⭐ | ~30-60 秒 | 21GB 模型 | 备选，无显卡 |

**为什么保留两个方案：**
- **API 首选**：速度快、免模型、零维护、日常使用足够
- **CLI 备选**：完全离线、敏感案件不外传、大批量处理

**MinerU CLI GPU 加速：**
```
设备选择（magic-pdf.json）:
  "device-mode": "cpu"   → CPU 推理，~30-60 秒/文件
  "device-mode": "cuda"  → NVIDIA GPU，~10-15 秒/文件（快 3-4 倍）
  "device-mode": "mps"   → Apple Silicon GPU，~15-20 秒/文件（快 2-3 倍）

硬件要求:
  NVIDIA: CUDA 11.8+, 显存 ≥ 8GB（推荐 12GB+）
  Apple: M1/M2/M3/M4 芯片（自动启用 MPS）
  AMD: 不支持（需使用 CPU 模式）

自动检测:
  首次启用 CLI 时，自动检测可用设备
  优先选择: CUDA > MPS > CPU
  用户可手动覆盖选择
```

**MinerU API 配置：**
```typescript
interface MinerUConfig {
  token: string;              // 用户 Token（免费申请）
  modelVersion: 'pipeline' | 'vlm';  // pipeline 适合法律文书
  enableTable: boolean;       // 默认 true
  enableFormula: boolean;     // 默认 false（法律文书少见公式）
  language: 'ch' | 'en';      // 默认 ch
}
```

**本地 CLI 配置（备选）：**
```typescript
interface MinerUCLIConfig {
  enabled: boolean;           // 用户手动开启
  modelsPath: string;         // ~/mineru-models/
  layoutModel: string;        // doclayout_yolo
  deviceMode: 'auto' | 'cpu' | 'cuda' | 'mps';  // 自动检测或手动指定
}

**本地 CLI 作为高级选项（可选）：**
- 仅面向有技术能力的用户
- 需要手动放置模型到 `~/mineru-models/`
- 完全离线，适合大批量/敏感场景
- 在设置中作为「高级功能」隐藏入口

---

## 三、现有资产清单

### 3.1 可直接复用

| 模块 | 文件 | 行数 | 复用方式 |
|------|------|------|----------|
| **去水印** | `backend/pdf_processor.py` | 227 | Python 子进程调用 |
| **拆分引擎** | `backend/case_splitter.py` | 361 | Python 子进程调用 |
| **PDF→MD** | `backend/pdf_to_md.py` | 385 | Python 子进程调用 + 新增 MinerU CLI |
| **案卷分析** | `backend/analyzer_api.py` | 1424 | Python 子进程调用 |
| **LLM 客户端** | `backend/llm_client.py` | 594 | Python 子进程调用 |
| **法律知识库** | `backend/legal_knowledge.py` | 545 | Python 子进程调用 |
| **前端组件** | `frontend/src/components/` | ~1500 | 90% 直接复用 |
| **拆分页面** | `frontend/src/components/` | 包含 Timeline, PageGrid 等 | 复用 |
| **分析页面** | `frontend/src/pages/AnalyzePage.tsx` | 1993 | 大幅复用 |
| **处理页面** | `frontend/src/pages/ProcessPage.tsx` | 575 | 复用 + 改造 |

**总计：~6000 行代码可复用，开发量减少 70%**

### 3.2 需要新建

| 模块 | 说明 | 预计行数 |
|------|------|----------|
| **Tauri 骨架** | Rust 主程序、窗口配置、菜单 | ~300 |
| **工作流引擎** | 串联 4 个步骤的状态机 | ~500 |
| **进度追踪** | 实时进度条 + 状态通知 | ~400 |
| **本地数据库** | SQLite 案件记录 + 工作流状态 | ~300 |
| **工作流 UI** | 引导式步骤界面 | ~800 |
| **拖拽导入** | 文件拖拽 + 批量选择 | ~200 |
| **配置管理** | 模型路径、API Key 等 | ~150 |

**总计：~2650 行新增代码**

---

## 四、详细开发计划

### 4.1 阶段一：基础骨架（1-2 周）

#### 目标
搭建 Tauri 应用框架，运行现有前端代码。

#### 任务清单

```
✅ 1. 初始化 Tauri v2 项目
   - 安装 Rust 工具链
   - npx create-tauri-app
   - 配置 tauri.conf.json

✅ 2. 迁移前端代码
   - 复制 frontend/src/ 到 src-tauri/frontend/
   - 修改构建配置指向现有 React 项目
   - 确保开发模式正常运行

✅ 3. 建立通信层
   - 定义 Tauri Commands（替代 HTTP API）
   - 实现 invoke 调用模式
   - 测试前后端通信

✅ 4. 原生功能
   - 文件拖拽到窗口
   - macOS 菜单栏
   - 系统通知（处理完成提醒）
   - 原生文件选择对话框
```

#### 关键代码结构

```
criminal-pdf-app/
├── src-tauri/                    # Rust 后端
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   ├── src/
│   │   ├── main.rs              # 入口
│   │   ├── commands/
│   │   │   ├── mod.rs
│   │   │   ├── watermark.rs     # 去水印命令
│   │   │   ├── split.rs         # 拆分命令
│   │   │   ├── pdf2md.rs        # PDF转MD命令
│   │   │   └── analyze.rs       # 案卷分析命令
│   │   ├── workflow/
│   │   │   ├── mod.rs
│   │   │   ├── engine.rs        # 工作流引擎
│   │   │   └── state.rs         # 状态管理
│   │   ├── db/
│   │   │   ├── mod.rs
│   │   │   └── sqlite.rs        # 本地数据库
│   │   └── python/
│   │       ├── mod.rs
│   │       └── runner.rs        # Python 子进程管理
│   └── assets/                   # 图标等资源
│
├── src/                          # React 前端（现有代码迁移）
│   ├── App.tsx                   # 改为标签页模式
│   ├── components/               # 复用现有组件
│   ├── pages/
│   │   ├── WorkflowPage.tsx      # 新建：工作流引导页
│   │   ├── WatermarkPage.tsx     # 改造：去水印
│   │   ├── SplitPage.tsx         # 改造：拆分
│   │   ├── ConvertPage.tsx       # 新建：PDF转MD
│   │   └── AnalyzePage.tsx       # 复用：案卷分析
│   ├── hooks/
│   │   ├── useWorkflow.ts        # 工作流状态
│   │   └── useTauri.ts           # Tauri 通信
│   └── types/
│       └── workflow.ts           # 类型定义
│
└── python-backend/               # 现有 Python 代码
    ├── pdf_processor.py
    ├── case_splitter.py
    ├── pdf_to_md.py
    ├── analyzer_api.py
    ├── llm_client.py
    └── legal_knowledge.py
```

### 4.2 阶段二：去水印模块（3-5 天）

#### 现有逻辑
`backend/pdf_processor.py` 已实现：
- 基于 XObject 的水印图层识别
- 批量处理
- 密码支持

#### 桌面化改造

```typescript
// 前端：WatermarkPage.tsx（改造 ProcessPage.tsx）
interface WatermarkJob {
  id: string;
  files: string[];       // 本地文件路径
  removeWatermark: boolean;
  addOCR: boolean;       // OCR 三明治
  password?: string;
  progress: number;      // 0-100
  status: 'pending' | 'running' | 'done' | 'error';
}

// Rust Command
#[tauri::command]
async fn remove_watermark(
  files: Vec<String>,
  password: Option<String>,
  app: AppHandle,
) -> Result<WatermarkResult, String> {
  // 调用 Python 子进程
  let output = run_python_script(
    "pdf_processor.py",
    "remove_watermarks",
    json!({ "files": files, "password": password })
  ).await?;
  
  Ok(WatermarkResult::from(output))
}
```

#### 新增功能
- **拖拽导入**：直接把 PDF 文件拖到窗口
- **实时进度**：每处理一个文件更新进度条
- **预览对比**：去水印前后对比查看
- **批量队列**：多个文件排队处理

### 4.3 阶段三：拆分模块（1 周）

#### 现有逻辑
`backend/case_splitter.py` 已实现：
- 智能文书类型识别（基于文件名前缀 + AI）
- 按《刑事诉讼法》证据类型分类
- 文件名前缀分割

#### 桌面化改造

```typescript
// SplitPage.tsx（基本复用现有 Timeline 组件）
interface SplitJob {
  id: string;
  inputFile: string;       // 源 PDF 路径
  pages: PageData[];       // 页面缩略图 + AI 分析
  splitPlan: SplitPlan;    // 分割方案
  progress: number;
}

// AI 分析页面类型（本地调用 LLM）
async function analyzePages(filePaths: string[]): Promise<SplitPlan> {
  return await invoke('split_analyze', {
    files: filePaths,
    // 本地 LLM 调用
    model: 'qwen3.5-plus'
  });
}
```

#### 新增功能
- **可视化时间轴**：复用现有 Timeline 组件
- **缩略图预览**：复用 PageGrid 组件
- **拖拽调整**：在时间轴上拖拽分割点
- **AI 建议**：调用 LLM 智能分组（本地 Gateway）

### 4.4 阶段四：PDF→MD 转换（1 周）⭐核心

#### 4.4.1 转换引擎架构

```
┌───────────────────────────────────────────────┐
│           PDF → MD 转换引擎                    │
├───────────────────────────────────────────────┤
│                                               │
│  优先级 1：已有 MD 缓存                         │
│  ├─ 检查 .md 文件存在                          │
│  ├─ 检查 mtime >= PDF mtime                   │
│  └─ 直接返回（< 0.1s）                        │
│                                               │
│  优先级 2：MinerU API（云端）⭐首选              │
│  ├─ mineru.net/api/v4                         │
│  ├─ 需要 MINERU_TOKEN（用户免费申请）           │
│  ├─ 模型版本: pipeline（适合法律文书）          │
│  ├─ 速度: ~1 分钟/文件                         │
│  └─ 输出: 高质量 Markdown（保留表格/公式）      │
│                                               │
│  优先级 3：MinerU CLI（本地）⚙️备选              │
│  ├─ magic_pdf_compat.py                       │
│  ├─ 需要用户手动配置模型路径                    │
│  ├─ 模型: ~/mineru-models/ (21GB)             │
│  ├─ 速度: ~30 秒/文件（CPU）                   │
│  └─ 适用：离线/敏感案件/大批量                  │
│                                               │
│  ❌ 失败处理：提示用户检查配置                  │
│  ├─ API 失败：检查 Token 是否有效               │
│  └─ CLI 失败：检查模型路径是否正确              │
│                                               │
└───────────────────────────────────────────────
```

#### 4.4.2 MinerU API 集成（首选）

```python
# pdf_to_md.py - 默认使用 MinerU API
def _mineru_api_convert(pdf_path: Path, output_dir: Path) -> Optional[str]:
    """使用 MinerU 云端 API 转换（最高质量，推荐）"""
    token = _get_mineru_token()
    if not token:
        return None

    stem = pdf_path.stem

    try:
        # 1. 获取上传链接
        resp = requests.post(
            f"{MINERU_API}/file-urls/batch",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "files": [{"name": pdf_path.name, "data_id": stem}],
                "model_version": "pipeline",  # 法律文书用 pipeline 足够
                "enable_formula": False,
                "enable_table": True,
                "language": "ch",
            },
            timeout=30,
        )
        result = resp.json()
        if result.get("code") != 0:
            return None

        batch_id = result["data"]["batch_id"]
        upload_url = result["data"]["file_urls"][0]

        # 2. 上传
        with open(pdf_path, "rb") as f:
            r = requests.put(upload_url, data=f.read(), timeout=120)
        if r.status_code not in (200, 203):
            return None

        # 3. 轮询等待（云端 GPU 通常 30-60 秒）
        waited = 0
        while waited < 300:
            r = requests.get(
                f"{MINERU_API}/extract-results/batch/{batch_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            data = r.json().get("data", {})
            results = data.get("extract_result", [])
            if not results:
                time.sleep(5); waited += 5; continue

            state = results[0].get("state")
            if state == "done":
                # 4. 下载解压
                zip_path = output_dir / f"{stem}.zip"
                zip_path.write_bytes(requests.get(results[0]["full_zip_url"], timeout=120).content)
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(output_dir)
                zip_path.unlink()

                full_md = output_dir / "full.md"
                target_md = output_dir / f"{stem}.md"
                if full_md.exists():
                    full_md.rename(target_md)

                if target_md.exists() and target_md.stat().st_size > 100:
                    text = target_md.read_text(encoding="utf-8")
                    # 清理中间文件
                    for f in output_dir.iterdir():
                        if f.name == "images" and f.is_dir():
                            shutil.rmtree(f, ignore_errors=True)
                        elif f.suffix == ".json":
                            f.unlink()
                    return text
                return None
            elif state == "failed":
                return None

            time.sleep(5); waited += 5

        return None  # 超时

    except Exception:
        return None
```

#### 4.4.3 MinerU CLI 集成（备选方案）

```python
# pdf_to_md.py - 备选方案
def _detect_device() -> str:
    """自动检测最佳计算设备"""
    # 1. 检测 NVIDIA GPU
    try:
        import torch
        if torch.cuda.is_available():
            return 'cuda'
    except ImportError:
        pass
    
    # 2. 检测 Apple Silicon
    import platform
    if platform.system() == 'Darwin':
        chip = platform.machine()
        if chip in ('arm64', 'arm64e'):  # M1/M2/M3/M4
            return 'mps'
    
    # 3. 默认 CPU
    return 'cpu'

def _mineru_cli_convert(
    pdf_path: Path,
    output_dir: Path,
    device: str = 'auto'
) -> Optional[str]:
    """使用本地 MinerU CLI 转换（备选，完全离线）"""
    # 检查用户是否手动配置了本地模型
    models_dir = Path.home() / "mineru-models"
    if not models_dir.exists():
        return None  # 未配置本地模型，跳过
    
    # 自动检测设备
    if device == 'auto':
        device = _detect_device()
    
    # 更新配置文件中的设备
    config_path = Path.home() / "magic-pdf.json"
    if config_path.exists():
        import json
        with open(config_path) as f:
            config = json.load(f)
        config['device-mode'] = device
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
    
    compat_script = Path(__file__).parent / "scripts" / "magic_pdf_compat.py"
    if not compat_script.exists():
        return None
    
    import subprocess
    # 根据设备类型设置超时时间
    timeout = {
        'cuda': 180,   # GPU 快，超时短
        'mps': 240,    # Apple GPU 中等
        'cpu': 300     # CPU 慢，超时长
    }.get(device, 300)
    
    result = subprocess.run([
        sys.executable, str(compat_script),
        "-p", str(pdf_path),
        "-o", str(output_dir),
        "-m", "ocr"
    ], capture_output=True, text=True, timeout=timeout)
    
    if result.returncode != 0:
        return None
    
    stem = pdf_path.stem
    md_candidates = list(output_dir.glob(f"**/{stem}.md"))
    if md_candidates:
        return md_candidates[0].read_text(encoding="utf-8")
    return None
```

#### 4.4.4 批量转换界面

```typescript
// ConvertPage.tsx（新建）
interface ConvertJob {
  id: string;
  files: string[];       // 待转换的 PDF
  method: 'auto' | 'api' | 'cli';  // auto=优先API，失败尝试CLI
  results: ConvertResult[];
}

interface ConvertResult {
  pdf: string;
  md?: string;           // 输出 MD 路径
  method: 'api' | 'cli'; // 实际使用的方法
  size: number;          // MD 文件大小
  duration: number;      // 转换耗时（秒）
  status: 'success' | 'failed';
  error?: string;
}
```

#### 4.4.5 新增功能
- **批量转换**：选择多个文件/目录，一键全部转换
- **转换方法选择**：自动（API→CLI）/ 仅 API / 仅 CLI
- **缓存管理**：查看/清理 MD 缓存文件
- **质量报告**：转换质量评分、表格识别率
- **进度追踪**：实时显示每个文件的转换状态
- **失败自动切换**：API 失败时自动尝试本地 CLI（如果已配置）

### 4.5 阶段五：案卷分析（1 周）

#### 现有逻辑
- `backend/analyzer_api.py`：1424 行，完整的分析引擎
- `backend/llm_client.py`：594 行，LLM 调用
- `backend/legal_knowledge.py`：545 行，法律知识库
- 三阶层理论 + 构成要件拆解

#### 桌面化改造

```typescript
// AnalyzePage.tsx（大幅复用，约 80% 代码不变）
interface AnalysisJob {
  id: string;
  caseDir: string;         // 案卷目录
  evidenceFiles: string[]; // 选择的证据（MD 文件）
  defenseTarget: string;   // 辩护对象
  report: AnalysisReport;  // 分析报告
  chatHistory: ChatMsg[];  // 对话历史
}

// 桌面版特有功能
async function analyzeWithMarkdown(evidenceMdFiles: string[]) {
  return await invoke('case_analyze', {
    evidence_files: evidenceMdFiles,
    defense_target: defenseTarget,
    // 使用本地 LLM Gateway
    model: 'qwen3.5-plus'
  });
}
```

#### 新增功能
- **MD 证据直读**：直接读取 `.md` 文件，无需再次转换
- **证据组合**：选择多个证据组合分析
- **报告导出**：HTML/PDF/Markdown 导出
- **对话历史**：本地保存所有对话记录
- **批量分析**：多个案件批量分析

### 4.6 阶段六：工作流引擎（1 周）⭐核心

#### 4.6.1 工作流状态机

```typescript
// 工作流定义
interface Workflow {
  id: string;
  name: string;
  createdAt: string;
  steps: WorkflowStep[];
  currentStep: number;
  status: 'draft' | 'running' | 'completed' | 'failed';
}

interface WorkflowStep {
  id: string;
  type: 'watermark' | 'split' | 'convert' | 'analyze';
  title: string;
  status: 'pending' | 'running' | 'done' | 'skipped' | 'error';
  input: StepInput;
  output?: StepOutput;
  progress: number;      // 0-100
  error?: string;
}

type StepInput = 
  | { type: 'watermark'; files: string[] }
  | { type: 'split'; file: string }
  | { type: 'convert'; files: string[] }
  | { type: 'analyze'; caseDir: string; evidenceFiles: string[] };

type StepOutput =
  | { type: 'watermark'; outputFiles: string[] }
  | { type: 'split'; outputFiles: string[] }
  | { type: 'convert'; mdFiles: string[] }
  | { type: 'analyze'; reportPath: string };
```

#### 4.6.2 工作流引导界面

```
┌─────────────────────────────────────────────┐
│  📋 刑事案卷处理工作流                        │
├─────────────────────────────────────────────┤
│                                             │
│  ① 去水印      ② 拆分      ③ 转MD    ④ 分析  │
│  ┌─────┐      ┌─────┐      ┌─────┐  ┌─────┐ │
│  │  ✅  │─────→│  🔄  │─────→│  ⏳  │─→│  ⏳  │ │
│  │完成  │      │进行中│      │等待  │  │等待  │ │
│  └─────┘      └─────┘      └─────┘  └─────┘ │
│                                             │
│  📁 导入文件: 拖拽 PDF 到此处                 │
│  📊 当前进度: 5/12 文件已去水印               │
│  ⏱️  预计剩余: 15 分钟                        │
│                                             │
└─────────────────────────────────────────────┘
```

#### 4.6.3 自动流转

```
用户导入 PDF 文件
  ↓
[步骤 1] 去水印（用户确认是否去水印）
  ├─ 是 → 执行去水印 → 输出干净 PDF
  └─ 否 → 跳过，使用原始 PDF
  ↓
[步骤 2] 拆分（用户确认是否拆分）
  ├─ 是 → 可视化拆分 → 输出多个独立 PDF
  └─ 否 → 跳过，使用单个 PDF
  ↓
[步骤 3] PDF → MD（自动执行）
  ├─ 批量转换所有 PDF 文件
  ├─ 自动缓存 .md 文件
  └─ 输出 MD 文件列表
  ↓
[步骤 4] 案卷分析（用户输入辩护对象）
  ├─ 选择要分析的证据
  ├─ AI 生成辩护分析报告
  └─ 导出/保存报告
```

### 4.7 设置页（新建）⭐

#### 4.7.1 初始设置向导（首次启动）

用户首次启动时，引导完成必要配置：

```
┌─────────────────────────────────────────────┐
│  🎉 欢迎使用刑事案卷处理系统                  │
│  请完成以下设置以开始使用                     │
├─────────────────────────────────────────────┤
│                                             │
│  步骤 1/2：选择 PDF 转 Markdown 方式         │
│                                             │
│  ┌───────────────────────────────────────┐  │
│  │ ☑️ MinerU API（推荐）                   │  │
│  │    云端转换，高质量，~1分钟/文件         │  │
│  │    需要申请免费 Token                  │  │
│  │    Token: [输入你的 MinerU Token __]   │  │
│  │    [前往申请 →]                         │  │
│  ├───────────────────────────────────────┤  │
│  │ ☐ MinerU CLI（本地）                   │  │
│  │    完全离线，需要手动放置模型           │  │
│  │    模型路径: [选择路径 __________]      │  │
│  │    加速设备: [🔄 自动检测 ▼]            │  │
│  │      ├─ 🍎 Apple GPU (MPS)             │  │
│  │      ├─ 🟢 NVIDIA GPU (CUDA)           │  │
│  │      └─ 💻 CPU（较慢）                 │  │
│  │    预计速度: ~15 秒/文件 (MPS)          │  │
│  └───────────────────────────────────────┘  │
│                                             │
│  步骤 2/2：配置 AI 分析模型                 │
│                                             │
│  选择供应商（可添加多个）：                   │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ │
│  │ 百炼│ │ 智谱│ │OpenAI│ │Ollama│ │ MLX │ │本地 │ │
│  └─────┘ └─────┘ └─────┘ └─────┘ └─────┘ └─────┘ │
│                                             │
│  API Key: [输入你的 API Key _______]        │
│  [测试连接]  →  ✅ 连接成功！                 │
│  默认模型: [qwen-plus ▼]                    │
│                                             │
│  [ 跳过，稍后设置 ]     [ 完成设置 → ]       │
│                                             │
└─────────────────────────────────────────────┘
```

#### 4.7.2 设置主界面（Cherry Studio 风格）

```
┌─────────────────────────────────────────────┐
│  ⚙️ 设置                                     │
├─────────────────────────────────────────────┤
│                                             │
│  📄 PDF 转 Markdown                          │
│  ┌─────────────────────────────────────────┐ │
│  │ 首选方式:                                │ │
│  │ ◉ MinerU API（云端）                     │ │
│  │ ○ MinerU CLI（本地）                     │ │
│  │                                          │ │
│  │ MinerU Token:                            │ │
│  │ •••••••••••••••••••• [显示] [重新生成]  │ │
│  │ 模型版本: [pipeline ▼]                   │ │
│  │ [前往申请免费 Token] → 打开 mineru.net   │ │
│  │                                          │ │
│  │ 备选方案: MinerU CLI                     │ │
│  │ [ ] 启用本地 CLI 作为备选                 │ │
│  │     模型路径: [~/mineru-models/] [浏览]  │ │
│  │     加速设备: [🔄 自动检测 ▼]             │ │
│  │       ├─ 🍎 Apple GPU (MPS)              │ │
│  │       ├─ 🟢 NVIDIA GPU (CUDA)            │ │
│  │       └─ 💻 CPU（较慢）                  │ │
│  │     ℹ️ API 失败时自动切换到此方案          │ │
│  └─────────────────────────────────────────┘ │
│                                             │
│  🤖 AI 模型管理（Cherry Studio 风格）         │
│  ┌─────────────────────────────────────────┐ │
│  │ 已添加的供应商:                           │ │
│  │ ┌───────────────────────────────────┐   │ │
│  │ │ 🏢 百炼 DashScope          [编辑] │   │ │
│  │ │    API Key: sk-•••••••••••• [显示]│   │ │
│  │ │    可用模型: qwen-plus, qwen-max  │   │ │
│  │ │    默认: [qwen-plus ▼]            │   │ │
│  │ │    状态: ✅ 已启用                │   │ │
│  │ ├───────────────────────────────────┤   │ │
│  │ │ 🦙 Ollama                  [编辑] │   │ │
│  │ │    地址: http://localhost:11434   │   │ │
│  │ │    可用模型: qwen2.5:32b, llama3  │   │ │
│  │ │    默认: [qwen2.5:32b ▼]         │   │ │
│  │ │    状态: ✅ 运行中                 │   │ │
│  │ ├───────────────────────────────────┤   │ │
│  │ │ 🏢 智谱 AI                  [编辑] │   │ │
│  │ │    API Key: 未配置          [填写] │   │ │
│  │ │    可用模型: glm-4-plus, glm-4    │   │ │
│  │ │    状态: ❌ 未启用                │   │ │
│  │ └───────────────────────────────────┘   │ │
│  │                                          │ │
│  │ [+ 添加供应商]                            │ │
│  │                                          │ │
│  │ 常用预设:                                 │ │
│  │ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ │
│  │ │ 百炼│ │ 智谱│ │OpenAI│ │Ollama│ │ MLX │ │本地 │ │
│  │ └─────┘ └─────┘ └─────┘ └─────┘ └─────┘ └─────┘ │
│  └─────────────────────────────────────────┘ │
│                                             │
│  🔄 工作流设置                               │
│  ┌─────────────────────────────────────────┐ │
│  │ [✓] 拆分后自动转为 Markdown              │ │
│  │ [ ] 转换后自动开始分析                   │ │
│  │ 默认辩护对象: [____________________]     │ │
│  └─────────────────────────────────────────┘ │
│                                             │
└─────────────────────────────────────────────┘
```

#### 4.7.3 配置持久化

```typescript
// 配置存储在 Tauri 的 app config 目录
interface AppConfig {
  // PDF → MD 配置
  pdf2md: {
    preferredMethod: 'api' | 'cli';  // 首选方式
    mineruApi: {
      token: string;
      modelVersion: 'pipeline' | 'vlm';
    };
    mineruCli: {
      enabled: boolean;           // 是否启用本地 CLI 备选
      modelsPath: string;         // 本地模型路径
    };
  };

  // LLM 供应商管理（Cherry Studio 风格）
  llm: {
    providers: LLMProvider[];     // 所有添加的供应商
    defaultProviderId: string;    // 默认供应商
    defaultModel: string;         // 默认模型
  };

  // 工作流设置
  workflow: {
    autoConvertToMd: boolean;     // 拆分后自动转 MD
    autoAnalyze: boolean;         // 转换后自动分析
    defaultDefenseTarget: string; // 默认辩护对象
  };
}

interface LLMProvider {
  id: string;                     // 唯一 ID
  name: string;                   // 显示名称
  icon?: string;                  // 图标 emoji
  baseURL: string;                // API 地址
  apiKey: string;                 // API Key
  models: string[];               // 可用模型列表
  defaultModel: string;           // 默认模型
  enabled: boolean;               // 是否启用
  isCustom: boolean;              // 是否自定义（非预设）
  addedAt: string;                // 添加时间
}
```

### 4.8 工作流持久化

```rust
// SQLite 数据库表设计
CREATE TABLE workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT CHECK(status IN ('draft', 'running', 'completed', 'failed')),
    current_step INTEGER DEFAULT 0,
    config TEXT  -- JSON 配置
);

CREATE TABLE workflow_steps (
    id TEXT PRIMARY KEY,
    workflow_id TEXT REFERENCES workflows(id),
    step_type TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    input TEXT,      -- JSON
    output TEXT,     -- JSON
    progress INTEGER DEFAULT 0,
    error TEXT,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE case_files (
    id TEXT PRIMARY KEY,
    workflow_id TEXT REFERENCES workflows(id),
    original_path TEXT NOT NULL,
    processed_path TEXT,
    md_path TEXT,
    file_type TEXT,    -- watermark, split, md
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

---

## 五、关键技术实现

### 5.1 Python 子进程调用

```rust
// src-tauri/src/python/runner.rs
use tokio::process::Command;
use serde_json::Value;

pub struct PythonRunner {
    venv_python: PathBuf,
    backend_dir: PathBuf,
}

impl PythonRunner {
    pub fn new() -> Self {
        // 使用内置的 Python 虚拟环境或系统 Python
        Self {
            venv_python: PathBuf::from("/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"),
            backend_dir: PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../python-backend"),
        }
    }

    pub async fn run_module(
        &self,
        module: &str,
        function: &str,
        args: Value,
    ) -> Result<Value, String> {
        let output = Command::new(&self.venv_python)
            .arg("-c")
            .arg(format!(
                "import json, sys; \
                 sys.path.insert(0, '{}'); \
                 from {} import {}; \
                 result = {}(json.loads(sys.stdin.read())); \
                 print(json.dumps(result))",
                self.backend_dir.display(),
                module,
                function,
                function
            ))
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .output()
            .await
            .map_err(|e| format!("Failed to run Python: {}", e))?;

        if !output.status.success() {
            return Err(String::from_utf8_lossy(&output.stderr).to_string());
        }

        serde_json::from_slice(&output.stdout)
            .map_err(|e| format!("Failed to parse output: {}", e))
    }
}
```

### 5.2 Tauri Commands 示例

```rust
// src-tauri/src/commands/watermark.rs
use tauri::State;
use crate::python::runner::PythonRunner;
use crate::db::sqlite::AppDb;

#[derive(serde::Serialize)]
pub struct WatermarkResult {
    pub success: bool,
    pub output_files: Vec<String>,
    pub duration: f64,
    pub error: Option<String>,
}

#[tauri::command]
pub async fn cmd_remove_watermark(
    files: Vec<String>,
    password: Option<String>,
    python: State<'_, PythonRunner>,
    db: State<'_, AppDb>,
) -> Result<WatermarkResult, String> {
    let start = std::time::Instant::now();
    
    let args = serde_json::json!({
        "files": files,
        "password": password,
    });
    
    let result = python.run_module(
        "pdf_processor",
        "remove_watermarks",
        args,
    ).await?;
    
    let duration = start.elapsed().as_secs_f64();
    
    // 记录到数据库
    db.log_operation("watermark", &files, &result).await
        .map_err(|e| format!("DB error: {}", e))?;
    
    Ok(WatermarkResult {
        success: true,
        output_files: result["output_files"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect(),
        duration,
        error: None,
    })
}
```

### 5.3 前端通信层

```typescript
// src/hooks/useTauri.ts
import { invoke } from '@tauri-apps/api/core';

export function useWatermark() {
  const removeWatermark = async (files: string[]) => {
    return invoke<WatermarkResult>('cmd_remove_watermark', {
      files,
      password: null,
    });
  };

  return { removeWatermark };
}

export function useSplit() {
  const analyzeSplit = async (file: string) => {
    return invoke<SplitPlan>('cmd_split_analyze', { file });
  };

  const executeSplit = async (file: string, plan: SplitPlan) => {
    return invoke<SplitResult>('cmd_split_execute', { file, plan });
  };

  return { analyzeSplit, executeSplit };
}

export function useConvert() {
  const convertToMd = async (files: string[], method = 'auto') => {
    return invoke<ConvertResult[]>('cmd_convert_to_md', { files, method });
  };

  return { convertToMd };
}

export function useAnalyze() {
  const analyzeCase = async (caseDir: string, evidenceFiles: string[], defenseTarget: string) => {
    return invoke<AnalysisReport>('cmd_analyze_case', {
      caseDir,
      evidenceFiles,
      defenseTarget,
    });
  };

  return { analyzeCase };
}
```

### 5.4 Tauri 配置

```json
// src-tauri/tauri.conf.json
{
  "productName": "Criminal PDF",
  "version": "3.0.0",
  "identifier": "com.zhanghan.criminal-pdf",
  "build": {
    "frontendDist": "../dist",
    "devUrl": "http://localhost:5173",
    "beforeDevCommand": "npm run dev",
    "beforeBuildCommand": "npm run build"
  },
  "app": {
    "withGlobalTauri": true,
    "windows": [
      {
        "title": "刑事案卷处理系统",
        "width": 1400,
        "height": 900,
        "resizable": true,
        "fullscreen": false
      }
    ],
    "security": {
      "csp": null
    }
  },
  "bundle": {
    "active": true,
    "targets": "all",
    "icon": ["icons/32x32.png", "icons/128x128.png", "icons/icon.icns"],
    "macOS": {
      "frameworks": [],
      "minimumSystemVersion": "12.0",
      "signingIdentity": "-"
    },
    "resources": {
      "../python-backend/": "python-backend/",
      "../scripts/": "scripts/"
    }
  },
  "plugins": {
    "fs": {
      "scope": ["$HOME/**", "/Volumes/**", "/Users/**"]
    }
  }
}
```

---

## 六、依赖与环境

### 6.1 系统要求

| 组件 | 最低 | 推荐 |
|------|------|------|
| macOS | 12.0 (Monterey) | 14.0 (Sonoma) |
| 内存 | 8GB | 16GB+ |
| 磁盘 | 50GB（含模型） | 100GB+ |
| Python | 3.11 | 3.13 |

### 6.2 构建依赖

```bash
# Rust 工具链
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup default stable

# Node.js（已有）
node v24+

# Python 环境（已有）
python3 3.13
pip install -r backend/requirements.txt

# MinerU 模型（已迁移）
~/mineru-models/  (21GB)
```

### 6.3 运行时依赖

| 依赖 | 用途 | 是否可选 |
|------|------|----------|
| `MINERU_TOKEN` | MinerU API（PDF→MD） | 必选（免费申请） |
| LLM API Key | AI 分析（用户自备） | 必选 |
| MinerU CLI（`magic-pdf`） | PDF→MD 备选（离线） | 可选（需用户手动配置） |

**不需要打包的依赖：**
- ❌ 21GB 本地模型 — 用户需要时手动放置

---

## 七、构建与分发

### 7.1 开发模式

```bash
# 安装依赖
cd src-tauri && cargo build
cd .. && npm install

# 开发服务器（热重载）
npm run tauri dev
```

### 7.2 生产构建

```bash
# 构建 macOS App
npm run tauri build

# 输出位置
src-tauri/target/release/bundle/macos/Criminal PDF.app
src-tauri/target/release/bundle/dmg/Criminal PDF_3.0.0_aarch64.dmg
```

### 7.3 应用打包

```
Criminal PDF.app/
├── Contents/
│   ├── MacOS/
│   │   └── Criminal PDF          # Rust 二进制
│   ├── Resources/
│   │   ├── python-backend/       # Python 代码
│   │   ├── scripts/              # 脚本（magic_pdf_compat.py）
│   │   └── icons/                # 应用图标
│   └── Info.plist
```

### 7.4 自动更新

```json
{
  "plugins": {
    "updater": {
      "active": true,
      "endpoints": [
        "https://github.com/user/criminal-pdf/releases/latest/download/latest.json"
      ],
      "pubkey": "dW50cnVzdGVkIGNvbW1lbnQ6..."
    }
  }
}
```

---

## 八、风险评估与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| **MinerU API 额度** | 免费额度用完 | 低 | 用户可升级付费，或切换本地 CLI |
| **Python 环境依赖** | 分发复杂 | 中 | 使用 `pyinstaller` 打包 Python 或要求用户预装 |
| **PDF 格式多样性** | 解析失败 | 中 | 4 层回退确保总有输出 |
| **大文件性能** | 内存溢出 | 低 | 流式处理，分页加载 |
| **LLM API 依赖** | 离线不可用 | 低 | 缓存分析结果，支持离线查看历史 |
| **用户 LLM 配置** | 配置错误 | 中 | 内置预设 + 配置测试按钮 + 清晰文档 |

---

## 九、里程碑与时间线

| 阶段 | 内容 | 预计工时 | 里程碑 |
|------|------|----------|--------|
| **M0** | Tauri 骨架 + 前端迁移 | 1-2 周 | 应用窗口运行，文件拖拽 |
| **M1** | 去水印模块桌面化 | 3-5 天 | 拖拽 PDF → 去水印 → 输出 |
| **M2** | 拆分模块桌面化 | 1 周 | 可视化拆分完成 |
| **M3** | PDF→MD 转换（含 MinerU CLI） | 1 周 | 批量转换 + 缓存管理 |
| **M4** | 案卷分析桌面化 | 1 周 | 分析 + 对话 + 导出 |
| **M5** | 工作流引擎 | 1 周 | 4 步串联，自动流转 |
| **M6** | 优化 + 测试 + 打包 | 1 周 | DMG 安装包发布 |

**总计：6-8 周**

---

## 十、下一步行动

### 立即可做（不写代码）
1. ✅ **确认技术选型**：Tauri v2 + 现有 Python 后端
2. ✅ **确认工作流**：去水印 → 拆分 → 转MD → 分析
3. ✅ **LLM 策略**：Cherry Studio 风格多供应商管理，用户自选
4. ✅ **PDF→MD 策略**：MinerU API 首选，本地 CLI 备选（2 方案）
5. ✅ **初始设置向导**：首次启动引导用户配置必要选项
6. ⏳ **设计 UI 原型**：工作流引导页的交互设计

### 第一步代码（M0）
```bash
# 1. 初始化 Tauri 项目
cd /Users/zhanghan/.openclaw/workspace/skills/criminal-pdf-webui
npx create-tauri-app@latest

# 2. 配置指向现有前端
# 3. 实现初始设置向导（PDF转MD选择 + LLM供应商添加）
# 4. 实现设置主界面（Cherry Studio 风格供应商管理）
# 5. 实现第一个 Command（去水印）
# 6. 测试运行
```

---

*文档版本：v1.0 | 创建时间：2026-04-10 | 作者：零*
