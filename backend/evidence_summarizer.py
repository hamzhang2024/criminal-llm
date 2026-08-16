"""证据详细摘要：8 栏目结构化浓缩摘要的生成与保真校验

背景：按份提取后证据全文保真（单份笔录 4-9K 字符），单发分析阶段
（时间线/矛盾分析）的 _truncate_all 装不下会截断。本模块生成浓缩摘要层：
- 事实透彻性由 8 个固定栏目承担（共谋分工/主观明知/获利分账/辩解否认必列）
- 保真度由确定性校验保障（金额/日期/人名实体覆盖率 ≥90%，不达标重试）
"""
import asyncio
import ast
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# 8 个固定栏目（顺序固定，无内容填"无"，不省略）
SECTION_TITLES = ["概述", "共谋与分工", "主观明知", "获利与分账",
                  "辩解与否认", "关键事实", "态度变化", "矛盾提示"]

# 短证据阈值：全文不足 800 字无需摘要，直接复制原文
SHORT_EVIDENCE_CHARS = 800

# 实体抽取正则
_AMOUNT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:万余元|万元|万|元)")
_RATE_RE = re.compile(r"(?:月息|月利率|日息|利率)\s*\d+(?:\.\d+)?\s*(?:分|毛|厘|%|％)|(?:月息|月利率)\s*[一二三四五六七八九十]\s*(?:分|毛|厘)")
# 已知边界：只匹配完整"X年X月X日"；"2022年9月底"这类无日日期不进入实体集
_DATE_RE = re.compile(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
# persons 字段完整条目："姓名（角色）" → (姓名, 角色)
_PERSON_ENTRY_RE = re.compile(r"([一-龥]{2,4})\s*[（(]([^）)]*)[）)]")
# 冒号式条目："角色：姓名"（按 ；;，,。或换行分隔，角色可带（证人）等括号限定）
# 姓名约束为 [一-龥]{2,4}（可带括号限定，多人以 、/ 分隔），且值末端须落在
# 分隔符/行尾/"等"上：防"时间：2026年…""地点：江阴市看守所"这类非人名字段
# 被捕获为伪人名，触发"人名未出现"重试死循环
_COLON_NAME = r"[一-龥]{2,4}(?:[（(][^）)]*[）)])?"
_PERSON_COLON_RE = re.compile(
    r"([一-龥（）()/]{2,12})[：:]\s*(" + _COLON_NAME + r"(?:[、/]" + _COLON_NAME + r")*)"
    r"(?=$|[\s；;，,。等])")
# dict 列表形态：{'name': 'X', 'role': 'Y'}（LLM 返回 list[dict] 时
# _norm_str 逐行 str() 拼接后落到 persons 字段的实际形态）
_PERSON_DICT_BLOCK_RE = re.compile(r"\{[^{}]*\}")
# 占位符人名：提取阶段对无法辨认的人写的占位，不是真名，不参与人名出现校验
# （真实案例：未具名（某派出所）被判成 name=未具名、role=派出所名，
# 要求"未具名"出现在摘要中 → 两轮重试必败的确定性死循环）
_PLACEHOLDER_NAMES = ("未具名", "不详", "匿名", "不明", "无名氏", "无此人", "暂无", "无")


def _is_placeholder_name(name: str) -> bool:
    """占位符人名判定：前缀/包含匹配覆盖"未具名1""姓名不详"等变体；
    单字"无"仅精确匹配，避免子串误伤真实姓名"""
    for p in _PLACEHOLDER_NAMES:
        if len(p) == 1:
            if name == p:
                return True
        elif name.startswith(p) or p in name:
            return True
    return False
# 供述主体角色：摘要是其本人陈述的浓缩，正文以"供述人/被害人"等指代而不重复本人姓名，
# 故人名检查豁免。涵盖讯问笔录（嫌疑人/犯罪嫌疑人/被拘留人/被告人/被讯问人）、
# 询问笔录（被害人/证人/被询问人）等供述主体。
_SPEAKER_ROLES = ("嫌疑人", "犯罪嫌疑人", "被拘留人", "被告人", "供述人", "被害人", "证人", "陈述人",
                  "被讯问人", "被询问人")
# 程序性人员角色：讯问人/询问人/记录人与实体事实无关，摘要通常不会提及，
# 不强制出现于摘要。
# 注意排序依赖：被讯问人/被询问人（供述主体，在 _SPEAKER_ROLES）含"讯问人"/"询问人"
# 子串，若豁免判定未来改回子串匹配，必须先判定 _SPEAKER_ROLES 再判定本清单，
# 否则被讯问人会被误豁免为程序性人员（当前为剥离括号限定后的精确等值匹配，无此问题）
_PROCEDURAL_ROLES = ("讯问人", "询问人", "记录人")

COVERAGE_THRESHOLD = 0.9

_ROLE_QUALIFIER_RE = re.compile(r"[（(][^）)]*[）)]")


def _role_tokens(role: str) -> list[str]:
    """角色规范化：剥离括号限定（证人（已死亡）→ 证人），再按 /、和逗号拆分组合角色，
    供豁免清单做精确等值匹配（子串匹配会误豁免"证人家属""伪证人员"等涉案角色）。
    逗号拆分覆盖真实数据形态"犯罪嫌疑人，男，1981年…"（角色后接人口学信息）"""
    base = _ROLE_QUALIFIER_RE.sub("", role or "")
    return [t.strip() for t in re.split(r"[/、,，]", base) if t.strip()]


def extract_entities(text: str) -> set[str]:
    """从全文抽取关键实体（金额/利率/日期的去重集合）"""
    entities = set()
    for rx in (_AMOUNT_RE, _RATE_RE, _DATE_RE):
        for m in rx.finditer(text or ""):
            entities.add(re.sub(r"\s+", "", m.group(0)))
    return entities


def _erase_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """剔除已消费的文本段，避免后续正则重复捕获"""
    parts, last = [], 0
    for s, e in spans:
        parts.append(text[last:s])
        last = e
    parts.append(text[last:])
    return "".join(parts)


def _iter_person_entries(persons: str) -> list[tuple[str, str]]:
    """解析 persons 字段为 (姓名, 角色) 列表，兼容三种实际形态（可混合出现）：
    - 括号式：姓名（角色）
    - 冒号式：角色：姓名（；;，,。或换行分隔，值内多个姓名以、分隔，姓名可带（限定）括号）
    - dict 列表形态：{'name': 'X', 'role': 'Y'} 逐行拼接
    """
    text = str(persons or "")
    entries = []

    # dict 列表形态优先：抽取后从文本剔除，避免括号/冒号正则误匹配 dict 内容
    dict_spans = []
    for m in _PERSON_DICT_BLOCK_RE.finditer(text):
        try:
            d = ast.literal_eval(m.group(0))
        except Exception:
            continue
        if isinstance(d, dict) and d.get("name"):
            entries.append((str(d["name"]).strip(), str(d.get("role", "")).strip()))
            dict_spans.append(m.span())
    if dict_spans:
        text = _erase_spans(text, dict_spans)

    # 冒号式：角色与姓名位置固定，值内先去括号限定再按、拆分多个姓名。
    # 匹配段从文本剔除，避免角色括号限定（如"被询问人（证人）"）被下方括号式重复捕获
    colon_spans = []
    for m in _PERSON_COLON_RE.finditer(text):
        role_raw, names_raw = m.group(1), m.group(2)
        role = re.sub(r"[（(][^）)]*[）)]", "", role_raw).strip()
        names_clean = re.sub(r"[（(][^）)]*[）)]", "", names_raw)
        for name in re.split(r"[、/]", names_clean):
            name = name.strip().strip("[]").rstrip("等").strip()
            # 空值词（"同案犯：无"）不产生伪条目
            if name and not _is_placeholder_name(name):
                entries.append((name, role))
        colon_spans.append(m.span())
    if colon_spans:
        text = _erase_spans(text, colon_spans)

    # 括号式：姓名（角色）。与冒号式可混合出现（如"讯问人：夏海峰；张三（同案犯）"），
    # 对剔除冒号式后的剩余文本始终再跑一遍
    entries.extend((n.strip(), r.strip()) for n, r in _PERSON_ENTRY_RE.findall(text))

    return entries


def verify_summary_fidelity(full_text: str, summary: str, persons: str = "") -> list[str]:
    """确定性保真校验。返回问题列表（空 = 通过）

    - 8 栏目齐全
    - 金额/利率/日期实体覆盖率 ≥ 90%（全文实体过少时跳过）
    - persons 字段中的人名全部出现（供述主体角色豁免，见 _SPEAKER_ROLES）
    """
    summary = summary or ""
    issues = []

    # 栏目齐全性
    missing = [t for t in SECTION_TITLES if f"## {t}" not in summary]
    if missing:
        issues.append(f"栏目缺失：{'、'.join(missing)}")

    # 实体覆盖率
    entities = extract_entities(full_text)
    if len(entities) >= 3:  # 实体太少不校验（程序性文书等）
        covered = sum(1 for e in entities if e in re.sub(r"\s+", "", summary))
        ratio = covered / len(entities)
        if ratio < COVERAGE_THRESHOLD:
            issues.append(f"覆盖率 {ratio:.0%} 低于 {COVERAGE_THRESHOLD:.0%}（{covered}/{len(entities)}）")

    # 人名
    for name, role in _iter_person_entries(persons):
        if _is_placeholder_name(name):
            continue  # 占位符不是真名，摘要不可能也不应写出
        tokens = _role_tokens(role)
        if any(t in _SPEAKER_ROLES for t in tokens):
            continue
        if any(t in _PROCEDURAL_ROLES for t in tokens):
            continue
        if name not in summary:
            issues.append(f"人名未出现：{name}")

    return issues


_SUMMARY_SYSTEM = """你是刑事案卷阅卷助手。给定一份证据全文，输出结构化详细摘要。

输出 Markdown，必须包含且仅包含以下 8 个栏目（## 标题，顺序固定，无内容填"无"，不得省略栏目）：

## 概述
事件脉络叙述（长度随事实量自然伸缩）
## 共谋与分工
谁提议/谁出资/谁执行/如何约定，逐环节保留
## 主观明知
明知内容（如利率违法性、资金来源等）
## 获利与分账
总获利、每人分得数额、分配方式、分配时间
## 辩解与否认
无罪/罪轻辩解，逐条保留，绝不省略
## 关键事实
逐笔一行：时间｜主体｜行为｜金额｜（利率/资金来源/分成）
## 态度变化
供述稳定性、翻供、认罪认罚
## 矛盾提示
本份证据内部的前后矛盾（供述前后不一致、与同一份笔录内其他陈述的矛盾）

硬性要求：全部金额、日期、人名必须出现在摘要中，不得用"等""若干"概括。"""


async def summarize_one(client, ev: dict, full_text: str, source_name: str,
                        timeout: int = 600) -> tuple[str, bool]:
    """生成单份证据的详细摘要。

    Returns:
        (digest, warning)：digest 为摘要文本；warning=True 表示两轮校验未过（仍保留结果）
    """
    # 短证据无需摘要，直接复制原文
    if len(full_text) < SHORT_EVIDENCE_CHARS:
        return full_text, False

    persons = ev.get("persons", "")
    base_msg = f"证据名称：《{ev.get('name', '')}》（来源：{source_name}）\n\n证据全文：\n{full_text}"

    result = ""
    issues = ["调用失败"]
    for attempt in (1, 2):
        try:
            # 第二轮把上次校验问题带给 LLM，要求修正后重出
            user_msg = base_msg if attempt == 1 else (
                base_msg + f"\n\n⚠️ 上次输出未通过校验：{'；'.join(issues)}。请务必修正后重新输出完整 8 栏目。")
            result = await asyncio.wait_for(client.chat([
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": user_msg},
            ]), timeout=timeout)
        except Exception as e:
            logger.warning(f"[证据摘要] 《{ev.get('name')}》第 {attempt} 次调用失败: {e}")
            issues = ["调用失败"]
            continue

        issues = verify_summary_fidelity(full_text, result, persons)
        if not issues:
            break
        logger.warning(f"[证据摘要] 《{ev.get('name')}》第 {attempt} 次校验未过: {issues}")

    if not result:
        # 两轮调用都失败：回退全文，保证分析端有内容可消费
        return full_text, True
    return result, bool(issues)


async def summarize_evidence(client, case_dir: Path, concurrency: int = 3,
                             should_abort=None, progress_cb=None) -> dict:
    """证据详细摘要主流程：提取完成后自动串联调用。

    双写：index.json 每条证据的 digest/digest_warning 字段 + evidence/summaries/ 落盘缓存。
    断点续传：缓存 meta 的 src_len 与证据 MD 当前长度一致则复用。
    失败不抛异常（摘要步骤不阻塞分析，分析端对无 digest 的证据回退全文）。
    起诉书/起诉意见书跳过（消费端对指控文书不用摘要，避免白调 LLM）。
    should_abort：可选取消回调（提取被停止/证据被清除时），返回 True 则不写回 index.json
    ——防止摘要中途目录被 clear-evidence 清空后，写回导致 index.json 复活。
    progress_cb(done, total, name)：每份处理完成（含缓存/跳过/失败）后回调，供前端进度条。

    Returns:
        {"total", "done", "cached", "skipped", "failed", "aborted"}
    """
    evidence_dir = Path(case_dir) / "evidence"
    index_file = evidence_dir / "index.json"
    stats = {"total": 0, "done": 0, "cached": 0, "skipped": 0, "failed": 0, "aborted": False}
    if not index_file.exists():
        logger.warning("[证据摘要] index.json 不存在，跳过")
        return stats

    index = json.loads(index_file.read_text(encoding="utf-8"))
    evidences = index.get("evidence", [])
    stats["total"] = len(evidences)

    summaries_dir = evidence_dir / "summaries"
    summaries_dir.mkdir(exist_ok=True)

    concurrency = max(1, concurrency)
    sem = asyncio.Semaphore(concurrency)

    def _is_indictment(ev: dict) -> bool:
        """指控文书判定（起诉书/起诉意见书，与 analysis_engine 口径一致）"""
        ev_type = ev.get("type", "")
        ev_name = ev.get("name", "")
        return ("起诉书" in ev_type or "起诉意见书" in ev_type or
                "起诉书" in ev_name or "起诉意见书" in ev_name)

    def _report(name: str):
        """逐份进度回调：done+cached+skipped+failed 即已处理数"""
        if progress_cb:
            try:
                processed = stats["done"] + stats["cached"] + stats["skipped"] + stats["failed"]
                progress_cb(processed, stats["total"], name)
            except Exception:
                pass

    async def _one(ev: dict):
        if should_abort and should_abort():
            return
        md_name = ev.get("md_file", "")
        md_path = evidence_dir / md_name
        if not md_name or not md_path.exists():
            stats["failed"] += 1
            _report(md_name)
            return
        # 起诉书/起诉意见书跳过：消费端对指控文书保留原文全文，摘要从不被消费
        if _is_indictment(ev):
            stats["skipped"] += 1
            _report(md_name)
            return
        try:
            full_text = md_path.read_text(encoding="utf-8")

            cache_md = summaries_dir / md_name
            cache_meta = summaries_dir / (Path(md_name).stem + ".meta.json")

            # 断点续传：缓存有效则复用
            if cache_md.exists() and cache_meta.exists():
                try:
                    meta = json.loads(cache_meta.read_text(encoding="utf-8"))
                    if meta.get("src_len") == len(full_text):
                        ev["digest"] = cache_md.read_text(encoding="utf-8")
                        ev["digest_warning"] = bool(meta.get("warning", False))
                        stats["cached"] += 1
                        _report(md_name)
                        return
                except Exception:
                    pass

            async with sem:
                digest, warning = await summarize_one(client, ev, full_text, ev.get("source", ""))

            ev["digest"] = digest
            ev["digest_warning"] = warning
            if warning:
                # 校验未过或异常回退：不落盘缓存（避免瞬时失败被永久锁定），只计 failed，下次可重试
                stats["failed"] += 1
            elif len(full_text) < SHORT_EVIDENCE_CHARS:
                stats["skipped"] += 1  # 短证据复制原文，不落缓存
            else:
                stats["done"] += 1
                try:
                    cache_md.write_text(digest, encoding="utf-8")
                    cache_meta.write_text(json.dumps(
                        {"src_len": len(full_text), "warning": False},
                        ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    logger.warning(f"[证据摘要] 缓存写入失败 {md_name}: {e}")
        except Exception as e:
            stats["failed"] += 1
            logger.warning(f"[证据摘要] {md_name} 处理异常: {e}")
        _report(md_name)

    await asyncio.gather(*(_one(ev) for ev in evidences))

    # 取消检查：摘要中途若用户清除证据/停止提取，不写回 index.json
    # （否则目录已被 clear-evidence 清空重建，写回会让证据清单复活）
    if should_abort and should_abort():
        stats["aborted"] = True
        logger.info(f"[证据摘要] 已取消，不写回 index.json: {stats}")
        return stats

    index_file.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[证据摘要] 完成: {stats}")
    return stats
