# 案例检索服务设计文档

> 日期：2026-07-29
> 状态：已获用户批准
> 范围：criminal-llm（桌面端）、criminal-llm-auth（云端认证服务，部署于 118.196.83.43）、新增 criminal-llm-cases（云端案例微服务）

## 1. 背景与目标

刑事案卷分析系统（criminal-llm）报告页现有"类案检索"功能使用 qwen 联网搜索 + LLM 现场生成案例 JSON（`backend/stage_api.py:1106`），靠"严禁虚构"提示词约束，存在幻觉风险。阶段 4（法律法规梳理）的提示词也明确要求"不要编造案号"（`backend/analysis_engine.py` 阶段 4）。

本设计以本地《刑事审判参考》MD 库（1750 篇，28MB，位于 `~/Desktop/刑事审判参考_MD`）为数据源，构建**自建案例检索服务**：LLM 离线提炼知识卡片 → 云端独立微服务提供 FTS 检索（API Key 鉴权）→ 桌面端报告页手动检索、勾选案例、注入阶段 4 提示词局部重生成，让辩护报告引用**真实案号与裁判要旨**。

### 已确认的决策（用户拍板）

| 决策点 | 结论 |
|---|---|
| 使用场景 | 报告页手动检索为主，检索结果供 LLM 分析时引用（不做阶段 4 自动触发） |
| 预处理方式 | LLM 离线提炼结构化知识卡片（Karpathy 式"先处理后检索"），卡片用于检索与注入，原文按需查看 |
| 检索引擎 | SQLite FTS5 + 中文 bigram 预处理，0 结果降级 LIKE |
| 调用链路 | 桌面前端 → 本地后端 :8080 代理 → 云端案例服务 :8001 |
| 触发方式 | 仅手动：用户检索 → 勾选 → 局部重生成阶段 4 |
| 数据来源 | 先只用自建库；北大法宝 MCP API 作为二期候选（需先确认定价与商用授权） |
| 部署形态 | 云端独立案例微服务（criminal-llm-cases，端口 8001，systemd 守护） |
| 开放策略 | 独立 API Key 体系：官网申请页 + 管理审批 + 桌面设置页配置 Key |
| 案例扩充 | 增量提炼，按案号跳过已有，不重跑全库 |

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────┐
│ 离线阶段（开发机，一次性 + 案例库扩充时重跑）              │
│  刑事审判参考_MD (1750篇)                                 │
│    └─> scripts/distill_cases.py (百炼 deepseek)          │
│          └─> cases.db (卡片表 + 原文表 + FTS5 索引)       │
└──────────────────────┬──────────────────────────────────┘
                       │ scp 部署（增量覆盖 + systemctl restart）
┌──────────────────────▼──────────────────────────────────┐
│ 云服务器 (118.196.83.43)，Caddy 反代 HTTPS                │
│  criminal-llm-auth  :8000  现有 + 扩展 API Key 管理       │
│  criminal-llm-cases :8001  新增独立微服务（只读 auth.db） │
└──────────────────────▲──────────────────────────────────┘
                       │ X-API-Key 鉴权
