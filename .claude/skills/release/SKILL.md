---
name: release
description: criminal-llm 发版清单——从代码改动到三平台上线，防漏步骤
---

# 发版清单（criminal-llm）

## 前置检查
1. `python3 scripts/prebuild_check.py` 通过（0 fatal）
2. `cd frontend && npx tsc --noEmit` 通过
3. 确认改动已 commit（工作区干净、在 main 分支、与远程同步）

## 发版流程
1. `cd frontend && python3 bump-version.py`（递增 patch；minor/major 手动改 package.json + tauri.conf.json）
2. `cd frontend && npm run build` 前端构建验证（失败则中止）
3. `git add -A && git commit -m "chore: 版本号更新至 X.Y.Z"`
4. `git tag vX.Y.Z`
5. `git push origin main && git push origin vX.Y.Z`（触发 CI）
6. 等 CI 三平台全绿（`gh run watch` / `gh run list --limit 1`）
7. CI 绿后发布到官网：
   - `gh release download vX.Y.Z`（**不要用 gh-proxy**，大文件截断）
   - **校验下载文件大小** vs GitHub Release `.size`（`stat -f "%z"`）
   - `scp` 三平台包到 `root@118.196.83.43:/opt/criminal-llm-auth/data/uploads/`
   - SSH 写 release_notes（`data/release_notes.json`）
   - curl `/api/latest-version` 验证
8. 测 Mac（本机装 dmg + 启动 + 转换）+ Windows（如有用户反馈）

## CI 产物
| 平台 | 产物 |
|------|------|
| Windows | `.exe` / `.msi`（onedir: exe + `_internal/`） |
| macOS (Apple Silicon) | `.dmg` / `.app.tar.gz` |
| macOS (Intel) | `.dmg` |

## 中止/回滚
- 标签推送前可 `git reset HEAD~1` 撤销提交
- 标签推送后需 `git tag -d v<版本号> && git push origin :refs/tags/v<版本号>` 删除远程标签

## 常见坑
- CI 没绿就 tag → 发版失败，要删 tag 重打
- gh-proxy 下大文件截断 → 用 `gh release download`（GitHub 直连）
- prebuild_check 只 grep ModuleNotFoundError → v1.7.0 改为 grep ❌（含 lazy 规则）
- onedir 后 Verify 不能检查 exe >10MB（exe 只含 bootloader ~10MB），要检查 `_internal/` 存在
- Linux AppImage 可能 linuxdeploy 失败 → `--bundles deb,rpm` 跳过 AppImage
