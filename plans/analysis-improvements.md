# 刑事案卷分析系统三项改进计划

## 概述

三个改进点独立但互补，按依赖关系排序：
- **改进 2（断点续传）**：基础能力，改进 1 依赖此能力
- **改进 1（渐进式报告）**：用户体验改进
- **改进 3（LLM 并发自适应）**：性能稳定性改进

---

## 改进 2：分析断点续传

### 问题

当前每个步骤（step1-5）完成后保存 `step_N_result.json`，但步骤内部的子任务没有中间状态保存。如果分析过程中断（网络断开、服务重启），已完成的子任务需要重做。

### 现状

- `AnalysisPipeline` 已有 `_save_step_result()` / `_load_step_result()`
- 步骤 2（逐次总结）：已有跳过已存在总结文件的逻辑（`_load_summary_file`），**部分具备断点续传能力**
- 步骤 3（矛盾分析）：已有跳过已存在矛盾分析文件的逻辑（`_load_contradiction_file`），**部分具备断点续传能力**
- 步骤 4（Wiki 构建）：各子步骤通过 `_wiki_page_exists()` 检查跳过，**部分具备断点续传能力**
- 步骤 5（辩护意见）：无断点续传，每次重跑

### 改动

#### 2.1 统一断点状态文件

**文件**：`backend/analysis_pipeline.py`

在 `analysis/` 下新增 `analysis_state.json`，记录每个子步骤的状态：

```json
{
  "version": 1,
  "updated_at": "2026-05-17T...",
  "steps": {
    "1": { "status": "completed", "completed_at": "..." },
    "2": {
      "status": "completed",
      "completed_at": "...",
      "details": {
        "顾君燕_讯问笔录": "done",
        "江涛_讯问笔录": "done"
      }
    },
    "3": {
      "status": "completed",
      "completed_at": "...",
      "details": {
        "顾君燕": "done"
      }
    },
    "4": {
      "status": "completed",
      "completed_at": "...",
      "sub_steps": {
        "4a": "done",
        "4b": { "顾君燕_讯问笔录": "done", "江涛_讯问笔录": "done" },
        "4c": "done",
        "4d": "done"
      }
    },
    "5": { "status": "idle" }
  }
}
```

新增方法：
```python
def _save_analysis_state(self):
    """保存分析状态到 analysis_state.json"""
    
def _load_analysis_state(self) -> dict:
    """加载分析状态，不存在则返回默认结构"""
    
def _mark_substep_done(self, step: str, substep_key: str):
    """标记子步骤完成并持久化"""
```

#### 2.2 新增断点恢复 API

**文件**：`backend/pipeline_api.py`

新增端点：
- `GET /api/pipeline/{case_id}/state` — 返回当前分析状态（各步骤/子步骤完成度）
- `POST /api/pipeline/{case_id}/resume` — 从断点恢复，自动找到未完成的步骤继续执行

```python
@router.post("/{case_id}/resume")
async def resume_pipeline(case_id: str, defendant: str = Body(..., embed=True), crime_type: Optional[str] = Body(default=None, embed=True)):
    """从断点恢复，自动找到下一个未完成的步骤执行"""
    # 读取 analysis_state.json，找到第一个未完成的步骤
    # 自动执行该步骤（已有 skip 逻辑，不会重做已完成的子步骤）
```

#### 2.3 前端断点恢复按钮

**文件**：`frontend/src/pages/CaseDetailPage.tsx`

在分析步骤 UI 中增加"继续分析"按钮：
- 读取 `/api/pipeline/{case_id}/state`
- 显示已完成/未完成的步骤进度条
- 点击"继续分析"调用 `/api/pipeline/{case_id}/resume`

### 验证

- 中断步骤 2（总结 3 个人中的第 2 个后杀进程）→ 重启后点继续 → 只分析第 3 个人
- 验证 `analysis_state.json` 每次子步骤完成后更新

---

## 改进 1：分析报告渐进式输出

### 问题

