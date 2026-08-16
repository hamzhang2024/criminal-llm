"""控辩对抗三层 bug 修复测试

1. 45d 法官裁决：LLM 返回空串时不得保存 0 字节产物、不得标记 done
2. 跳过守卫：0 字节的对抗分析.md 视为不存在，重跑可自愈
3. 合并清单：full_report 使用实际产物文件名（01-控方指控/02-辩方辩护/03-交叉对决/对抗分析）
4. stage_6 路径：固定解析到共享层 analysis/04.5-控辩对抗/对抗分析.md
"""
import asyncio
from pathlib import Path

import stage_api
from analysis_pipeline import AnalysisPipeline


def _make_pipeline(tmp_path: Path) -> AnalysisPipeline:
    """构造最小可用的 pipeline（含 Wiki 前序材料，避免 LLM prompt 为空）"""
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    wiki = analysis_dir / "indictment_wiki"
    (wiki / "03-证据分析").mkdir(parents=True)
    (wiki / "04-法律依据").mkdir(parents=True)
    (wiki / "01-指控要素.md").write_text("指控要素", encoding="utf-8")
    (wiki / "06-综合结论.md").write_text("综合结论", encoding="utf-8")
    (wiki / "05-矛盾记录.md").write_text("矛盾记录", encoding="utf-8")
    return AnalysisPipeline("case_001", case_path)


def test_45d_empty_llm_result_not_saved_as_done(tmp_path):
    """45d LLM 返回空串：不保存成功产物、不标记完成（修复前：0 字节文件 + done）"""
    pipe = _make_pipeline(tmp_path)

    # 45a-45c 已有产物，只跑 45d
    debate_dir = tmp_path / "case_001" / "analysis" / "04.5-控辩对抗"
    debate_dir.mkdir(parents=True, exist_ok=True)
    (debate_dir / "01-控方指控.md").write_text("控方", encoding="utf-8")
    (debate_dir / "02-辩方辩护.md").write_text("辩方", encoding="utf-8")
    (debate_dir / "03-交叉对决.md").write_text("交叉", encoding="utf-8")

    # 45d 调用返回空串
    async def empty_chat(messages, **kw):
        return ""

    pipe.llm.chat = empty_chat

    result = asyncio.run(pipe.step45_debate_simulation("张三", "诈骗罪"))
    content_file = debate_dir / "对抗分析.md"
    if content_file.exists():
        assert content_file.read_text(encoding="utf-8").strip() != "", \
            "空结果不得保存为成功产物"
    # 45d 状态不得为 done
    status = [s for s in result["sub_steps"] if s.get("step") == "45d"]
    assert not status or status[0]["status"] != "done", "空结果不得标记 done"


def test_empty_debate_file_treated_as_missing(tmp_path):
    """0 字节的对抗分析.md 视为不存在，重跑 45d 可自愈"""
    pipe = _make_pipeline(tmp_path)
    debate_dir = tmp_path / "case_001" / "analysis" / "04.5-控辩对抗"
    debate_dir.mkdir(parents=True, exist_ok=True)
    for fn in ["01-控方指控.md", "02-辩方辩护.md", "03-交叉对决.md"]:
        (debate_dir / fn).write_text("内容", encoding="utf-8")
    (debate_dir / "对抗分析.md").write_text("", encoding="utf-8")  # 0 字节空文件

    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "法官裁决内容"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step45_debate_simulation("张三", "诈骗罪"))

    assert len(calls) >= 1, "0 字节文件应触发 45d 重跑"
    # 45d 拆分后合并产物 = ①裁决总览 + ②攻防建议（本桩两次应答相同）
    merged = (debate_dir / "对抗分析.md").read_text(encoding="utf-8")
    assert "法官裁决内容" in merged and merged.strip() != ""


def test_merge_filenames_match_actual(tmp_path):
    """合并 full_report 包含实际子步骤产物内容（修复前：文件名错配只剩标题头）"""
    pipe = _make_pipeline(tmp_path)

    async def fake_chat(messages, **kw):
        return "对抗内容"

    pipe.llm.chat = fake_chat
    result = asyncio.run(pipe.step45_debate_simulation("张三", "诈骗罪"))
    full = result.get("full_report", "")
    # 合并报告应包含各子步骤内容，而不是只有标题
    assert full.count("对抗内容") >= 3, f"合并报告缺少子步骤内容: {full[:200]}"


def test_stage6_resolves_shared_layer(tmp_path):
    """stage_6 解析到共享层 04.5-控辩对抗（修复前：按罪名层读永远 404）

    实际签名：_resolve_stage_path(case_path: Path, stage_num: int, charge: Optional[str] = None) -> Path
    """
    case_path = tmp_path / "case_001"
    debate_dir = case_path / "analysis" / "04.5-控辩对抗"
    debate_dir.mkdir(parents=True)
    (debate_dir / "对抗分析.md").write_text("法官裁决", encoding="utf-8")
    # 调用 _resolve_stage_path 时带 charge 也应命中共享层
    resolved = stage_api._resolve_stage_path(case_path, 6, charge="诈骗罪")
    assert resolved is not None and resolved.exists(), "带罪名时 stage_6 应解析到共享层"


def test_45a_empty_llm_result_not_saved(tmp_path):
    """45a LLM 返回空串：不保存 0 字节产物、不标 done，45b-d 正常继续"""
    pipe = _make_pipeline(tmp_path)

    call_count = {"n": 0}

    async def fake_chat(messages, **kw):
        call_count["n"] += 1
        # 第一次调用（45a 控方沙箱）返回空，其余正常
        if call_count["n"] == 1:
            return ""
        return "对抗内容"

    pipe.llm.chat = fake_chat
    result = asyncio.run(pipe.step45_debate_simulation("张三", "诈骗罪"))

    debate_dir = tmp_path / "case_001" / "analysis" / "04.5-控辩对抗"
    f = debate_dir / "01-控方指控.md"
    if f.exists():
        assert f.read_text(encoding="utf-8").strip() != "", "空结果不得保存"
    status_45a = [s for s in result["sub_steps"] if s.get("step") == "45a"]
    assert status_45a and status_45a[0]["status"] != "done", "空结果不得标 done"
