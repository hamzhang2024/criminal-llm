"""按份提取：多笔录文件的两阶段证据提取

背景：整卷一次调用时，"全部笔录全文保留"的输出要求物理不可达，
LLM 会保数量砍内容（占位符敷衍）。本模块改为两级流程：

- 第一级（目录清点）：整文件一次调用 → 文书清单（名称/类型/日期）
- 第二级（按份提取）：每份笔录一次调用，名称+日期双锚点定位——
  程序绝不切分文本，边界判断全部交给 LLM 语义理解
- 第三级（确定性校验）：输出日期须匹配目录日期、无占位符、问答数达标，
  失败重试一次，仍失败标记警告但不静默丢弃

设计约束：DeepSeek 缓存——固定规则在 system，文件内容在 user 前段固定不变，
只有目标指令变化，最大化 prompt cache 命中。
"""
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

from llm_client import humanize_llm_error

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 第一级：目录清点
# ═══════════════════════════════════════════════════════════
_CATALOG_SYSTEM = """你是案卷整理员，只做客观清点，不做分析。
给定一份案卷 MD 文件（含多份独立文书：封面、目录、告知书、多份讯问/询问笔录等），
列出其中每一份独立文书的清单。

输出严格 JSON 数组（不要输出其他内容）：
[{"name": "冯叶飞第一次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-03-12"}, ...]

要求：
- 每份独立笔录（以"被讯问人/被询问人"基本信息栏为标志）必须单独列出，同一人的多次按顺序编号命名
- date 为该文书的讯问/询问/落款日期（YYYY-M-D），无日期填 ""
- 封面、卷内目录等程序性文书也要列出（type 填 程序性文书）
- 不得遗漏任何一份"""

# ═══════════════════════════════════════════════════════════
# 第二级：按份提取
# ═══════════════════════════════════════════════════════════
_PERDOC_SYSTEM = """你是刑事案卷证据提取专家。给定一份案卷 MD 文件（含多份独立文书）和你负责提取的目标文书。

**你的任务：只提取目标文书一份（以名称+日期双重定位），其他文书一律忽略。**

只输出一个 JSON 对象（不要输出任何其他内容）：
{
  "name": "目标文书名称",
  "type": "证据类型（犯罪嫌疑人供述和辩解/证人证言/被害人陈述/书证/程序性文书等）",
  "time": "讯问/询问/落款时间（原文精确到时分，如 2026年4月17日15时05分至15时23分；无则填空）",
  "page_range": "页码范围（如原文可辨）",
  "persons": "涉案人员及角色（讯问人/被讯问人/证人等）",
  "key_facts": ["关键事实（时间+主体+行为+结果，保留具体金额、地点、数字）", "至少3条"],
  "summary": "详细摘要：笔录类以问答形式完整保留全部问答原文——从第一问第一答到最后一问最后一答，不得筛选、不得省略、不得概括；仅与案情完全无关的程序性问答（权利义务告知等）可省略并标注[程序性问答略]。书证类列明全部具体数据",
  "original_quotes": "关键原文直接引用，不少于2段",
  "contradiction_hints": "供述前后是否一致、与其他证据的潜在矛盾；无则填 无",
  "related_entities": "关联信息（手机号/微信号/银行账号/身份证号/车牌号/地址等），格式：[类型] 内容 — 涉及人员",
  "fund_flows": ["资金往来，每笔一条：转出人→转入人｜金额｜时间｜账号/渠道｜用途；仅当本证据涉及资金往来时输出，无则填空数组"],
  "charges": ["关联罪名"],
  "elements": ["关联构成要件"]
}

金额、时间、人名必须精确，不要用"约""左右"等模糊词。"""

# 非笔录短文书的批量提取 prompt（沿用整卷规则的精简版，这些文书短，一次调用可行）
_BATCH_SYSTEM = """你是刑事案卷证据提取专家。给定一份案卷 MD 文件和一份目标文书清单。

**你的任务：只提取清单中列出的文书，每份一个条目，其他文书（尤其是讯问/询问笔录）一律忽略。**

输出严格 JSON 数组（不要输出其他内容）：
[{"name": "...", "type": "...", "page_range": "", "persons": "...", "key_facts": ["..."],
  "summary": "...", "original_quotes": "...", "contradiction_hints": "无",
  "related_entities": "...", "fund_flows": [], "charges": [], "elements": []}, ...]

要求：
- 程序性文书（起诉意见书、起诉书等）**必须完整保留每笔犯罪事实的原文描述**，不得概括简化
- 告知类、手续类文书概括核心内容即可
- 金额、时间、人名必须精确"""

