# 图片 OCR 增强 + 资金流梳理验证 设计文档

> 日期：2026-08-08
> 状态：已批准（用户逐节确认）
> 范围：PaddleOCR 引擎图片转文字 + 分析流水线新增资金流梳理子步骤

---

## 1. 背景与问题

案件事实常涉及资金往来，案卷中大量关键证据是**图片形式**的转账凭证、银行流水拍照/截图。现状：

- **MinerU 引擎**：保留文档结构，图片块仅保存为图片文件并在 MD 中留 `![]()` 引用，图片内容不识别——LLM 分析时这部分信息完全缺失。
- **PaddleOCR 引擎**：同样只把图片块（`img_in_image_box_xxx.jpg`）下载保存并留 `<img>` 引用，图片内容不进入 `markdown.text`。

结果：LLM 证据提取和分析看不到图片中的金额、账号、时间，涉及资金流的起诉内容无法被有效核验。

## 2. 目标

1. **图片转文字**：PaddleOCR 引擎在 PDF→MD 转换时，将 PDF 中所有图片块的内容识别为文字，内联到 MD 文件对应位置；原图引用保留，供人工对照原图。
2. **资金流梳理验证**：分析流水线新增子步骤，从证据中重建资金链条，与起诉书指控的涉案金额逐笔对照，输出印证强度分析，供后续分析阶段自动消费。

## 3. 已确认的关键决策

| 决策点 | 结论 |
|--------|------|
| 资金流功能形态 | 分析流水线新子步骤（非独立工具页） |
| MinerU 引擎 | **完全不动**，保持现状；图片识别能力仅 PaddleOCR 引擎提供 |
| 图片识别范围 | 全部图片都识别，不做预过滤 |
| 图片来源区分 | 资金流抽取必须区分客观证据（流水/凭证）与言词证据（供述/证言），仅有言词证据支撑的指控要明确标记 |
| 实现架构 | 方案 A：PaddleOCR 走原生参数开关，不引入第三方识别通道 |

## 4. API 调研结论

### 4.1 PaddleOCR-VL-1.6（https://paddleocr.aistudio-app.com/api/v2/ocr/jobs）

| 参数 | 默认 | 作用 |
|------|------|------|
| `useOcrForImageBlock` | `False` | **是否对图片块中的文字进行识别**——本功能的核心开关 |
| `useSealRecognition` | `False` | 印章识别（银行流水/凭证常见印章） |
| `promptLabel` | `ocr` | 提示标签，纯文字识别模式 |

