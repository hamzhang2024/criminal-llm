"""A'：笔录类证据原文摘录为问答全录"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import case_manager


def test_rules_require_full_qa_transcript():
    # _EVIDENCE_EXTRACTION_RULES 是模块级字符串常量，取整个模块源码检查规则文本
    src = inspect.getsource(case_manager)
    assert "全部问答" in src
    assert "不得筛选" in src or "不得省略" in src
