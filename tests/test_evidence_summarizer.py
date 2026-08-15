"""证据详细摘要：保真校验与栏目齐全性测试"""
import asyncio
import json
from pathlib import Path

from evidence_summarizer import SECTION_TITLES, verify_summary_fidelity

FULL_TEXT = """
讯问时间：2026年3月12日14时。问：你和高蓉的借贷怎么回事？
答：2022年9月底，高蓉房子抵押，我分两笔转了20万给她，月息7分也就是14000元，
分给孙琴芳6000元，我和唐鑫一人4000元。问：一共放贷多少？答：400万元不到点，
获利30万元左右，一人15万元左右。
""".strip()

GOOD_SUMMARY = """## 概述
2022年9月底高蓉房产抵押借款20万，月息7分。
## 共谋与分工
唐鑫揽客收息，与供述人平分。
## 主观明知
明知月息7分。
## 获利与分账
获利30万元左右，一人15万元左右；孙琴芳分6000元，与唐鑫各分4000元。
## 辩解与否认
无
## 关键事实
- 2022年9月｜高蓉｜20万｜月息7分（14000元）｜2026年3月12日讯问确认
- 累计放贷400万元不到点
## 态度变化
无
## 矛盾提示
无
"""


def test_good_summary_passes():
    issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons="张某（嫌疑人）")
    assert issues == []


def test_missing_sections_detected():
    bad = GOOD_SUMMARY.replace("## 主观明知", "## 明知")  # 栏目名被改
    issues = verify_summary_fidelity(FULL_TEXT, bad, persons="")
    assert any("栏目缺失" in i and "主观明知" in i for i in issues)


def test_low_entity_coverage_detected():
    # 摘要丢掉全部金额和日期
    bad = """## 概述
供述人承认放贷。
## 共谋与分工
无
## 主观明知
无
## 获利与分账
无
## 辩解与否认
无
## 关键事实
无
## 态度变化
无
## 矛盾提示
无
"""
    issues = verify_summary_fidelity(FULL_TEXT, bad, persons="")
    assert any("覆盖率" in i for i in issues)


def test_missing_person_detected():
    # 非供述主体角色（同案犯/民警）必须在摘要中出现
    issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons="李某（同案犯）、王五（民警）")
    assert any("李某" in i for i in issues)
    assert any("王五" in i for i in issues)


def test_victim_and_witness_roles_exempt():
    """被害人/证人作为供述主体时也豁免人名检查（其本人陈述的摘要不重复本人姓名）"""
    issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons="王某（被害人）")
    assert issues == []


# ---------- persons 占位符与多格式解析（冯叶飞案 25 份证据校验未过的修复） ----------

def test_placeholder_person_name_exempt():
    """占位符人名（未具名/不详/匿名/不明）不是真名，不要求出现在摘要中。
    真实案件踩到：未具名（江阴市公安局月城派出所）被误判为 name=未具名、
    role=派出所名（不在豁免清单）→ 要求"未具名"出现在摘要 → 两轮重试必败。"""
    issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY,
                                     persons="未具名（江阴市公安局月城派出所）")
    assert not any("未具名" in i for i in issues)
    for ph in ("未具名", "不详", "匿名", "不明"):
        issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons=f"{ph}（同案犯）")
        assert not any(ph in i for i in issues)


def test_colon_format_interrogated_person_exempt():
    """冒号式被讯问人/被询问人是供述主体：摘要以"供述人"指代，豁免人名检查"""
    persons = "讯问人：夏海峰；记录人：张福如；被讯问人：冯叶飞"
    assert verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons=persons) == []


def test_colon_format_procedural_roles_exempt():
    """冒号式讯问人/询问人/记录人是程序性人员：与实体事实无关，不强制出现于摘要"""
    persons = "询问人：陈红；记录人：张福如；被询问人（证人）：何芝莲"
    assert verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons=persons) == []


def test_colon_format_real_names_verified():
    """冒号式中非豁免角色的真名必须出现在摘要中
    （此前校验器只解析括号式，冒号式的真名从未被校验，属覆盖漏洞）"""
    persons = "讯问人：夏海峰；记录人：张福如；被讯问人：冯叶飞；涉案人员：赵志强、沈嘉豪"
    issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons=persons)
    assert any("赵志强" in i for i in issues)
    assert any("沈嘉豪" in i for i in issues)
    # 豁免角色不误报
    assert not any("夏海峰" in i for i in issues)
    assert not any("张福如" in i for i in issues)
    assert not any("冯叶飞" in i for i in issues)


