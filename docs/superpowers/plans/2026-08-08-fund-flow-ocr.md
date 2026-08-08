# 图片 OCR 增强 + 资金流梳理验证 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PaddleOCR 引擎转换时将 PDF 中全部图片块 OCR 为文字内联进 MD；分析流水线新增 4e 资金流梳理子步骤，将起诉书指控金额与证据逐笔对照验证。

**Architecture:** PaddleOCR 走原生参数开关（`useOcrForImageBlock` + `useSealRecognition`），payload 抽为共享构造函数；MinerU 引擎完全不动。4e 子步骤插入步骤 4 的 4b 与 4d 之间，产出 `02-事实要素/资金流梳理.md`，并显式注入 4d/4.5/5 的上下文装配处。

**Tech Stack:** Python 3.13 / FastAPI / pytest（`tests/`，conftest 已把 `backend/` 和 `scripts/` 加入 sys.path）/ React + TypeScript（设置页）。

**Spec:** `docs/superpowers/specs/2026-08-08-fund-flow-ocr-design.md`

**通用约定：**
- 所有代码注释用中文。
- pytest 运行命令统一为 `python3 -m pytest tests/<file> -v`（在仓库根目录执行）。
- 提交信息格式 `<type>: <描述>`（中文描述，无归属尾注）。
- 后端无热重载，本计划全部为模块级改动 + pytest，不需要启动服务验证（除 Task 8 前端构建）。

---

### Task 1: 先行实测门禁 — PaddleOCR 图片识别参数实测

设计文档 6.1 节要求：正式开发前必须用真实案卷 PDF 实测 `useOcrForImageBlock` 的功能与质量。本任务产出实测脚本；实测动作由用户执行（需要真实案卷 PDF 和有效 PaddleOCR Token）。

**Files:**
- Create: `scripts/test_paddle_image_ocr.py`

- [ ] **Step 1: 编写实测脚本**

```python
#!/usr/bin/env python3
"""PaddleOCR 图片识别参数实测脚本（一次性验证工具，不进产品代码）

用法：
    python3 scripts/test_paddle_image_ocr.py <案卷PDF路径> [--baseline]

    默认：开启 useOcrForImageBlock + useSealRecognition 转换并分析产物
    --baseline：用原参数转换，用于耗时对比

验收对照（设计文档 6.1）：
1. MD 中图片位置出现识别文字
2. 抽 10 张资金类凭证人工比对，金额/账号/日期准确率 ≥ 90%
3. <img> 标签是否保留（决定折叠正则修复是否保留）
4. 与 --baseline 耗时对比，增幅 ≤ 约 50%
"""
import re
import sys
import time
import tempfile
from pathlib import Path

# 让脚本可 import backend 模块
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import paddleocr_remote


def analyze_md(md_path: Path) -> None:
    """分析产物 MD：统计 <img> 标签数量，并打印每个标签后的识别文字片段"""
    text = md_path.read_text(encoding="utf-8")
    img_tags = re.findall(r"<img\s[^>]*>", text)
    print(f"\n===== 产物分析 =====")
    print(f"MD 文件: {md_path}")
    print(f"总字符数: {len(text)}")
    print(f"<img> 标签数: {len(img_tags)}（>0 说明图片引用保留，折叠修复有意义）")

    # 打印每个 <img> 标签后 300 字符，供人工比对识别质量
    for i, m in enumerate(re.finditer(r"<img\s[^>]*>", text)):
        after = text[m.end():m.end() + 300].strip()
        print(f"\n--- 图片 {i + 1} 后续内容（前 300 字符）---")
        print(after if after else "（无识别文字）")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    baseline = "--baseline" in sys.argv
    if not pdf_path.exists():
        print(f"文件不存在: {pdf_path}")
        sys.exit(1)

    if not baseline:
        # 在原 payload 基础上追加两个新参数（不改产品代码）
        paddleocr_remote.PADDLEOCR_OPTIONAL_PAYLOAD = {
            **paddleocr_remote.PADDLEOCR_OPTIONAL_PAYLOAD,
            "useOcrForImageBlock": True,
            "useSealRecognition": True,
        }
        print("[实测] 已开启 useOcrForImageBlock + useSealRecognition")
    else:
        print("[实测] baseline 模式（原参数）")

    out_dir = Path(tempfile.mkdtemp(prefix="paddle_image_ocr_"))
    start = time.time()
    result = paddleocr_remote.paddleocr_convert(pdf_path, out_dir)
    elapsed = time.time() - start

    if not result or not result[0]:
        print("[实测] 转换失败")
        sys.exit(1)

    print(f"\n[实测] 转换耗时: {elapsed:.0f} 秒")
    analyze_md(out_dir / f"{pdf_path.stem}.md")
    print(f"\n产物目录: {out_dir}（图片在 {{stem}}_images/，可对照原图人工核验）")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 用户执行实测（人工门禁）**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm
# 确认已配置 PaddleOCR Token（配置文件 DATA_DIR/criminal-llm-config.json 的 paddleocr_token，或环境变量 PADDLEOCR_TOKEN）
python3 scripts/test_paddle_image_ocr.py <含转账截图的案卷.pdf>
python3 scripts/test_paddle_image_ocr.py <同一PDF> --baseline
```

人工核对四条验收标准（设计文档 6.1）：
1. 图片位置出现识别文字；2. 抽 10 张凭证比对准确率 ≥ 90%；3. `<img>` 标签是否保留；4. 耗时增幅 ≤ 约 50%。

注意：本脚本通过 monkeypatch 模块常量注入新参数，**必须在 Task 3 实施之前运行**；Task 3 之后 payload 改由 `build_optional_payload()` 从配置读取，届时开关直接改配置即可。

- [ ] **Step 3: 记录实测结论并提交**

把实测结论（通过/不通过、准确率、`<img>` 是否保留、耗时增幅）追加到设计文档「8. 风险与未决项」之后，新增「9. 实测结论」一节。

**门禁规则：**
- 实测通过且 `<img>` 保留 → 继续全部后续任务
- 实测通过但 `<img>` 被移除 → 跳过 Task 4（折叠正则修复），在设计文档 5.3 标注移除原因
- 实测不通过 → 停止，回到设计文档 6.1 的预案（调 minPixels/maxPixels 或改图片二次提交方案）重新评估