- 识别文字进入 `layoutParsingResults[].markdown.text`；图片仍通过 `markdown.images` 返回（裁剪图 + 下载 URL）。
- **待实测确认**：开启 `useOcrForImageBlock` 后 `<img>` 标签是否原样保留在 markdown.text 中（决定 5.3 折叠正则修复是否保留）。
- 参考：[PaddleOCR-VL 使用教程](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.md)（`use_ocr_for_image_block` "是否对图片中的文字进行识别"，默认 False）、[issue #18083](https://github.com/PaddlePaddle/PaddleOCR/issues/18083)（1.6 请求实例含 `useOcrForImageBlock: true`）。

### 4.2 MinerU API v4（https://mineru.net/api/v4）

- 无图片块 OCR 开关；vlm 模式同样把图片块保留为图片引用。
- 支持直接提交图片文件（png/jpg/jpeg/jp2/webp/gif/bmp）。
- 开源 3.3 版有 `effort=high`（medium 不支持 image analysis），但云端 API 文档未列出该参数。
- **本次不使用**：MinerU 保持不变，以上仅作调研记录。

## 5. 设计

### 5.1 图片转文字层（仅 PaddleOCR）

**改动点**：`backend/paddleocr_remote.py` 的 `PADDLEOCR_OPTIONAL_PAYLOAD` 增加：

```python
"useOcrForImageBlock": True,    # 识别图片块中的文字（转账凭证/流水截图）
"useSealRecognition": True,     # 印章识别（银行流水/凭证印章）
```

**payload 常量化**：`paddleocr_remote.py` 与 `paddleocr_async.py` 当前各自重复定义 `PADDLEOCR_OPTIONAL_PAYLOAD`。抽为 `paddleocr_remote.py` 中的单一常量 + 构造函数 `build_optional_payload(image_ocr_enabled: bool)`，`paddleocr_async.py` import 使用，消除两处漂移。

**配置开关**：`config_manager.py` 新增 `image_ocr_enabled`（默认 `true`）。关闭时 payload 不包含上述两个参数，完全回退旧行为。设置页增加开关项（PaddleOCR 引擎卡片内）。

**产物形态**（转换完成后 MD 中图片位置）：

```markdown
<img src="./xxx_images/img_in_image_box_001.jpg">

（识别出的凭证文字，直接进入 markdown.text）
收款人：张某某  金额：50,000.00元  交易时间：2024-03-15 14:32 ...
```

原图文件与引用必须保留：律师需对照原图核验识别准确性，出庭举证需出示原始凭证。

**边界说明**：MinerU 引擎转换的案件图片依旧不识别。资金流功能入口和说明书注明：资金流完整性依赖 PaddleOCR 引擎转换。

### 5.2 MinerU 引擎

不做任何修改。

### 5.3 图片折叠正则修复（附带，实测确认后保留或拿掉）

`_fold_consecutive_images`（`pdf_to_md.py`，`mineru_async.py` 中有副本）正则只匹配 `![]()`，不匹配 `<img>`，导致 PaddleOCR 产物的连续图片折叠从未生效。

- 修复方式：扩展正则同时匹配 `<img>` 标签（纯增量）。
- MinerU 走 `![]()` 分支，行为不变——单元测试锁定：MinerU 格式输入，修复前后输出逐字节一致。
- **前置条件**：若 6.1 实测发现开启图片识别后 `<img>` 标签不再出现在 MD 中，则没有折叠对象，本项从范围中拿掉。

### 5.4 资金流梳理子步骤（4e）

**位置**：`analysis_pipeline.py` 步骤 4 中，顺序：

```
4a 指控要素 → 4c 法律框架 → 4b 逐人证据摄入 → 【4e 资金流梳理】→ 4d 综合结论
```

**输入**：
- `analysis/04-案件Wiki/01-指控要素.md` 中的"涉案金额及计算方式"（起诉书指控的资金事实）
- 全部证据 MD 的资金类内容，加载机制与 4b 相同（`evidence/index.json` + 证据 MD 全文）

**LLM 任务**（单次调用，单任务聚焦）：

1. **资金流重建**：抽取全部资金往来记录（时间、付款方、收款方、金额、渠道、来源类型、证据出处），按时间排序成资金链条。
   - **来源类型必填**：`客观证据·银行流水` / `客观证据·转账凭证` / `言词证据·被告人供述` / `言词证据·被害人陈述` / `言词证据·证人证言` 等。
2. **逐笔对照验证**：以起诉书指控的每笔金额为基准核对，结论四档：
   - ✅ 客观证据印证——有流水/凭证直接支撑
   - 🗣 仅言词证据——只有供述/证言提及，无客观证据印证（重要辩点：刑诉法第 55 条，重证据不轻信口供，只有被告人供述不能定案）
   - ⚠️ 证据矛盾——言词与客观证据之间、或不同言词证据间金额/时间冲突
   - ❌ 无证据支撑——指控金额找不到任何来源
   - 另加 🔍 反向发现：证据中有、起诉书未指控的大额资金往来

**输出**：`analysis/04-案件Wiki/02-事实要素/资金流梳理.md`

```markdown
# 资金流梳理与指控验证
## 资金链条总览（时间序表格，含来源类型列）
## 逐笔对照表
| 指控笔次 | 指控金额 | 指控时间 | 来源类型 | 证据出处 | 流水金额 | 核对结论 |
## 差异与疑点分析
## 对辩护的意义
```

**存储与续传**：走既有 `_save_wiki_page` + `_mark_substep_done`；`_wiki_page_exists` 自动跳过已完成。

**下游消费（零改动）**：4d 综合结论、4.5 控辩对抗、步骤 5 辩护意见通过 `_list_wiki_pages`/`_load_wiki_page` 自动消费该页。

**降级策略**：
- 无资金类证据 → 输出"本案未检测到资金类证据"说明页，标记完成，不阻塞流水线
- 无起诉书/指控要素 → 只做资金流重建，不做对照验证

## 6. 错误处理、配额与测试

### 6.1 先行实测（正式开发前必须完成，验收标准明确）

用含转账截图/流水拍照的真实案卷 PDF 跑 PaddleOCR 转换（开/关新参数对比）：

1. **功能验证**：MD 中图片位置出现识别文字（而非只有 `<img>`）
2. **准确率抽查**：抽 10 张资金类凭证逐张人工比对，金额/账号/日期三类字段准确率 ≥ 90% 通过
3. **`<img>` 标签确认**：决定 5.3 折叠正则修复是否保留
4. **耗时对比**：转换时长增幅 ≤ 约 50% 可接受

**实测不通过的预案**：
- 个别字段质量差 → 先调 `minPixels`/`maxPixels`（提高分辨率）再测
- 整体不达标 → 退回"图片二次提交"方案：`{stem}_images/` 逐张提交 PaddleOCR 单图任务（`promptLabel: "ocr"`），绕开版面上下文专注单张凭证；代价是每张图一次 API 请求

### 6.2 错误处理

| 场景 | 处理 |
|------|------|
| 开启新参数后转换失败率上升 | 转换层已有重试；`image_ocr_enabled=false` 一键回退 |
| 识别产出乱码/幻觉文本 | 沿用既有后处理链（`_strip_hallucinated_tables`、`_fix_ocr_errors`），不新增特殊逻辑 |
| 资金流子步骤 LLM 调用失败 | 复用流水线既有重试与断点续传，从 4e 重跑 |
| 无资金证据 / 无起诉书 | 降级策略（见 5.4），输出说明页，不阻塞流水线 |

### 6.3 配额与性能

- `useOcrForImageBlock`/`useSealRecognition` 是任务内能力，不增加 API 请求次数；单页耗时可能略增（实测验证）
- 资金流子步骤为单次 LLM 调用，复用 `llm_client` 并发限流/重试/缓存统计；输入超预算时按既有分块策略处理

### 6.4 测试

1. 单元测试（pytest）：
   - `build_optional_payload` 在 `image_ocr_enabled` 开/关下分别包含/排除新参数
   - 折叠正则修复：`<img>` 连续图片可折叠；MinerU `![]()` 输入输出逐字节不变
   - 资金流子步骤降级分支（无资金证据/无起诉书）
2. E2E：`tests/frontend-render.spec.ts` 补断言"分析完成后资金流 wiki 页存在"（成本过高则以单元 + 手工验证替代，实施时定夺）
3. 说明书：`docs/user-manual.html` 更新（引擎差异 + 资金流功能），跑 `scripts/sync-manual.sh`

## 7. 改动文件清单

| 文件 | 改动 |
|------|------|
| `backend/paddleocr_remote.py` | payload 增加 2 参数；抽 `build_optional_payload()` |
| `backend/paddleocr_async.py` | 改为 import 共享 payload 构造函数 |
| `backend/config_manager.py` | 新增 `image_ocr_enabled` 配置项 |
| `backend/pdf_to_md.py` | `_fold_consecutive_images` 正则扩展 `<img>`（实测确认后） |
| `backend/mineru_async.py` | 同步折叠正则副本（同上；不改变 MinerU 产物行为） |
| `backend/analysis_pipeline.py` | 新增 4e 资金流梳理子步骤 |
| `frontend/src/pages/SettingsPage.tsx` | 图片识别开关 UI |
| `docs/user-manual.html` | 引擎差异 + 资金流功能说明 |
| `tests/` | 新增单元测试 |

## 8. 风险与未决项

| 项 | 说明 | 解决时点 |
|----|------|----------|
| `<img>` 标签保留 | 开启识别后图片引用是否保留未文档化 | 6.1 实测 |
| 识别准确率 | 文档无质量承诺，资金字段错一个数字即事故 | 6.1 实测（≥90% 验收线） |
| 无图片识别时资金流质量 | MinerU 转换的案件只有言词证据可用，对照结论会偏"仅言词证据" | 产品层面接受，说明书注明 |
