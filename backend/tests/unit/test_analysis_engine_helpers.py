"""
analysis_engine 纯函数单元测试

测试目标：
1. _infer_evidence_type - 文件名推断证据类型
2. _split_indictment_and_evidence - 指控文书与证据分离
3. _truncate_all - 证据文本分层截断（大案件上下文管理核心）

这些是 LLM 分析前的数据预处理纯逻辑，直接影响分析质量。
LLM 调用/IO 方法不在此测。
"""

import pytest

from analysis_engine import (
    _infer_evidence_type,
    _split_indictment_and_evidence,
    _truncate_all,
)


class TestInferEvidenceType:
    """文件名推断证据类型（analysis_engine 版本）"""

    @pytest.mark.parametrize("filename,expected", [
        ("起诉书.pdf", "起诉书"),
        ("公诉书.pdf", "起诉书"),
        ("起诉意见书.pdf", "起诉意见书"),
        ("呈请起诉.pdf", "起诉意见书"),
        ("指控材料.pdf", "指控材料"),
        ("讯问笔录.pdf", "讯问笔录"),
        ("询问笔录.pdf", "证人证言"),
        ("证人证言.pdf", "证人证言"),
        ("鉴定意见.pdf", "鉴定意见"),
        ("勘验笔录.pdf", "勘验笔录"),
        ("辨认笔录.pdf", "辨认笔录"),
        ("银行流水.pdf", "书证-金融"),
        ("转账记录.pdf", "书证-金融"),
        ("合同.pdf", "书证-合同"),
        ("协议书.pdf", "书证-合同"),
        ("拘留证.pdf", "程序性文书"),
        ("逮捕证.pdf", "程序性文书"),
        ("取保候审.pdf", "程序性文书"),
        ("未知文件.pdf", "其他证据"),
    ])
    def test_type_inference(self, filename, expected):
        assert _infer_evidence_type(filename) == expected

    def test_case_insensitive(self):
        assert _infer_evidence_type("REPORT.PDF") == "其他证据"


class TestSplitIndictmentAndEvidence:
    """指控文书与证据分离"""

    def test_split_mixed_list(self):
        """混合列表正确分离"""
        texts = [
            {"filename": "起诉书.pdf", "type": "起诉书", "text": "指控内容", "is_indictment": True},
            {"filename": "笔录1.pdf", "type": "讯问笔录", "text": "供述", "is_indictment": False, "evidence_ref": "证据1"},
            {"filename": "笔录2.pdf", "type": "询问笔录", "text": "证言", "is_indictment": False, "evidence_ref": "证据2"},
        ]
        ind_cat, ind_text, ev_cat, evidences = _split_indictment_and_evidence(texts)
        assert "起诉书" in ind_cat
        assert "指控内容" in ind_text
        assert "证据1" in ev_cat
        assert "证据2" in ev_cat
        assert len(evidences) == 2

    def test_indictment_marked_non_evidence(self):
        """指控文书目录标注'非证据'"""
        texts = [{"filename": "起诉书.pdf", "type": "起诉书", "text": "x", "is_indictment": True}]
        ind_cat, _, _, _ = _split_indictment_and_evidence(texts)
        assert "非证据" in ind_cat

    def test_evidence_catalog_with_ref(self):
        """证据目录带编号"""
        texts = [
            {"filename": "a.pdf", "type": "讯问笔录", "text": "x", "is_indictment": False, "evidence_ref": "证据1"},
        ]
        _, _, ev_cat, _ = _split_indictment_and_evidence(texts)
        assert ev_cat.startswith("证据1")

    def test_empty_list(self):
        """空列表返回空字符串"""
        ind_cat, ind_text, ev_cat, evidences = _split_indictment_and_evidence([])
        assert ind_cat == ""
        assert ind_text == ""
        assert ev_cat == ""
        assert evidences == []

    def test_all_indictments(self):
        """全是指控文书"""
        texts = [
            {"filename": "起诉书.pdf", "type": "起诉书", "text": "x", "is_indictment": True},
            {"filename": "起诉意见书.pdf", "type": "起诉意见书", "text": "y", "is_indictment": True},
        ]
        _, _, ev_cat, evidences = _split_indictment_and_evidence(texts)
        assert ev_cat == ""
        assert len(evidences) == 0


class TestTruncateAll:
    """证据文本分层截断"""

    def test_no_truncation_when_under_limit(self):
        """总量未超限 → 完整合并，不截断"""
        texts = [
            {"filename": "a.pdf", "type": "讯问笔录", "text": "内容A"},
            {"filename": "b.pdf", "type": "书证", "text": "内容B"},
        ]
        result = _truncate_all(texts, max_total=1000)
        assert "内容A" in result
        assert "内容B" in result
        assert "### a.pdf" in result

    def test_truncation_when_over_limit(self):
        """总量超限 → 启用分层截断"""
        long_text = "X" * 500
        texts = [
            {"filename": "a.pdf", "type": "讯问笔录", "text": long_text},
        ]
        result = _truncate_all(texts, max_total=100)
        # 截断后应远小于原 500 字符
        assert len(result) < 500
        # 标题仍保留
        assert "### a.pdf" in result

    def test_key_facts_preserved_during_truncation(self):
        """截断时 key_facts 全文保留（不缩减）"""
        long_text = "X" * 1000
        key_facts = "关键事实内容"
        texts = [
            {"filename": "a.pdf", "type": "讯问笔录", "text": long_text, "key_facts": key_facts},
        ]
        result = _truncate_all(texts, max_total=100)
        # key_facts 必须完整保留
        assert key_facts in result

    def test_original_quotes_preserved(self):
        """截断时 original_quotes 全文保留"""
        long_text = "X" * 1000
        quotes = "原文摘录内容"
        texts = [
            {"filename": "a.pdf", "type": "讯问笔录", "text": long_text, "original_quotes": quotes},
        ]
        result = _truncate_all(texts, max_total=100)
        assert quotes in result

    def test_essential_exceeds_budget_only_keeps_essential(self):
        """必保留部分已超预算 → 仅保留 essential"""
        # key_facts 极长，超预算
        huge_facts = "F" * 200
        texts = [
            {"filename": "a.pdf", "type": "讯问笔录", "text": "X" * 500, "key_facts": huge_facts},
        ]
        result = _truncate_all(texts, max_total=100)
        assert huge_facts in result
        # 全文应被丢弃（仅保留 essential）
        assert "X" * 500 not in result

    def test_proportional_allocation(self):
        """多份证据按原长度比例分配预算"""
        texts = [
            {"filename": "a.pdf", "type": "笔录", "text": "A" * 300},
            {"filename": "b.pdf", "type": "笔录", "text": "B" * 100},
        ]
        result = _truncate_all(texts, max_total=200)
        # a 原长 300 > b 原长 100，a 分配到的预算更多
        a_count = result.count("A")
        b_count = result.count("B")
        assert a_count >= b_count

    def test_empty_texts(self):
        """空列表返回空字符串"""
        assert _truncate_all([], max_total=1000) == ""

    def test_strategy_info_logged_not_crash(self):
        """传 strategy_info 不报错（仅日志）"""
        texts = [{"filename": "a.pdf", "type": "笔录", "text": "x"}]
        result = _truncate_all(texts, max_total=1000, strategy_info={
            "limit_k": "200k", "strategy": "精简模式", "warning": "数据量大"
        })
        assert "x" in result
