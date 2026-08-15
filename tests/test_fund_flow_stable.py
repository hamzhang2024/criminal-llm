"""资金流三层分离：数据层确定性聚合 + 校验层机器对账"""
import json
from pathlib import Path

from fund_flow import (
    normalize_amount, aggregate_fund_flows, build_master_table, verify_fund_output,
)


def test_normalize_amount():
    """金额规范化：万元/万/元 → 元"""
    assert normalize_amount("5万元") == 50000
    assert normalize_amount("50000元") == 50000
    assert normalize_amount("1.5万") == 15000
    assert normalize_amount("约3万元") == 30000
    assert normalize_amount("2000") is None  # 无单位裸数字不视为金额（防页码/编号误判）
    assert normalize_amount("无金额") is None


def test_aggregate_groups_duplicates(tmp_path):
    """数据层：同一笔往来被多份证据记录 → 分组去重，标注印证数"""
    ev_dir = tmp_path / "evidence"
    ev_dir.mkdir(parents=True)
    (ev_dir / "index.json").write_text(json.dumps({"evidence": [
        {"name": "银行流水", "type": "书证",
         "fund_flows": ["赵志强→唐鑫｜50000元｜2023-04-08｜银行｜垫资"]},
        {"name": "赵志强讯问笔录", "type": "犯罪嫌疑人供述和辩解",
         "fund_flows": ["赵志强→唐鑫｜5万元｜2023年4月8日｜银行转账｜垫资"]},  # 同一笔，金额表述不同
        {"name": "唐鑫讯问笔录", "type": "犯罪嫌疑人供述和辩解",
         "fund_flows": ["唐鑫→孙琴芳｜30000元｜2023-05-01｜微信｜利息"]},
    ]}, ensure_ascii=False), encoding="utf-8")

    rows = aggregate_fund_flows(tmp_path)
    assert len(rows) == 2  # 两笔去重后
    zhao = next(r for r in rows if r["from"] == "赵志强")
    assert zhao["amount"] == 50000  # 规范化后一致，分进同组
    assert len(zhao["sources"]) == 2  # 流水+供述双源印证
    assert any("客观" in t for t in zhao["source_types"])  # 书证映射为客观证据


def test_build_master_table():
    """主表：Markdown 表格含来源类型和印证数"""
    rows = [{
        "date": "2023-04-08", "from": "赵志强", "to": "唐鑫", "amount": 50000,
        "channel": "银行", "purpose": "垫资",
        "sources": ["银行流水", "赵志强讯问笔录"], "source_types": {"书证", "犯罪嫌疑人供述和辩解"},
    }]
    table = build_master_table(rows)
    assert "赵志强" in table and "50000" in table
    assert "银行流水" in table
    assert "2" in table  # 印证数


def test_verify_fund_output_flags_hallucinated_amounts():
    """校验层：LLM 输出引用的金额不在主表/起诉书中 → 标记待人工核对"""
    master_amounts = {50000, 30000}
    indictment_amounts = {79300}
    llm_output = "指控金额 79300 元，流水显示 50000 元，另有 88888 元去向不明。"
    issues = verify_fund_output(llm_output, master_amounts, indictment_amounts)
    assert any("88888" in i for i in issues)
    assert not any("50000" in i for i in issues)
    assert not any("79300" in i for i in issues)


def test_verify_fund_output_clean():
    """校验层：全部金额可溯源 → 通过"""
    issues = verify_fund_output("流水 50000 元与指控一致", {50000}, set())
    assert issues == []


def test_aggregate_empty_when_no_fund_flows(tmp_path):
    """无 fund_flows（旧案件）→ 空列表，调用方回退旧路径"""
    ev_dir = tmp_path / "evidence"
    ev_dir.mkdir(parents=True)
    (ev_dir / "index.json").write_text(json.dumps({"evidence": [
        {"name": "笔录", "type": "证人证言"}
    ]}, ensure_ascii=False), encoding="utf-8")
    assert aggregate_fund_flows(tmp_path) == []