```bash
git add scripts/test_paddle_image_ocr.py docs/superpowers/specs/2026-08-08-fund-flow-ocr-design.md
git commit -m "test: PaddleOCR 图片识别参数实测脚本及实测结论"
```

---

### Task 2: 配置项 image_ocr_enabled（后端）

**Files:**
- Modify: `backend/config_manager.py:16-29`（DEFAULTS）、`backend/config_manager.py:65-90`（get_config_status）
- Test: `tests/test_image_ocr_config.py`

- [ ] **Step 1: 写失败测试**

```python
"""image_ocr_enabled 配置项测试"""
import json

import config_manager


def test_default_enabled(tmp_path, monkeypatch):
    """未配置时默认开启"""
    monkeypatch.setattr(config_manager, "CONFIG_PATH", tmp_path / "cfg.json")
    assert config_manager.load_config()["image_ocr_enabled"] is True


def test_user_can_disable(tmp_path, monkeypatch):
    """用户显式关闭后读取为 False"""
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"image_ocr_enabled": False}), encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_PATH", cfg)
    assert config_manager.load_config()["image_ocr_enabled"] is False


def test_config_status_includes_key(tmp_path, monkeypatch):
    """GET /api/config 的状态字段包含 image_ocr_enabled（供设置页表单填充）"""
    monkeypatch.setattr(config_manager, "CONFIG_PATH", tmp_path / "cfg.json")
    status = config_manager.get_config_status()
    assert status["image_ocr_enabled"] is True
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_image_ocr_config.py -v`
Expected: FAIL（`KeyError: 'image_ocr_enabled'`）

- [ ] **Step 3: 实现**

`backend/config_manager.py` DEFAULTS 字典中（`"pdf_engine"` 行之后）添加：

```python
    "image_ocr_enabled": True,      # PaddleOCR 图片块文字识别开关（转账凭证/流水截图）
```

`get_config_status()` 返回字典中（`"paddleocr_token_value"` 行之后）添加：

```python
        "image_ocr_enabled": config.get("image_ocr_enabled", True),
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_image_ocr_config.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add backend/config_manager.py tests/test_image_ocr_config.py
git commit -m "feat: 新增 image_ocr_enabled 配置项（默认开启）"
```

---

### Task 3: PaddleOCR payload 共享构造函数 + 图片识别参数

**Files:**
- Modify: `backend/paddleocr_remote.py:37-64`（payload 定义）、`backend/paddleocr_remote.py:133-140`（_submit_job 使用处）
- Modify: `backend/paddleocr_async.py:73-98`（删除重复 payload）、`backend/paddleocr_async.py:620-625`（_submit_job 使用处）
- Test: `tests/test_paddle_payload.py`

- [ ] **Step 1: 写失败测试**

```python
"""PaddleOCR optionalPayload 构造函数测试"""
import paddleocr_remote
import paddleocr_async


def test_payload_enabled_by_default(monkeypatch):
    """image_ocr_enabled 开启时 payload 包含图片识别参数"""
    monkeypatch.setattr("config_manager.get_config_value", lambda key, default="": True)
    payload = paddleocr_remote.build_optional_payload()
    assert payload["useOcrForImageBlock"] is True
    assert payload["useSealRecognition"] is True


def test_payload_disabled(monkeypatch):
    """image_ocr_enabled 关闭时 payload 回退旧行为（不含新参数）"""
    monkeypatch.setattr("config_manager.get_config_value", lambda key, default="": False)
    payload = paddleocr_remote.build_optional_payload()
    assert "useOcrForImageBlock" not in payload
    assert "useSealRecognition" not in payload
    # 原有参数不受影响
    assert payload["useLayoutDetection"] is True
    assert payload["mergeTables"] is True


def test_async_module_shares_builder():
    """paddleocr_async 不再重复定义 payload，改为共享 paddleocr_remote 的构造函数"""
    assert not hasattr(paddleocr_async, "PADDLEOCR_OPTIONAL_PAYLOAD"), \
        "paddleocr_async 应删除重复定义的 PADDLEOCR_OPTIONAL_PAYLOAD"
    assert paddleocr_async.build_optional_payload is paddleocr_remote.build_optional_payload
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_paddle_payload.py -v`
Expected: FAIL（`AttributeError: module 'paddleocr_remote' has no attribute 'build_optional_payload'`）

- [ ] **Step 3: 实现**

`backend/paddleocr_remote.py`：保留 `PADDLEOCR_OPTIONAL_PAYLOAD` 常量定义（其他可能的引用方不受影响），在其后新增：

```python
def build_optional_payload() -> dict:
    """构建 optionalPayload，按 image_ocr_enabled 配置注入图片识别参数

    useOcrForImageBlock：识别图片块中的文字（转账凭证/银行流水截图）
    useSealRecognition：印章识别（流水/凭证常见印章）
    """
    payload = dict(PADDLEOCR_OPTIONAL_PAYLOAD)
    try:
        from config_manager import get_config_value
        enabled = get_config_value("image_ocr_enabled", True)
    except ImportError:
        enabled = True
    if enabled:
        payload["useOcrForImageBlock"] = True
        payload["useSealRecognition"] = True
    return payload
```

`_submit_job` 中（`paddleocr_remote.py` 约 136-139 行）：

```python
    data = {
        "model": PADDLEOCR_MODEL,
        "optionalPayload": json.dumps(build_optional_payload()),
    }
```

`backend/paddleocr_async.py`：删除本地 `PADDLEOCR_OPTIONAL_PAYLOAD` 常量定义（73-98 行），在文件顶部 import 区添加：

```python
from paddleocr_remote import build_optional_payload
```

`_submit_job` 中（约 624-625 行）把：

```python
            data.add_field("optionalPayload", json.dumps(PADDLEOCR_OPTIONAL_PAYLOAD))
```

改为：

```python
            data.add_field("optionalPayload", json.dumps(build_optional_payload()))
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_paddle_payload.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add backend/paddleocr_remote.py backend/paddleocr_async.py tests/test_paddle_payload.py
git commit -m "feat: PaddleOCR 开启图片块文字识别与印章识别，payload 抽为共享构造函数"
```

---

### Task 3A: 修复后处理吞数字缺陷（实测发现，必须修复）

