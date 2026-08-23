"""按份提取（两阶段）测试"""
import asyncio
import json
from pathlib import Path

import evidence_perdoc
from evidence_perdoc import extract_by_document, verify_perdoc_output, catalog_documents


def _fake_client(responses):
    """按调用次序返回响应的假 client"""
    calls = []
    state = {"i": 0}

    async def fake_chat(messages, **kw):
        calls.append(messages)
        r = responses[min(state["i"], len(responses) - 1)]
        state["i"] += 1
        return r

    client = type("C", (), {"chat": staticmethod(fake_chat)})()
    client._calls = calls
    return client


CATALOG_JSON = json.dumps([
    {"name": "卷宗封面", "type": "程序性文书", "date": ""},
    {"name": "张某第一次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-03-12"},
    {"name": "张某第二次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-04-17"},
], ensure_ascii=False)

GOOD_DOC_OUTPUT = json.dumps({
    "name": "张某第一次讯问笔录",
    "type": "犯罪嫌疑人供述和辩解",
    "persons": "张某（嫌疑人）",
    "key_facts": ["事实1", "事实2", "事实3"],
    "summary": "问：你说一下经过？答：……\n问：钱去哪了？答：……\n问：还有谁参与？答：……（讯问时间 2026年3月12日，详细问答全文）",
    "original_quotes": "引用1\n引用2",
    "contradiction_hints": "无",
    "related_entities": "手机号 13800000000 — 张某",
    "charges": ["诈骗罪"],
    "elements": [],
}, ensure_ascii=False)


def test_catalog_parse():
    """目录清点：解析 JSON 数组"""
    client = _fake_client([CATALOG_JSON])
    docs = asyncio.run(catalog_documents(client, "第2卷.md", "文件内容"))
    assert len(docs) == 3
    assert docs[1]["date"] == "2026-03-12"


def test_catalog_garbage_returns_none():
    """目录返回非 JSON：返回 None（回退整卷路径）"""
    client = _fake_client(["这不是 JSON"])
    docs = asyncio.run(catalog_documents(client, "第2卷.md", "文件内容"))
    assert docs is None


def test_verify_date_mismatch():
    """校验：输出日期与目录日期不符 → 报问题"""
    doc = {"name": "张某第七次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-04-17"}
    bad_output = "讯问时间：2026年4月1日……问：……答：……问：……答：……问：……"
    issues = verify_perdoc_output(doc, bad_output)
    assert any("日期不匹配" in i for i in issues)


def test_verify_placeholder_detected():
    """校验：占位符敷衍 → 报问题"""
    doc = {"name": "张某第一次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": ""}
    issues = verify_perdoc_output(doc, "详细摘要：（关键问答摘录）")
    assert any("占位符" in i for i in issues)


def test_verify_good_output_passes():
    """校验：正常输出通过"""
    doc = {"name": "张某第一次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-03-12"}
    good = "讯问时间 2026年3月12日\n问：a\n答：b\n问：c\n答：d\n问：e\n答：f"
    assert verify_perdoc_output(doc, good) == []


def test_verify_phone_record_format_passes():
    """校验：电话询问记录的「（问）」格式也应被识别为问答
    （真实案件踩到的误报：原文为 周某（问）：…秦某（答）：…，无「问：」字面量）"""
    doc = {"name": "电话询问记录（秦某）", "type": "证人证言", "date": "2026-04-18"}
    output = ("2026年4月18日9时18分电话询问。周立峰（问）：你是秦某吗？秦某（答）：是的。"
              "周立峰（问）：你投资过吗？秦某（答）：有的。"
              "周立峰（问）：钱拿回来了吗？秦某（答）：都拿回来了。")
    assert verify_perdoc_output(doc, output) == []


def test_is_transcript_identification_record_not_qa():
    """辨认笔录不是问答体（是辨认过程记录文书），不判为笔录类。
    真实案件踩到：辨认笔录被误判为讯问笔录，强制 ≥3 问答对必败。"""
    from evidence_perdoc import _is_transcript
    assert _is_transcript({"name": "沈海鹰辨认笔录", "type": "辨认笔录"}) is False
    # 不误伤真正的讯问/询问笔录
    assert _is_transcript({"name": "张某讯问笔录", "type": "犯罪嫌疑人供述和辩解"}) is True
    assert _is_transcript({"name": "王某询问笔录", "type": "证人证言"}) is True


