# Criminal LLM - 刑法知识库本地推理系统

## 项目定位

**专注于本地LLM推理 + 向量检索 + RAG问答**

与 **criminal-pdf-webui**（PDF拆分WebUI）分离，独立发展。

---

## 核心功能

- **向量检索**：基于LanceDB的案例/法条/概念向量搜索
- **RAG问答**：检索增强生成的刑法问答
- **三阶层分析**：构成要件符合性 → 违法性 → 有责性
- **本地推理**：Ollama本地模型，无需联网

---

## 数据来源

基于刑法知识库：
- 案例索引：1331个（刑事审判参考）
- 法条文档：350个
- 概念定义：33个
- 三向关联：已建立

---

## 快速开始

```bash
# 1. 启动Ollama（需要已安装）
ollama serve

# 2. 向量化知识库数据（M1阶段）
cd vector_service
python embed_cases.py
python embed_laws.py
python embed_concepts.py

# 3. 启动问答API（M2阶段）
cd api
python main.py
```

---

## 开发计划

详见：[CRIMINAL_LLM_DEV_PLAN.md](CRIMINAL_LLM_DEV_PLAN.md)

---

## 与criminal-pdf-webui的区别

| 项目 | criminal-llm | criminal-pdf-webui |
|------|--------------|-------------------|
| **定位** | LLM推理系统 | PDF拆分WebUI |
| **功能** | 向量检索 + 问答 | PDF处理 + 案卷分析 |
| **位置** | workspace/criminal-llm | skills/criminal-pdf-webui |
| **技术** | LanceDB + Ollama | FastAPI + React |

---

*更新：2026-04-11*