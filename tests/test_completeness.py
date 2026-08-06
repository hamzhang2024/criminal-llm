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


def test_llm_covered_clears_rule_false_positive(monkeypatch):
    """LLM 确认覆盖时，规则误报（章节标题被误识别为编号项）被清除"""
    monkeypatch.setattr(completeness, "_llm_spot_check",
                        AsyncMock(return_value={"covered": True, "missing_items": []}))
    files = {"起诉意见书.md": "一、犯罪嫌疑人基本情况\n二、犯罪事实\n三、盗窃手机"}
    extracted_by_file = {"起诉意见书.md": ["盗窃手机"]}
    report = asyncio.run(completeness.check_completeness(files, extracted_by_file))
    entry = report["files"]["起诉意见书.md"]
    assert entry["status"] == "ok"
    assert entry["missing"] == []
    assert len(entry["rule_missing"]) == 2  # 误报移入参考字段


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


def test_boilerplate_rights_filtered():
    source = "诉讼权利：\n一、不通晓当地语言时有权要求配备翻译\n二、对侵权行为有权提出控告\n三、2023年1月盗窃手机"
    extracted = ["盗窃手机"]
    result = reconcile_numbered_items(source, extracted)
    assert result["source_items"] == 1  # 两条权利条款被过滤
    assert result["covered"] == 1


def test_llm_arbitration_for_non_key_suspect_files(monkeypatch):
    """非关键文书但规则存疑：也走 LLM 仲裁，确认覆盖后清除误报"""
    monkeypatch.setattr(completeness, "_llm_spot_check",
                        AsyncMock(return_value={"covered": True, "missing_items": []}))
    files = {"第10卷_去水印.md": "一、2023年1月在某小区盗窃手机一部\n二、2023年2月在某商场盗窃电脑一台"}
    extracted_by_file = {"第10卷_去水印.md": ["手机被盗案", "电脑被盗案"]}
    # 规则对账因表述差异判为遗漏，LLM 仲裁确认覆盖后清除误报
    report = asyncio.run(completeness.check_completeness(files, extracted_by_file))
    entry = report["files"]["第10卷_去水印.md"]
    assert entry["llm_checked"] is True
    assert entry["status"] == "ok"


def test_no_llm_call_when_fully_covered_non_key(monkeypatch):
    """非关键文书且规则无遗漏：不调 LLM（成本控制）"""
    spy = AsyncMock(return_value={"covered": True, "missing_items": []})
    monkeypatch.setattr(completeness, "_llm_spot_check", spy)
    files = {"讯问笔录.md": "一、盗窃手机"}
    extracted_by_file = {"讯问笔录.md": ["盗窃手机"]}
    report = asyncio.run(completeness.check_completeness(files, extracted_by_file))
    assert spy.call_count == 0
    assert report["files"]["讯问笔录.md"]["llm_checked"] is False


def test_covered_elsewhere_downgrades_missing(monkeypatch):
    """本文件未提取但其他卷已覆盖：移到 covered_elsewhere，状态 ok"""
    monkeypatch.setattr(completeness, "_llm_spot_check",
                        AsyncMock(return_value={"covered": False, "missing_items": ["王兆威第一次询问笔录"]}))
    files = {"第10卷.md": "一、王兆威第一次询问笔录"}
    extracted_by_file = {"第10卷.md": ["其他证据"]}
    all_names = ["其他证据", "王兆威第一次询问笔录"]  # 第8卷已提取
    report = asyncio.run(completeness.check_completeness(files, extracted_by_file, all_names))
    entry = report["files"]["第10卷.md"]
    assert entry["status"] == "ok"
    assert entry["missing"] == []
    assert entry["covered_elsewhere"] == ["王兆威第一次询问笔录"]


def test_llm_spot_check_exception_marks_failed(monkeypatch):
    """LLM 抽检抛异常：状态 failed（而非静默吞掉），llm_checked=False"""
    monkeypatch.setattr(completeness, "_llm_spot_check",
                        AsyncMock(side_effect=RuntimeError("LLM 服务不可用")))
    files = {"起诉意见书.md": "一、盗窃手机\n二、盗窃电脑"}
    extracted_by_file = {"起诉意见书.md": ["盗窃手机", "盗窃电脑"]}
    report = asyncio.run(completeness.check_completeness(files, extracted_by_file))
    entry = report["files"]["起诉意见书.md"]
    assert entry["status"] == "failed"
    assert entry["llm_checked"] is False
    assert report["summary"]["failed"] == 1


def test_true_missing_stays_suspect(monkeypatch):
    """全案件都没有的条目：保持 suspect"""
    monkeypatch.setattr(completeness, "_llm_spot_check",
                        AsyncMock(return_value={"covered": False, "missing_items": ["根本不存在的笔录"]}))
    files = {"第10卷.md": "一、根本不存在的笔录"}
    extracted_by_file = {"第10卷.md": []}
    report = asyncio.run(completeness.check_completeness(files, extracted_by_file, ["其他证据"]))
    entry = report["files"]["第10卷.md"]
    assert entry["status"] == "suspect"
    assert entry["missing"] == ["根本不存在的笔录"]


def test_spot_check_prompt_semantic_matching():
    """抽检 prompt 要求语义覆盖判断（防命名差异误报）"""
    import inspect
    src = inspect.getsource(completeness._llm_spot_check)
    assert "语义" in src
    assert "合并命名" in src or "并入" in src