当前步骤 5（辩护意见生成）一次性调用 LLM 生成完整报告。用户需要等所有步骤全部完成后才能看到任何结果。

### 目标

- 分析不必全部完成才出报告
- 每完成一个子步骤，UI 就能展示该子步骤的结果
- 最终报告是各子步骤结果的组合
- UI 以"逐步填充"的方式呈现（骨架屏 / 逐步渲染）

### 改动

#### 1.1 步骤 5 拆分为渐进式

**文件**：`backend/analysis_pipeline.py`

将 `step5_defense_opinion()` 拆分为多个子阶段，每个阶段独立保存：

```python
async def step5_defense_opinion(self, defendant, crime_type=None, progress_cb=None):
    """分阶段生成辩护意见，每阶段独立保存"""
    
    # 5a: 案件概述 + 指控要素
    if not self._wiki_page_exists("05-辩护意见", "01-案件概述.md"):
        # LLM 调用 → 保存
        ...
    
    # 5b: 证据支撑评估
    if not self._wiki_page_exists("05-辩护意见", "02-证据评估.md"):
        ...
    
    # 5c: 矛盾点辩护利用
    if not self._wiki_page_exists("05-辩护意见", "03-矛盾利用.md"):
        ...
    
    # 5d: 三阶层辩护
    if not self._wiki_page_exists("05-辩护意见", "04-三阶层辩护.md"):
        ...
    
    # 5e: 量刑情节
    if not self._wiki_page_exists("05-辩护意见", "05-量刑情节.md"):
        ...
    
    # 5f: 最终结论 + 建议
    if not self._wiki_page_exists("05-辩护意见", "06-结论建议.md"):
        ...
    
    # 最后：合并所有子章节为完整报告
    self._assemble_final_report()
```

#### 1.2 新增子步骤结果 API

**文件**：`backend/pipeline_api.py`

新增端点：
- `GET /api/pipeline/{case_id}/defense-stages` — 返回辩护意见各子阶段完成状态
- `GET /api/pipeline/{case_id}/defense-stage/{stage_name}` — 返回指定子阶段的内容

```python
@router.get("/{case_id}/defense-stages")
async def get_defense_stages(case_id: str):
    """返回辩护意见各子阶段完成状态"""
    # 检查 05-辩护意见/ 下的文件，返回 { "01-案件概述": "done", "02-证据评估": "pending", ... }
```

#### 1.3 前端渐进式报告 UI

**文件**：`frontend/src/pages/CaseDetailPage.tsx` / `ReportPage.tsx`

改动报告展示逻辑：
- 报告页的 8 个内容标签不再等全部完成才显示
- 每个标签页有自己的完成状态（idle / loading / done）
- 完成的标签页立即可读，未完成显示骨架屏或进度条
- 使用 SSE 或轮询 `/api/pipeline/{case_id}/defense-stages` 检测新完成的子步骤

具体 UI 变化：
```
之前：[全部分析完] → [一次性显示报告]
之后：[步骤1完成] → [步骤1标签亮起]
      [步骤2完成] → [步骤2标签亮起]
      [步骤N完成] → [步骤N标签亮起]
      [全部完成]  → [完整报告可导出]
```

报告页标签状态：
- 指控要素（依赖 4a）→ 4a 完成即可看
- 事实要素（依赖 4b 逐人分析）→ 每分析完一个人就更新
- 证据分析（依赖 4b 逐人分析）→ 每分析完一个人就更新
- 法律依据（依赖 4c）→ 4c 完成即可看
- 矛盾记录（依赖步骤 3）→ 步骤 3 完成即可看
- 综合结论（依赖 4d）→ 4d 完成即可看
- 完整报告（依赖步骤 5 全部子阶段）→ 逐步填充

### 验证

- 运行分析 → 观察报告页各标签逐步亮起
- 确认每个标签的内容是逐步追加而非一次性替换
- 确认最终完整报告是所有子章节的组合

