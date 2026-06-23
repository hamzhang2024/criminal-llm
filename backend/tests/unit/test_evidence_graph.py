"""
证据关联图谱单元测试

测试目标：
1. generate_evidence_graph - 人物节点提取、共现/矛盾/关联边构建、Mermaid 生成
2. _safe_id - 人名转安全 Mermaid ID
3. 边界：无证据清单、空证据、IO 错误

用 tmp_path 构造真实 index.json，无需 mock。
"""

import json

import pytest

from evidence_graph import _safe_id, generate_evidence_graph


def _write_index(tmp_path, evidence_list):
    """在 evidence 目录写 index.json"""
    index_file = tmp_path / "index.json"
    index_file.write_text(json.dumps({"evidence": evidence_list}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


class TestSafeId:
    """人名转 Mermaid 节点 ID"""

    def test_id_starts_with_p(self):
        assert _safe_id("张三").startswith("p")

    def test_id_is_safe(self):
        sid = _safe_id("张三")
        # 不含 Mermaid 特殊字符
        assert sid.isalnum()

    def test_same_name_same_id(self):
        assert _safe_id("张三") == _safe_id("张三")

    def test_different_name_different_id(self):
        assert _safe_id("张三") != _safe_id("李四")


class TestEmptyAndError:
    """边界与错误处理"""

    def test_no_index_file(self, tmp_path):
        result = generate_evidence_graph(tmp_path, case_id="c1")
        assert result["mermaid"] == ""
        assert result["nodes"] == []
        assert "不存在" in result["error"]

    def test_invalid_json(self, tmp_path):
        (tmp_path / "index.json").write_text("not json{", encoding="utf-8")
        result = generate_evidence_graph(tmp_path)
        assert "读取失败" in result["error"]

    def test_empty_evidence_list(self, tmp_path):
        _write_index(tmp_path, [])
        result = generate_evidence_graph(tmp_path)
        assert "无证据" in result["error"]


class TestPersonExtraction:
    """人物节点提取"""

    def test_extracts_persons_from_field(self, tmp_path):
        _write_index(tmp_path, [
            {"id": 1, "name": "讯问笔录", "persons": "张三"},
            {"id": 2, "name": "讯问笔录", "persons": "张三"},
        ])
        result = generate_evidence_graph(tmp_path)
        # 张三出现 2 次（>=2 阈值），应作为节点
        labels = [n["label"] for n in result["nodes"]]
        assert "张三" in labels

    def test_single_occurrence_person_excluded(self, tmp_path):
        """只出现 1 次的人物不作为节点（避免稀疏）"""
        _write_index(tmp_path, [
            {"id": 1, "name": "笔录", "persons": "张三"},
            {"id": 2, "name": "笔录", "persons": "李四"},
        ])
        result = generate_evidence_graph(tmp_path)
        labels = [n["label"] for n in result["nodes"]]
        # 各出现 1 次，都不够 2 次阈值
        assert "张三" not in labels
        assert "李四" not in labels

    def test_multiple_persons_in_one_evidence(self, tmp_path):
        """一条证据含多人（逗号/顿号分隔）"""
        _write_index(tmp_path, [
            {"id": 1, "name": "笔录", "persons": "张三,李四,王五"},
            {"id": 2, "name": "笔录", "persons": "张三,李四,王五"},
        ])
        result = generate_evidence_graph(tmp_path)
        labels = [n["label"] for n in result["nodes"]]
        assert set(["张三", "李四", "王五"]).issubset(set(labels))


class TestCoOccurrenceEdges:
    """共现边（两人同出一条证据）"""

    def test_co_occurrence_edge_created(self, tmp_path):
        _write_index(tmp_path, [
            {"id": 1, "name": "笔录", "persons": "张三,李四"},
            {"id": 2, "name": "笔录", "persons": "张三,李四"},
        ])
        result = generate_evidence_graph(tmp_path)
        # 共现 2 次 >= 2 阈值，应有共现边
        assert result["stats"]["co_occurrence_edges"] >= 1
        co_edges = [e for e in result["edges"] if e["type"] == "co_occurrence"]
        assert len(co_edges) >= 1

    def test_co_occurrence_below_threshold_no_edge(self, tmp_path):
        """共现 1 次不画边"""
        _write_index(tmp_path, [
            {"id": 1, "name": "笔录", "persons": "张三,李四"},
            {"id": 2, "name": "笔录", "persons": "张三"},
            {"id": 3, "name": "笔录", "persons": "李四"},
        ])
        result = generate_evidence_graph(tmp_path)
        # 张三李四各出现 2 次但只在 id=1 共现 1 次，共现边阈值 2 不满足
        co_edges = [e for e in result["edges"] if e["type"] == "co_occurrence"]
        assert len(co_edges) == 0


class TestContradictionEdges:
    """矛盾边（contradiction_hints 中提到的人物间）"""

    def test_contradiction_edge_created(self, tmp_path):
        _write_index(tmp_path, [
            {"id": 1, "name": "笔录", "persons": "张三,李四",
             "contradiction_hints": "张三与李四供述矛盾"},
            {"id": 2, "name": "笔录", "persons": "张三,李四"},
        ])
        result = generate_evidence_graph(tmp_path)
        assert result["stats"]["contradiction_edges"] >= 1

    def test_no_contradiction_for_empty_hint(self, tmp_path):
        _write_index(tmp_path, [
            {"id": 1, "name": "笔录", "persons": "张三,李四", "contradiction_hints": "无"},
            {"id": 2, "name": "笔录", "persons": "张三,李四"},
        ])
        result = generate_evidence_graph(tmp_path)
        assert result["stats"]["contradiction_edges"] == 0


class TestRelationEdges:
    """同人多笔关联边"""

    def test_relation_edge_created(self, tmp_path):
        _write_index(tmp_path, [
            {"id": 1, "name": "笔录", "persons": "张三", "related_evidence_ids": [2]},
            {"id": 2, "name": "笔录", "persons": "张三", "related_evidence_ids": [1]},
        ])
        result = generate_evidence_graph(tmp_path)
        assert result["stats"]["relation_edges"] >= 1


class TestMermaidOutput:
    """Mermaid 图谱生成"""

    def test_mermaid_starts_with_graph_td(self, tmp_path):
        _write_index(tmp_path, [
            {"id": 1, "name": "笔录", "persons": "张三,李四"},
            {"id": 2, "name": "笔录", "persons": "张三,李四"},
        ])
        result = generate_evidence_graph(tmp_path)
        assert result["mermaid"].startswith("graph TD")

    def test_mermaid_contains_node_definitions(self, tmp_path):
        _write_index(tmp_path, [
            {"id": 1, "name": "笔录", "persons": "张三"},
            {"id": 2, "name": "笔录", "persons": "张三"},
        ])
        result = generate_evidence_graph(tmp_path)
        # 应包含节点定义（safe_id + label）
        assert "份证据" in result["mermaid"]

    def test_stats_complete(self, tmp_path):
        _write_index(tmp_path, [
            {"id": 1, "name": "笔录", "persons": "张三,李四"},
            {"id": 2, "name": "笔录", "persons": "张三,李四"},
        ])
        result = generate_evidence_graph(tmp_path)
        stats = result["stats"]
        assert stats["total_evidence"] == 2
        assert "persons_extracted" in stats
        assert "frequent_persons" in stats
        assert "co_occurrence_edges" in stats

    def test_case_id_passed_through(self, tmp_path):
        _write_index(tmp_path, [
            {"id": 1, "name": "笔录", "persons": "张三"},
            {"id": 2, "name": "笔录", "persons": "张三"},
        ])
        result = generate_evidence_graph(tmp_path, case_id="case_abc")
        assert result["case_id"] == "case_abc"