实测发现 `backend/paddleocr_remote.py:393-394` 的清理规则 8 `re.sub(r'>\d+>{0,3}-?\)?', '', text)` 会吞掉 `<td>` 后紧跟的数字：`<td>8,051</td>` 中的 `>8` 被当作乱码删除，导致表格金额全部被破坏（如 `8,051` → `,051`）。该规则本意是清理 `>12>>-)` 类 OCR 噪声，但 `>{0,3}` 允许零个 `>`，误伤正常表格单元格。

**Files:**
- Modify: `backend/paddleocr_remote.py:393-394`
- Test: `tests/test_latex_cleanup_digits.py`

- [ ] **Step 1: 写失败测试**

```python
"""后处理吞数字缺陷回归测试（实测发现：规则 8 吃掉 <td> 后的金额）"""
from paddleocr_remote import _clean_latex_markup


def test_table_cell_amount_preserved():
    """表格单元格中的金额必须完整保留"""
    text = "<td>8,051</td><td>25,578</td><td>-10,404</td>"
    assert _clean_latex_markup(text) == text


def test_table_cell_plain_digit_preserved():
    """单元格内容为纯数字时保留"""
    text = "<td>8</td><td>0</td><td>14</td>"
    assert _clean_latex_markup(text) == text


def test_noise_still_removed():
    """原目标噪声 >数字>>-) 仍被清理"""
    assert _clean_latex_markup("正文>12>>-)正文") == "正文正文"


def test_inline_gt_digit_without_trailing_gt_preserved():
    """无尾 > 的 >数字 不是目标噪声，保留（误伤面收窄的边界锁定）"""
    assert ">5人" in _clean_latex_markup("见证人>5人在场")
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_latex_cleanup_digits.py -v`
Expected: `test_table_cell_amount_preserved` 和 `test_table_cell_plain_digit_preserved` FAIL

- [ ] **Step 3: 实现**

`backend/paddleocr_remote.py` 第 393-394 行，把：

```python
    # 8. 残留乱码：>数字>>-)（OCR 把表格边框识别成尖括号序列）
    text = re.sub(r'>\d+>{0,3}-?\)?', '', text)
```

改为：

```python
    # 8. 残留乱码：>数字>>-)（OCR 把表格边框识别成尖括号序列）
    # 注意：数字后必须至少跟一个 > 才算噪声，否则会吞掉 <td>8,051</td> 中的 >8（实测教训）
    text = re.sub(r'>\d+>+-?\)?', '', text)
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_latex_cleanup_digits.py -v`
Expected: 4 passed

- [ ] **Step 5: 离线复验（用实测保存的原始 JSONL，不消耗 API）**

```bash
python3 << 'EOF'
import json, re
from pathlib import Path
import sys
sys.path.insert(0, 'backend')
from paddleocr_remote import _apply_postprocessing

raw = Path('/var/folders/5l/9n828fys6lx8zm9wcnqjmd180000gn/T/paddle_image_ocr_nofty66o/实测_第7卷_p110-117_json/content_list.jsonl').read_text(encoding='utf-8')
# 拼接原始 markdown 文本，跑后处理，验证金额存活
texts = []
for line in raw.strip().split('\n'):
    for pr in json.loads(line).get('result', {}).get('layoutParsingResults', []):
        md = pr.get('markdown', {})
        if isinstance(md, dict):
            texts.append(md.get('text', ''))
processed = _apply_postprocessing('\n\n'.join(texts))
for num in ['8,051', '25,578', '17,576', '9,508', '32,494', '9,292']:
    n = processed.count(num)
    print(f'{num}: {n} 次', '✅' if n > 0 else '❌ 被吞')
assert all(processed.count(num) > 0 for num in ['8,051', '25,578', '17,576']), '金额仍被吞'
print('复验通过：金额字段在后处理后完整存活')
EOF
```

Expected: 全部 ✅，最后一行打印"复验通过"

- [ ] **Step 6: 提交**

```bash
git add backend/paddleocr_remote.py tests/test_latex_cleanup_digits.py
git commit -m "fix: 后处理规则8吞掉表格单元格数字（实测发现），收窄为数字后必须跟>"
```

---

### Task 3B: 修复幻觉表格过滤器误删真实台账（审查发现，必须修复）

Task 3A 审查发现第二个吞金额成因：`backend/pdf_to_md.py:330-365` 的 `_strip_hallucinated_tables` 判据是"非数字单元格内容重复 ≥5 次即整表删除"。真实台账中交易对方/产品名同列合法重复（如实测案中"3G时尚莫尼卡"重复 20+ 次），整张流水表被误删。且该过滤器只匹配无属性的 `<table>`/`<td>`，PaddleOCR 产物（`<table border=1 ...>`、`<td style=...>`）从未被检测——覆盖也有缺陷。

修复方向：判据改为**行级重复**——整行内容完全相同的行重复 ≥5 次才判幻觉（VLM 循环幻觉的特征是整行复读；真实台账每行的单号/时间/金额不同）。表格/单元格正则放宽到允许属性。

**Files:**
- Modify: `backend/pdf_to_md.py:330-365`（_strip_hallucinated_tables）
- Test: `tests/test_hallucination_filter.py`

- [ ] **Step 1: 写失败测试**

