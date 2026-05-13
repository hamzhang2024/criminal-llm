# Criminal LLM - 刑法知识库本地推理系统

> 基于 Ollama + LanceDB + RAG 的刑法问答系统

---

## 项目定位

**criminal-llm**：专注于本地LLM推理 + 向量检索 + RAG问答

**criminal-pdf-webui**：专注于PDF拆分 + 案卷分析 WebUI（独立技能）

---

## 系统架构

```
用户提问 → 向量检索 → RAG增强 → 本地LLM → 三阶层分析
    ↓           ↓           ↓          ↓          ↓
  Embedding  LanceDB    Context   Ollama    辩护报告
```

---

## 核心数据（基于刑法知识库）

| 数据类型 | 数量 | 状态 |
|----------|------|------|
| 案例索引 | 1331个 | ✅ 已完成 |
| 法条文档 | 350个 | ✅ 已完成 |
| 概念定义 | 33个 | ✅ 已完成 |
| 三向关联 | 已建立 | ✅ 已完成 |

---

## 目录结构

```
criminal-llm/
├── vector_service/     # 向量化服务
│   ├── embed_cases.py  # 案例向量化
│   ├── embed_laws.py   # 法条向量化
│   └── embed_concepts.py # 概念向量化
│
├── api/                # API服务
│   ├── qa_api.py       # 问答API
│   ├── search_api.py   # 检索API
│   └── main.py         # FastAPI入口
│
├── config/             # 配置
│   └── settings.py     # 系统配置
│
├── data/               # 数据
│   ├── lancedb/        # 向量数据库
│   └── knowledge/      # 知识库链接
│
└── CRIMINAL_LLM_DEV_PLAN.md  # 开发计划
```

---

## 开发里程碑

### M1：向量库建设（1周）

**任务：**
1. 提取案例索引数据（1331个）
2. 提取法条数据（350个）
3. 提取概念数据（33个）
4. 向量化并存储到LanceDB
5. 测试向量检索效果

**验收标准：**
- 所有数据成功向量化
- 检索速度 < 100ms
- 相关性准确率 > 80%

---

### M2：问答引擎（1周）

**任务：**
1. 实现多源向量检索
2. 实现RAG Context构建
3. 实现三阶层分析模板
4. 集成Ollama推理
5. 测试问答效果

**验收标准：**
- 问答响应时间 < 5秒
- 三阶层分析结构完整
- 辩护建议有针对性

---

### M3：API服务（1周）

**任务：**
1. FastAPI接口设计
2. 问答API实现
3. WebSocket实时对话
4. 前端集成

---

### M4：前端界面（1周）

**任务：**
1. 问答对话界面
2. 三阶层分析展示
3. 相关案例/法条引用
4. 导出分析报告

---

## 技术栈

| 模块 | 技术 |
|------|------|
| 向量数据库 | LanceDB |
| 向量模型 | nomic-embed-text (Ollama) |
| 推理模型 | qwen2.5:32b / gemma4:26b |
| API框架 | FastAPI |
| 前端 | React（新建） |

---

## 下一步：启动M1向量库建设

---

*文档更新：2026-04-11*