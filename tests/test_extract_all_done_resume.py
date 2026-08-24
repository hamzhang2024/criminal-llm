"""回归：断点续传"所有文件已提取"路径的 UnboundLocalError

背景：`_do_extract_evidence` 中 `indictment_extracted`、`_classify_indictment_doc`
原在 `if pending_files:` 分支内部初始化/定义，但分支外部（起诉意见书结果合并、
起诉书兜底分类）继续使用。断点续传全部跳过时（日志"所有文件已提取，跳过并发处理"），
后台任务崩溃：`UnboundLocalError: cannot access local variable 'indictment_extracted'`。
"""
import asyncio
import json
from pathlib import Path

import case_manager
import completeness
import config_manager
import evidence_summarizer
import extraction_framework
import power_manager


class _NoopPowerInhibitor:
    """测试用电源管理桩：不拉起 caffeinate 子进程"""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


async def _fake_build_framework(evidence_dir, charges, keywords):
    return {}


async def _fake_check_completeness(source_texts, extracted_by_file, all_names):
    return {"summary": "test-skip"}


async def _fake_summarize(client, case_path, **kwargs):
    return {"summarized": 0}


def _setup_mocks(monkeypatch):
    """隔离外部依赖：法律框架构建、电源管理、完整性校验、证据摘要、用户配置"""
    monkeypatch.setattr(extraction_framework, "build_extraction_framework", _fake_build_framework)
    monkeypatch.setattr(power_manager, "PowerInhibitor", _NoopPowerInhibitor)
    monkeypatch.setattr(completeness, "check_completeness", _fake_check_completeness)
    monkeypatch.setattr(evidence_summarizer, "summarize_evidence", _fake_summarize)
    monkeypatch.setattr(config_manager, "load_config", lambda: {})


# 超过 3000 字节：classify_document 走整卷豁免，直接归证据（不触发 LLM 兜底分类）
VOLUME_CONTENT = "# 第一卷\n\n" + "讯问笔录正文内容。" * 400

# 检察院独立起诉书（有检字号 + 起诉书标题，无公安特征）→ standalone 直接复制
INDICTMENT_CONTENT = (
    "某某市人民检察院\n"
    "起 诉 书\n"
    "某检刑诉〔2026〕123号\n"
    "被告人张某某，男，因涉嫌盗窃罪被刑事拘留。\n"
    "本案由某某市公安局侦查终结，移送本院审查起诉。\n"
)


def _make_case(tmp_path: Path, with_new_indictment: bool) -> tuple:
    """构造断点续传现场：md/ 有卷宗文件，evidence/index.json 已覆盖其 source

    with_new_indictment=True 时额外放一份尚未提取的独立起诉书，
    用于覆盖分支外 `_classify_indictment_doc` 的调用路径。
    返回 (case_path, md_dir, evidence_dir)。
    """
    case_path = tmp_path / "案件_测试_20260816"
    md_dir = case_path / "md"
    evidence_dir = case_path / "evidence"
    md_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)

    (md_dir / "第1卷_去水印.md").write_text(VOLUME_CONTENT, encoding="utf-8")
    if with_new_indictment:
        (md_dir / "起诉书.md").write_text(INDICTMENT_CONTENT, encoding="utf-8")

    (evidence_dir / "001_张某某第一次讯问笔录.md").write_text(
        "# 张某某第一次讯问笔录\n\n## 详细摘要\n\n问：你把事情经过讲一下？答：……",
        encoding="utf-8")
    index_data = {
        "case_id": "case_test_resume",
        "total_evidence": 1,
        "evidence": [{
            "id": 1,
            "name": "张某某第一次讯问笔录",
            "type": "犯罪嫌疑人供述和辩解",
            "source": "第1卷_去水印.md",
            "md_file": "001_张某某第一次讯问笔录.md",
        }],
        "case_charges": ["盗窃罪"],
        # 完成标记：93e228f 起「有证据但不在 completed_sources 的卷」会被判为中断卷
        # 删除重提——本测试场景是已完成续传，必须带标记才不会触发重提
        "completed_sources": ["第1卷_去水印.md"],
        "generated_at": "2026-08-16T00:00:00",
    }
    (evidence_dir / "index.json").write_text(
        json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
    (case_path / "case.json").write_text(
        json.dumps({"charges": ["盗窃罪"], "defendant": "张某某"}, ensure_ascii=False),
        encoding="utf-8")
    return case_path, md_dir, evidence_dir


def test_all_files_extracted_resume_with_new_indictment(tmp_path, monkeypatch):
    """全部卷宗已提取 + 新增独立起诉书：不抛 UnboundLocalError，起诉书直接复制入库"""
    _setup_mocks(monkeypatch)
    case_path, md_dir, evidence_dir = _make_case(tmp_path, with_new_indictment=True)

    result = asyncio.run(case_manager._do_extract_evidence(
        "case_test_resume", case_path, md_dir, evidence_dir))

    assert result["success"] is True
    sources = {ev["source"] for ev in result["evidence"]}
    assert sources == {"第1卷_去水印.md", "起诉书.md"}
    indictment = [ev for ev in result["evidence"] if ev["source"] == "起诉书.md"]
    assert len(indictment) == 1
    assert indictment[0]["type"] == "起诉书"
    # 直接复制的起诉书文件已落盘，index.json 已重写
    assert (evidence_dir / indictment[0]["md_file"]).exists()
    index = json.loads((evidence_dir / "index.json").read_text(encoding="utf-8"))
    assert index["total_evidence"] == 2


def test_all_files_extracted_resume_no_new_files(tmp_path, monkeypatch):
    """全部已提取且无新增文书（真实崩溃场景）：走跳过路径，结果与既有 index 一致"""
    _setup_mocks(monkeypatch)
    case_path, md_dir, evidence_dir = _make_case(tmp_path, with_new_indictment=False)

    result = asyncio.run(case_manager._do_extract_evidence(
        "case_test_resume", case_path, md_dir, evidence_dir))

    assert result["success"] is True
    assert result["total_evidence"] == 1
    assert result["evidence"][0]["source"] == "第1卷_去水印.md"
    index = json.loads((evidence_dir / "index.json").read_text(encoding="utf-8"))
    assert index["total_evidence"] == 1
    assert index["evidence"][0]["name"] == "张某某第一次讯问笔录"
