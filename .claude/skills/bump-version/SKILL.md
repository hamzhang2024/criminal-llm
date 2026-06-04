---
name: bump-version
description: 递增版本号（package.json + tauri.conf.json 同步更新）
disable-model-invocation: true
---

# 版本号递增

将 package.json 和 tauri.conf.json 的版本号末位 +1。

## 执行步骤

1. 运行递增脚本：
   ```bash
   cd frontend && python3 bump-version.py
   ```

2. 确认输出，例如：`版本号: 1.3.4 → 1.3.5`

3. 提交变更：
   ```bash
   git add frontend/package.json frontend/src-tauri/tauri.conf.json
   git commit -m "chore: 版本号更新至 <新版本号>"
   ```

## 注意

- 版本号格式为 semver（MAJOR.MINOR.PATCH），脚本仅递增 PATCH 位
- 不自动推送，需手动 push 或配合 /release skill 使用