#### 1.4 Markdown → HTML 增强渲染

**设计原则**：LLM 仍然生成 Markdown（作为 source of truth），前端在渲染层将特定章节增强为 HTML 组件。编辑模式保持 Markdown textarea 不变。

**文件**：新建 `frontend/src/components/report/` 目录

**架构**：

```
LLM 生成 Markdown（不变，source of truth）
    ↓
marked 解析为基础 HTML（已有）
    ↓
增强渲染层（新增）：识别特定章节 → 替换为定制组件
    ↓
编辑模式 → Markdown textarea（不变）
    ↓
导出 → Markdown → pandoc → DOCX/PDF（不变）
```

**核心思路**：用 `marked` 的 `renderer` 扩展能力，在渲染过程中拦截特定模式的 Markdown（如章节标题、特定表格格式），替换为定制 React 组件。

**新建组件**（`frontend/src/components/report/`）：

| 组件 | 触发条件 | 渲染效果 |
|------|----------|----------|
| `RelationshipGraphCard` | Markdown 中的人物关系表格 | 复用 AnalyzePage 已有的 SVG 关系图，支持 hover 高亮 |
| `EvidenceContrastTable` | 包含"是否矛盾"列的对比表 | 自动标红矛盾行、标绿印证行 |
| `StrengthBadge` | 证据强度评级文字（强/中/弱） | 替换为颜色徽章（绿/橙/红） |
| `ThreeTierCard` | "六、辩护要点"中的三阶层表格 | 三栏卡片布局，每层一个卡片，带颜色标识 |
| `SectionSkeleton` | 未完成/正在生成的章节 | 骨架屏动画，显示"分析中..." |
| `ReportRenderer` | 主渲染组件 | 组合以上所有子组件，处理章节级渐进渲染 |

**ReportRenderer 工作方式**：

```tsx
// 1. 用 marked 将 Markdown 解析为 HTML 字符串
const html = marked.parse(markdownContent)

// 2. 将 HTML 转为 React 可操作的结构（或用 dangerouslySetInnerHTML + post-process）
// 3. 识别特定模式并替换：
//    - "| 人物 | 角色 | 关联人物 |..." → <RelationshipGraphCard />
//    - "| 强/中/弱 |" 的评级列 → <StrengthBadge />
//    - "## 一、指控要素分析" 等标题 → 带锚点的 SectionHeader
//    - "（分析中...）" 占位 → <SectionSkeleton />
```

**具体渲染策略**：

1. **人物关系图**：解析 Markdown 表格中的 `| 人物 | 角色 | 关联人物 | 关系类型 | 关系说明 |`，提取数据传入已有的 `RelationshipGraph` 组件。报告页和 AnalyzePage 复用同一组件。

2. **证据对比高亮**：识别包含"是否矛盾"列的表格，对"矛盾"行加红色背景，对"印证"行加绿色背景，对"待补充"行加灰色。

3. **三阶层卡片**：将"六、辩护要点"中的构成要件表格拆为三个独立卡片：
   - 构成要件符合性（蓝色卡片）
   - 违法性（绿色卡片）
   - 有责性（橙色卡片）

4. **渐进式章节渲染**：
   ```tsx
   // 每个章节有自己的状态
   const [sections, setSections] = useState({
     '指控要素': { status: 'done', content: '...' },
     '人物关系': { status: 'done', content: '...' },
     '证据分析': { status: 'loading', progress: 0.6 },
     '三阶层': { status: 'idle' },
     '完整报告': { status: 'idle' },
   })
   // 完成时 slide-in 动画
   // 分析中时显示骨架屏 + 进度条
   // 未开始时显示灰色占位 + 提示"等待分析完成"
   ```

5. **章节锚点导航**：报告左侧自动生成章节 TOC，点击锚点平滑滚动到对应章节。章节完成时 TOC 项从灰色变高亮。

**修改文件**：
- `frontend/src/pages/ReportPage.tsx` — 替换当前的 `marked` 直出逻辑，接入 `ReportRenderer`
- `frontend/src/components/report/` — 新建组件目录