# 笔录类判定
_TRANS_RECORD_RE = re.compile(r"笔录|供述|证言|陈述")
# 非问答体的"笔录"：辨认笔录是辨认过程记录文书，不是问答复录，
# 不适用问答对数量校验（真实案例：辨认笔录被误判为讯问笔录，问答对<3 必败）
_NON_QA_RECORD_RE = re.compile(r"辨认笔录")


def _is_transcript(doc: dict) -> bool:
    """是否笔录类文书（需要按份全文保真提取）"""
    text = doc.get("name", "") + doc.get("type", "")
    if _NON_QA_RECORD_RE.search(text):
        return False
    return bool(_TRANS_RECORD_RE.search(text))


def _norm_str(v) -> str:
    """字符串字段规范化：LLM 可能返回数组，统一为换行拼接的字符串
    （下游 case_manager 对这些字段做 .strip()/f-string 插值，必须是 str）"""
    if v is None:
        return ""
    if isinstance(v, list):
        return "\n".join(str(x) for x in v)
    return str(v)


def _norm_list(v) -> list:
    """数组字段规范化：LLM 可能返回字符串，统一包成数组"""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    s = str(v).strip()
    return [s] if s else []


def _norm_date(s: str) -> Optional[tuple]:
    """从各种日期格式提取 (年, 月, 日) 元组；失败返回 None"""
    m = re.search(r"(20\d\d)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", s or "")
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(20\d\d)[-/.](\d{1,2})[-/.](\d{1,2})", s or "")
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


async def catalog_documents(client, md_name: str, md_text: str, timeout: int = 600) -> Optional[list]:
    """第一级：目录清点。返回 [{name, type, date}]，失败返回 None"""
    try:
        result = await asyncio.wait_for(client.chat([
            {"role": "system", "content": _CATALOG_SYSTEM},
            {"role": "user", "content": f"案卷文件：{md_name}\n\n{md_text}"},
        ]), timeout=timeout)
    except Exception as e:
        logger.warning(f"[按份提取] {md_name}: 目录清点调用失败: {e}")
        return None
    m = re.search(r"\[[\s\S]*\]", result)
    if not m:
        logger.warning(f"[按份提取] {md_name}: 目录清点未返回 JSON 数组")
        return None
    try:
        docs = json.loads(m.group(0))
        if not isinstance(docs, list) or not docs:
            return None
        # 规范化
        normalized = []
        for d in docs:
            if isinstance(d, dict) and d.get("name"):
                normalized.append({
                    "name": str(d["name"]),
                    "type": str(d.get("type", "")),
                    "date": str(d.get("date", "")),
                })
        return normalized or None
    except Exception as e:
        logger.warning(f"[按份提取] {md_name}: 目录解析失败: {e}")
        return None


_ORDINALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
             "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15, "十六": 16, "十七": 17,
             "十八": 18, "十九": 19, "二十": 20}


def _parse_transcript_name(name: str) -> tuple | None:
    """从目录名解析 (人名, 第几次)：'黄卫第一次讯问笔录' → ('黄卫', 1)；'张某讯问笔录' → ('张某', 1)"""
    m = re.match(r"^(.+?)第([一二三四五六七八九十]+|\d+)次(讯问笔录|询问笔录)$", name)
    if m:
        ordinal = _ORDINALS.get(m.group(2))
        if ordinal is None and m.group(2).isdigit():
            ordinal = int(m.group(2))
        if ordinal:
            return (m.group(1), ordinal)
    m = re.match(r"^(.+?)(讯问笔录|询问笔录)$", name)
    if m:
        return (m.group(1), 1)
    return None