def test_dict_list_persons_parsed():
    """dict 列表形态正常解析（_norm_str 将 LLM 返回的 [{'name':..,'role':..}]
    逐行 str() 后落到 persons 字段的实际形态）"""
    persons = ("{'name': '封卫珍', 'role': '被讯问人'}\n"
               "{'name': '程敏杰', 'role': '同案犯'}\n"
               "{'name': '沈文钊', 'role': '记录人'}")
    issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons=persons)
    # 被讯问人（供述主体）与记录人（程序性）豁免；同案犯真名必须出现
    assert any("程敏杰" in i for i in issues)
    assert not any("封卫珍" in i for i in issues)
    assert not any("沈文钊" in i for i in issues)


def test_section_titles_are_eight():
    assert SECTION_TITLES == ["概述", "共谋与分工", "主观明知", "获利与分账",
                              "辩解与否认", "关键事实", "态度变化", "矛盾提示"]


# ---------- 单份证据摘要生成（summarize_one） ----------

def _fake_client(responses):
    """按调用次序返回响应的假 client"""
    state = {"i": 0}

    async def fake_chat(messages, **kw):
        r = responses[min(state["i"], len(responses) - 1)]
        state["i"] += 1
        return r

    return type("C", (), {"chat": staticmethod(fake_chat)})()


LONG_TEXT = "讯问笔录内容。" * 120  # 840 字，超过 800 字阈值


def test_short_evidence_copies_original():
    """短证据（<800字）不调 LLM，直接复制原文"""
    from evidence_summarizer import summarize_one
    ev = {"name": "询问通知书", "persons": "", "md_file": "001_x.md"}
    client = _fake_client(["不应被调用"])
    digest, warning = asyncio.run(summarize_one(client, ev, "短内容", "案件.md"))
    assert digest == "短内容"
    assert warning is False


def test_summary_generated_and_verified():
    """长证据调 LLM，输出通过校验"""
    from evidence_summarizer import summarize_one
    ev = {"name": "张某讯问笔录", "persons": "张某（嫌疑人）", "md_file": "002_x.md"}
    summary = """## 概述
内容。## 共谋与分工
无
## 主观明知
无
## 获利与分账
无
## 辩解与否认
无
## 关键事实
无
## 态度变化
无
## 矛盾提示
无
"""
    client = _fake_client([summary])
    digest, warning = asyncio.run(summarize_one(client, ev, LONG_TEXT, "案件.md"))
    assert "## 概述" in digest
    assert warning is False


def test_summary_retry_then_warning():
    """两轮都不达标：保留结果但标记警告"""
    from evidence_summarizer import summarize_one
    ev = {"name": "张某讯问笔录", "persons": "", "md_file": "002_x.md"}
    bad = "## 概述\n只有概述，缺栏目。"
    client = _fake_client([bad, bad])  # 两次都坏
    digest, warning = asyncio.run(summarize_one(client, ev, LONG_TEXT, "案件.md"))
    assert digest == bad
    assert warning is True


# ---------- 证据摘要主流程（summarize_evidence） ----------

def _make_case(tmp_path: Path, evidences: list) -> Path:
    """构造测试案件目录：evidence/index.json + evid md 文件"""
    case_dir = tmp_path / "case_x"
    ev_dir = case_dir / "evidence"
    ev_dir.mkdir(parents=True)
    for ev in evidences:
        (ev_dir / ev["md_file"]).write_text(ev.pop("_full_text"), encoding="utf-8")
    (ev_dir / "index.json").write_text(json.dumps(
        {"evidence": evidences}, ensure_ascii=False), encoding="utf-8")
    return case_dir


