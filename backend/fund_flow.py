"""资金流梳理共享模块（pipeline 引擎 4e 与 stage 引擎 stage_35 共用）

职责：
- 资金类关键词抽取（从原始 MD 全文 + 证据摘要双源）
- 资金流梳理 prompt 构建（四档印证结论）

设计要点：
- 双源扫描：证据提取后的摘要（evidence/）可能漏掉流水细节，
  必须同时扫转换后的原始 MD 全文（md/），截图 OCR 文字也在其中
"""
import re
from typing import List, Dict

# 资金类内容关键词（覆盖流水/凭证/言词证据中的资金表述）
FUND_KEYWORDS_RE = re.compile(
    r"(转账|汇款|收款|付款|银行流水|交易明细|银行卡|卡号|账号|支付宝|"
    r"微信(?:支付|红包|转账)|借条|欠条|涉案金额|资金|现金|取现|万元|\d+元)"
)


def collect_fund_paragraphs(
    texts: List[Dict],
    max_chars: int,
    per_file_chars: int = 3000,
) -> str:
    """从文本列表中抽取资金相关段落，控制 token 预算

    逐份文本按空行分段，保留命中资金关键词的段落；单份上限 per_file_chars，
    总量超 max_chars 停止追加。无命中返回空串。

    Args:
        texts: [{"filename": str, "text": str}, ...]
        max_chars: 总量预算
        per_file_chars: 单份上限
    """
    parts = []
    total = 0
    for f in texts:
        paragraphs = [p for p in f.get("text", "").split("\n\n") if FUND_KEYWORDS_RE.search(p)]
        if not paragraphs:
            continue
        excerpt = "\n\n".join(paragraphs)[:per_file_chars]
        block = f"### {f.get('filename', '未知')}\n{excerpt}\n"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


def build_fund_prompt(indictment_content: str, fund_evidence: str) -> str:
    """构建资金流梳理 prompt

    有起诉书（指控要素）时做逐笔对照验证（四档结论 + 反向发现）；
    无起诉书时只做资金流重建。
    """
    has_indictment = bool(indictment_content) and not indictment_content.startswith(("本案未发现", "分析失败"))
    indictment_section = (
        f"## 指控要素（含涉案金额及计算方式）\n{indictment_content[:5000]}"
        if has_indictment else
        "## 指控要素\n本案未发现起诉书/起诉意见书，跳过对照验证，只做资金流重建"
    )
    return f"""{indictment_section}

## 证据中的资金相关内容
{fund_evidence}

请完成以下分析：
1. 【资金流重建】从证据中抽取全部资金往来记录，输出时间序表格：
   | 时间 | 付款方 | 收款方 | 金额 | 渠道 | 来源类型 | 证据出处 |
   来源类型必须标注：客观证据·银行流水 / 客观证据·转账凭证 / 言词证据·被告人供述 / 言词证据·被害人陈述 / 言词证据·证人证言 等
2. 【逐笔对照验证】（仅当提供了指控要素时执行）以起诉书指控的每笔涉案金额为基准逐笔核对，输出对照表：
   | 指控笔次 | 指控金额 | 指控时间 | 来源类型 | 证据出处 | 流水金额 | 核对结论 |
   核对结论分四档：
   - ✅客观证据印证：有流水/凭证直接支撑
   - 🗣仅言词证据：只有供述/证言提及、无客观证据印证（指出刑诉法第55条：重证据、不轻信口供，只有被告人供述不能定案）
   - ⚠️证据矛盾：言词与客观证据之间、或不同言词证据间金额/时间冲突（说明冲突点）
   - ❌无证据支撑：指控金额找不到任何来源
   另列 🔍反向发现：证据中有、起诉书未指控的大额资金往来
3. 【差异与疑点分析】
4. 【对辩护的意义】

请输出 Markdown 格式。"""