**不变的部分**：
- LLM prompt 不变，仍然输出 Markdown
- 编辑模式不变，textarea 直接编辑 Markdown 原文
- 导出不变，仍然从 Markdown 源文件转 DOCX/PDF

### 验证（HTML 渲染）

- 打开一个已完成的报告，确认人物关系表格渲染为 SVG 关系图
- 确认矛盾对比表有红绿高亮
- 确认三阶层分析以卡片形式展示
- 点击编辑按钮，确认仍然是 Markdown textarea
- 确认导出 DOCX/PDF 格式正确

---

## 改进 3：LLM 并发数自动调整

### 问题

当前 `evidence_concurrency` 是用户手动配置的固定值（1-10）。太高会触发 API 限流导致卡死，太低则浪费模型能力。

### 目标

- 根据模型响应情况自动调整并发数
- 不依赖用户手动干预
- 在设置中保留手动上限配置

### 改动

#### 3.1 自适应并发控制器

**新建文件**：`backend/concurrency_controller.py`

```python
class ConcurrencyController:
    """根据 LLM 响应情况动态调整并发数"""
    
    def __init__(self, initial: int = 3, min_concurrency: int = 1, max_concurrency: int = 10):
        self.current = initial
        self.min_concurrency = min_concurrency
        self.max_concurrency = max_concurrency
        
        # 滑动窗口统计
        self.success_count = 0
        self.error_count = 0
        self.timeout_count = 0
        self.recent_latencies: list[float] = []  # 最近请求延迟
        self.window_size = 20  # 滑动窗口大小
        
    def record_success(self, latency_ms: float):
        """记录成功请求"""
        self.success_count += 1
        self.recent_latencies.append(latency_ms)
        if len(self.recent_latencies) > self.window_size:
            self.recent_latencies.pop(0)
        self._adjust()
    
    def record_timeout(self):
        """记录超时"""
        self.timeout_count += 1
        self.error_count += 1
        self._adjust()
    
    def record_error(self, error_type: str):
        """记录其他错误（429 限流、5xx 等）"""
        self.error_count += 1
        if "429" in error_type or "rate_limit" in error_type:
            self.timeout_count += 1  # 限流等同于限流
        self._adjust()
    
    def _adjust(self):
        """根据统计结果调整并发数"""
        total = self.success_count + self.error_count
        if total < 5:
            return  # 样本不足，不调整
        
        error_rate = self.error_count / total
        avg_latency = sum(self.recent_latencies) / len(self.recent_latencies) if self.recent_latencies else 0
        
        # 策略：
        # 1. 错误率 > 30% → 减半
        # 2. 超时率 > 20% → 减 1
        # 3. 错误率 < 5% 且延迟 < 10s → 加 1
        # 4. 错误率 < 10% 且延迟 < 5s → 加 1（激进模式）
        
        if error_rate > 0.3:
            self.current = max(self.min_concurrency, self.current // 2)
        elif self.timeout_count / total > 0.2:
            self.current = max(self.min_concurrency, self.current - 1)
        elif error_rate < 0.05 and avg_latency < 10000:
            self.current = min(self.max_concurrency, self.current + 1)
        elif error_rate < 0.1 and avg_latency < 5000:
            self.current = min(self.max_concurrency, self.current + 1)
        
        # 重置窗口
        self.success_count = 0
        self.error_count = 0
        self.timeout_count = 0
    
    @property
    def concurrency(self) -> int:
        return self.current
```

#### 3.2 集成到 LLM 客户端

**文件**：`backend/llm_client.py`

在 `chat()` 方法中集成并发控制器：

