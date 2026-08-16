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

要求：程序性文书概括核心内容即可；金额、时间、人名必须精确。"""

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
) -> Optional[dict]:
    """第二级：提取单份文书，带第三级校验与一次重试。返回 ev_block 或 None"""
    date_hint = f"，讯问/落款日期 {doc['date']}" if doc.get("date") else ""
    target_msg = f"你负责的目标文书是：《{doc['name']}》{date_hint}。请只提取这一份，其他一律忽略。{charges_str}"

    result = ""
    issues = ["调用失败"]
    for attempt in (1, 2):
        try:
            result = await asyncio.wait_for(client.chat([
                {"role": "system", "content": _PERDOC_SYSTEM},
                {"role": "user", "content": f"案卷文件：{md_name}\n\n{md_text}"},
                {"role": "user", "content": target_msg if attempt == 1 else
                    target_msg + "\n\n⚠️ 上次输出未通过校验，请务必：1) 锁定正确文书（核对日期）2) 完整输出全部问答 3) 只输出 JSON 对象"},
            ]), timeout=timeout)
        except Exception as e:
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
        return None
    try:
        item = json.loads(m.group(0))
    except Exception:
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
) -> Optional[list]:
    """两阶段按份提取主流程

    progress_cb(done, total)：每份笔录完成（含缓存命中/失败）后回调，供前端进度条。
    skip_names：卷内已存在于 index.json 的文书名集合（失败重提场景），
        命中的文书直接跳过、不产出证据块，避免整卷重提时重复提取成功文书。

    Returns:
        ev_block 列表（与 _parse_evidence_blocks 输出同构）；失败返回 None（调用方回退整卷路径）
    """
    md_name = md_file.name

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

    transcripts = [d for d in docs if _is_transcript(d)]
    others = [d for d in docs if not _is_transcript(d)]
    logger.info(f"[按份提取] {md_name}: 目录 {len(docs)} 份（笔录 {len(transcripts)}，其他 {len(others)}）")

    if not transcripts:
        return None  # 无笔录，没必要走按份路径

    results = {}  # name -> block

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
            block = await _extract_one_document(client, md_name, md_text, doc, charges_str, timeout)
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

    # 其他短文书：一次批量调用
    async def _batch_others():
        todo = [d for d in others if not _is_skipped(d)]
        if not todo:
            return
        names = "、".join(f"《{d['name']}》" for d in todo)
        try:
            result = await asyncio.wait_for(client.chat([
                {"role": "system", "content": _BATCH_SYSTEM},
                {"role": "user", "content": f"案卷文件：{md_name}\n\n{md_text}"},
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
            blocks.append({
                "name": d["name"],
                "type": d.get("type", "其他证据"),
                "source": md_name,
                "page_range": "", "persons": "", "key_facts": [],
                "summary": f"⚠️ 本文书提取失败或校验未通过，请重新提取。目录日期：{d.get('date', '未知')}",
                "original_quotes": "",
                "contradiction_hints": "⚠️ 按份提取失败，需重提",
                "related_entities": "", "fund_flows": [], "charges": [], "elements": [],
                "proves_facts": [], "proves_details": {},
                "raw_text": json.dumps(d, ensure_ascii=False),
            })
    return blocks
