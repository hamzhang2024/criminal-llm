# Autoresearch Configuration

## Goal
刑事案卷分析系统桌面应用开发完成度优化 - 按照 DESKTOP_APP_DEV_PLAN.md 完成所有功能模块，并优化用户体验

## Metric
- **Name**: 功能完成度评分 (0-100)
- **Direction**: higher is better
- **Extract command**: 检查所有页面和组件是否正常工作，功能是否完整

## Target Files
- `frontend/src/components/*.tsx` - UI 组件（可修改优化）
- `frontend/src/pages/*.tsx` - 页面组件（可修改优化）
- `frontend/src/styles/macOS.css` - 样式系统（可修改优化）
- `frontend/src/App.tsx` - 路由配置（可修改）

## Read-Only Files
- `DESKTOP_APP_DEV_PLAN.md` - 开发手册（需求文档，不可修改）
- `frontend/package.json` - 依赖配置（不添加新依赖）

## Run Command
```
cd /Users/zhanghan/.openclaw/workspace/criminal-llm/frontend && npm run build 2>&1 | tail -20
```

## Time Budget
- **Per experiment**: 30 秒（编译 + 检查）
- **Kill timeout**: 60 秒

## Constraints
- 不添加新的 npm 依赖包
- 保持 macOS 风格设计系统
- 保持 3 步工作流：PDF处理 → PDF转MD → 案卷分析
- 模型配置使用 bailian/qwen3.6-plus

## Branch
autoresearch/dev-optimization

## Notes
- 按照 DESKTOP_APP_DEV_PLAN.md 继续开发
- 优化每个页面的功能和用户体验
- 确保所有页面都能正常访问和交互
