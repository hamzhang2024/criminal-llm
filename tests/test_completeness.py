"""完整性校验：编号项规则对账 + LLM 抽检关键文书"""
import asyncio
from unittest.mock import AsyncMock

import completeness
from completeness import reconcile_numbered_items, check_completeness


def test_reconcile_numbered_items_full_coverage():
    source = "犯罪事实：\n一、2023年1月盗窃手机\n二、2023年2月盗窃电脑\n三、2023年3月盗窃现金"
    extracted = ["盗窃手机", "盗窃电脑", "盗窃现金"]
    result = reconcile_numbered_items(source, extracted)
    assert result["source_items"] == 3
    assert result["covered"] == 3
    assert result["missing"] == []


def test_reconcile_numbered_items_missing():
    source = "一、2023年1月盗窃手机\n二、2023年2月盗窃电脑"
    extracted = ["盗窃手机"]
    result = reconcile_numbered_items(source, extracted)
    assert result["covered"] == 1
    assert len(result["missing"]) == 1
    assert "电脑" in result["missing"][0]


def test_reconcile_bi_and_qi_patterns():
    source = "第一笔：诈骗甲公司50万元\n第二笔：诈骗乙公司30万元"
    extracted = ["诈骗甲公司", "诈骗乙公司"]
    result = reconcile_numbered_items(source, extracted)
    assert result["source_items"] == 2
    assert result["covered"] == 2


def test_check_completeness_report(tmp_path, monkeypatch):
    """生成完整性报告：每文件状态 + LLM 抽检关键文书"""
    monkeypatch.setattr(completeness, "_llm_spot_check",
                        AsyncMock(return_value={"covered": True, "missing_items": []}))
    files = {
        "起诉意见书.md": "一、盗窃手机\n二、盗窃电脑",
        "讯问笔录.md": "问：你干了什么？答：盗窃了手机和电脑。",
    }
    extracted_by_file = {
        "起诉意见书.md": ["盗窃手机", "盗窃电脑"],
        "讯问笔录.md": ["盗窃手机", "盗窃电脑"],
    }
    report = asyncio.run(completeness.check_completeness(files, extracted_by_file))
    assert report["files"]["起诉意见书.md"]["status"] == "ok"
    assert report["files"]["起诉意见书.md"]["llm_checked"] is True
    assert report["files"]["讯问笔录.md"]["llm_checked"] is False
    assert report["summary"]["ok"] >= 1


def test_check_completeness_suspect(monkeypatch):
    """有遗漏：状态 suspect"""
    monkeypatch.setattr(completeness, "_llm_spot_check",
                        AsyncMock(return_value={"covered": False, "missing_items": ["第二笔盗窃电脑"]}))
    files = {"起诉意见书.md": "一、盗窃手机\n二、盗窃电脑"}
    extracted_by_file = {"起诉意见书.md": ["盗窃手机"]}
    report = asyncio.run(completeness.check_completeness(files, extracted_by_file))
    entry = report["files"]["起诉意见书.md"]
    assert entry["status"] == "suspect"
    assert entry["needs_review"] is True
    assert "第二笔盗窃电脑" in entry["missing"]