```python
"""幻觉表格过滤器误伤修复测试（真实台账同列重复不应被删）"""
from pdf_to_md import _strip_hallucinated_tables


def _make_ledger_table(rows: int) -> str:
    """构造真实台账：交易对方同列重复，但单号/金额逐行不同"""
    body = "".join(
        f"<tr><td>2400{i:02d}</td><td>3G时尚莫尼卡</td><td>唐某</td>"
        f"<td>{8000 + i},051</td><td>未付</td></tr>"
        for i in range(rows)
    )
    return f"<table><tr><td>单号</td><td>对方</td><td>经手</td><td>金额</td><td>状态</td></tr>{body}</table>"


def test_real_ledger_preserved():
    """同列重复 ≥5 次但逐行内容不同的真实台账必须保留（实测误伤回归）"""
    text = f"前文\n\n{_make_ledger_table(10)}\n\n后文"
    result = _strip_hallucinated_tables(text)
    assert "3G时尚莫尼卡" in result
    assert "幻觉表格已移除" not in result


def test_identical_rows_hallucination_removed():
    """整行完全重复 ≥5 次（VLM 循环幻觉特征）仍判定幻觉并移除"""
    row = "<tr><td>项目</td><td>3G时尚莫尼卡</td><td>8,051</td><td>未付</td></tr>"
    table = f"<table>{row * 6}</table>"
    result = _strip_hallucinated_tables(f"前文\n\n{table}\n\n后文")
    assert "幻觉表格已移除" in result
    assert "前文" in result and "后文" in result


def test_small_table_untouched():
    """行数不足 5 的小表格不检测"""
    table = _make_ledger_table(3)
    assert _strip_hallucinated_tables(table) == table


def test_paddle_style_table_attributes_supported():
    """PaddleOCR 风格（<table border=1 ...> / <td style=...>）也纳入行级检测"""
    row = ("<tr><td style='text-align: center;'>项目</td>"
           "<td style='text-align: center;'>幻觉内容</td></tr>")
    table = f"<table border=1 style='margin: auto;'>{row * 6}</table>"
    result = _strip_hallucinated_tables(table)
    assert "幻觉表格已移除" in result
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_hallucination_filter.py -v`
Expected: `test_real_ledger_preserved` FAIL（被误删）；`test_paddle_style_table_attributes_supported` FAIL（带属性表格未纳入检测）

- [ ] **Step 3: 实现**

`backend/pdf_to_md.py` 的 `_strip_hallucinated_tables` 整体替换为：

```python
def _strip_hallucinated_tables(text: str) -> str:
    """检测并移除 VLM 模型产生的整页幻觉表格

    判据（行级）：整行内容完全相同的行重复 ≥5 次，视为 VLM 循环幻觉，
    将整个 <table>...</table> 块替换为注释标记。

    注意：不要按"单列内容重复"判断——真实台账中交易对方/产品名
    同列合法重复（实测教训：重复 20 次的真实流水表被误删）。
    """
    import re

    def _check_table(match: re.Match) -> str:
        table_html = match.group(0)
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
        if len(rows) < 5:
            return table_html  # 小表格不检测

        # 行级签名：整行单元格内容完全一致才算重复（真实台账每行单号/金额不同）
        row_counts: dict[tuple, int] = {}
        for r in rows:
            cells = tuple(
                re.sub(r'<[^>]+>', '', c).strip()
                for c in re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)
            )
            if not any(cells):
                continue
            row_counts[cells] = row_counts.get(cells, 0) + 1

        for sig, count in row_counts.items():
            if count >= 5:
                preview = " ".join(sig)[:20]
                print(f"[幻觉检测] 表格中相同行重复 {count} 次（「{preview}」），移除幻觉表格")
                return f'\n<!-- 幻觉表格已移除：相同行重复 {count} 次 -->\n'

        return table_html

    # 兼容 PaddleOCR 带属性的 <table border=1 ...>
    return re.sub(r'<table[^>]*>.*?</table>', _check_table, text, flags=re.DOTALL)
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_hallucination_filter.py tests/test_latex_cleanup_digits.py -v`
Expected: 全部 passed

- [ ] **Step 5: 真实数据复验**

```bash
python3 << 'EOF'
import sys
sys.path.insert(0, 'backend')
from pdf_to_md import _strip_hallucinated_tables
from pathlib import Path
# 用实测案卷的 MinerU 产物（含真实流水表）验证不再误删
md = Path.home() / 'Documents/.criminal-llm-data/cases/case_6330558b/案件_顾某某诈骗罪_20260714/md/顾某某第7卷_去水印.md'
text = md.read_text(encoding='utf-8')
before = text.count('3G时尚莫尼卡')
after = _strip_hallucinated_tables(text).count('3G时尚莫尼卡')
print(f'修复前过滤器输入中 3G时尚莫尼卡: {before} 次, 过滤后存活: {after} 次')
assert after == before, '真实台账仍被误删'
print('复验通过：真实台账完整保留')
EOF
```

Expected: 复验通过

- [ ] **Step 6: 提交**

```bash
git add backend/pdf_to_md.py tests/test_hallucination_filter.py
git commit -m "fix: 幻觉表格过滤器改为行级重复判据，修复真实台账同列重复被误删"
```

---

### Task 4: 图片折叠正则修复（实测已确认 `<img>` 保留，本任务执行）

**Files:**
- Modify: `backend/pdf_to_md.py:612-666`（_fold_consecutive_images）
- Modify: `backend/mineru_async.py:260-298`（同名副本同步）
- Test: `tests/test_fold_images.py`

注意：折叠块文案 `📎 签名/印章图片（共 N 张，点击展开）` **保持不变**——改文案会改变 MinerU 产物，超出本次范围。

- [ ] **Step 1: 写失败测试**

```python
"""_fold_consecutive_images 正则扩展测试（支持 <img> 标签）"""
from pdf_to_md import _fold_consecutive_images


def test_markdown_images_still_folded():
    """MinerU 格式 ![]() 输入：修复前后行为一致（锁定回归）"""
    text = "前文\n\n![](a.jpg)\n\n![](b.jpg)\n\n后文"
    folded, count = _fold_consecutive_images(text)
    assert count == 1
    assert "<details>" in folded
    assert "![](a.jpg)" in folded and "![](b.jpg)" in folded
    assert "前文" in folded and "后文" in folded


def test_img_tags_folded():
    """PaddleOCR 格式 <img> 连续标签可折叠"""
    text = '前文\n\n<img src="./x_images/1.jpg">\n\n<img src="./x_images/2.jpg">\n\n后文'
    folded, count = _fold_consecutive_images(text)
    assert count == 1
    assert "<details>" in folded
    assert '<img src="./x_images/1.jpg">' in folded


def test_img_tag_with_following_text_not_folded_together():
    """<img> 标签与识别文字混合时，文字行不打断已折叠图片块之外的结构"""
    text = '<img src="./x_images/1.jpg">\n\n识别出的凭证文字\n\n<img src="./x_images/2.jpg">'
    folded, count = _fold_consecutive_images(text)
    # 两组图片被文字分隔，各自成块
    assert count == 2
    assert "识别出的凭证文字" in folded


def test_mineru_output_byte_identical():
    """MinerU 典型产物修复前后输出逐字节一致（用旧逻辑预计算期望值）"""
    text = "段落一\n\n![](images/a.jpg)\n\n![](images/b.jpg)\n\n![](images/c.jpg)\n\n段落二"
    expected = (
        "段落一\n\n"
        "<details><summary>📎 签名/印章图片（共 3 张，点击展开）</summary>\n\n"
        "![](images/a.jpg)\n\n![](images/b.jpg)\n\n![](images/c.jpg)\n"
        "</details>\n\n段落二"
    )
    folded, count = _fold_consecutive_images(text)
    assert count == 1
    assert folded == expected
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_fold_images.py -v`
Expected: `test_img_tags_folded` 和 `test_img_tag_with_following_text_not_folded_together` FAIL；其余 PASS

