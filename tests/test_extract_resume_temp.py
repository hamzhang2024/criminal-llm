"""断点续传修复（方案 A）：中断后 temp 产物复用 + .done 指纹 + 重提入口清理

背景：2026-08-23 冯叶飞案提取中途停止，已完成 4 卷产出全部丢失。
根因：启动时无条件 rmtree(_temp_extract) 自毁 .done 机制（9dd15fa 引入即缺陷）；
且合并阶段对 .done 卷用 _parse_evidence_blocks 重解析 evid 展示文本，产出垃圾块，
必须改为整卷完成时落结构化 _evidence_list.json 供合并直读。
"""
import asyncio
import json
from pathlib import Path

import case_manager
import completeness
import config_manager
import evidence_summarizer
import extraction_framework
import llm_client
import power_manager


class _NoopPowerInhibitor:
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
    monkeypatch.setattr(extraction_framework, "build_extraction_framework", _fake_build_framework)
    monkeypatch.setattr(power_manager, "PowerInhibitor", _NoopPowerInhibitor)
    monkeypatch.setattr(completeness, "check_completeness", _fake_check_completeness)
    monkeypatch.setattr(evidence_summarizer, "summarize_evidence", _fake_summarize)
    monkeypatch.setattr(config_manager, "load_config", lambda: {})


# 无"被讯问人"等笔录特征 → 单块直发路径（一次 LLM 调用）
VOLUME_TEXT = "# 卷宗\n\n" + "案卷正文内容。" * 400


class FakeLLM:
    """记录调用内容的假 LLM：返回单份证据的 JSON 数组"""

    def __init__(self):
        self.calls = []

    async def chat(self, messages, **kw):
        self.calls.append(messages)
        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        # 从提示词里拿卷名生成可区分的证据名
        name = "第2卷证据笔录" if "第2卷" in user else "其他卷证据笔录"
        return json.dumps([{
            "name": name, "type": "犯罪嫌疑人供述和辩解",
            "summary": "问：经过？答：……", "original_quotes": "引用",
        }], ensure_ascii=False)


def _use_fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(llm_client, "_clients", {"evidence": fake})
    return fake


def _make_case(tmp_path: Path, volumes: list[str]):
    case_path = tmp_path / "案件_测试_20260823"
    md_dir = case_path / "md"
    evidence_dir = case_path / "evidence"
    md_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)
    for v in volumes:
        (md_dir / v).write_text(VOLUME_TEXT, encoding="utf-8")
    (case_path / "case.json").write_text(
        json.dumps({"charges": ["盗窃罪"], "defendant": "张某"}, ensure_ascii=False),
        encoding="utf-8")
    return case_path, md_dir, evidence_dir


def _seed_done_volume(md_dir: Path, evidence_dir: Path, volume: str,
                      text_len: int | None = None, with_json: bool = True):
    """构造上轮完成卷的 temp 现场：.done（含 text_len 指纹）+ 子目录（结构化产出 + evid 文件）"""
    stem = Path(volume).stem
    temp = evidence_dir / "_temp_extract"
    sub = temp / stem
    sub.mkdir(parents=True)
    md_text = (md_dir / volume).read_text(encoding="utf-8")
    # text_len=None 表示模拟旧格式（空 .done）
    fingerprint = str(len(md_text)) if text_len is None else str(text_len)
    (temp / f"{stem}.done").write_text(fingerprint, encoding="utf-8")
    (sub / "evid_000_张某第一次讯问笔录.md").write_text(
        "# 张某第一次讯问笔录\n\n## 详细摘要\n\n问：经过？答：……", encoding="utf-8")
    if with_json:
        (sub / "_evidence_list.json").write_text(json.dumps([{
            "name": "张某第一次讯问笔录",
            "type": "犯罪嫌疑人供述和辩解",
            "source": volume,
            "page_range": "", "persons": "张某（嫌疑人）", "related_entities": "",
            "summary_preview": "问：经过？答：……",
            "has_quotes": True,
            "md_file": "evid_000_张某第一次讯问笔录.md",
            "_temp_dir": str(sub),
        }], ensure_ascii=False), encoding="utf-8")


def _run(case_path, md_dir, evidence_dir):
    return asyncio.run(case_manager._do_extract_evidence(
        "case_test", case_path, md_dir, evidence_dir))


def test_done_volume_merges_from_temp_without_llm(tmp_path, monkeypatch):
    """核心：上轮完成卷的 temp 产出在新一轮直接落库，不再调 LLM、不重新提取"""
    _setup_mocks(monkeypatch)
    fake = _use_fake_llm(monkeypatch)
    case_path, md_dir, evidence_dir = _make_case(tmp_path, ["第1卷_去水印.md", "第2卷_去水印.md"])
    _seed_done_volume(md_dir, evidence_dir, "第1卷_去水印.md")

    result = _run(case_path, md_dir, evidence_dir)

    assert result["success"] is True
    # 第1卷走 temp 落库（名字来自结构化产出），第2卷走 LLM
    names = {ev["name"] for ev in result["evidence"]}
    assert "张某第一次讯问笔录" in names
    assert "第2卷证据笔录" in names
    # LLM 只被第2卷调用（第1卷不重复提取）
    called_text = " ".join(m["content"] for call in fake.calls for m in call)
    assert "第2卷" in called_text
    assert len(fake.calls) == 1
    # temp 目录在成功合并后被清理
    assert not (evidence_dir / "_temp_extract").exists()
    # 证据文件已移动到 evidence/ 并重新编号
    index = json.loads((evidence_dir / "index.json").read_text(encoding="utf-8"))
    assert index["total_evidence"] == 2
    for ev in index["evidence"]:
        assert (evidence_dir / ev["md_file"]).exists()