def _transcript_start(md_text: str, field_pos: int) -> int:
    """笔录文书起点：优先 300 字符内的笔录标题行；没有（OCR 丢标题）则回退到
    600 字符内最近的空行（笔录头块起点：时间/地点/讯问人…被讯问人）；再退字段位置本身。
    不限窗口的全局回退会把相邻两份笔录锚到同一标题（胡凯 #9/#10 同点事故）。
    """
    window_start = max(0, field_pos - 300)
    heading = None
    for hm in re.finditer(r"^#.*笔录.*$", md_text[window_start:field_pos], re.MULTILINE):
        heading = window_start + hm.start()
    if heading is not None:
        return heading
    blank = md_text.rfind("\n\n", max(0, field_pos - 600), field_pos)
    return (blank + 2) if blank >= 0 else field_pos


def _locate_doc_spans(md_text: str, docs: list) -> list:
    """定位目录中每份文书在正文中的区间：返回 [(start, end) | None]，与 docs 同序

    超大卷窗口守卫用。定位策略（按优先级）：
    1. 文书名直搜：跳过目录提及行（短行 + 序号前缀/页码结尾）
    2. 泛化标题笔录：按「被讯问人/被询问人」字段定位（正文标题常只有 # 讯问笔录 不含人名）
    同名文书（目录重复条目）按目录顺序依次占用后续候选位置，避免多份映射到同一点。
    区间 = [本文书起点, 下一份已定位文书的起点或文末)。
    """
    n = len(md_text)

    def _is_directory_mention(line: str, name_len: int) -> bool:
        if len(line) > name_len + 15:
            return False
        if re.match(r"^\d+\s*[.、．]", line):
            return True
        if re.search(r"[\d\-—~]+\s*$", line) and re.search(r"\d", line[-6:]):
            return True
        return False

    def _find_name_positions(name: str) -> list:
        """文书名的所有正文位置（滤掉目录提及行），按出现顺序"""
        out = []
        start = 0
        while True:
            idx = md_text.find(name, start)
            if idx < 0:
                break
            line_start = md_text.rfind("\n", 0, idx) + 1
            line_end = md_text.find("\n", idx)
            line = md_text[line_start: line_end if line_end > 0 else n].strip()
            if not _is_directory_mention(line, len(name)):
                out.append(line_start)
            start = idx + len(name)
        return out

    # 为每份目录文书确定候选位置列表（名称直搜优先，泛化标题笔录按人名字段）
    keys = []
    candidates = {}
    for d in docs:
        name = re.sub(r"\s+", "", d.get("name", ""))
        name_key = ("name", name)
        if name and name_key not in candidates:
            candidates[name_key] = _find_name_positions(name)
        if candidates.get(name_key):
            keys.append(name_key)
            continue
        parsed = _parse_transcript_name(name) if name else None
        if parsed:
            person_key = ("person", parsed[0])
            if person_key not in candidates:
                fields = []
                for m in re.finditer(r"被(?:讯问|询问)人[：:]?\s*([^\s，,。、（(]+?)(?=[\s，,。、]|性别|$)", md_text):
                    if m.group(1) == parsed[0]:
                        fields.append(_transcript_start(md_text, m.start()))
                candidates[person_key] = sorted(fields)
            keys.append(person_key)
        else:
            keys.append(name_key)  # 无候选 → 该文书定位 None

    # 按目录顺序依次占用候选位置（同名文书取后续位置）
    ptr = {}
    positions = []
    for key in keys:
        lst = candidates.get(key, [])
        i = ptr.get(key, 0)
        if i < len(lst):
            positions.append(lst[i])
            ptr[key] = i + 1
        else:
            positions.append(None)

    located = sorted((p, i) for i, p in enumerate(positions) if p is not None)
    spans = [None] * len(docs)
    for k, (p, i) in enumerate(located):
        end = located[k + 1][0] if k + 1 < len(located) else n
        # 区间过小（<50 字符，基本是定位撞点）视为定位失败：回退整卷/批量带上下文
        spans[i] = (p, end) if end - p >= 50 else None
    return spans


