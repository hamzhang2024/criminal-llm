"""终审修复 A1/A3-b：阶段 5 显式重跑失效旧产物 + 5C 空内容检查

1. 单阶段端点 /run-stage/5：删除 stage_52 产物 + 5C 子节目录后真正重跑（LLM 被真实调用）
2. run-all 多罪名：罪名循环开始前只失效一次，第二个罪名 5B 仍复用共享层（Task 5 设计意图不破坏）
3. 5C 子调用返回空：不写节产物、stage_53 不落盘、本节标记失败，重跑可自愈
"""
import asyncio
import json

import llm_client
import stage_api
from analysis_engine import AnalysisEngine


class FakeClient:
    """canned 应答的假 LLM 客户端，记录调用次数；empty_on 指定的第 N 次调用返回空串"""

    def __init__(self, empty_on=()):
        self.calls = 0
        self.empty_on = set(empty_on)

    async def chat(self, messages, model=None, model_override=None):
        self.calls += 1
        if self.calls in self.empty_on:
            return ""
        return f"## 分析结果（第 {self.calls} 次调用）\n\n内容。"


def _make_case(tmp_path):
    """最小案件：1 份供述 + 1 份证言"""
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
    return case_dir


def _seed_stage5_products(case_dir, charges=()):
    """预置旧的 5B 共享层产物 + 各罪名 5C 六节产物"""
    ad = case_dir / "analysis"
    (ad / "stage_52").mkdir(parents=True, exist_ok=True)
    (ad / "stage_52" / "output.md").write_text("旧矛盾分析", encoding="utf-8")
    targets = list(charges) or [None]
    for charge in targets:
        sections = (ad / charge / "stage_5" / "sections") if charge else (ad / "stage_5" / "sections")
        sections.mkdir(parents=True, exist_ok=True)
        for name in ["01-案件概述.md", "02-证据评估.md", "03-矛盾利用.md",
                     "04-三阶层辩护.md", "05-量刑情节.md", "06-结论建议.md"]:
            (sections / name).write_text("旧章节内容", encoding="utf-8")


def test_single_stage5_rerun_invalidates_old_products(tmp_path, monkeypatch):
    """显式重跑（单阶段端点 /run-stage/5）：删除 5B/5C 旧产物，LLM 被真实调用"""
    case_dir = _make_case(tmp_path)
    _seed_stage5_products(case_dir)
    fake = FakeClient()
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: fake)
    monkeypatch.setattr(stage_api, "find_case_path", lambda cid: case_dir)

    result = asyncio.run(stage_api.run_single_stage(
        case_id="case_test", stage_num=5, defendant="张三",
        crime_type=None, indictment_file=None, reference_case_nos=None,
    ))

    assert result["success"] is True
    # 5B 三次 + 5C 六次，全部真实重跑（修复前：0 次调用，直接复用旧产物）
    assert fake.calls == 9
    stage52 = (case_dir / "analysis" / "stage_52" / "output.md").read_text(encoding="utf-8")
    assert "旧矛盾分析" not in stage52
    section = (case_dir / "analysis" / "stage_5" / "sections" / "01-案件概述.md").read_text(encoding="utf-8")
    assert "旧章节内容" not in section


def test_run_all_invalidates_once_before_charge_loop(tmp_path, monkeypatch):
    """run-all：罪名循环开始前只失效一次（不是每个罪名都删，保住 5B 共享层复用）"""
    case_dir = _make_case(tmp_path)
    monkeypatch.setattr(stage_api, "find_case_path", lambda cid: case_dir)
    events = []

    class FakeEngine:
        def __init__(self, *a, **k):
            pass

        def invalidate_stage5_cache(self, charges):
            events.append(("invalidate", list(charges or [None])))

        async def stage_1_read_indictment(self, *a, **k):
            return {}

        async def stage_2_character_relations(self, *a, **k):
            return {}

        async def stage_3_event_timeline(self, *a, **k):
            return {}

        async def stage_35_fund_flow(self, *a, **k):
            return {}

        async def stage_4_legal_regulations(self, defendant, charge=None):
            return {}

        async def stage_5_full_defense(self, defendant, charge=None):
            events.append(("s5", charge))
            return {}

    monkeypatch.setattr(stage_api, "AnalysisEngine", FakeEngine)
    asyncio.run(stage_api._execute_all_stages("case_test", "张三", ["诈骗罪", "职务侵占罪"]))

    inv = [e for e in events if e[0] == "invalidate"]
    s5 = [e for e in events if e[0] == "s5"]
    assert len(inv) == 1, "run-all 应在罪名循环开始前只失效一次"
    assert [c for _, c in s5] == ["诈骗罪", "职务侵占罪"]
    assert events.index(inv[0]) < events.index(s5[0]), "失效必须发生在第一个罪名 stage_5 之前"


def test_run_all_second_charge_reuses_5b(tmp_path, monkeypatch):
    """run-all 失效一次后：第一个罪名全量重跑，第二个罪名 5B 复用共享层（Task 5 设计意图）"""
    case_dir = _make_case(tmp_path)
    _seed_stage5_products(case_dir, charges=["诈骗罪", "职务侵占罪"])
    fake = FakeClient()
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: fake)

    engine = AnalysisEngine("case_test", case_dir)
    engine.invalidate_stage5_cache(["诈骗罪", "职务侵占罪"])

    asyncio.run(engine.stage_5_full_defense("张三", "诈骗罪"))
    assert fake.calls == 9, "第一个罪名：5B 三次 + 5C 六次全量重跑"

    asyncio.run(engine.stage_5_full_defense("张三", "职务侵占罪"))
    assert fake.calls == 15, "第二个罪名：5B 复用共享层不重跑，只跑 5C 六节"

    # 罪名层 5C 产物确实重新生成
    section = (case_dir / "analysis" / "职务侵占罪" / "stage_5" / "sections" / "01-案件概述.md")
    assert "旧章节内容" not in section.read_text(encoding="utf-8")


def test_5c_empty_section_marked_failed_and_self_heals(tmp_path, monkeypatch):
    """5C 子调用返回空：不写节产物、stage_53 不落盘、标记失败；重跑只补失败节"""
    case_dir = _make_case(tmp_path)
    # 调用序号：5B 三次（1-3）+ 5C 六节（4-9）；第 6 次 = 5C 第三节（03-矛盾利用）
    fake = FakeClient(empty_on={6})
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: fake)

    engine = AnalysisEngine("case_test", case_dir)
    data = asyncio.run(engine.stage_5_full_defense("张三"))

    sections = case_dir / "analysis" / "stage_5" / "sections"
    assert (sections / "01-案件概述.md").exists(), "成功的节正常落盘"
    assert not (sections / "03-矛盾利用.md").exists(), "空内容不得写节产物"
    assert not (case_dir / "analysis" / "stage_53" / "output.md").exists(), "缺节时 stage_53 不落盘"
    assert data.get("partial") is True
    assert "三、核心矛盾点及其法律影响" in data.get("failed_sections", [])

    # 重跑：5B 与成功节跳过，只补失败节（1 次 LLM 调用）
    fake.empty_on = set()
    calls_before = fake.calls
    data2 = asyncio.run(engine.stage_5_full_defense("张三"))
    assert fake.calls - calls_before == 1, "重跑只补失败的节"
    assert (sections / "03-矛盾利用.md").exists()
    assert (case_dir / "analysis" / "stage_53" / "output.md").exists(), "六节齐全后 stage_53 正常落盘"
    assert not data2.get("partial")
