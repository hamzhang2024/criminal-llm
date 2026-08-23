"""按份提取窗口守卫：超大卷不再整卷塞入每次调用

背景（2026-08-23）：冯叶飞案第 11-14 卷（73K-237K tokens）在 64K 窗口的
Ollama 上全部 400——目录清点和按份调用都把整卷原文塞进请求。
修复：超大卷按文书位置切片发送；目录分片清点合并。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import evidence_perdoc
from evidence_perdoc import _locate_doc_spans, _extract_one_document


# ── 文书位置定位 ──

VOLUME = (
    "卷内文书目录\n1. 张某第一次讯问笔录\n2. 张某第二次讯问笔录\n"  # 目录区提及（应跳过）
    + " filler 填充内容。\n" * 50
    + "张某第一次讯问笔录\n\n问：经过？答：……" + "一。" * 200 + "\n"
    + "张某第二次讯问笔录\n\n问：还有吗？答：……" + "二。" * 200 + "\n"
)

DOCS = [
    {"name": "卷内文书目录", "type": "程序性文书", "date": ""},
    {"name": "张某第一次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-03-12"},
    {"name": "张某第二次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-04-17"},
]


def test_locate_doc_spans_skips_directory_mentions():
    """文书名在目录区的提及不算，正文区首次出现才是文书起点"""
    spans = _locate_doc_spans(VOLUME, DOCS)
    s1 = spans[1]
    s2 = spans[2]
    assert s1 is not None and s2 is not None
    # 第一份的起点必须晚于目录区（不能指向目录里的提及）
    assert s1[0] > 200
    # 第一份区间的终点 = 第二份起点
    assert s1[1] == s2[0]
    # 区间内容确实以对应文书开头
    assert VOLUME[s1[0]:].lstrip().startswith("张某第一次讯问笔录")
    assert VOLUME[s2[0]:].lstrip().startswith("张某第二次讯问笔录")


def test_locate_doc_spans_missing_doc_returns_none():
    """目录里找不到的文书 → None（调用方回退整卷发送）"""
    docs = [{"name": "根本不存在的文书", "type": "其他证据", "date": ""}]
    spans = _locate_doc_spans(VOLUME, docs)
    assert spans == [None]


# ── 按份调用切片发送 ──

def test_perdoc_sends_slice_when_volume_oversized():
    """卷超过预算时：按份调用只发目标文书附近切片，不塞整卷"""
    big_volume = VOLUME + "无关内容。" * 5000  # ~3 万字符，远超测试预算
    docs = [
        {"name": "张某第一次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-03-12"},
        {"name": "张某第二次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-04-17"},
    ]
    spans = _locate_doc_spans(big_volume, docs)
    assert spans[0] is not None

    sent = []

    async def fake_chat(messages, **kw):
        sent.append(messages)
        return json.dumps({
            "name": "张某第一次讯问笔录", "type": "犯罪嫌疑人供述和辩解",
            "persons": "张某（嫌疑人）",
            "key_facts": ["事实1", "事实2", "事实3"],
            "summary": "问：经过？答：……\n问：钱？答：……\n问：谁参与？答：……（讯问时间 2026年3月12日）",
            "original_quotes": "引用1\n引用2",
            "contradiction_hints": "无",
        }, ensure_ascii=False)

    client = type("C", (), {"chat": staticmethod(fake_chat)})()
    budget = 6000  # 字符预算（远小于 3 万字符的卷）
    block = asyncio.run(_extract_one_document(
        client, "第2卷.md", big_volume, docs[0], "", 600,
        span=spans[0], budget_chars=budget,
    ))

    assert block is not None and block["name"] == "张某第一次讯问笔录"
    # 实际发送的正文远小于整卷
    user_text = next(m["content"] for m in sent[0] if "案卷文件" in m["content"])
    assert len(user_text) < len(big_volume) * 0.3
    # 但切片里必须包含目标文书内容
    assert "问：经过" in user_text


def test_perdoc_sends_whole_when_fits():
    """卷在预算内：保持整卷发送（现状行为，不因守卫改变）"""
    docs = [{"name": "张某第一次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-03-12"}]
    sent = []

    async def fake_chat(messages, **kw):
        sent.append(messages)
        return json.dumps({
            "name": "张某第一次讯问笔录", "persons": "张某（嫌疑人）",
            "key_facts": ["a", "b", "c"], "summary": "问：经过？答：……（讯问时间 2026年3月12日）",
            "original_quotes": "引", "contradiction_hints": "无",
        }, ensure_ascii=False)

    client = type("C", (), {"chat": staticmethod(fake_chat)})()
    block = asyncio.run(_extract_one_document(
        client, "第2卷.md", VOLUME, docs[0], "", 600,
        span=(0, len(VOLUME)), budget_chars=10 ** 9,  # 预算巨大 → 整卷
    ))
    assert block is not None
    user_text = next(m["content"] for m in sent[0] if "案卷文件" in m["content"])
    assert "无关内容" not in user_text and len(user_text) >= len(VOLUME)


# ── 目录分片清点合并 ──

def test_catalog_sliced_merge_dedupes():
    """超大卷：目录分片清点，按名字+日期去重合并"""
    from evidence_perdoc import _catalog_sliced
    part1 = json.dumps([
        {"name": "卷内文书目录", "type": "程序性文书", "date": ""},
        {"name": "张某第一次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-03-12"},
    ], ensure_ascii=False)
    part2 = json.dumps([
        {"name": "张某第一次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-03-12"},  # 跨片重复
        {"name": "张某第二次讯问笔录", "type": "犯罪嫌疑人供述和辩解", "date": "2026-04-17"},
    ], ensure_ascii=False)
    responses = iter([part1, part2])

    async def fake_chat(messages, **kw):
        return next(responses)

    client = type("C", (), {"chat": staticmethod(fake_chat)})()
    docs = asyncio.run(_catalog_sliced(client, "第11卷.md", "x" * 10000, budget_chars=5000, timeout=60))

    assert docs is not None
    names = [d["name"] for d in docs]
    assert names == ["卷内文书目录", "张某第一次讯问笔录", "张某第二次讯问笔录"]