def test_done_invalid_when_md_text_changed(tmp_path, monkeypatch):
    """md 内容变化（重转/修复）：.done 指纹不匹配 → 该卷重新提取"""
    _setup_mocks(monkeypatch)
    fake = _use_fake_llm(monkeypatch)
    case_path, md_dir, evidence_dir = _make_case(tmp_path, ["第1卷_去水印.md"])
    _seed_done_volume(md_dir, evidence_dir, "第1卷_去水印.md", text_len=999999)

    _run(case_path, md_dir, evidence_dir)

    assert len(fake.calls) == 1  # 被重提


def test_done_invalid_without_structured_json(tmp_path, monkeypatch):
    """.done 存在但缺 _evidence_list.json（旧格式/损坏）→ 重提而非解析 evid 文本"""
    _setup_mocks(monkeypatch)
    fake = _use_fake_llm(monkeypatch)
    case_path, md_dir, evidence_dir = _make_case(tmp_path, ["第1卷_去水印.md"])
    _seed_done_volume(md_dir, evidence_dir, "第1卷_去水印.md", with_json=False)

    _run(case_path, md_dir, evidence_dir)

    assert len(fake.calls) == 1  # 被重提


def test_prune_failed_clears_done_and_temp(tmp_path, monkeypatch):
    """失败重提入口：prune_failed_evidence 删除该卷的 .done + temp 子目录（防旧产物复活）"""
    _setup_mocks(monkeypatch)
    case_path, md_dir, evidence_dir = _make_case(tmp_path, ["第1卷_去水印.md"])
    # 失败空壳条目
    (evidence_dir / "001_空壳.md").write_text("# 空壳\n\n⚠️ 按份提取失败，需重提", encoding="utf-8")
    (evidence_dir / "index.json").write_text(json.dumps({
        "evidence": [{"id": 1, "name": "空壳", "source": "第1卷_去水印.md", "md_file": "001_空壳.md"}],
        "total_evidence": 1,
    }, ensure_ascii=False), encoding="utf-8")
    _seed_done_volume(md_dir, evidence_dir, "第1卷_去水印.md")

    removed = case_manager.prune_failed_evidence(case_path)

    assert removed == ["空壳"]
    temp = evidence_dir / "_temp_extract"
    assert not (temp / "第1卷_去水印.done").exists()
    assert not (temp / "第1卷_去水印").exists()


def test_invalidate_clears_done_and_temp(tmp_path, monkeypatch):
    """乱码修复重提入口：invalidate_evidence_for_source 删除该卷 .done + temp 子目录"""
    from page_rotation import invalidate_evidence_for_source
    _setup_mocks(monkeypatch)
    case_path, md_dir, evidence_dir = _make_case(tmp_path, ["第1卷_去水印.md"])
    (evidence_dir / "001_正常证据.md").write_text("# 正常证据\n\n内容", encoding="utf-8")
    (evidence_dir / "index.json").write_text(json.dumps({
        "evidence": [{"id": 1, "name": "正常证据", "source": "第1卷_去水印.md", "md_file": "001_正常证据.md"}],
        "total_evidence": 1,
    }, ensure_ascii=False), encoding="utf-8")
    _seed_done_volume(md_dir, evidence_dir, "第1卷_去水印.md")

    removed = invalidate_evidence_for_source(case_path, "第1卷_去水印.md")

    assert removed == ["正常证据"]
    temp = evidence_dir / "_temp_extract"
    assert not (temp / "第1卷_去水印.done").exists()
    assert not (temp / "第1卷_去水印").exists()


def test_reextract_drops_stale_evid_files(tmp_path, monkeypatch):
    """重提某卷：子目录残留的旧 evid 文件不得混入新合并（但 _perdoc 缓存保留）"""
    _setup_mocks(monkeypatch)
    fake = _use_fake_llm(monkeypatch)
    case_path, md_dir, evidence_dir = _make_case(tmp_path, ["第2卷_去水印.md"])
    stem = "第2卷_去水印"
    sub = evidence_dir / "_temp_extract" / stem
    sub.mkdir(parents=True)
    # 旧产出残留（无 .done，会重提）+ 卷内缓存（应保留）
    (sub / "evid_005_旧证据.md").write_text("# 旧证据\n\n过期内容", encoding="utf-8")
    (sub / "_perdoc_000.json").write_text("{}", encoding="utf-8")

    result = _run(case_path, md_dir, evidence_dir)

    names = {ev["name"] for ev in result["evidence"]}
    assert "旧证据" not in names
    assert "第2卷证据笔录" in names