FUND_SYSTEM_PROMPT = "你是刑事律师，擅长经济犯罪案件的资金流分析。请基于证据材料梳理资金链条并验证指控金额。"


# ═══════════════════════════════════════════════════════════
# 三层分离：数据层（确定性聚合）+ 校验层（机器对账）
# 原则：LLM 只做分析判断，不做数据转录——转录错误是资金流不准的主因
# ═══════════════════════════════════════════════════════════

_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万余元|万元|万|元)")

# 证据类型 → 来源类型（客观/言词）
_SOURCE_TYPE_MAP = {
    "书证": "客观证据·书证", "物证": "客观证据·物证", "电子数据": "客观证据·电子数据",
    "视听资料": "客观证据·视听资料",
    "犯罪嫌疑人供述和辩解": "言词证据·被告人供述",
    "证人证言": "言词证据·证人证言", "被害人陈述": "言词证据·被害人陈述",
}


def normalize_amount(text: str):
    """金额规范化为元（int）：5万元/5万/50000元/约3万元 → 50000/50000/50000/30000

    无法解析返回 None。
    """
    if not text:
        return None
    m = _AMOUNT_RE.search(text.replace("约", "").replace("近", ""))
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2)
    if unit in ("万元", "万余元", "万"):
        value *= 10000
    return int(round(value))


def _parse_flow(flow: str) -> dict | None:
    """解析单条 fund_flows 文本：转出人→转入人｜金额｜时间｜账号/渠道｜用途"""
    parts = [p.strip() for p in re.split(r"[｜|]", flow or "") if p.strip()]
    if len(parts) < 2:
        return None
    transfer = parts[0]
    if "→" not in transfer:
        return None
    from_, to = (s.strip() for s in transfer.split("→", 1))
    if not from_ or not to:
        return None
    amount = normalize_amount(parts[1]) if len(parts) > 1 else None
    # 日期规范化：2023年4月8日 / 2023-04-08 → 2023-04-08
    date = ""
    if len(parts) > 2:
        dm = re.search(r"(20\d\d)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", parts[2])
        if dm:
            date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
        else:
            dm = re.search(r"(20\d\d)[-/.](\d{1,2})[-/.](\d{1,2})", parts[2])
            if dm:
                date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
    return {
        "from": from_, "to": to, "amount": amount, "date": date,
        "channel": parts[3] if len(parts) > 3 else "",
        "purpose": parts[4] if len(parts) > 4 else "",
    }


