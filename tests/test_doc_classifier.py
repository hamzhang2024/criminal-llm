"""文书分类：封面/目录/封底/备考表标注非证据（保留不删除），拿不准归证据"""
import asyncio
import json
from unittest.mock import AsyncMock

import doc_classifier
from doc_classifier import classify_document, classify_evidence_item, NON_EVIDENCE_TYPES


def classify(name, head, file_size=0):
    return asyncio.run(classify_document(name, head, file_size))


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


def test_volume_exempted():
    """整卷（>3000 字符）即使文件名/内容含封面特征也归证据"""
    assert classify("第1卷_去水印.md", "# 刑事侦查卷宗\n卷内文书目录", file_size=50000) == "evidence"
    assert classify("诉讼文书卷_去水印.md", "# 刑事诉讼卷宗", file_size=51853) == "evidence"


def test_evidence_item_classification():
    """证据条目级分类：封面/目录/封底/备考表条目标注非证据（条目保留）"""
    assert classify_evidence_item("卷宗封面") == "non_evidence:封面"
    assert classify_evidence_item("卷内文书目录") == "non_evidence:目录"
    assert classify_evidence_item("备考表") == "non_evidence:备考表"
    assert classify_evidence_item("张三第一次讯问笔录") == "evidence"
    assert classify_evidence_item("银行流水明细") == "evidence"


def test_rules_no_longer_require_cover_extraction():
    """提取规则要求封面/目录照常提取为条目（后续标注非证据，但须保留）"""
    import inspect
    import case_manager
    src = inspect.getsource(case_manager)
    assert "必须保留在提取结果中以保证案卷完整性" in src
    # 旧的"封面必须提取为独立证据"表述已移除
    assert '- 封面（刑事侦查卷宗信息）：提取为"卷宗封面"' not in src


def test_dead_strip_functions_removed():
    import inspect
    import case_manager
    src = inspect.getsource(case_manager)
    assert "_strip_cover_page" not in src
    assert "_strip_non_evidence_sections" not in src


def test_pipeline_load_md_files_filters_non_evidence(tmp_path):
    """_load_md_files 跳过 non_evidence 条目（旧案件无 doc_type 字段不受影响）"""
    from analysis_pipeline import AnalysisPipeline

    case_dir = tmp_path / "case_test"
    evidence_dir = case_dir / "evidence"
    evidence_dir.mkdir(parents=True)

    entries = [
        {"name": "卷宗封面", "md_file": "001_卷宗封面.md", "type": "程序性文书",
         "doc_type": "non_evidence:封面"},
        {"name": "张三第一次讯问笔录", "md_file": "002_讯问笔录.md", "type": "犯罪嫌疑人供述和辩解",
         "doc_type": "evidence"},
        {"name": "银行流水明细", "md_file": "003_银行流水.md", "type": "书证"},
    ]
    for ev in entries:
        (evidence_dir / ev["md_file"]).write_text(f"# {ev['name']}\n内容", encoding="utf-8")
    (evidence_dir / "index.json").write_text(
        json.dumps({"evidence": entries}, ensure_ascii=False), encoding="utf-8")

    pipeline = AnalysisPipeline("case_test", str(case_dir))
    files = pipeline._load_md_files()
    names = [f["filename"] for f in files]
    assert "卷宗封面" not in names
    assert "张三第一次讯问笔录" in names
    assert "银行流水明细" in names