- [ ] **Step 3: 实现**

`backend/pdf_to_md.py` 的 `_fold_consecutive_images` 中，把 is_image 判定行（622 行）：

```python
    is_image = [bool(re.match(r'^!\[.*\]\([^)]+\)$', line.strip())) for line in lines]
```

改为：

```python
    # 同时匹配 MinerU 的 ![]() 和 PaddleOCR 的 <img> 单行标签
    is_image = [
        bool(
            re.match(r'^!\[.*\]\([^)]+\)$', line.strip())
            or re.match(r'^<img\s[^>]*>$', line.strip())
        )
        for line in lines
    ]
```

`backend/mineru_async.py` 的同名副本（约 263 行）做相同修改（保持两份代码逻辑一致）。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_fold_images.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add backend/pdf_to_md.py backend/mineru_async.py tests/test_fold_images.py
git commit -m "fix: 图片折叠正则支持 <img> 标签，PaddleOCR 产物连续图片可折叠"
```

---

### Task 5: 4e 资金流梳理子步骤

**Files:**
- Modify: `backend/analysis_pipeline.py`（新增 `_collect_fund_evidence` 方法；`step4_build_case_wiki` 中 SUB_STEPS 与 4e 块，约 1038、1292-1294 行处）
- Test: `tests/test_fund_flow.py`

- [ ] **Step 1: 写失败测试**

```python
"""步骤 4e 资金流梳理子步骤测试"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import analysis_pipeline
from analysis_pipeline import AnalysisPipeline


def _make_pipeline(tmp_path: Path, with_fund: bool = True) -> AnalysisPipeline:
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    (analysis_dir / "summaries" / "讯问笔录").mkdir(parents=True)
    (analysis_dir / "preprocess").mkdir(parents=True)
    (analysis_dir / "step_1_result.json").write_text(json.dumps({
        "merged_files": [{"person": "张三", "type": "讯问笔录", "session_count": 1}]
    }), encoding="utf-8")
    (analysis_dir / "step_2_result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (analysis_dir / "summaries" / "讯问笔录" / "张三_共1次_总结.md").write_text(
        "张三供述：收到转账。", encoding="utf-8")

    # 证据目录：一份流水（含资金内容）+ 一份无关证据
    evidence_dir = case_path / "evidence"
    evidence_dir.mkdir(parents=True)
    fund_text = "银行账户交易明细\n\n2024年3月15日 收到李某转账 50000元\n\n余额 80000元" if with_fund else "普通谈话记录，与资金无关。"
    (evidence_dir / "001_流水.md").write_text(fund_text, encoding="utf-8")
    (evidence_dir / "002_其他.md").write_text("与案件无关的日常记录。", encoding="utf-8")
    (evidence_dir / "index.json").write_text(json.dumps({
        "evidence": [
            {"name": "银行账户交易明细", "md_file": "001_流水.md", "type": "书证"},
            {"name": "其他材料", "md_file": "002_其他.md", "type": "书证"},
        ]
    }), encoding="utf-8")
    return AnalysisPipeline("case_001", case_path)


def _patch_common(monkeypatch):
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("起诉书：指控张三诈骗，涉案金额50000元。", "起诉书")))
    monkeypatch.setattr("case_framework.fetch_case_rules", lambda charges, keywords=None, size=3: {})


def test_collect_fund_evidence_filters(tmp_path, monkeypatch):
    """资金段落抽取：只保留含资金关键词的段落，无关证据被过滤"""
    _patch_common(monkeypatch)
    pipe = _make_pipeline(tmp_path)
    result = pipe._collect_fund_evidence(max_chars=10000)
    assert "50000元" in result
    assert "001_流水.md" in result or "银行账户交易明细" in result
    assert "与案件无关的日常记录" not in result


def test_collect_fund_evidence_budget_cap(tmp_path, monkeypatch):
    """预算上限：超出 max_chars 的内容被截断"""
    _patch_common(monkeypatch)
    pipe = _make_pipeline(tmp_path)
    result = pipe._collect_fund_evidence(max_chars=50)
    assert len(result) <= 200  # 允许单块超出少量，但绝不能全量装入


def test_step4e_creates_fund_flow_page(tmp_path, monkeypatch):
    """4e 在 4b 之后、4d 之前执行；产出 02-事实要素/资金流梳理.md；prompt 含来源类型与四档结论要求"""
    _patch_common(monkeypatch)
    pipe = _make_pipeline(tmp_path)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "分析结果"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step4_build_case_wiki("张三", "诈骗罪"))

    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki" / "02-事实要素"
    assert (wiki / "资金流梳理.md").exists()

    idx_4b = next(i for i, c in enumerate(calls) if "待分析证据" in c)
    idx_4e = next(i for i, c in enumerate(calls) if "资金流" in c)
    idx_4d = next(i for i, c in enumerate(calls) if "证据链条的完整性" in c)
    assert idx_4b < idx_4e < idx_4d, "4e 必须位于 4b 之后、4d 之前"

    prompt_4e = calls[idx_4e]
    assert "来源类型" in prompt_4e
    assert "客观证据印证" in prompt_4e
    assert "仅言词证据" in prompt_4e
    assert "50000元" in prompt_4e  # 证据中的资金内容进入 prompt


def test_step4e_no_fund_evidence_degrades(tmp_path, monkeypatch):
    """无资金类证据：输出说明页并标记完成，不调用 LLM，不阻塞流水线"""
    _patch_common(monkeypatch)
    pipe = _make_pipeline(tmp_path, with_fund=False)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "分析结果"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step4_build_case_wiki("张三", "诈骗罪"))

    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki" / "02-事实要素"
    page = (wiki / "资金流梳理.md").read_text(encoding="utf-8")
    assert "未检测到资金类内容" in page
    # 用 4e 专属标记断言（下游 4d 注入资金流页属正常行为，不能笼统断言"资金流"字样）
    assert not any("逐笔对照" in c or "只做资金流重建" in c for c in calls), \
        "无资金证据时不应调用 LLM 做资金流分析"


def test_step4e_no_indictment_rebuild_only(tmp_path, monkeypatch):
    """无起诉书：只做资金流重建，prompt 中说明跳过对照验证"""
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("", "")))
    monkeypatch.setattr("case_framework.fetch_case_rules", lambda charges, keywords=None, size=3: {})
    pipe = _make_pipeline(tmp_path)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "分析结果"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step4_build_case_wiki("张三", "诈骗罪"))

    prompt_4e = next(c for c in calls if "资金流" in c)
    assert "只做资金流重建" in prompt_4e
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki" / "02-事实要素"
    assert (wiki / "资金流梳理.md").exists()


def test_step4e_resume_skips(tmp_path, monkeypatch):
    """断点续传：资金流梳理.md 已存在时跳过 4e"""
    _patch_common(monkeypatch)
    pipe = _make_pipeline(tmp_path)
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki" / "02-事实要素"
    wiki.mkdir(parents=True)
    (wiki / "资金流梳理.md").write_text("已有分析", encoding="utf-8")
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "分析结果"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step4_build_case_wiki("张三", "诈骗罪"))
    assert not any("只做资金流重建" in c or "逐笔对照" in c for c in calls)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_fund_flow.py -v`
Expected: FAIL（`AttributeError: 'AnalysisPipeline' object has no attribute '_collect_fund_evidence'`）

- [ ] **Step 3: 实现 — `_collect_fund_evidence` 方法**

在 `backend/analysis_pipeline.py` 的 `_load_md_files` 方法之后（约 304 行后）新增：

```python
    # 资金类内容关键词（覆盖流水/凭证/言词证据中的资金表述）
    _FUND_KEYWORDS_RE = None  # 惰性编译，见 _collect_fund_evidence

    def _collect_fund_evidence(self, max_chars: int) -> str:
        """从证据全文中抽取资金相关段落，控制 token 预算

        逐份证据按空行分段，保留命中资金关键词的段落；单份证据上限 3000 字，
        总量超 max_chars 停止追加。无命中返回空串。
        """
        import re as _re
        if AnalysisPipeline._FUND_KEYWORDS_RE is None:
            AnalysisPipeline._FUND_KEYWORDS_RE = _re.compile(
                r"(转账|汇款|收款|付款|银行流水|交易明细|银行卡|卡号|账号|支付宝|"
                r"微信(?:支付|红包|转账)|借条|欠条|涉案金额|资金|万元|\d+元)"
            )
        pattern = AnalysisPipeline._FUND_KEYWORDS_RE

        try:
            files = self._load_md_files()
        except ValueError:
            return ""

        parts = []
        total = 0
        for f in files:
            paragraphs = [p for p in f["text"].split("\n\n") if pattern.search(p)]
            if not paragraphs:
                continue
            excerpt = "\n\n".join(paragraphs)[:3000]
            block = f"### {f['filename']}\n{excerpt}\n"
            if total + len(block) > max_chars:
                break
            parts.append(block)
            total += len(block)
        return "\n".join(parts)
```

- [ ] **Step 4: 实现 — step4 中插入 4e**

`backend/analysis_pipeline.py` 约 1038 行，SUB_STEPS 改为：

```python
        SUB_STEPS = ["4a-指控要素", "4c-法律框架", "4b-证据摄入", "4e-资金流", "4d-综合结论"]
```

约 1292-1294 行（4b 循环结束后、4d 之前），把：

```python
        sub_done = 3
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4：生成综合结论")
```

改为：

```python
        sub_done = 3
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4：资金流梳理")

        # ===== 4e: 资金流梳理（4b 之后、4d 之前） =====
        if not self._wiki_page_exists("02-事实要素", "资金流梳理.md"):
            print("[步骤 4e] 梳理资金流...")
            fund_evidence = self._collect_fund_evidence(
                max_chars=int(context_budget.content_budget_chars() * 0.6)
            )
            if not fund_evidence.strip():
                self._save_wiki_page("02-事实要素", "资金流梳理.md",
                                     "本案证据中未检测到资金类内容，无需进行资金流梳理。")
                results_log["sub_steps"].append({"step": "4e", "name": "资金流梳理", "status": "no_fund_evidence"})
                print("[步骤 4e] 无资金类证据，跳过")
            else:
                # 无起诉书时只做资金流重建，不做对照验证
                has_indictment = bool(indictment_content) and "未发现起诉书" not in indictment_content
                indictment_section = (
                    f"## 指控要素（含涉案金额及计算方式）\n{indictment_content[:5000]}"
                    if has_indictment else
                    "## 指控要素\n本案未发现起诉书/起诉意见书，跳过对照验证，只做资金流重建"
                )
                try:
                    fund_prompt = f"""{indictment_section}

