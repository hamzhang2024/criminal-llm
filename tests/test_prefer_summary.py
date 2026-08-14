"""分析端双层消费：prefer_summary=True 用 digest，False 用全文"""
from analysis_engine import _apply_digest


def test_digest_used_when_preferred():
    ev = {"name": "张某笔录", "digest": "浓缩摘要内容"}
    assert _apply_digest(ev, "全文内容", prefer_summary=True) == "# 张某笔录\n\n浓缩摘要内容"


def test_fulltext_when_not_preferred():
    ev = {"name": "张某笔录", "digest": "浓缩摘要内容"}
    assert _apply_digest(ev, "全文内容", prefer_summary=False) == "全文内容"


def test_fallback_fulltext_when_no_digest():
    ev = {"name": "张某笔录", "digest": ""}
    assert _apply_digest(ev, "全文内容", prefer_summary=True) == "全文内容"
    ev2 = {"name": "张某笔录"}
    assert _apply_digest(ev2, "全文内容", prefer_summary=True) == "全文内容"