def test_verify_identification_record_passes_without_qa():
    """辨认笔录输出无问答对也能通过校验"""
    doc = {"name": "沈海鹰辨认笔录", "type": "辨认笔录", "date": ""}
    output = "辨认人在侦查人员主持下，从12张照片中辨认出7号照片为涉案人员。"
    assert verify_perdoc_output(doc, output) == []


def test_extract_by_document_full_flow(tmp_path):
    """完整流程：目录 → 按份提取 → 按目录顺序合并，per-doc prompt 含双锚点"""
    md_file = tmp_path / "第2卷.md"
    md_file.write_text("案卷内容", encoding="utf-8")
    calls = []

    async def routing_chat(messages, **kw):
        calls.append(messages)
        last = messages[-1]["content"]
        if "目标文书清单" in last:
            return json.dumps([{"name": "卷宗封面", "type": "程序性文书", "summary": "封面内容"}], ensure_ascii=False)
        if "《张某第一次" in last:
            return GOOD_DOC_OUTPUT
        if "《张某第二次" in last:
            return GOOD_DOC_OUTPUT.replace("第一次", "第二次").replace("2026年3月12日", "2026年4月17日")
        return CATALOG_JSON

    client = type("C", (), {"chat": staticmethod(routing_chat), "_calls": calls})()
    blocks = asyncio.run(extract_by_document(client, md_file, "案卷内容", "", tmp_path))

    assert blocks is not None
    assert len(blocks) == 3
    assert blocks[0]["name"] == "卷宗封面"
    assert "第二次" in blocks[2]["name"]
    # per-doc 调用应是「system + 文件 + 目标指令」三消息结构，目标含名称+日期
    # （批量调用第三条消息含"目标文书清单"，以此区分）
    perdoc_calls = [c for c in calls if len(c) == 3 and "目标文书是" in c[2]["content"]]
    assert len(perdoc_calls) == 2
    target_msg = perdoc_calls[0][2]["content"]
    assert "张某第一次讯问笔录" in target_msg and "2026-03-12" in target_msg


def test_extract_by_document_catalog_failure_fallback(tmp_path):
    """目录清点失败：返回 None 让调用方回退"""
    md_file = tmp_path / "x.md"
    md_file.write_text("内容", encoding="utf-8")
    client = _fake_client(["垃圾输出"])
    blocks = asyncio.run(extract_by_document(client, md_file, "内容", "", tmp_path))
    assert blocks is None


def test_list_fields_normalized_to_strings(tmp_path):
    """LLM 把 original_quotes/persons 等返回为数组时，须规范化为字符串
    （真实案件验证中踩到的 bug：下游 .strip() 崩溃）"""
    md_file = tmp_path / "第2卷.md"
    md_file.write_text("案卷内容", encoding="utf-8")
    listy_output = json.dumps({
        "name": "张某第一次讯问笔录",
        "type": "犯罪嫌疑人供述和辩解",
        "persons": ["张某（嫌疑人）", "李某（侦查员）"],
        "key_facts": "事实只有一条字符串",
        "summary": "问：a\n答：b\n问：c\n答：d\n问：e\n答：f（2026年3月12日）",
        "original_quotes": ["引用1", "引用2"],
        "contradiction_hints": ["矛盾1", "矛盾2"],
        "related_entities": ["手机号 138 — 张某"],
        "charges": "诈骗罪",
        "elements": "虚构事实",
    }, ensure_ascii=False)

    async def listy_chat(messages, **kw):
        last = messages[-1]["content"]
        if "目标文书清单" in last:
            return json.dumps([{"name": "卷宗封面", "original_quotes": ["q1", "q2"]}],
                              ensure_ascii=False)
        if "《张某第一次" in last:
            return listy_output
        if "《张某第二次" in last:
            return GOOD_DOC_OUTPUT.replace("第一次", "第二次").replace("2026年3月12日", "2026年4月17日")
        return CATALOG_JSON

    client = type("C", (), {"chat": staticmethod(listy_chat)})()
    blocks = asyncio.run(extract_by_document(client, md_file, "案卷内容", "", tmp_path))

    assert blocks is not None
    b = next(b for b in blocks if "第一次" in b["name"])
    assert isinstance(b["original_quotes"], str) and "引用1" in b["original_quotes"]
    assert isinstance(b["persons"], str) and "张某" in b["persons"]
    assert isinstance(b["contradiction_hints"], str) and "矛盾1" in b["contradiction_hints"]
    assert isinstance(b["related_entities"], str)
    assert isinstance(b["key_facts"], list) and b["key_facts"] == ["事实只有一条字符串"]
    assert isinstance(b["charges"], list) and b["charges"] == ["诈骗罪"]
    assert isinstance(b["elements"], list) and b["elements"] == ["虚构事实"]
    # 批量路径同样规范化
    cover = next(b for b in blocks if b["name"] == "卷宗封面")
    assert isinstance(cover["original_quotes"], str) and "q1" in cover["original_quotes"]


