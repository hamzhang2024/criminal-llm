# 选择性 OCR 图片 — 设计文档

**日期**：2026-08-15
**状态**：已确认

---

## 1. 背景与问题

MinerU 转 MD 后，卷宗里嵌入的照片/截图/印章**不转录图片内文字**（实测 vlm 和 pipeline 引擎的 image 块文字 spans=0）。此前靠 `image_ocr_enabled` 开关触发「转换后自动全量回填」——对 14 卷 1922 张图逐张 PaddleOCR 单图识别，约 1 小时，且大量是印章、签名、三面照片等**无效图片**，纯浪费。

**目标**：让用户**选择性 OCR**——规则预筛排除印章/小图后，用户按卷勾选需要识别的图片（转账凭证/流水截图），批量单图识别回填 MD，替代无差别全量回填。

## 2. 关键决策（已确认）

| 决策 | 选择 |
|------|------|
| 预筛力度 | 仅规则预筛（排除 seal 印章 + 小图） |
| OCR 入口 | 流水线内可选步骤（转换后、提取前，不打断自动串联） |
| 图片列表组织 | 按卷分组（组内缩略图网格 + 组级全选/清空） |
| 默认勾选状态 | 默认全选（用户取消无效图） |
| 回填目标 | 直接改 md 文件（复用现有回填格式） |
| `image_ocr_enabled` 开关 | **只给 PaddleOCR 引擎**（useOcrForImageBlock）；MinerU 删除自动回填路径 |

## 3. 架构与数据流

```
MinerU 转换完成（14 卷 → md/*.md + *_images/ + *_layout.json）
        ↓ 前端 Step1 出现「选择性 OCR 图片」卡片（默认折叠，可跳过）
用户展开 → GET /api/cases/{id}/ocr-images
        ↓ 后端读各卷 layout.json 预筛：排除 sub_type=seal + 小图（短边<120px）
        ↓ 返回按卷分组的图片列表（缩略图 URL + 文件名 + 尺寸，图片去重）
前端按卷分组展示，默认全选，用户取消无效图
        ↓ POST /api/cases/{id}/ocr-images   {卷名: [图片名...]}
        ↓ 后台异步任务：对选中图做 PaddleOCR 单图识别（复用 _recognize_single_image）
        ↓ 回填对应卷 md（复用「> 📄 图片内容识别：」格式）
        ↓ GET /api/cases/{id}/ocr-status 轮询进度
用户点「提取证据」继续（OCR 文字已进 md）
```

## 4. 预筛规则

后端读每个卷的 `_layout.json`，遍历 image 块，提取 `sub_type`、`image_path`（文件名）、`bbox`：

| 规则 | 排除对象 |
|------|---------|
| `sub_type == 'seal'` | 印章（红章） |
| bbox 短边 < 120px | 图标、签名条、小图 |

image 块的文件名取自 `blocks[0].lines[0].spans[0].image_path`（实测确认）。图片按文件名**去重**（一张图被多处引用只列一次）。不去区分「照片 vs 截图」——规则分不开，交给用户确认。

## 5. 后端接口（3 个，挂在 case_router）

| 接口 | 作用 |
|------|------|
| `GET /api/cases/{id}/ocr-images` | 预筛全部卷，返回按卷分组：`[{卷名: {图片名: {url, w, h}}}]`；无图片/全被过滤返回空 |
| `POST /api/cases/{id}/ocr-images` | body `{卷名: [图片名...]}`，启动后台 OCR 任务，返回 `{success, task_started}` |
| `GET /api/cases/{id}/ocr-status` | 轮询：`{status, done, total, current, failed: [图片名]}` |

**复用**：
- 单图识别 `image_ocr_backfill._recognize_single_image`
- 回填格式 `> 📄 图片内容识别：\n> {text}`（插到图片引用后）
- 缓存 `images/_ocr.json`（已识别图标记，断点续传）
- 缩略图走现有 `serve-file` 端点（已支持图片 FileResponse）

## 6. 前端 UI

- **位置**：`CaseDetailPage` Step1（转换+提取）区域，转换完成后、提取前，插「选择性 OCR 图片」卡片（默认折叠）
- **展开后**：按卷分组的缩略图网格，每卷一个折叠组 + 组级「全选/清空」+ 图片计数；默认全选
- **顶部**：「OCR 选中图片（N 张）」按钮 + 进度条（轮询 ocr-status）
- 用户可关闭卡片，点「提取证据」跳过 OCR（不打断全自动流程）

## 7. 与 `image_ocr_enabled` 开关的关系

- `image_ocr_enabled` 开关**仅对 PaddleOCR 引擎生效**：控制 `build_optional_payload` 里的 `useOcrForImageBlock` + `useSealRecognition`
- **MinerU 引擎删除自动回填路径**：`mineru_async.py` 和 `pdf_to_md.py` 里转换后的 `backfill_image_ocr` 调用移除，图片 OCR 完全由「选择性 OCR」手动承担
- 即：PaddleOCR 引擎 = 整卷转换自带图片 OCR（开关控制）；MinerU 引擎 = 正文转换 + 手动选择性 OCR

## 8. 错误处理

| 场景 | 行为 |
|------|------|
| 单图 OCR 失败 | 标记 failed、跳过，不阻塞其他图，最终展示失败列表 |
| layout.json 缺失/损坏 | 该卷返回空列表，不报错 |
| PaddleOCR token 未配置 | 前端提示「未配置 PaddleOCR Token」 |
| 预筛后无图片 | 卡片不显示 |

## 9. 测试

- **预筛**：mock layout.json，验证「排除 seal + 小图、保留 ? 类型、按卷分组、图片去重」
- **接口**：GET 返回分组列表；POST 空列表/部分卷边界；ocr-status 进度
- **回填改造**：`image_ocr_backfill` 支持「指定图片列表」，只 OCR 选中图、缓存命中跳过
- **断点**：OCR 中断后，已识别图复用 `_ocr.json` 缓存

## 10. 改造范围

| 文件 | 改动 |
|------|------|
| `backend/image_ocr_backfill.py` | 加「指定图片列表」参数，支持选择性 OCR |
| `backend/case_manager.py`（或新 `ocr_api.py`） | 3 个接口 + 预筛 + 后台 OCR 任务 |
| `backend/mineru_async.py` | 删除转换后自动回填 |
| `backend/pdf_to_md.py` | 删除同步路径自动回填 |
| `frontend/.../Step1Extract.tsx`（或新组件） | 选择性 OCR 卡片 + 缩略图网格 + 勾选 |
