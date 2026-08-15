"""fund_flows 结构化字段测试：提取输出 + 资金流分析消费"""
import json

from case_manager import _parse_evidence_blocks
from analysis_engine import AnalysisEngine, _fund_flows_to_text


def test_parse_evidence_blocks_fund_flows_json():
    """整卷提取 JSON 解析：fund_flows 字段被正确解析"""
    llm_out = json.dumps([{
        "name": "转账记录截图",
        "type": "书证",
        "key_facts": ["赵志强转账给唐鑫 5 万元"],
        "summary": "2023年4月8日转账",
        "fund_flows": ["赵志强→唐鑫｜50000元｜2023-04-08｜微信｜垫资"],
        "related_entities": "",
    }], ensure_ascii=False)
    blocks = _parse_evidence_blocks(llm_out, "第10卷.md")
    assert blocks[0]["fund_flows"] == ["赵志强→唐鑫｜50000元｜2023-04-08｜微信｜垫资"]


def test_parse_evidence_blocks_fund_flows_text():
    """整卷提取文本解析：资金往来条目被解析"""
    llm_out = """### 证据1：转账记录截图
- **证据类型**：书证
- **关键事实**：赵志强转账 5 万元
- **详细摘要**：2023年4月8日
- **资金往来**：赵志强→唐鑫｜50000元｜2023-04-08｜微信｜垫资；唐鑫→孙琴芳｜30000元｜2023-05-01｜银行｜利息"""
    blocks = _parse_evidence_blocks(llm_out, "第10卷.md")
    assert len(blocks[0]["fund_flows"]) == 2
    assert "赵志强→唐鑫" in blocks[0]["fund_flows"][0]
    assert "唐鑫→孙琴芳" in blocks[0]["fund_flows"][1]


def test_fund_flows_to_text():
    """资金流分析：结构化 fund_flows 组装成文本"""
    flows = ["赵志强→唐鑫｜50000元｜2023-04-08｜微信｜垫资", "唐鑫→孙琴芳｜30000元｜2023-05-01｜银行｜利息"]
    text = _fund_flows_to_text("转账记录截图", flows)
    assert "转账记录截图" in text
    assert "赵志强→唐鑫" in text and "50000元" in text


def test_fund_flows_to_text_empty():
    """无资金往来 → 返回空"""
    assert _fund_flows_to_text("讯问笔录", []) == ""


def test_analysis_uses_fund_flows(tmp_path):
    """集成：stage_35 数据源优先含 fund_flows 的 evidence，不依赖 md 全文重扫"""
    case_dir = tmp_path / "case"
    ev_dir = case_dir / "evidence"
    md_dir = case_dir / "md"
    ev_dir.mkdir(parents=True); md_dir.mkdir(parents=True)

    (ev_dir / "001_转账记录.md").write_text("转账记录全文", encoding="utf-8")
    (ev_dir / "index.json").write_text(json.dumps({"evidence": [{
        "id": 1, "name": "转账记录截图", "type": "书证", "md_file": "001_转账记录.md",
        "summary": "转账摘要", "fund_flows": ["赵志强→唐鑫｜50000元｜2023-04-08｜微信｜垫资"],
    }]}, ensure_ascii=False), encoding="utf-8")

    engine = AnalysisEngine("case_id", case_dir)
    texts = engine._load_fund_source_texts()
    # 资金流文本应含结构化 fund_flows 内容
    joined = "\n".join(t["text"] for t in texts)
    assert "赵志强→唐鑫" in joined and "50000元" in joined