```python
class LLMClient:
    def __init__(self):
        # ... 现有代码 ...
        from concurrency_controller import ConcurrencyController
        config = load_config()
        self.concurrency_controller = ConcurrencyController(
            initial=config.get("evidence_concurrency", 3),
            max_concurrency=config.get("max_concurrency", 10),  # 新增配置项
        )
    
    async def chat(self, messages, model=None):
        start = time.time()
        for attempt in range(5):
            try:
                response = await self.client.post(...)
                latency_ms = (time.time() - start) * 1000
                self.concurrency_controller.record_success(latency_ms)
                return ...
            except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                self.concurrency_controller.record_timeout()
                # ... 重试逻辑 ...
            except httpx.HTTPStatusError as e:
                self.concurrency_controller.record_error(str(e.response.status_code))
                # ... 错误处理 ...
```

#### 3.3 新增并发状态 API

**文件**：`backend/config_manager.py` + `backend/main.py`

配置新增字段：
- `auto_concurrency`: bool — 是否启用自适应并发（默认 true）
- `max_concurrency`: int — 并发上限（默认 10）

新增端点：
```python
@router.get("/api/config/concurrency-status")
async def get_concurrency_status():
    """返回当前并发状态"""
    return {
        "current_concurrency": llm.concurrency_controller.concurrency,
        "auto_mode": config.get("auto_concurrency", True),
        "error_rate": llm.concurrency_controller.error_rate,
        "avg_latency": llm.concurrency_controller.avg_latency,
    }
```

#### 3.4 前端设置页更新

**文件**：`frontend/src/pages/SettingsPage.tsx`

设置页新增：
- 开关「自适应并发」（默认开启）
- 滑块「最大并发数」（1-10，默认 10）
- 显示当前实际并发数、错误率、平均延迟（当自适应开启时）
- 原有的 `evidence_concurrency` 改为「初始并发数」（仅在自适应关闭时生效）

### 验证

- 设置并发上限为 5，分析一个案件，观察并发数是否在高延迟时自动下降
- 模拟 429 错误，确认并发数立即下降
- 设置页实时显示并发状态

---

## 实施顺序

```
Step 1: 断点续传基础（改进 2）
  ├── 2.1 analysis_state.json 状态管理（后端）
  ├── 2.2 断点恢复 API（后端）
  └── 2.3 前端断点恢复按钮（前端）

Step 2: 渐进式报告 + HTML 增强渲染（改进 1）
  ├── 2.1 步骤 5 拆分为子阶段（后端）
  ├── 2.2 子步骤结果 API（后端）
  ├── 2.3 新建 report/ 组件目录（RelationshipGraphCard、EvidenceContrastTable 等）（前端）
  ├── 2.4 ReportRenderer 主组件 + marked 扩展（前端）
  └── 2.5 渐进式 UI（标签逐步亮起 + 骨架屏 + slide-in）（前端）

Step 3: LLM 并发自适应（改进 3）
  ├── 3.1 ConcurrencyController（新建文件）
  ├── 3.2 集成 LLMClient（后端）
  ├── 3.3 并发状态 API（后端）
  └── 3.4 前端设置更新（前端）
```

**Steps 2 和 3 相互独立，可以并行实施。**
**Step 2 内部：2.1-2.2 先做后端，2.3-2.5 可并行前端。**

## 风险评估

| 风险 | 影响 | 缓解 |
|------|------|------|
| 步骤 5 拆分后 prompt 设计不当 | 报告质量下降 | 保留完整报告的合并逻辑，每个子阶段用独立 prompt |
| 自适应并发调整过于激进 | 分析速度骤降 | 调整阈值保守，确保不会降到 1 以下 |
| 前端状态轮询频繁 | 性能开销 | 使用 3 秒轮询间隔（已有先例） |
| 断点状态文件与磁盘文件不一致 | 跳过本应重做的步骤 | 每次恢复时校验磁盘文件完整性 |
| Markdown → HTML 渲染器解析错误 | 报告格式混乱 | 保留纯 Markdown fallback 模式，HTML 渲染失败时降级为 marked 直出 |
| RelationshipGraph 数据提取失败 | 关系图不显示 | 解析不到标准表格时回退为纯表格渲染 |