def aggregate_fund_flows(case_dir) -> list:
    """数据层：聚合全部证据的 fund_flows，规范化金额后按笔分组去重

    同一笔往来（转出人/转入人/金额/日期一致）被多份证据记录时合并为一组，
    sources 记录全部来源（来源数 = 印证强度），source_types 标注客观/言词。

    Returns:
        [{date, from, to, amount, channel, purpose, sources, source_types}]，按日期排序
    """
    import json as _json
    from pathlib import Path as _Path

    index_file = _Path(case_dir) / "evidence" / "index.json"
    if not index_file.exists():
        return []
    try:
        index = _json.loads(index_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    groups: dict = {}
    for ev in index.get("evidence", []):
        ev_name = ev.get("name", ev.get("md_file", ""))
        ev_type = ev.get("type", "")
        for flow in ev.get("fund_flows") or []:
            parsed = _parse_flow(str(flow))
            if not parsed or parsed["amount"] is None:
                continue
            key = (parsed["from"], parsed["to"], parsed["amount"], parsed["date"])
            g = groups.setdefault(key, {
                "date": parsed["date"], "from": parsed["from"], "to": parsed["to"],
                "amount": parsed["amount"], "channel": parsed["channel"],
                "purpose": parsed["purpose"], "sources": [], "source_types": set(),
            })
            if parsed["channel"] and not g["channel"]:
                g["channel"] = parsed["channel"]
            if parsed["purpose"] and not g["purpose"]:
                g["purpose"] = parsed["purpose"]
            if ev_name and ev_name not in g["sources"]:
                g["sources"].append(ev_name)
            g["source_types"].add(_SOURCE_TYPE_MAP.get(ev_type, ev_type or "其他"))

    rows = sorted(groups.values(), key=lambda r: r["date"] or "9999")
    return rows


def build_master_table(rows: list) -> str:
    """主表：聚合后的资金往来 Markdown 表格（LLM 分析层的输入，不经 LLM 转录）"""
    if not rows:
        return ""
    lines = [
        "| 时间 | 付款方 | 收款方 | 金额(元) | 渠道 | 用途 | 来源类型 | 证据出处 | 印证数 |",
        "|------|--------|--------|----------|------|------|----------|----------|--------|",
    ]
    for r in rows:
        source_types = "、".join(sorted(r["source_types"]))
        sources = "、".join(r["sources"])
        lines.append(
            f"| {r['date'] or '未知'} | {r['from']} | {r['to']} | {r['amount']} | "
            f"{r['channel'] or '—'} | {r['purpose'] or '—'} | {source_types} | {sources} | {len(r['sources'])} |"
        )
    return "\n".join(lines)


def extract_amounts(text: str) -> set:
    """从文本提取全部金额（规范化为元），供校验层构建可溯源集合"""
    return {v for v in (normalize_amount(m.group(0)) for m in _AMOUNT_RE.finditer(text or "")) if v is not None}


def verify_fund_output(llm_output: str, master_amounts: set, indictment_amounts: set) -> list:
    """校验层：LLM 分析引用的金额必须能在主表或起诉书中找到，否则标记待人工核对

    Returns:
        问题列表（空 = 全部可溯源）
    """
    allowed = set(master_amounts) | set(indictment_amounts)
    issues = []
    for m in _AMOUNT_RE.finditer(llm_output or ""):
        value = normalize_amount(m.group(0))
        if value is None:
            continue
        if value not in allowed:
            issues.append(f"金额 {m.group(0)} 无来源（不在主表/起诉书中），待人工核对")
    return issues


def build_fund_prompt_v2(indictment_content: str, master_table: str) -> str:
    """分析层 prompt：主表已确定性生成，LLM 只做对照验证与分析判断"""
    has_indictment = bool(indictment_content) and not indictment_content.startswith(("本案未发现", "分析失败"))
    indictment_section = (
        f"## 指控要素（含涉案金额及计算方式）\n{indictment_content[:5000]}"
        if has_indictment else
        "## 指控要素\n本案未发现起诉书/起诉意见书，跳过对照验证，只做资金流向分析"
    )
    return f"""{indictment_section}

## 资金往来主表（已从证据结构化聚合，金额以元计，可直接引用，不得改动数字）
{master_table}

请完成以下分析（**不得修改主表中的任何金额数字**，引用金额必须与主表/指控要素一致）：
1. 【逐笔对照验证】（仅当提供了指控要素时执行）以起诉书指控的每笔涉案金额为基准，与主表逐笔核对，输出对照表：
   | 指控笔次 | 指控金额 | 指控时间 | 主表匹配 | 来源类型 | 核对结论 |
   核对结论分四档：
   - ✅客观证据印证：主表中有客观证据（书证/流水/凭证）来源的对应往来
   - 🗣仅言词证据：主表对应往来只有供述/证言来源（指出刑诉法第55条：重证据、不轻信口供）
   - ⚠️证据矛盾：言词与客观证据之间金额/时间冲突（说明冲突点）
   - ❌无证据支撑：指控金额在主表中找不到任何对应
   另列 🔍反向发现：主表中有、起诉书未指控的大额资金往来
2. 【资金流向分析】按主表梳理资金链条（谁出资→谁经手→谁获利），标注印证数低的薄弱环节
3. 【差异与疑点分析】
4. 【对辩护的意义】

请输出 Markdown 格式。"""