def test_summarize_evidence_writes_digest(tmp_path):
    """主流程：摘要写入 index.json digest 字段 + summaries/ 落盘"""
    from evidence_summarizer import summarize_evidence
    long_text = "2026年3月12日讯问。" + "内容。" * 300
    case_dir = _make_case(tmp_path, [
        {"name": "张某讯问笔录", "type": "犯罪嫌疑人供述和辩解", "persons": "",
         "md_file": "001_张某.md", "_full_text": long_text},
        {"name": "通知书", "type": "程序性文书", "persons": "",
         "md_file": "002_通知.md", "_full_text": "短内容"},
    ])
    good_summary = """## 概述
x
## 共谋与分工
无
## 主观明知
无
## 获利与分账
无
## 辩解与否认
无
## 关键事实
无
## 态度变化
无
## 矛盾提示
无
"""
    client = _fake_client([good_summary])
    stats = asyncio.run(summarize_evidence(client, case_dir, concurrency=2))

    assert stats["total"] == 2 and stats["done"] == 1 and stats["skipped"] == 1
    index = json.loads((case_dir / "evidence" / "index.json").read_text(encoding="utf-8"))
    assert index["evidence"][0]["digest"] == good_summary
    assert index["evidence"][0]["digest_warning"] is False
    assert index["evidence"][1]["digest"] == "短内容"
    assert (case_dir / "evidence" / "summaries" / "001_张某.md").exists()
    assert not (case_dir / "evidence" / "summaries" / "002_通知.md").exists()


def test_summarize_evidence_resume_skips_cached(tmp_path):
    """断点续传：已有缓存且源文件未变 → 不重复调用 LLM"""
    from evidence_summarizer import summarize_evidence
    long_text = "内容。" * 300
    case_dir = _make_case(tmp_path, [
        {"name": "张某讯问笔录", "type": "x", "persons": "", "md_file": "001_张某.md",
         "_full_text": long_text},
    ])
    # 预置缓存
    summaries = case_dir / "evidence" / "summaries"
    summaries.mkdir()
    (summaries / "001_张某.md").write_text("已缓存摘要", encoding="utf-8")
    (summaries / "001_张某.meta.json").write_text(
        json.dumps({"src_len": len(long_text)}), encoding="utf-8")

    client = _fake_client(["不应被调用"])
    stats = asyncio.run(summarize_evidence(client, case_dir))
    assert stats["cached"] == 1 and stats["done"] == 0
    index = json.loads((case_dir / "evidence" / "index.json").read_text(encoding="utf-8"))
    assert index["evidence"][0]["digest"] == "已缓存摘要"


def test_summarize_evidence_regenerates_on_source_change(tmp_path):
    """源 MD 变化 → 缓存失效重新生成"""
    from evidence_summarizer import summarize_evidence
    long_text = "内容。" * 300
    case_dir = _make_case(tmp_path, [
        {"name": "张某讯问笔录", "type": "x", "persons": "", "md_file": "001_张某.md",
         "_full_text": long_text},
    ])
    summaries = case_dir / "evidence" / "summaries"
    summaries.mkdir()
    (summaries / "001_张某.md").write_text("旧摘要", encoding="utf-8")
    (summaries / "001_张某.meta.json").write_text(
        json.dumps({"src_len": 999}), encoding="utf-8")  # 长度不匹配 → 失效

    good = ("## 概述\nx\n" + "".join(f"## {t}\n无\n" for t in SECTION_TITLES[1:])).strip()
    client = _fake_client([good])
    stats = asyncio.run(summarize_evidence(client, case_dir))
    assert stats["done"] == 1
    assert (summaries / "001_张某.md").read_text(encoding="utf-8") == good


def test_summarize_one_both_attempts_raise_falls_back():
    """两轮调用都抛异常：回退全文 + warning=True（补的用例）"""
    from evidence_summarizer import summarize_one

    async def raising_chat(messages, **kw):
        raise RuntimeError("网络错误")

    client = type("C", (), {"chat": staticmethod(raising_chat)})()
    ev = {"name": "张某讯问笔录", "persons": "", "md_file": "002_x.md"}
    digest, warning = asyncio.run(summarize_one(client, ev, LONG_TEXT, "案件.md"))
    assert digest == LONG_TEXT
    assert warning is True


def test_warning_evidence_not_cached_and_counted_failed(tmp_path):
    """warning 证据：只计 failed、不写缓存，下次可重试（不永久锁死）"""
    from evidence_summarizer import summarize_evidence
    long_text = "内容。" * 300
    case_dir = _make_case(tmp_path, [
        {"name": "张某讯问笔录", "type": "x", "persons": "", "md_file": "001_张某.md",
         "_full_text": long_text},
    ])
    bad = "## 概述\n缺栏目，两轮都坏。"
    client = _fake_client([bad, bad])
    stats = asyncio.run(summarize_evidence(client, case_dir))
    assert stats["done"] == 0 and stats["failed"] == 1
    # 不写缓存 → summaries 目录要么不存在要么为空
    summaries = case_dir / "evidence" / "summaries" / "001_张某.md"
    assert not summaries.exists()


