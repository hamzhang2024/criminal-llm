"""单任务聚焦拆分测试：step2 笔录总结 + 45c/45d 控辩对抗

拆分契约：

1. step2 逐次笔录总结：原 prompt 捆绑「忠实转录」+「转换错误标记」两个认知方向
   相反的任务。下游调查结论：step3 矛盾分析、step4b 证据摄入、总结内容 API
   均只把总结文件整体作为上下文/展示内容消费，无任何代码解析「转换错误标记」
   部分 → 无下游消费，删除任务二，转录回归纯单任务。
   契约：每次笔录仍 1 次调用，prompt 不再含「转换错误标记」。

2. 45c 交叉对决：1 次调用 4 板块 → 2 次聚焦调用
   ①逐焦点攻防表（核心产物）②三条路径评估（无罪/改变定性/罪轻）
   两次调用共享 system + material 前缀（build_cached_messages），
   子产物分别落盘支持断点续跑，合并写回 03-交叉对决.md（对外契约不变）。

3. 45d 法官裁决：1 次调用 6 板块 → 2 次聚焦调用
   ①裁决总览（总览表+三路径可行性+综合评估）
   ②攻防建议（控方攻击点+辩方加强点+交叉询问预演）
   同样共享前缀、子产物落盘、合并写回 对抗分析.md（对外契约不变）。
"""
import asyncio
import json
from pathlib import Path

from analysis_pipeline import AnalysisPipeline


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


def _all_text(messages) -> str:
    return "\n".join(m["content"] for m in messages)


# ========== 拆分点 1：step2 删除「转换错误标记」任务 ==========

def _make_step2_pipeline(tmp_path: Path) -> AnalysisPipeline:
    """最小 step2 场景：step1 结果 + 1 份预处理笔录（无时间标记 → 单次笔录）"""
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    preprocess_dir = analysis_dir / "preprocess" / "讯问笔录"
    preprocess_dir.mkdir(parents=True)
    (preprocess_dir / "张某讯问笔录.md").write_text("问：你做了什么？答：我没有拿钱。", encoding="utf-8")

    pipe = AnalysisPipeline("case_001", case_path)
    pipe._save_step_result(1, {
        "merged_files": [{
            "person": "张某", "type": "讯问笔录",
            "filename": "张某讯问笔录.md", "session_count": 1,
        }],
    })
    return pipe


def test_step2_prompt_single_task_no_ocr_marking(tmp_path):
    """step2 prompt 回归纯转录单任务：不含「转换错误标记」，每次笔录仍 1 次调用"""
    pipe = _make_step2_pipeline(tmp_path)
    client = CaptureClient(["转录内容"])
    pipe.llm.chat = client.chat

    asyncio.run(pipe.step2_detailed_summaries("张某"))

    assert len(client.calls) == 1, f"单次笔录应 1 次调用，实际 {len(client.calls)}"
    prompt_text = _all_text(client.calls[0])
    assert "转换错误标记" not in prompt_text, "OCR 错误标记任务应删除（无下游消费）"
    assert "识别错误" not in prompt_text, "system 中的 OCR 质检职责也应删除"
    assert "忠实转录" in prompt_text, "转录主任务应保留"


def test_step2_ocr_task_removed_from_source():
    """源码级契约：step2 不再包含「转换错误标记」任务段"""
    import inspect
    src = inspect.getsource(AnalysisPipeline.step2_detailed_summaries)
    assert "转换错误标记" not in src


# ========== 拆分点 2：45c/45d 各拆 2 次聚焦调用 ==========

def _make_45_pipeline(tmp_path: Path) -> AnalysisPipeline:
    """45a/45b 已有产物，聚焦验证 45c/45d 的拆分行为"""
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    wiki = analysis_dir / "indictment_wiki"
    (wiki / "03-证据分析").mkdir(parents=True)
    (wiki / "04-法律依据").mkdir(parents=True)
    (wiki / "01-指控要素.md").write_text("指控要素", encoding="utf-8")
    (wiki / "06-综合结论.md").write_text("综合结论", encoding="utf-8")
    (wiki / "05-矛盾记录.md").write_text("矛盾记录", encoding="utf-8")

    debate_dir = analysis_dir / "04.5-控辩对抗"
    debate_dir.mkdir(parents=True, exist_ok=True)
    (debate_dir / "01-控方指控.md").write_text("控方论点", encoding="utf-8")
    (debate_dir / "02-辩方辩护.md").write_text("辩方论点", encoding="utf-8")
    return AnalysisPipeline("case_001", case_path)


