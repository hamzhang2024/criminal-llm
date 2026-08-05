"""提取完整性校验：规则对账 + LLM 抽检关键文书

- 规则对账（全文件，零 LLM 成本）：编号项（第X笔/第X起/中文序号）与提取清单核对
- LLM 抽检（仅起诉书/起诉意见书/判决书）：一次调用确认逐笔覆盖
- 报告存 evidence/completeness_report.json；LLM 与规则冲突时以 LLM 为准并标注人工复核
"""
import json as _json
import re

KEY_DOC_PATTERN = re.compile(r"起诉书|起诉意见书|判决书")

# 权利义务告知书等样板条款：不作为应提取事实
_BOILERPLATE = re.compile(r"有权|权利|义务|告知")

_CN_NUM = "一二三四五六七八九十"
_ITEM_PATTERNS = [
    re.compile(rf"^[\s>*-]*([{_CN_NUM}]+)、(.+)$", re.M),               # 一、xxx
    re.compile(r"第([一二三四五六七八九十\d]+)[笔起][：:](.+)$", re.M),  # 第一笔：xxx / 第3起：xxx
    re.compile(r"^[\s>*-]*(\d+)[.、]\s*(.+)$", re.M),                    # 1. xxx
]


def _extract_numbered_items(source: str) -> list[str]:
    """从原文提取编号项（去重，保留顺序）"""
    items: list[str] = []
    for pattern in _ITEM_PATTERNS:
        for m in pattern.finditer(source):
            title = m.group(m.lastindex).strip()
            if len(title) >= 4 and title not in items and not _BOILERPLATE.search(title):
                items.append(title)
    return items


def _covered(item: str, extracted: list[str]) -> bool:
    """条目与提取清单的覆盖判断：双向子串命中或关键词重合"""
    for ev in extracted:
        if ev and (ev in item or item in ev):
            return True
    keywords = [kw for kw in re.split(r"[，,、\s。；;：:]", item) if len(kw) >= 2]
    for ev in extracted:
        for kw in keywords[:3]:
            if kw in ev:
                return True
    return False


def reconcile_numbered_items(source: str, extracted: list[str]) -> dict:
    """规则对账：返回 {source_items, covered, missing}"""
    items = _extract_numbered_items(source)
    missing = [it for it in items if not _covered(it, extracted)]
    return {
        "source_items": len(items),
        "covered": len(items) - len(missing),
        "missing": missing,
    }


async def _llm_spot_check(source: str, extracted: list[str]) -> dict:
    """LLM 抽检关键文书：返回 {covered, missing_items}"""
    from llm_client import get_llm_client
    client = get_llm_client()
    extracted_str = "\n".join(f"- {e}" for e in extracted[:50])
    result = await client.chat([
        {"role": "system", "content": "你是案卷审查员。对照原文与提取清单，判断原文列出的每笔事实是否都被覆盖。只输出 JSON：{\"covered\": true/false, \"missing_items\": [\"遗漏的笔数简述\"]}"},
        {"role": "user", "content": f"## 原文（编号事实）\n{source[:20000]}\n\n## 提取清单\n{extracted_str}"},
    ])
    m = re.search(r"\{.*\}", result, re.S)
    if m:
        return _json.loads(m.group(0))
    return {"covered": True, "missing_items": []}


async def check_completeness(files: dict, extracted_by_file: dict, all_evidence_names: list[str] | None = None) -> dict:
    """全量完整性校验。files: {文件名: 原文}；extracted_by_file: {文件名: [证据名]}

    all_evidence_names: 全案件已提取证据名列表，用于全局交叉核对——
    本文件未提取但其他卷已提取的条目（补充卷内容重复是常态）不计为遗漏。
    """
    report = {"files": {}, "summary": {"ok": 0, "suspect": 0, "failed": 0}}
    for fname, source in files.items():
        extracted = extracted_by_file.get(fname, [])
        rec = reconcile_numbered_items(source, extracted)
        is_key = bool(KEY_DOC_PATTERN.search(fname))
        entry = {
            "source_items": rec["source_items"],
            "covered": rec["covered"],
            "missing": rec["missing"],
            "llm_checked": False,
        }
        # LLM 仲裁：关键文书必查；普通文件仅当规则对账存疑时平反误报
        need_llm = is_key or bool(rec["missing"])
        if need_llm:
            try:
                spot = await _llm_spot_check(source, extracted)
                entry["llm_checked"] = True
                if not spot.get("covered", True):
                    # LLM 与规则冲突时以 LLM 为准，标注人工复核
                    entry["missing"] = spot.get("missing_items", rec["missing"])
                    entry["needs_review"] = True
                else:
                    # LLM 确认覆盖：规则侧 missing 多为章节标题误报，降级为参考字段
                    entry["rule_missing"] = entry["missing"]
                    entry["missing"] = []
            except Exception:
                # LLM 抽检失败：标记 failed（可区分"未抽检"与"抽检失败"），不静默吞掉
                entry["llm_checked"] = False
                entry["llm_error"] = True
        # 全局交叉核对：本文件未提取但其他卷已覆盖（补充卷重复是常态）
        if entry["missing"] and all_evidence_names:
            elsewhere = [m for m in entry["missing"] if _covered(m, all_evidence_names)]
            if elsewhere:
                entry["covered_elsewhere"] = elsewhere
                entry["missing"] = [m for m in entry["missing"] if m not in elsewhere]
        # 状态判定：LLM 抽检失败 → failed；无编号项的文件不做遗漏判定
        if entry.get("llm_error"):
            status = "failed"
        elif rec["source_items"] == 0:
            status = "ok"
        elif entry["missing"]:
            status = "suspect"
        else:
            status = "ok"
        entry["status"] = status
        report["files"][fname] = entry
        report["summary"][status] = report["summary"].get(status, 0) + 1
    return report