┌──────────────────────┴──────────────────────────────────┐
│ 桌面应用 criminal-llm                                    │
│  React 报告页「案例库检索」面板（检索→勾选→引用重生成）    │
│    → 本地 FastAPI :8080 代理转发云端（从 config 读 Key）   │
│    → 阶段 4 注入选中案例卡片局部重生成                     │
└─────────────────────────────────────────────────────────┘
```

关键约束：云端只存数据、不提供 LLM 能力；LLM 调用全部在桌面端（沿用现有百炼配置）。

## 3. 离线案例提炼流水线

**脚本**：`criminal-llm/scripts/distill_cases.py`（本地运行，复用桌面端百炼 deepseek 配置）。

### 3.1 卡片 Schema

| 字段 | 来源 | 说明 |
|---|---|---|
| `case_no` | 文件名解析 | 如"第1000号"，主键，命名不规范的文件拒绝处理并打印 |
| `title` | 文件名解析 | 完整案例名 |
| `charges` | LLM 提炼 | 涉及罪名列表 |
| `issue` | 原文摘录 | "二、主要问题"一节原文照搬（程序规则截取，不改写） |
| `holding_summary` | LLM 提炼 | 裁判要旨 200~400 字 |
| `reasoning_excerpt` | 原文摘录 | "三、裁判理由"核心段落原文摘录 ≤500 字 |
| `keywords` | LLM 提炼 | 5~10 个检索关键词 |
| `full_text` | 原文 | 完整 MD |

### 3.2 设计要点

- `issue`、`reasoning_excerpt` 由程序按 MD 标题结构（`## 二、主要问题`、`## 三、裁判理由`）截取原文，**不经 LLM 改写**——防幻觉底线；LLM 只负责 `charges / holding_summary / keywords`
- LLM 输出严格 JSON，校验失败自动重试 1 次，再失败记入 `failed_cases.json` 人工复查清单，不中断整批
- 断点续跑 + 增量：按 `case_no` 跳过已提炼案例；新增案例只跑增量，FTS 索引随 INSERT 自动更新
- 产出单文件 `cases.db`：卡片表 + 原文表 + FTS5 虚表（对 `title / charges / issue / holding_summary / keywords` 建索引，中文 bigram 预处理）
- 成本估算：1750 篇约 1600 万 token，deepseek 量级 30~60 元，1~2 小时（并发限流 5~10）
- 质量门：随机抽 30 篇，脚本比对 `issue` 与原文一致性 + 人工过 `holding_summary`，达标才部署

## 4. 云端案例微服务（criminal-llm-cases）

**位置**：`/opt/criminal-llm-cases/`，FastAPI + uvicorn，端口 8001，systemd 守护。单文件 `main.py`（≤300 行）+ 部署的 `cases.db`。

### 4.1 API（全部要求 `X-API-Key` 头）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/cases/search?q=&charge=&page=&size=` | FTS5 检索卡片，bm25 排序，返回 `[{case_no, title, charges, issue, holding_summary, snippet, rank}]`，默认 20 条/页 |
| GET | `/api/cases/{case_no}` | 完整卡片（含 `reasoning_excerpt`、`keywords`） |
| GET | `/api/cases/{case_no}/full` | 完整原文 MD |
| GET | `/api/charges` | 罪名去重列表（前端筛选下拉） |
| GET | `/health` | 健康检查（免鉴权） |

### 4.2 实现要点

- 中文检索：建库与查询两侧同步做 bigram 预处理（纯 Python，零外部依赖）
- 0 结果降级：FTS 无结果时对标题和罪名做 `LIKE` 模糊匹配
- Key 校验：只读打开 auth.db（SQLite `mode=ro`）查 `api_keys` 表（存在 + active + 未超当日配额）
- CORS 收紧到桌面端来源（`http://localhost:8080`、`tauri://localhost`）
- IP 级限流 60 req/min 防 Key 爆破
- 部署流程：本地增量提炼 → `scp cases.db` 覆盖 → `systemctl restart`

## 5. API Key 体系

### 5.1 职责划分

- **criminal-llm-auth（扩展）**：Key 签发/吊销/列表、申请审批管理页、官网申请页
- **criminal-llm-cases（新增）**：只读共享 auth.db 的 `api_keys` 表做校验（同服务器单文件共享，零网络跳转）

### 5.2 数据表（auth.db 新增）

```sql
api_keys (
  id INTEGER PK,
  user_id INTEGER REFERENCES users(id),
  key_hash TEXT UNIQUE,      -- 只存 SHA-256，不存明文
  key_prefix TEXT,           -- 前 8 位用于界面识别
  status TEXT,               -- pending / active / revoked
  quota_per_day INTEGER,     -- 默认 200
  created_at, approved_at
)
api_key_usage ( key_id, date, count )   -- 超配额返回 429
```

### 5.3 用户流程

1. 官网新增 `/api-access` 页（登录态）：申请案例检索 API → `pending`；文案注明数据范围（刑事审判参考 1750 篇）与配额规则
2. 管理页 `/admin/api-keys`（独立管理员密码，环境变量配置，不走用户体系）：审批 → 生成 `cca_` + 32 位随机 Key，**明文只显示一次**
3. 桌面端设置页新增"案例检索 API"区块：输入 Key + [验证] 按钮（调云端校验，显示"有效 · 今日已用 x/200"）→ 存入 `config_manager`
4. 检索调用：本地后端代理从 config 读 Key → `X-API-Key` 转发云端

