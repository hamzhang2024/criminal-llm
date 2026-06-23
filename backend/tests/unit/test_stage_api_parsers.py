"""
Stage API 解析函数单元测试

测试目标：
1. _detect_edge_type - 边标签→关系类型推断（关键词优先级）
2. _parse_person_relation - 从 Markdown 解析人物关系（表格+mermaid）
3. _parse_event_timeline - 从 Markdown 解析事件时间线（日期规范化+类型推断）

这些函数解析 LLM 输出的 Markdown，逻辑复杂且无 IO 依赖，是高价值纯函数。
"""

import pytest

from stage_api_parsers import (
    _detect_edge_type,
    _parse_event_timeline,
    _parse_person_relation,
)


class TestDetectEdgeType:
    """边标签→关系类型推断"""

    @pytest.mark.parametrize("label,expected", [
        # participation（纠集/指使，优先级最高）
        ("纠集他人", "participation"),
        ("指使作案", "participation"),
        ("共犯关系", "participation"),
        ("同案", "participation"),
        # cooperation（合作/雇佣）
        ("雇佣关系", "cooperation"),
        ("同事", "cooperation"),
        ("合伙经营", "cooperation"),
        # family（亲属）
        ("夫妻", "family"),
        ("兄弟", "family"),
        ("父子", "family"),
        # friendship
        ("朋友", "friendship"),
        ("邻居", "friendship"),
        # conflict
        ("冲突", "conflict"),
        ("殴打", "conflict"),
        # introduction（注：friendship 的"认识"优先级更高，"介绍认识"归 friendship）
        ("介绍", "introduction"),
        ("引荐", "introduction"),
        # financial
        ("债务", "financial"),
        ("转账", "financial"),
    ])
    def test_keyword_mapping(self, label, expected):
        assert _detect_edge_type(label) == expected

    def test_empty_label_returns_other(self):
        assert _detect_edge_type("") == "other"
        assert _detect_edge_type(None) == "other"

    def test_unknown_label_returns_other(self):
        assert _detect_edge_type("某种未知关系") == "other"

    def test_participation_priority_over_cooperation(self):
        """'纠集同事'应判为 participation（行为动词优先于关系性质）"""
        assert _detect_edge_type("纠集同事") == "participation"

    def test_friendship_priority_over_introduction(self):
        """已知优先级：friendship 的'认识'先于 introduction 的'介绍'检查，
        故'介绍认识'归 friendship 而非 introduction"""
        assert _detect_edge_type("介绍认识") == "friendship"


class TestParsePersonRelation:
    """人物关系解析（表格 + mermaid）"""

    def test_empty_content_returns_empty(self):
        result = _parse_person_relation("")
        assert result == {"nodes": [], "edges": []}

    def test_parse_table_roles_fallback(self):
        """无 mermaid 时从表格解析人物+角色，并按角色生成边"""
        content = """## 涉案人员

| 姓名 | 角色 | 关系 |
| --- | --- | --- |
| 张三 | 被告人 | 主犯 |
| 李四 | 从犯 | 共犯 |
| 王五 | 被害人 | 受害 |
"""
        result = _parse_person_relation(content)
        names = [n["name"] for n in result["nodes"]]
        assert "张三" in names
        assert "李四" in names
        # 角色正确
        zhang = next(n for n in result["nodes"] if n["name"] == "张三")
        assert zhang["role"] == "defendant"
        li = next(n for n in result["nodes"] if n["name"] == "李四")
        assert li["role"] == "co_defendant"
        # 被告人与从犯应有共犯边
        coop_edges = [e for e in result["edges"] if e["type"] == "cooperation"]
        assert any(e["source"] == "张三" and e["target"] == "李四" for e in coop_edges)

    def test_skip_table_header(self):
        """表头行（姓名/涉案人员）不应作为节点"""
        content = """| 姓名 | 角色 |
| --- | --- |
| 涉案人员 | 角色 |
| 张三 | 被告人 |
"""
        result = _parse_person_relation(content)
        names = [n["name"] for n in result["nodes"]]
        assert "姓名" not in names
        assert "涉案人员" not in names

    def test_mermaid_nodes_and_edges_parsed(self):
        """解析 mermaid graph 的节点定义和边"""
        content = """## 人物关系

| 姓名 | 角色 |
| --- | --- |
| 张三 | 被告人 |
| 李四 | 证人 |

```mermaid
graph TD
    A[张三]
    B[李四]
    A -- "纠集" --> B
```
"""
        result = _parse_person_relation(content)
        names = [n["name"] for n in result["nodes"]]
        assert "张三" in names
        assert "李四" in names
        # 边应解析出张三→李四，类型 participation
        assert len(result["edges"]) >= 1
        edge = result["edges"][0]
        assert edge["source"] == "张三"
        assert edge["target"] == "李四"
        assert edge["type"] == "participation"

    def test_role_inferred_from_node_label(self):
        """表格无角色时从 mermaid 节点标签推断"""
        content = """```mermaid
graph TD
    A[被告人张三]
    B[证人李四]
```
"""
        result = _parse_person_relation(content)
        zhang = next(n for n in result["nodes"] if "张三" in n["name"])
        assert zhang["role"] == "defendant"

    def test_table_separator_variants(self):
        """支持各种表格分隔线变体"""
        content = """| 姓名 | 角色 |
| :--- | :---: |
| 张三 | 被告人 |
"""
        result = _parse_person_relation(content)
        names = [n["name"] for n in result["nodes"]]
        assert "张三" in names