def test_45c_45d_each_split_two_focused_calls(tmp_path):
    """45c/45d 各 2 次聚焦调用：共享前缀、子产物落盘、合并产物含全部板块"""
    pipe = _make_45_pipeline(tmp_path)
    client = CaptureClient(["逐焦点攻防表", "三路径评估", "裁决总览", "攻防建议"])
    pipe.llm.chat = client.chat

    result = asyncio.run(pipe.step45_debate_simulation("张三", "诈骗罪"))

    # (a) 45a/45b 跳过，捕获到的 4 次调用 = 45c 两次 + 45d 两次
    assert len(client.calls) == 4, f"45c+45d 应共 4 次调用，实际 {len(client.calls)}"

    # (b) 同一子阶段内两次调用共享 system 与 material 前缀（缓存命中）
    for pair in [(0, 1), (2, 3)]:
        m1, m2 = client.calls[pair[0]], client.calls[pair[1]]
        assert m1[0]["content"] == m2[0]["content"], "同一子阶段两次调用 system 应一致"
        mat1 = m1[-1]["content"].rsplit("\n\n---\n\n", 1)[0]
        mat2 = m2[-1]["content"].rsplit("\n\n---\n\n", 1)[0]
        assert mat1 == mat2, "同一子阶段两次调用 material 前缀应一致"
        assert "控方论点" in mat1 and "辩方论点" in mat1

    # (c) 各次调用指令聚焦单一任务（声明「只输出」）
    instr = [c[-1]["content"].rsplit("\n\n---\n\n", 1)[1] for c in client.calls]
    assert "只输出" in instr[0] and "逐焦点攻防" in instr[0]
    assert "只输出" in instr[1] and ("无罪" in instr[1] or "路径" in instr[1])
    assert "只输出" in instr[2] and ("总览" in instr[2] or "可行性" in instr[2])
    assert "只输出" in instr[3] and ("攻击" in instr[3] or "加强" in instr[3])
    # 45d 材料应包含交叉对决产物
    assert "逐焦点攻防表" in client.calls[2][-1]["content"]

    # (d) 合并产物对外契约不变：03-交叉对决.md 含攻防表+路径评估
    debate_dir = tmp_path / "case_001" / "analysis" / "04.5-控辩对抗"
    clash = (debate_dir / "03-交叉对决.md").read_text(encoding="utf-8")
    assert "逐焦点攻防表" in clash and "三路径评估" in clash
    # 对抗分析.md 含裁决总览+攻防建议
    verdict = (debate_dir / "对抗分析.md").read_text(encoding="utf-8")
    assert "裁决总览" in verdict and "攻防建议" in verdict

    # (e) 子产物分别落盘（断点续跑粒度）
    sub_files = [f.name for f in debate_dir.iterdir()]
    assert any("逐焦点攻防" in n or "攻防表" in n for n in sub_files), f"缺少 45c①子产物: {sub_files}"
    assert any("路径评估" in n for n in sub_files), f"缺少 45c②子产物: {sub_files}"
    assert any("裁决总览" in n for n in sub_files), f"缺少 45d①子产物: {sub_files}"
    assert any("攻防建议" in n for n in sub_files), f"缺少 45d②子产物: {sub_files}"

    # (f) 45c/45d 状态均 done
    steps = {s["step"]: s["status"] for s in result["sub_steps"]}
    assert steps.get("45c") == "done" and steps.get("45d") == "done"


def test_45c_resume_skips_existing_subproduct(tmp_path):
    """45c 断点续跑：已有子产物跳过，只为缺失子任务调用 LLM，合并仍完整"""
    pipe = _make_45_pipeline(tmp_path)
    debate_dir = tmp_path / "case_001" / "analysis" / "04.5-控辩对抗"
    # 预置 45c 第一个子产物（断点场景），45d 两子产物也预置以聚焦 45c 行为
    pipe._save_debate_file("03-交叉对决-逐焦点攻防.md", "既有攻防表")
    pipe._save_debate_file("对抗分析-裁决总览.md", "既有总览")
    pipe._save_debate_file("对抗分析-攻防建议.md", "既有建议")

    client = CaptureClient(["新路径评估"])
    pipe.llm.chat = client.chat

    asyncio.run(pipe.step45_debate_simulation("张三", "诈骗罪"))

    assert len(client.calls) == 1, f"只应补 45c② 一次调用，实际 {len(client.calls)}"
    # 既有子产物不被覆盖
    assert (debate_dir / "03-交叉对决-逐焦点攻防.md").read_text(encoding="utf-8") == "既有攻防表"
    # 合并产物同时含既有与新产物
    clash = (debate_dir / "03-交叉对决.md").read_text(encoding="utf-8")
    assert "既有攻防表" in clash and "新路径评估" in clash
    verdict = (debate_dir / "对抗分析.md").read_text(encoding="utf-8")
    assert "既有总览" in verdict and "既有建议" in verdict


def test_45c_empty_subcall_marks_failed(tmp_path):
    """45c 子调用返回空串：不保存子产物、不生成合并产物、45c 标记 failed"""
    pipe = _make_45_pipeline(tmp_path)

    async def empty_chat(messages, **kw):
        return ""

    pipe.llm.chat = empty_chat
    result = asyncio.run(pipe.step45_debate_simulation("张三", "诈骗罪"))

    debate_dir = tmp_path / "case_001" / "analysis" / "04.5-控辩对抗"
    clash_file = debate_dir / "03-交叉对决.md"
    if clash_file.exists():
        assert clash_file.read_text(encoding="utf-8").strip() != "", "空结果不得保存为合并产物"
    steps = {s["step"]: s["status"] for s in result["sub_steps"]}
    assert steps.get("45c") != "done", "空结果不得标 done"
