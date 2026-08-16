"""阶段 5（综合辩护）回归测试：5B 重构后 5C 必须还能拿到 LLM client

2026-08-07 用户反馈"阶段5执行失败: name 'client' is not defined"——
5B 抽成共用方法时把 client 定义带走，5C 裸用 client 抛 NameError。
"""
import json

import pytest

import llm_client
from analysis_engine import AnalysisEngine


class FakeClient:
    """ canned 应答的假 LLM 客户端，记录调用次数 """

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, model=None):
        self.calls += 1
        return f"## 分析结果（第 {self.calls} 次调用）\n\n| 时间 | 关键陈述 | 变化 | 可能原因 |\n|---|---|---|---|\n| - | - | - | - |"


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


@pytest.mark.asyncio
async def test_stage5_full_defense_completes(tmp_path, monkeypatch):
    """阶段 5 全链路（5A+5B+5C）必须一次跑通，不因变量缺失中断"""
    fake = FakeClient()
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: fake)

    engine = AnalysisEngine("case_test", _make_case(tmp_path))
    await engine.stage_5_full_defense("张三")

    # 5B 三次调用 + 5C 六次调用（六子章节拆分后）
    assert fake.calls == 9
    for stage in (51, 52, 53):
        out = engine.analysis_dir / f"stage_{stage}" / "output.md"
        assert out.exists(), f"stage_{stage} 未生成"
        assert len(out.read_text(encoding="utf-8")) > 0