## 证据中的资金相关内容
{fund_evidence}

请完成以下分析：
1. 【资金流重建】从证据中抽取全部资金往来记录，输出时间序表格：
   | 时间 | 付款方 | 收款方 | 金额 | 渠道 | 来源类型 | 证据出处 |
   来源类型必须标注：客观证据·银行流水 / 客观证据·转账凭证 / 言词证据·被告人供述 / 言词证据·被害人陈述 / 言词证据·证人证言 等
2. 【逐笔对照验证】（仅当提供了指控要素时执行）以起诉书指控的每笔涉案金额为基准逐笔核对，输出对照表：
   | 指控笔次 | 指控金额 | 指控时间 | 来源类型 | 证据出处 | 核对结论 |
   核对结论分四档：
   - ✅客观证据印证：有流水/凭证直接支撑
   - 🗣仅言词证据：只有供述/证言提及、无客观证据印证（指出刑诉法第55条：重证据、不轻信口供，只有被告人供述不能定案）
   - ⚠️证据矛盾：言词与客观证据之间、或不同言词证据间金额/时间冲突（说明冲突点）
   - ❌无证据支撑：指控金额找不到任何来源
   另列 🔍反向发现：证据中有、起诉书未指控的大额资金往来
3. 【差异与疑点分析】
4. 【对辩护的意义】