### 5.4 与 JWT 的关系

桌面端登录 JWT 照旧用于身份认证；案例检索 API **只认 API Key**。可单独吊销检索权限而不影响登录。

## 6. 桌面端改动

### 6.1 本地后端（新增 `backend/case_search_api.py`，挂载到 `main.py`）

| 端点 | 行为 |
|---|---|
| `GET /api/cases/search` | 透传参数转发云端，带 `X-API-Key`（从 config_manager 读） |
| `GET /api/cases/{case_no}` 和 `/full` | 同上 |
| `GET /api/charges` | 同上 |

- 云端地址进 config_manager（`CASE_SERVICE_URL` 环境变量可覆盖）
- 云端不可达/超时 10s → 503 + 友好提示；401 → 提示检查设置页 Key；429 → 提示配额

### 6.2 报告页 UI（法律法规 tab 内新增面板）

- 新组件 `frontend/src/components/report/CaseSearchPanel.tsx`（ReportPage.tsx 已 4029 行，只负责挂载，不再往里堆）
- 功能：关键词搜索框 + 罪名筛选下拉 + 结果列表（案号/标题/主要问题/要旨摘要 + 勾选框 + 查看全文弹窗）+ 已选计数 + [引用选中案例并重新生成] 按钮
- 沿用现有 macOS 设计系统与知识库面板布局语言

### 6.3 注入与局部重生成

1. 用户勾选案例 → 点"引用选中案例并重新生成" → 请求体带 `reference_case_nos: [...]`
2. 本地后端从云端拉取这几篇完整卡片，注入阶段 4 提示词：
   > "以下为本案检索到的真实参考案例（仅可引用这些案例，引用格式：【第X号】案例名 + 裁判要旨；禁止引用其他来源的案号）"
3. 只重跑阶段 4，产物照常落盘，报告页刷新该 tab
4. 重生成失败时保留旧版阶段 4 产物（先写临时文件，成功才替换）
5. 现有联网版 `searchSimilarCases` 降级为案例库不可用时的兜底

## 7. 错误处理

| 场景 | 行为 |
|---|---|
| 云端不可达/超时 | 本地后端 503；前端面板错误态可重试，不阻塞报告页其他功能 |
| Key 无效/吊销 | 401 → 提示检查设置页，附跳转入口 |
| 超配额 | 429 + 已用/上限 → 提示明日重置或联系管理员 |
| 检索 0 结果 | 降级 LIKE 仍无 → 提示换关键词，并建议联网版兜底 |
| 提炼 LLM 输出非法 | 重试 1 次 → 记入人工复查清单，不中断整批 |
| 阶段 4 重生成失败 | 保留旧产物不覆盖 |

## 8. 安全

- Key 只存 SHA-256；明文仅签发时显示一次
- 检索接口全部要求 `X-API-Key`；管理页独立密码
- SQLite 参数绑定 + FTS 查询串转义防注入
- 案例服务只读挂载 auth.db（`mode=ro`）
- **HTTPS**：Caddy 反代 + 自动证书，子域名（如 `api.casefix.cn`）覆盖两个服务——Key 体系上线前必须完成
- IP 级 60 req/min 限流

## 9. 测试（TDD，覆盖率 80%+）

| 层 | 测试 |
|---|---|
| 提炼脚本 | 文件名解析（含不规范拒绝）、JSON 校验重试、增量断点、摘录与原文一致性 |
| 云端案例服务 | FTS 检索/分页/降级、Key 校验（无/吊销/429）、只读约束 |
| auth 扩展 | Key 申请/审批/吊销流转、哈希存储、用量计数 |
| 桌面本地后端 | 代理转发（mock 云端）、超时 503、Key 注入、阶段 4 提示词包含且仅包含选中案例 |
| 前端 | 面板组件测试；视觉回归 1280/1440 截图基线 |
| 端到端 | 手工验收：申请 Key → 审批 → 配置 → 检索 → 勾选 → 重生成 → 报告出现真实案号引用 |

## 10. 二期候选（本期不做）

- 北大法宝 MCP API 接入（1.6 亿案例库）：先确认定价与商用授权条款
- 对话式追问注入（针对选中案例向 LLM 追问，结果手动合并进报告）
- 阶段 4 自动按罪名检索注入（无需手动勾选）