def test_chain_call_shape(monkeypatch, tmp_path):
    """签名约定：summarize_evidence 接收 (client, case_dir, concurrency)（真实串联行为由 Task 8 真实验证兜底）"""
    import evidence_summarizer

    called = {}

    async def fake_summarize(client, case_dir, concurrency=3):
        called["case_dir"] = case_dir
        called["concurrency"] = concurrency
        return {"total": 0, "done": 0, "cached": 0, "skipped": 0, "failed": 0}

    monkeypatch.setattr(evidence_summarizer, "summarize_evidence", fake_summarize)
    stats = asyncio.run(evidence_summarizer.summarize_evidence(None, tmp_path, concurrency=5))
    assert called["concurrency"] == 5 and stats["total"] == 0


def test_indictment_skipped_no_llm_call(tmp_path):
    """起诉书/起诉意见书跳过摘要生成：不调 LLM、不写 digest（消费端本来就不用）"""
    from evidence_summarizer import summarize_evidence
    long_text = "起诉书正文。" * 300  # 长文书，以前会白调 LLM
    case_dir = _make_case(tmp_path, [
        {"name": "起诉书", "type": "起诉书", "persons": "", "md_file": "001_起诉书.md",
         "_full_text": long_text},
        {"name": "起诉意见书", "type": "起诉意见书", "persons": "", "md_file": "002_意见书.md",
         "_full_text": long_text},
    ])
    client = _fake_client(["不应被调用"])
    stats = asyncio.run(summarize_evidence(client, case_dir))
    assert stats["skipped"] == 2 and stats["done"] == 0
    index = json.loads((case_dir / "evidence" / "index.json").read_text(encoding="utf-8"))
    assert "digest" not in index["evidence"][0] or not index["evidence"][0].get("digest")
    assert "digest" not in index["evidence"][1] or not index["evidence"][1].get("digest")


def test_should_abort_prevents_index_write(tmp_path):
    """摘要中途被取消（clear-evidence/停止提取）：不写回 index.json，防目录清空后复活"""
    from evidence_summarizer import summarize_evidence
    long_text = "内容。" * 300
    case_dir = _make_case(tmp_path, [
        {"name": "张某讯问笔录", "type": "x", "persons": "", "md_file": "001_张某.md",
         "_full_text": long_text},
    ])
    index_file = case_dir / "evidence" / "index.json"
    original_content = index_file.read_text(encoding="utf-8")

    good = ("## 概述\nx\n" + "".join(f"## {t}\n无\n" for t in SECTION_TITLES[1:])).strip()
    client = _fake_client([good])
    stats = asyncio.run(summarize_evidence(client, case_dir, should_abort=lambda: True))
    # 已取消：index.json 保持原样，不写回 digest
    assert index_file.read_text(encoding="utf-8") == original_content
    assert stats["aborted"] is True


def test_should_abort_none_by_default(tmp_path):
    """should_abort 默认 None：行为不变（向后兼容）"""
    from evidence_summarizer import summarize_evidence
    case_dir = _make_case(tmp_path, [
        {"name": "通知书", "type": "程序性文书", "persons": "", "md_file": "001_通知.md",
         "_full_text": "短内容"},
    ])
    stats = asyncio.run(summarize_evidence(None, case_dir))
    assert stats["skipped"] == 1 and stats.get("aborted") is not True


def test_summarize_progress_callback(tmp_path):
    """摘要主流程：每份完成后回调进度 (done, total, name)"""
    from evidence_summarizer import summarize_evidence
    long_text = "内容。" * 300
    case_dir = _make_case(tmp_path, [
        {"name": "张某讯问笔录", "type": "x", "persons": "", "md_file": "001_张某.md",
         "_full_text": long_text},
        {"name": "通知书", "type": "程序性文书", "persons": "", "md_file": "002_通知.md",
         "_full_text": "短内容"},
    ])
    good = ("## 概述\nx\n" + "".join(f"## {t}\n无\n" for t in SECTION_TITLES[1:])).strip()
    client = _fake_client([good])
    progress = []
    stats = asyncio.run(summarize_evidence(
        client, case_dir, concurrency=2,
        progress_cb=lambda done, total, name: progress.append((done, total, name))))
    assert stats["total"] == 2
    assert progress and progress[-1][1] == 2  # total=2
    assert progress[-1][0] == 2  # 最终 done=2
    names = [p[2] for p in progress]
    assert "001_张某.md" in names and "002_通知.md" in names
