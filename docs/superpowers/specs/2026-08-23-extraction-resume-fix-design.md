# 证据提取断点续传修复设计

> 日期：2026-08-23
> 状态：已获用户批准
> 方案：A（修复启动清理，激活既有断点续传机制）

## 背景

2026-08-23 冯叶飞案（14 卷）证据提取中途停止后，已完成的 4 卷（第 2-5 卷）产出全部丢失，
新一轮 14 卷全部重提。

## 根因

提取流程的断点续传机制自 2026-05-18（`9dd15fa`）引入起即自毁：

1. 每卷提取完成后，结果写入 `evidence/_temp_extract/{卷名}/evid_*.md`，
   并写 `{卷名}.done` 标记，`completed_sources` 增量写入 index.json
2. 合并落库（temp → evidence/ + index.json 条目）在全部卷完成后才执行
3. 但 `_do_extract_evidence` 开头**无条件** `rmtree(_temp_extract)`——
   本意是"完成后清理"，实际写成了"启动时清理"
4. 结果：任何中断（用户停止/卡死/关应用/进程被杀）→ 下轮启动即删除全部
   .done 标记和 temp 产出 → 断点续传从未真正生效

## 卷内已有缓存机制（本次一并激活）

| 路径 | 缓存 | 校验 |
|------|------|------|
| 多笔录卷（按份提取） | `_perdoc_{i:03d}.json` 每份笔录 | text_len |
| 大卷（分块并发） | `.chunk_{i}.done` + `_chunk_{i}_blocks.json` | `_chunking_meta.json`（预算+text_len+块数） |
| 小卷（单块直发） | 无（一次调用，全有或全无） | — |

evid 证据文件只在整卷完成后一次性写入，不存在半个 evid 文件混入合并的风险。

## 改动点

### 1. 启动不再删临时目录（`case_manager.py` `_do_extract_evidence`）

删除开头的无条件 `rmtree(_temp_extract)`。正常合并完成后的清理逻辑已存在
（"临时目录已清理"），保持不动。

效果：中断后下轮运行时，带 .done 的卷跳过提取，合并阶段直接从 temp 读上轮
产出落库（合并循环已有此分支：`.done` 卷从子目录读取证据）。

### 2. 重提某卷时只删旧 evid 产出，保留卷内缓存

`extract_and_save_temp` 开头：删除 `file_temp_dir` 下的 `evid_*.md` 和旧
`{stem}.done`，**保留** `_perdoc_*.json` / `.chunk_*` / `_chunking_meta.json`。

目的：防止上次完成的旧 evid 文件在新合并时被 glob 混入（新产出可能比旧产
出少），同时保住卷内续传缓存。

### 3. .done 加内容指纹

.done 文件写入 md 正文字符数（text_len）；启动/使用校验：与当前 md 不一致
则删除 .done 重提。覆盖"md 重转/乱码修复后内容变了但标记还在"的复活场景。

### 4. 重提入口精确清除对应卷的 temp 状态

- `prune_failed_evidence`（失败空壳重提）：删该卷 `{stem}.done` + temp 子目录全部内容
  （失败重提必须整卷重来，缓存也不能留——占位块的 doc 命中缓存会跳过 LLM）
- `invalidate_evidence_for_source`（乱码修复重提，`page_rotation.py`）：同上
- `clear-evidence`：整目录 rmtree，已覆盖，不改

## 中断行为矩阵（验收标准）

| 中断时机 | 重提代价 |
|---------|---------|
| 整卷已完成（有 .done） | 零——跳过提取，temp 产出直接落库 |
| 卷内按份提取到一半 | 只补剩余笔录份（_perdoc 缓存命中） |
| 卷内分块到一半 | 只补剩余块（.chunk 缓存命中） |
| 单块小卷调到一半 | 重跑一次调用 |
| 清除证据后 | 全部重来（整目录已删） |
| 乱码修复/失败重提某卷 | 该卷整卷重来（含缓存清除），其他卷不受影响 |
| md 重转内容变化 | 该卷 text_len 不一致 → 整卷重来 |

## 测试计划

新增（tests/test_extract_resume_temp.py）：

1. `test_done_survives_restart_and_merges`：模拟两卷 temp 产物 + .done → 重跑 →
   断言跳过提取且合并落库正确
2. `test_done_invalidated_on_text_change`：md 内容变化 → .done 失效重提
3. `test_prune_clears_done_and_temp`：prune_failed_evidence 后该卷 .done/temp 被删
4. `test_invalidate_clears_done_and_temp`：invalidate_evidence_for_source 同上
5. `test_temp_cleaned_after_successful_merge`：正常完成后 temp 被清理
6. `test_partial_perdoc_cache_reused`：卷内中断后重提只补剩余份（缓存命中不重复调 LLM）

回归：tests/test_extract_all_done_resume.py、test_extract_retry_failed.py、
test_evidence_perdoc.py、test_chunk_overlap_resume.py 全绿。

## 明确不做（YAGNI）

- 每卷完成即落库（方案 B 的增量合并）：编号顺序与并发复杂度不值当
- 中断时兜底合并（方案 C）：进程强杀场景本就靠 temp 复用兜底
- prompt/罪名变化的缓存失效：现状 processed_sources 同样不感知，超出本范围
