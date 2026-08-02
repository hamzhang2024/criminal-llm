"""文书分类：封面/目录/封底/备考表标注非证据（保留不删除），拿不准归证据"""
import asyncio
from unittest.mock import AsyncMock

import doc_classifier
from doc_classifier import classify_document, NON_EVIDENCE_TYPES


def classify(name, head):
    return asyncio.run(classify_document(name, head))


def test_filename_rules_hit():
    assert classify("第1卷封面_去水印.md", "") == "non_evidence:封面"
    assert classify("卷内目录_去水印.md", "") == "non_evidence:目录"
    assert classify("封底_去水印.md", "") == "non_evidence:封底"
    assert classify("备考表_去水印.md", "") == "non_evidence:备考表"


def test_normal_file_is_evidence():
    assert classify("讯问笔录_去水印.md", "## 讯问笔录\n时间：...") == "evidence"


def test_uncertain_defaults_to_evidence():
    """内容不明时宁可误提取，不误标非证据"""
    assert classify("第2卷_去水印.md", "## 一些内容") == "evidence"


def test_cover_by_content(monkeypatch):
    """文件名无特征但内容是封面：LLM 兜底判定"""
    monkeypatch.setattr(doc_classifier, "_llm_classify",
                        AsyncMock(return_value="non_evidence:封面"))
    assert classify("第1卷_去水印.md", "# 刑事侦查卷宗\n某公安局") == "non_evidence:封面"


def test_llm_uncertain_defaults_evidence(monkeypatch):
    monkeypatch.setattr(doc_classifier, "_llm_classify", AsyncMock(return_value="evidence"))
    assert classify("第3卷_去水印.md", "## 不明文书") == "evidence"


def test_rules_no_longer_require_cover_extraction():
    """提取规则不再要求把封面目录提取为证据"""
    import inspect
    import case_manager
    src = inspect.getsource(case_manager)
    assert "已在提取前标注为非证据" in src
    # 旧的"封面必须提取为独立证据"表述已移除
    assert '- 封面（刑事侦查卷宗信息）：提取为"卷宗封面"' not in src


def test_dead_strip_functions_removed():
    import inspect
    import case_manager
    src = inspect.getsource(case_manager)
    assert "_strip_cover_page" not in src
    assert "_strip_non_evidence_sections" not in src