请输出 Markdown 格式。"""
                    print(f"[预算] 步骤4e 资金流 prompt: {len(fund_prompt)} 字符 / 预算 {context_budget.content_budget_chars()}")
                    fund_analysis = await self.llm.chat([
                        {"role": "system", "content": "你是刑事律师，擅长经济犯罪案件的资金流分析。请基于证据材料梳理资金链条并验证指控金额。"},
                        {"role": "user", "content": fund_prompt},
                    ])
                    self._save_wiki_page("02-事实要素", "资金流梳理.md", fund_analysis)
                    results_log["sub_steps"].append({"step": "4e", "name": "资金流梳理", "status": "done"})
                    print("[步骤 4e] 完成资金流梳理")
                except Exception as e:
                    self._save_wiki_page("02-事实要素", "资金流梳理.md", f"分析失败：{e}")
                    results_log["sub_steps"].append({"step": "4e", "name": "资金流梳理", "status": "failed", "error": str(e)})
        else:
            results_log["sub_steps"].append({"step": "4e", "name": "资金流梳理", "status": "skipped"})

        sub_done = 4
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4：生成综合结论")
```

4d 结束处的 `sub_done = 4`（约 1349 行）改为 `sub_done = 5`。

- [ ] **Step 5: 运行确认通过**

Run: `python3 -m pytest tests/test_fund_flow.py tests/test_step4_reorder.py -v`
Expected: 全部 passed（含既有 step4 回归）

- [ ] **Step 6: 提交**

```bash
git add backend/analysis_pipeline.py tests/test_fund_flow.py
git commit -m "feat: 分析流水线新增 4e 资金流梳理子步骤（指控金额逐笔对照验证）"
```

---

### Task 6: 下游注入 — 4d / 4.5 / 步骤 5 消费资金流页

**Files:**
- Modify: `backend/analysis_pipeline.py:1314-1334`（4d prompt）、`backend/analysis_pipeline.py:1431-1437`（4.5 context_parts）、`backend/analysis_pipeline.py:2016-2024`（步骤 5 context join）
- Test: `tests/test_fund_flow_downstream.py`

- [ ] **Step 1: 写失败测试**

```python
"""资金流梳理页下游消费测试（4d / 4.5 / 步骤5）"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import analysis_pipeline
from analysis_pipeline import AnalysisPipeline

FUND_PAGE = "# 资金流梳理\n\n🗣 指控金额仅言词证据支撑，无流水印证。"


def _make_pipeline_with_fund_page(tmp_path: Path) -> AnalysisPipeline:
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    wiki = analysis_dir / "indictment_wiki"
    (wiki / "02-事实要素").mkdir(parents=True)
    (wiki / "03-证据分析").mkdir(parents=True)
    (wiki / "04-法律依据").mkdir(parents=True)
    (wiki / "02-事实要素" / "资金流梳理.md").write_text(FUND_PAGE, encoding="utf-8")
    (wiki / "03-证据分析" / "张三_讯问笔录.md").write_text("证据分析内容", encoding="utf-8")
    (wiki / "01-指控要素.md").write_text("指控要素内容", encoding="utf-8")
    (wiki / "06-综合结论.md").write_text("综合结论内容", encoding="utf-8")
    (wiki / "05-矛盾记录.md").write_text("矛盾记录内容", encoding="utf-8")
    (analysis_dir / "step_1_result.json").write_text(json.dumps({
        "merged_files": [{"person": "张三", "type": "讯问笔录", "session_count": 1}]
    }), encoding="utf-8")
    return AnalysisPipeline("case_001", case_path)


def test_4d_prompt_includes_fund_flow(tmp_path, monkeypatch):
    """4d 综合结论 prompt 注入资金流梳理内容"""
    pipe = _make_pipeline_with_fund_page(tmp_path)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "分析结果"

    pipe.llm.chat = fake_chat
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("起诉书内容", "起诉书")))
    monkeypatch.setattr("case_framework.fetch_case_rules", lambda charges, keywords=None, size=3: {})
    asyncio.run(pipe.step4_build_case_wiki("张三", "诈骗罪"))

    prompt_4d = next(c for c in calls if "证据链条的完整性" in c)
    assert "资金流梳理" in prompt_4d
    assert "仅言词证据支撑" in prompt_4d


def test_step45_context_includes_fund_flow(tmp_path, monkeypatch):
    """4.5 控辩对抗的上下文包含资金流梳理"""
    pipe = _make_pipeline_with_fund_page(tmp_path)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "对抗结果"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step45_debate_simulation("张三", "诈骗罪"))

    assert any("仅言词证据支撑" in c for c in calls), "4.5 各方 prompt 应包含资金流梳理内容"


def test_step5_context_includes_fund_flow(tmp_path, monkeypatch):
    """步骤 5 辩护意见的上下文中包含资金流梳理"""
    pipe = _make_pipeline_with_fund_page(tmp_path)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "辩护章节"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step5_defense_opinion("张三", "诈骗罪"))

    assert any("仅言词证据支撑" in c for c in calls), "步骤 5 prompt 应包含资金流梳理内容"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_fund_flow_downstream.py -v`
Expected: 3 个测试全部 FAIL（断言找不到资金流内容）

- [ ] **Step 3: 实现 — 4d 注入**

`backend/analysis_pipeline.py` 4d 段（约 1296 行 `if not self._wiki_page_exists("", "06-综合结论.md"):` 块内），在收集 legal_content 之后、构建 user_prompt 之前添加：

```python
            # 资金流梳理（4e 产物，存在则注入）
            fund_flow = self._load_wiki_page("02-事实要素", "资金流梳理.md")
            fund_flow_section = f"\n## 资金流梳理\n{fund_flow[:5000]}\n" if fund_flow.strip() else ""
```

user_prompt 中，`## 法律依据` 段之后插入 `{fund_flow_section}`，分析要求列表第 5 条之后添加一条：

```python
5. 对辩方有利的要点
6. 对控方不利的要点
7. 资金流与指控金额的印证情况（如提供了资金流梳理）
```

- [ ] **Step 4: 实现 — 4.5 注入**