class TestParseEventTimeline:
    """事件时间线解析"""

    def test_empty_content_returns_empty(self):
        result = _parse_event_timeline("")
        assert result == {"events": []}

    def test_extract_events_from_timeline_block(self):
        """从 mermaid timeline 块提取事件"""
        content = """```mermaid
timeline
    2024年3月 : 张三实施诈骗
    2024年5月 : 李四被拘留
```
"""
        result = _parse_event_timeline(content)
        assert len(result["events"]) == 2
        assert result["events"][0]["date"] == "2024-03-01"
        assert result["events"][1]["date"] == "2024-05-01"

    def test_event_type_inferred(self):
        """事件类型推断：crime/procedure/evidence/defense/other"""
        content = """```mermaid
timeline
    2024年1月 : 实施诈骗犯罪
    2024年2月 : 被拘留逮捕
    2024年3月 : 进行笔录鉴定
    2024年4月 : 委托律师辩护
    2024年5月 : 某普通事件
```
"""
        result = _parse_event_timeline(content)
        types = [e["type"] for e in result["events"]]
        assert "crime" in types
        assert "procedure" in types
        assert "evidence" in types
        assert "defense" in types
        assert "other" in types

    def test_evidence_refs_extracted(self):
        """从描述中提取证据引用"""
        content = """```mermaid
timeline
    2024年1月 : 诈骗行为 证据1 证据2
```
"""
        result = _parse_event_timeline(content)
        assert "证据1" in result["events"][0]["evidenceRefs"]
        assert "证据2" in result["events"][0]["evidenceRefs"]

    def test_date_normalization_year_only(self):
        """仅年份的日期补全为 -01-01"""
        content = """```mermaid
timeline
    2024年 : 某事件
```
"""
        result = _parse_event_timeline(content)
        assert result["events"][0]["date"] == "2024-01-01"

    def test_date_normalization_year_month(self):
        """年月补全日为 01"""
        content = """```mermaid
timeline
    2024年3月 : 某事件
```
"""
        result = _parse_event_timeline(content)
        assert result["events"][0]["date"] == "2024-03-01"

    def test_date_normalization_full_date(self):
        """完整年月日规范化"""
        content = """```mermaid
timeline
    2024年3月15日 : 某事件
```
"""
        result = _parse_event_timeline(content)
        assert result["events"][0]["date"] == "2024-03-15"

    def test_date_with_time_stripped(self):
        """带时间的日期去掉时间部分"""
        content = """```mermaid
timeline
    2024年3月15日15点30分 : 某事件
```
"""
        result = _parse_event_timeline(content)
        assert result["events"][0]["date"] == "2024-03-15"

    def test_short_description_skipped(self):
        """过短描述（<3字）跳过"""
        content = """```mermaid
timeline
    2024年1月 : ab
    2024年2月 : 完整事件描述
```
"""
        result = _parse_event_timeline(content)
        assert len(result["events"]) == 1

    def test_event_ids_sequential(self):
        """事件 id 顺序编号"""
        content = """```mermaid
timeline
    2024年1月 : 事件一描述
    2024年2月 : 事件二描述
```
"""
        result = _parse_event_timeline(content)
        assert result["events"][0]["id"] == "event_0"
        assert result["events"][1]["id"] == "event_1"
