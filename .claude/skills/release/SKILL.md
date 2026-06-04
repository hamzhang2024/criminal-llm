---
name: release
description: 发布新版本（bump → build → tag → push 触发 CI 多平台构建）
disable-model-invocation: true
---

# 发布流程

完整发版流程：递增版本号 → 构建验证 → 打标签 → 推送触发 GitHub Actions。

## 前置检查

1. 确认工作区干净：`git status` 无未提交变更
2. 确认在 main 分支：`git branch --show-current`
3. 确认与远程同步：`git pull origin main`

## 执行步骤

### 1. 递增版本号

```bash
cd frontend && python3 bump-version.py
```

### 2. 前端构建验证

```bash
cd frontend && npm run build
```

构建失败则中止，修复后重新开始。

### 3. 提交版本号变更

```bash
git add frontend/package.json frontend/src-tauri/tauri.conf.json
git commit -m "chore: 版本号更新至 <新版本号>"
```

### 4. 打标签并推送

```bash
git tag v<新版本号>
git push origin main --tags
```

推送后 GitHub Actions 自动触发三平台构建（Windows + macOS Intel + macOS Apple Silicon）。

### 5. 确认 CI

```bash
gh run list --limit 1
```

查看构建状态。构建完成后产物自动发布到 GitHub Release。

## CI 产物

| 平台 | 产物 |
|------|------|
| Windows | `.exe` / `.msi` |
| macOS (Apple Silicon) | `.dmg` / `.app.tar.gz` |

## 中止/回滚

- 标签推送前可随时 `git reset HEAD~1` 撤销提交
- 标签推送后需 `git tag -d v<版本号> && git push origin :refs/tags/v<版本号>` 删除远程标签
