"""LLM 调用单任务聚焦拆分测试

审计发现的多任务捆绑点之一的拆分契约：

stage_5C 三阶层综合辩护报告：一次调用输出 6 大板块 → 6 个聚焦子调用
（概述/证据评估/矛盾利用/三阶层/量刑情节/结论建议），
共享 system + 材料前缀命中 prompt 缓存，子章节落盘 stage_5/sections/ 支持断点续跑，
最终合并写回 stage_53/stage_5/full_defense_report.md（对外契约不变）。
"""
import asyncio
import json

import analysis_engine
from analysis_engine import AnalysisEngine


class CaptureClient:
    """捕获式假 LLM 桩：记录每次调用的 messages，按队列返回预设应答"""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    async def chat(self, messages, **kw):
        self.calls.append(messages)
        if self.responses:
            return self.responses.pop(0)
        return "分析内容"


def _patch_llm(monkeypatch, client):
    import llm_client
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: client)
    # 打桩法律知识（get_dynamic_legal_knowledge 会联网检索，拖慢且不确定）
    monkeypatch.setattr(analysis_engine, "get_legal_knowledge", lambda: "")
    monkeypatch.setattr(analysis_engine, "get_dynamic_legal_knowledge", lambda c: "")


def _make_case(tmp_path):
    """最小案件：1 份供述 + 1 份证言（供 stage_5 的 5A 证据目录使用）"""
    case_dir = tmp_path / "case_test"
    ev_dir = case_dir / "evidence"
    ev_dir.mkdir(parents=True)
    (ev_dir / "001_张某第一次讯问笔录.md").write_text("# 张某第一次讯问笔录\n\n供述内容。", encoding="utf-8")
    (ev_dir / "002_李某询问笔录.md").write_text("# 李某询问笔录\n\n证言内容。", encoding="utf-8")
    index = {
        "case_id": "case_test",
        "evidence": [
            {"id": 1, "name": "张某第一次讯问笔录", "type": "犯罪嫌疑人供述和辩解",
             "md_file": "001_张某第一次讯问笔录.md", "persons": "张某", "related_entities": "",
             "key_facts": "承认出借", "summary": "", "original_quotes": "", "contradiction_hints": ""},
            {"id": 2, "name": "李某询问笔录", "type": "证人证言",
             "md_file": "002_李某询问笔录.md", "persons": "李某", "related_entities": "",
             "key_facts": "证实转账", "summary": "", "original_quotes": "", "contradiction_hints": ""},
        ],
    }
    (ev_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    # 预置 5B 矛盾分析产物：5B 复用不消耗 LLM 调用，捕获到的即 5C 的调用
    stage52 = case_dir / "analysis" / "stage_52"
    stage52.mkdir(parents=True)
    (stage52 / "output.md").write_text("矛盾分析产物", encoding="utf-8")
    return case_dir


# ========== 拆分点 1：stage_5C 六子调用 ==========

SECTION_FILES = [
    "01-案件概述.md", "02-证据评估.md", "03-矛盾利用.md",
    "04-三阶层辩护.md", "05-量刑情节.md", "06-结论建议.md",
]


def test_5c_split_six_focused_calls(tmp_path, monkeypatch):
    """5C 拆分：每罪名 6 次聚焦子调用，共享 system 与材料前缀，子章节落盘后合并"""
    case_dir = _make_case(tmp_path)
    responses = [f"第{i}节内容" for i in range(1, 7)]
    client = CaptureClient(responses)
    _patch_llm(monkeypatch, client)

    engine = AnalysisEngine("case_test", case_dir)
    asyncio.run(engine.stage_5_full_defense("张三", "盗窃罪"))

    # (a) 5B 产物已预置（复用），捕获到的 6 次调用即 5C 的六子调用
    assert len(client.calls) == 6, f"5C 应拆为 6 次单任务调用，实际 {len(client.calls)}"

    # (b) 六次调用的 system 逐字节相同（缓存前缀第一段）
    systems = [msgs[0]["content"] for msgs in client.calls]
    assert len(set(systems)) == 1, "六子调用的 system 应逐字节一致"

    # (c) 六次调用的材料段逐字节相同（缓存前缀第二段），指令各异且在 user 末尾
    users = [msgs[1]["content"] for msgs in client.calls]
    materials = [u.rsplit("\n\n---\n\n", 1)[0] for u in users]
    instructions = [u.rsplit("\n\n---\n\n", 1)[1] for u in users]
    assert len(set(materials)) == 1, "六子调用的材料段应逐字节一致（共享缓存前缀）"
    # 材料含各阶段产物注入
    assert "## 阶段 5B：矛盾分析" in materials[0]
    assert "矛盾分析产物" in materials[0]
    # 各节指令聚焦词
    expected_focus = ["辩护概要", "证据支撑", "矛盾", "三阶层", "量刑情节", "综合辩护意见"]
    for instr, focus in zip(instructions, expected_focus):
        assert focus in instr, f"指令缺少聚焦词「{focus}」: {instr[:80]}"
        assert "只输出" in instr, "指令应声明本次只输出单一章节（单任务聚焦）"

    # (d) 子章节产物落盘 stage_5/sections/（罪名层），内容对应各次应答
    sections_dir = case_dir / "analysis" / "盗窃罪" / "stage_5" / "sections"
    for i, filename in enumerate(SECTION_FILES, 1):
        f = sections_dir / filename
        assert f.exists(), f"子章节未保存: {filename}"
        assert f.read_text(encoding="utf-8") == f"第{i}节内容"

    # (e) 合并产物：stage_53 含全部六节（三阶层标签读取路径不变）
    stage53 = (case_dir / "analysis" / "盗窃罪" / "stage_53" / "output.md").read_text(encoding="utf-8")
    for i in range(1, 7):
        assert f"第{i}节内容" in stage53, f"stage_53 合并产物缺少第{i}节"

    # (f) 完整报告与 stage_5 合并产物仍然生成（ReportPage/导出契约不变）
    full_report = (case_dir / "analysis" / "full_defense_report.md").read_text(encoding="utf-8")
    assert "矛盾分析产物" in full_report  # 5B 段保留
    assert "第1节内容" in full_report and "第6节内容" in full_report
    stage5 = case_dir / "analysis" / "盗窃罪" / "stage_5" / "output.md"
    assert stage5.exists(), "stage_5/output.md 合并产物未生成"
    assert "第3节内容" in stage5.read_text(encoding="utf-8")


def test_5c_split_resume_skips_existing_sections(tmp_path, monkeypatch):
    """5C 断点续跑：已存在的子章节跳过，只为缺失章节调用 LLM"""
    case_dir = _make_case(tmp_path)
    # 预置前两节（断点续跑场景）
    sections_dir = case_dir / "analysis" / "盗窃罪" / "stage_5" / "sections"
    sections_dir.mkdir(parents=True)
    (sections_dir / "01-案件概述.md").write_text("既有概述", encoding="utf-8")
    (sections_dir / "02-证据评估.md").write_text("既有评估", encoding="utf-8")

    responses = [f"新第{i}节" for i in range(3, 7)]
    client = CaptureClient(responses)
    _patch_llm(monkeypatch, client)

    engine = AnalysisEngine("case_test", case_dir)
    asyncio.run(engine.stage_5_full_defense("张三", "盗窃罪"))

    assert len(client.calls) == 4, f"已有 2 节应只补 4 次调用，实际 {len(client.calls)}"
    # 既有章节不被覆盖
    assert (sections_dir / "01-案件概述.md").read_text(encoding="utf-8") == "既有概述"
    # 合并产物同时含既有章节与新章节
    stage53 = (case_dir / "analysis" / "盗窃罪" / "stage_53" / "output.md").read_text(encoding="utf-8")
    assert "既有概述" in stage53 and "新第6节" in stage53