约 1398-1407 行（加载 wiki 材料处），在 `wiki_legal` 加载后添加：

```python
        wiki_fund_flow = self._load_wiki_page("02-事实要素", "资金流梳理.md")[:5000]
```

约 1431-1437 行 `context_parts` 列表中，`f"## 法律依据\n{wiki_legal}" ...` 之后添加一项：

```python
            f"## 资金流梳理\n{wiki_fund_flow}" if wiki_fund_flow.strip() else None,
```

- [ ] **Step 5: 实现 — 步骤 5 注入**

约 1945-1947 行（加载 wiki 材料处）添加：

```python
        wiki_fund_flow = self._load_wiki_page("02-事实要素", "资金流梳理.md")[:5000]
```

约 2016-2024 行 `context = "\n\n".join([...])` 的列表中，`f"## 证据分析汇总\n{wiki_evidence_summary}" ...` 之后添加一项：

```python
                f"## 资金流梳理\n{wiki_fund_flow}" if wiki_fund_flow.strip() else None,
```

- [ ] **Step 6: 运行确认通过**

Run: `python3 -m pytest tests/test_fund_flow_downstream.py tests/test_fund_flow.py tests/test_step4_reorder.py -v`
Expected: 全部 passed

- [ ] **Step 7: 提交**

```bash
git add backend/analysis_pipeline.py tests/test_fund_flow_downstream.py
git commit -m "feat: 4d/4.5/步骤5 上下文注入资金流梳理页"
```

---

### Task 7: 设置页图片识别开关（前端）

**Files:**
- Modify: `frontend/src/pages/SettingsPage.tsx`（ConfigForm 接口约 157-196 行、默认值约 196-210 行、hasUnsavedChanges 约 227-244 行、loadConfigStatus 约 264-296 行、handleSave 约 302-340 行、PaddleOCR 配置区块约 830-900 行）

前端无单元测试框架，验证方式为类型检查 + 构建。

- [ ] **Step 1: ConfigForm 接口与默认值**

接口定义中（`paddleocr_token: boolean`/`string` 声明附近）两处都添加：

```ts
  image_ocr_enabled: boolean
```

`useState` 初始值对象中（`paddleocr_token: '',` 附近）添加：

```ts
    image_ocr_enabled: true,
```

- [ ] **Step 2: hasUnsavedChanges 检测**

返回的布尔表达式中（`config.paddleocr_token !== initialConfig.paddleocr_token ||` 行之后）添加：

```ts
      config.image_ocr_enabled !== initialConfig.image_ocr_enabled ||
```

- [ ] **Step 3: loadConfigStatus 加载**

`if (data.paddleocr_token_value) ...` 行之后添加（注意 False 也是有效值，不能用真值判断）：

```ts
      if (data.image_ocr_enabled !== undefined) updates.image_ocr_enabled = data.image_ocr_enabled
```

- [ ] **Step 4: handleSave 保存**

PUT 请求 body 中（`paddleocr_token: config.paddleocr_token.trim(),` 行之后）添加：

```ts
          image_ocr_enabled: config.image_ocr_enabled,
```

- [ ] **Step 5: UI 开关**

PaddleOCR 配置区块（`{config.pdf_engine === 'paddleocr' && (` 开头的 JSX 内，PaddleOCR Token 输入框之前）插入：

```tsx
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', cursor: 'pointer', marginBottom: '16px' }}>
                <input
                  type="checkbox"
                  checked={config.image_ocr_enabled}
                  onChange={e => setConfig(prev => ({ ...prev, image_ocr_enabled: e.target.checked }))}
                  style={{ accentColor: 'var(--macos-accent)' }}
                />
                <span>
                  识别图片中的文字（转账凭证、银行流水截图等；关闭可回退旧行为）
                </span>
              </label>
```

- [ ] **Step 6: 类型检查 + 构建**

```bash
cd frontend && npx tsc --noEmit && npm run build && cd ..
```

Expected: 无类型错误，构建成功

- [ ] **Step 7: 提交**

```bash
git add frontend/src/pages/SettingsPage.tsx
git commit -m "feat: 设置页新增图片文字识别开关（PaddleOCR 引擎）"
```

---

### Task 8: 说明书更新 + 全量回归

**Files:**
- Modify: `docs/user-manual.html`
- Run: `scripts/sync-manual.sh`

- [ ] **Step 1: 更新说明书**

`docs/user-manual.html` 中找到 PDF 引擎相关章节，补充两点（HTML 格式，沿用文档既有风格）：
1. PaddleOCR 引擎默认开启"图片中文字识别"：转账凭证、银行流水截图等图片内容会识别为文字写入 Markdown，原图保留可对照；MinerU 引擎不识别图片内容。涉及资金流水的案件建议使用 PaddleOCR 引擎。
2. 分析流水线新增"资金流梳理"环节（步骤 4 内自动执行）：重建资金链条并与起诉书指控金额逐笔对照，标注客观证据印证/仅言词证据/证据矛盾/无证据支撑四档结论，结果汇入综合结论、控辩对抗与辩护意见。

- [ ] **Step 2: 同步到前端**

```bash
./scripts/sync-manual.sh
```

- [ ] **Step 3: 全量回归**

```bash
python3 -m pytest tests/ -q
```

Expected: 全部 passed（既有用例无回归；如有与本改动无关的历史失败，逐条核实并记录，不顺手修复）

- [ ] **Step 4: 提交**

```bash
git add docs/user-manual.html frontend/public/user-manual.html
git commit -m "docs: 说明书补充图片文字识别与资金流梳理功能说明"
```

---

## 自查记录（计划完成后对照 spec 复核）

- Spec 5.1（PaddleOCR 参数 + payload 共享 + 配置开关）→ Task 2、3、7 ✅
- Spec 5.2（MinerU 不动）→ 无任务，符合 ✅
- Spec 5.3（折叠正则，实测门禁）→ Task 1 门禁 + Task 4 ✅
- Spec 5.4（4e 子步骤 + 降级 + 下游注入）→ Task 5、6 ✅
- Spec 6.1（先行实测 + 验收标准 + 预案）→ Task 1 ✅
- Spec 6.4（单元测试 + 说明书）→ 各任务 TDD + Task 8 ✅；E2E 按 spec 取舍为单元 + 手工验证