async def _catalog_sliced(client, md_name: str, md_text: str, budget_chars: int, timeout: int) -> Optional[list]:
    """超大卷的分片目录清点：按预算切片（行边界），逐片清点，按 名字+日期 去重合并"""
    n = len(md_text)
    slices = []
    start = 0
    while start < n:
        end = min(n, start + budget_chars)
        if end < n:
            nl = md_text.rfind("\n", start + int(budget_chars * 0.8), end)
            if nl > start:
                end = nl
        slices.append(md_text[start:end])
        start = end
    logger.info(f"[按份提取] {md_name}: 卷超出窗口预算，目录分 {len(slices)} 片清点")
    merged, seen = [], set()
    for si, s in enumerate(slices):
        part = await catalog_documents(client, f"{md_name}（切片{si + 1}/{len(slices)}）", s, timeout)
        for d in part or []:
            key = (re.sub(r"\s+", "", d.get("name", "")), d.get("date", ""))
            if key[0] and key not in seen:
                seen.add(key)
                merged.append(d)
    return merged or None


def verify_perdoc_output(doc: dict, output: str) -> list:
    """第三级：确定性校验。返回问题列表（空 = 通过）"""
    issues = []
    # 日期匹配（目录有日期才校验）：优先看 time 字段，回退全文搜索
    catalog_date = _norm_date(doc.get("date", ""))
    if catalog_date:
        date_pool = output
        try:
            m = re.search(r"\{[\s\S]*\}", output)
            if m:
                item = json.loads(m.group(0))
                if item.get("time"):
                    date_pool = item["time"] + "\n" + output  # time 字段优先但全文兜底
        except Exception:
            pass
        output_dates = {_norm_date(m.group(0)) for m in re.finditer(
            r"20\d\d\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|20\d\d[-/.]\d{1,2}[-/.]\d{1,2}", date_pool)}
        if catalog_date not in output_dates:
            issues.append(f"日期不匹配（目录 {doc['date']}）")
    # 占位符敷衍
    if "关键问答摘录" in output:
        issues.append("含占位符（关键问答摘录）")
    # 笔录类问答数（兼容两种格式：标准「问：」和电话询问记录常用的「（问）」）
    if _is_transcript(doc):
        qa_count = output.count("问：") + len(re.findall(r"[（(]问[）)]", output))
        if qa_count < 3:
            issues.append("问答对少于 3 个")
    return issues


async def _extract_one_document(
    client, md_name: str, md_text: str, doc: dict, charges_str: str, timeout: int = 600,
    is_procedural: bool = False,  # 程序性文书标记
    error_sink: dict | None = None,  # 失败原因出口：doc_name → 人性化原因（随占位块展示到界面）
    span: tuple | None = None,       # 目标文书在正文中的区间（超大卷窗口守卫）
    budget_chars: int | None = None,  # 单次调用正文预算（字符）
) -> Optional[dict]:
    """第二级：提取单份文书，带第三级校验与一次重试。返回 ev_block 或 None"""
    date_hint = f"，讯问/落款日期 {doc['date']}" if doc.get("date") else ""
    target_msg = f"你负责的目标文书是：《{doc['name']}》{date_hint}。请只提取这一份，其他一律忽略。{charges_str}"

    # 超大卷窗口守卫：卷超出预算且有定位区间时，只发目标文书附近切片（防整卷 400）
    send_text = md_text
    slice_note = ""
    if span and budget_chars and len(md_text) > budget_chars:
        s, e = span
        send_text = md_text[max(0, s - 200): min(len(md_text), e + 200)]
        slice_note = "（切片：目标文书附近内容）"

    result = ""
    last_error = ""
    issues = ["调用失败"]
    for attempt in (1, 2):
        try:
            # 程序性文书用精简 prompt（不需要保留问答原文）
            system_prompt = _BATCH_SYSTEM if is_procedural else _PERDOC_SYSTEM
            result = await asyncio.wait_for(client.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"案卷文件：{md_name}{slice_note}\n\n{send_text}"},
                {"role": "user", "content": target_msg if attempt == 1 else
                    target_msg + "\n\n⚠️ 上次输出未通过校验，请务必：1) 锁定正确文书（核对日期）2) 完整输出全部问答 3) 只输出 JSON 对象"},
            ]), timeout=timeout)
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[按份提取] {md_name}《{doc['name']}》第 {attempt} 次调用失败: {e}")
            issues = ["调用失败"]
            continue

        issues = verify_perdoc_output(doc, result)
        if not issues:
            break
        logger.warning(f"[按份提取] {md_name}《{doc['name']}》第 {attempt} 次校验未过: {issues}")
    else:
        # 两次都没通过（含调用失败）——如果有一次产生了结果，用它并记警告
        logger.error(f"[按份提取] {md_name}《{doc['name']}》校验未通过，仍保留结果并标记")

    # 解析 JSON 对象
    m = re.search(r"\{[\s\S]*\}", result) if result else None
    if not result or not m:
        if error_sink is not None:
            error_sink[doc["name"]] = humanize_llm_error(last_error or "LLM 未按要求输出内容")
        return None
    try:
        item = json.loads(m.group(0))
    except Exception:
        if error_sink is not None:
            error_sink[doc["name"]] = "LLM 输出无法解析为 JSON（模型输出格式不规范）"
        return None

    block = {
        "name": _norm_str(item.get("name")) or doc["name"],
        "type": _norm_str(item.get("type")) or doc.get("type") or "其他证据",
        "source": md_name,
        "time": _norm_str(item.get("time")),
        "page_range": _norm_str(item.get("page_range")),
        "persons": _norm_str(item.get("persons")),
        "key_facts": _norm_list(item.get("key_facts")),
        "summary": _norm_str(item.get("summary")),  # 完整保留，不截断（按份提取的核心价值）
        "original_quotes": _norm_str(item.get("original_quotes")),
        "contradiction_hints": _norm_str(item.get("contradiction_hints")) or "无",
        "related_entities": _norm_str(item.get("related_entities")),
        "fund_flows": _norm_list(item.get("fund_flows")),
        "charges": _norm_list(item.get("charges")),
        "elements": _norm_list(item.get("elements")),
        "proves_facts": _norm_list(item.get("proves_facts")),
        "proves_details": item.get("proves_details") if isinstance(item.get("proves_details"), dict) else {},
        "raw_text": json.dumps(item, ensure_ascii=False, indent=2),
    }
    if issues:
        block["contradiction_hints"] = f"⚠️ 提取校验提示：{'；'.join(issues)}\n\n" + block["contradiction_hints"]
    return block


