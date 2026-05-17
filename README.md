# Criminal LLM — 刑事案卷智能分析桌面应用

> 基于 Tauri 2.x + FastAPI + LLM 的案卷分析工具

## 技术栈

| 层级 | 技术 |
|------|------|
| 桌面框架 | Tauri 2.x + React + TypeScript |
| 前端 | React 18 + Vite 5.4 |
| 后端 | FastAPI (Python 3.13) + PyInstaller |
| 模型 | qwen3.6-plus（阿里云百炼） |
| 认证 | 远程 FastAPI + JWT |

## 快速开始

```bash
# 前端开发
cd frontend && npm run dev

# 后端开发
cd backend && python3 main.py

# Tauri 开发模式
cd frontend && npx tauri dev

# 打包
cd frontend && npx tauri build
```

## 核心业务流程

上传 PDF → 去水印 → 转 MD → 证据提取 → 三阶层分析

详见 [CLAUDE.md](CLAUDE.md)