def test_failed_doc_gets_placeholder_block(tmp_path):
    """单份提取失败：占位块标注缺失，不静默遗漏"""
    md_file = tmp_path / "第2卷.md"
    md_file.write_text("案卷内容", encoding="utf-8")

    async def failing_chat(messages, **kw):
        if len(messages) == 3:  # per-doc 调用
            raise TimeoutError("超时")
        # 目录 + 批量正常
        if "目标文书清单" in messages[-1]["content"]:
            return json.dumps([{"name": "卷宗封面", "type": "程序性文书", "summary": "封面"}], ensure_ascii=False)
        return CATALOG_JSON

    client = type("C", (), {"chat": staticmethod(failing_chat)})()
    blocks = asyncio.run(extract_by_document(client, md_file, "案卷内容", "", tmp_path))

    assert blocks is not None and len(blocks) == 3
    assert "提取失败" in blocks[1]["summary"] or "校验未通过" in blocks[1]["summary"]


def test_extract_by_document_progress_callback(tmp_path):
    """按份提取：每份完成后回调进度 (done, total)"""
    md_file = tmp_path / "第2卷.md"
    md_file.write_text("案卷内容", encoding="utf-8")
    progress = []

    async def routing_chat(messages, **kw):
        last = messages[-1]["content"]
        if "目标文书清单" in last:
            return json.dumps([{"name": "卷宗封面", "type": "程序性文书", "summary": "封面"}], ensure_ascii=False)
        if "《张某第一次" in last:
            return GOOD_DOC_OUTPUT
        if "《张某第二次" in last:
            return GOOD_DOC_OUTPUT.replace("第一次", "第二次").replace("2026年3月12日", "2026年4月17日")
        return CATALOG_JSON

    client = type("C", (), {"chat": staticmethod(routing_chat)})()
    blocks = asyncio.run(extract_by_document(
        client, md_file, "案卷内容", "", tmp_path,
        progress_cb=lambda done, total: progress.append((done, total))))

    assert blocks is not None
    assert progress  # 有进度回调
    assert progress[-1] == (2, 3)  # 最后一次：2 份笔录完成（批量短文书不计），total=目录3份
    assert all(t == 3 for _, t in progress)


def test_failed_doc_placeholder_carries_humanized_reason(tmp_path):
    """单份提取失败：占位块 summary 携带人性化失败原因（供界面直接展示）

    背景：2026-08-23 Ollama num_ctx=8192 致 13 份全部 400，界面只显示"提取失败"，
    用户无法得知上下文超限。修复后占位块必须带具体原因。
    """
    md_file = tmp_path / "第2卷.md"
    md_file.write_text("案卷内容", encoding="utf-8")

    async def context_overflow_chat(messages, **kw):
        if len(messages) == 3:  # per-doc 调用
            raise Exception(
                'API 请求失败：400\n{"error":{"message":"request (12010 tokens) '
                'exceeds the available context size (8192 tokens)"}}'
            )
        if "目标文书清单" in messages[-1]["content"]:
            return json.dumps([{"name": "卷宗封面", "type": "程序性文书", "summary": "封面"}], ensure_ascii=False)
        return CATALOG_JSON

    client = type("C", (), {"chat": staticmethod(context_overflow_chat)})()
    blocks = asyncio.run(extract_by_document(client, md_file, "案卷内容", "", tmp_path))

    assert blocks is not None and len(blocks) == 3
    # 失败占位块：summary 必须包含人性化原因（上下文超限 + 8192 窗口数字 + num_ctx 解法）
    failed = [b for b in blocks if "提取失败" in b["summary"]]
    assert failed, "应有失败占位块"
    for b in failed:
        assert "上下文" in b["summary"], f"占位块缺少人性化原因: {b['summary']}"
        assert "8192" in b["summary"]