def _norm_name_key(name: str) -> str:
    """文书名匹配键：去全部空白字符，容忍 LLM 重跑时的空白差异

    只做空白规范化、不用子串模糊匹配：子串匹配会把"张三讯问笔录（第2次）"
    误判为已存在的"张三讯问笔录"而静默跳过（正是本机制要修复的遗漏问题），
    宁可因改名重复提取（可见、可去重），不可错误跳过（静默丢失）。
    """
    return "".join(str(name).split())


async def extract_by_document(
    client, md_file: Path, md_text: str, charges_str: str, temp_dir: Path,
    max_concurrent: int = 3, timeout: int = 600, progress_cb=None,
    skip_names: Optional[set] = None,
    is_procedural: bool = False,  # 程序性文书卷标记
) -> Optional[list]:
    """两阶段按份提取主流程

    progress_cb(done, total)：每份笔录完成（含缓存命中/失败）后回调，供前端进度条。
    skip_names：卷内已存在于 index.json 的文书名集合（失败重提场景），
        命中的文书直接跳过、不产出证据块，避免整卷重提时重复提取成功文书。
    is_procedural：程序性文书卷（无笔录，只有起诉意见书等程序性文书），使用精简 prompt。

    Returns:
        ev_block 列表（与 _parse_evidence_blocks 输出同构）；失败返回 None（调用方回退整卷路径）
    """
    md_name = md_file.name

    # 单次调用正文预算（字符）：当前提取 profile 窗口 × 25% × 1.35。
    # 超大卷（如 20 万 tokens 塞进 64K 窗口必 400）按此预算切片/分片
    import context_budget
    _ctx = getattr(client, "context_limit", 0) or context_budget.get_context_limit("evidence")
    _model = getattr(client, "model", "")
    budget_chars = int(context_budget.compute_input_chunk_tokens(_ctx, _model) * context_budget.CHARS_PER_TOKEN)
    oversized = len(md_text) > budget_chars

    # 目录缓存（文本变化即失效）
    catalog_cache = temp_dir / "_perdoc_catalog.json"
    docs = None
    if catalog_cache.exists():
        try:
            cached = json.loads(catalog_cache.read_text(encoding="utf-8"))
            if cached.get("text_len") == len(md_text):
                docs = cached.get("docs")
        except Exception:
            pass
    if docs is None:
        # 超大卷整卷清点必超窗口：分片清点合并
        if oversized:
            docs = await _catalog_sliced(client, md_name, md_text, budget_chars, timeout)
        else:
            docs = await catalog_documents(client, md_name, md_text, timeout)
        if docs:
            try:
                catalog_cache.write_text(json.dumps(
                    {"text_len": len(md_text), "docs": docs}, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    if not docs or len(docs) < 2:
        logger.info(f"[按份提取] {md_name}: 目录文书数 {len(docs) if docs else 0}，回退整卷路径")
        return None

    # 超大卷：定位每份文书的正文区间（按份调用只发切片，不塞整卷）
    spans = _locate_doc_spans(md_text, docs) if oversized else [None] * len(docs)

    transcripts = [d for d in docs if _is_transcript(d)]
    others = [d for d in docs if not _is_transcript(d)]
    logger.info(f"[按份提取] {md_name}: 目录 {len(docs)} 份（笔录 {len(transcripts)}，其他 {len(others)}）")

    if not transcripts:
        return None  # 无笔录，没必要走按份路径

    results = {}  # name -> block
    # 失败原因记录（doc_name → 人性化原因）：写入占位块 summary，随 index.json 展示到界面
    fail_reasons = {}

    # 卷内跳过名册：失败重提时，卷内已成功文书（已在 index.json 中）按名称跳过
    skip_keys = {_norm_name_key(n) for n in (skip_names or set()) if n}

    def _is_skipped(doc: dict) -> bool:
        return _norm_name_key(doc.get("name", "")) in skip_keys

    # 笔录：按份提取（并发 + 校验 + 断点续传）
    sem = asyncio.Semaphore(max_concurrent)
    progress_done = {"n": 0}

    def _report_progress():
        """逐份进度回调（前端进度条：当前卷已完成份数/总份数）"""
        if progress_cb:
            try:
                progress_cb(progress_done["n"], len(docs))
            except Exception:
                pass

    async def _one(i: int, doc: dict):
        # 已存在于 index.json 的文书：直接跳过，不计入提取结果
        if _is_skipped(doc):
            progress_done["n"] += 1
            _report_progress()
            logger.info(f"[按份提取] {md_name}《{doc['name']}》已存在，跳过")
            return
        cache_file = temp_dir / f"_perdoc_{i:03d}.json"
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if cached.get("text_len") == len(md_text):
                    results[i] = cached["block"]
                    progress_done["n"] += 1
                    _report_progress()
                    return
            except Exception:
                pass
        async with sem:
            block = await _extract_one_document(
                client, md_name, md_text, doc, charges_str, timeout,
                is_procedural=is_procedural,
                error_sink=fail_reasons,
                span=spans[i] if i < len(spans) else None,
                budget_chars=budget_chars,
            )
        if block:
            results[i] = block
            try:
                cache_file.write_text(json.dumps(
                    {"text_len": len(md_text), "block": block}, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
            logger.info(f"[按份提取] {md_name}《{doc['name']}》完成（{i + 1}/{len(docs)}）")
        else:
            logger.error(f"[按份提取] {md_name}《{doc['name']}》提取失败")
        progress_done["n"] += 1
        _report_progress()

    # 其他短文书：一次批量调用（超大卷按定位切片 + 预算打包成多批）
    async def _batch_others():
        todo = [(i, d) for i, d in enumerate(docs) if not _is_transcript(d) and not _is_skipped(d)]
        if not todo:
            return
        if not oversized:
            batches = [(todo, md_text)]
        else:
            # 按文书区间切片，贪心地按预算打包；定位失败的文书标原因跳过（不塞整卷）
            batches = []
            cur_docs, cur_texts, cur_size = [], [], 0
            for i, d in todo:
                sp = spans[i] if i < len(spans) else None
                if sp is None:
                    logger.warning(f"[按份提取] {md_name}《{d['name']}》正文定位失败，跳过切片批量")
                    fail_reasons[d["name"]] = "正文定位失败（目录名与正文标题不一致），请人工核对"
                    continue
                s, e = sp
                piece = md_text[max(0, s - 100): min(len(md_text), e + 100)]
                if cur_size + len(piece) > budget_chars and cur_docs:
                    batches.append((cur_docs, "\n\n".join(cur_texts)))
                    cur_docs, cur_texts, cur_size = [], [], 0
                cur_docs.append((i, d))
                cur_texts.append(piece)
                cur_size += len(piece)
            if cur_docs:
                batches.append((cur_docs, "\n\n".join(cur_texts)))

        for batch_docs, batch_text in batches:
            names = "、".join(f"《{d['name']}》" for _, d in batch_docs)
            try:
                result = await asyncio.wait_for(client.chat([
                    {"role": "system", "content": _BATCH_SYSTEM},
                    {"role": "user", "content": f"案卷文件：{md_name}{'（切片）' if oversized else ''}\n\n{batch_text}"},
                    {"role": "user", "content": f"目标文书清单：{names}。只提取这些，每份一个条目。{charges_str}"},
                ]), timeout=timeout)
                m = re.search(r"\[[\s\S]*\]", result)
                if m:
                    for item in json.loads(m.group(0)):
                        if isinstance(item, dict) and item.get("name"):
                            # 按名称找回目录下标
                            for j, d in enumerate(docs):
                                if d["name"] == item["name"] or d["name"] in item["name"] or item["name"] in d["name"]:
                                    results[j] = {
                                        "name": _norm_str(item.get("name")) or d["name"],
                                        "type": _norm_str(item.get("type")) or d.get("type", "程序性文书"),
                                        "source": md_name,
                                        "page_range": _norm_str(item.get("page_range")),
                                        "persons": _norm_str(item.get("persons")),
                                        "key_facts": _norm_list(item.get("key_facts")),
                                        "summary": _norm_str(item.get("summary")),
                                        "original_quotes": _norm_str(item.get("original_quotes")),
                                        "contradiction_hints": _norm_str(item.get("contradiction_hints")) or "无",
                                        "related_entities": _norm_str(item.get("related_entities")),
                                        "fund_flows": _norm_list(item.get("fund_flows")),
                                        "charges": _norm_list(item.get("charges")),
                                        "elements": _norm_list(item.get("elements")),
                                        "proves_facts": _norm_list(item.get("proves_facts")),
                                        "proves_details": item.get("proves_details") if isinstance(item.get("proves_details"), dict) else {},
                                        "raw_text": json.dumps(item, ensure_ascii=False, indent=2),
                                    }
                                    break
            except Exception as e:
                logger.warning(f"[按份提取] {md_name}: 短文书批量提取失败: {e}")
                # 批量调用失败：该批所有文书记同一原因（人性化后供界面展示）
                for _, d in batch_docs:
                    fail_reasons[d["name"]] = humanize_llm_error(str(e))

    await asyncio.gather(
        *(_one(i, d) for i, d in enumerate(docs) if _is_transcript(d)),
        _batch_others(),
    )

    # 按目录顺序输出；失败的笔录补一个占位块（宁可标注缺失也不静默遗漏）
    blocks = []
    for i, d in enumerate(docs):
        if _is_skipped(d):
            continue  # 已存在于 index.json，不重复产出
        if i in results:
            blocks.append(results[i])
        else:
            logger.error(f"[按份提取] {md_name}《{d['name']}》缺失，写入占位块")
            # 失败原因（人性化）写入占位块 summary：随 index.json 的 summary_preview 展示到界面
            reason = fail_reasons.get(d["name"], "")
            fail_text = f"提取失败：{reason}" if reason else "提取失败或校验未通过"
            blocks.append({
                "name": d["name"],
                "type": d.get("type", "其他证据"),
                "source": md_name,
                "page_range": "", "persons": "", "key_facts": [],
                "summary": f"⚠️ 本文书{fail_text}，请重新提取。目录日期：{d.get('date', '未知')}",
                "original_quotes": "",
                "contradiction_hints": "⚠️ 按份提取失败，需重提",
                "related_entities": "", "fund_flows": [], "charges": [], "elements": [],
                "proves_facts": [], "proves_details": {},
                "raw_text": json.dumps(d, ensure_ascii=False),
            })
    return blocks
