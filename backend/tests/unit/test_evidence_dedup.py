"""
证据去重与关联模块单元测试

测试目标：
1. _normalize_name - 名称规范化（去前缀/日期/次数/括号）
2. _dedup_key - 去重键生成（page_range 空时用 key_facts 区分）
3. _extract_interrogatee_and_date - 被讯问人/日期/次数提取
4. dedup_and_link - 重复标记 + 同人多笔关联
5. group_evidence_by_chain - 按链条分组

业务关键：去重不能误删（仅标记不合并），关联需正确识别同人。
"""

import pytest

from evidence_dedup import (
    _dedup_key,
    _extract_interrogatee_and_date,
    _normalize_name,
    dedup_and_link,
    group_evidence_by_chain,
)


class TestNormalizeName:
    """名称规范化"""

    def test_strip_whitespace(self):
        assert _normalize_name("  讯问笔录  ") == "讯问笔录"

    def test_remove_numeric_prefix(self):
        assert _normalize_name("001_讯问笔录") == "讯问笔录"
        assert _normalize_name("12 讯问笔录") == "讯问笔录"

    def test_remove_date_in_parens(self):
        # 各种日期格式（点分/横线/纯数字/中文年月日）
        assert _normalize_name("讯问笔录(2024.10.29)") == "讯问笔录"
        assert _normalize_name("讯问笔录(2024-10-29)") == "讯问笔录"
        assert _normalize_name("讯问笔录(20241029)") == "讯问笔录"
        assert _normalize_name("讯问笔录(2024年10月29日)") == "讯问笔录"

    def test_remove_sequence_marker_arabic(self):
        # 阿拉伯数字次数
        assert _normalize_name("讯问笔录第2次") == "讯问笔录"

    def test_remove_sequence_marker_chinese(self):
        # 中文数字次数（实际案卷常见）
        assert _normalize_name("讯问笔录第一次") == "讯问笔录"
        assert _normalize_name("讯问笔录第三次") == "讯问笔录"

    def test_preserve_person_name_in_parens(self):
        # 人名应保留
        result = _normalize_name("讯问笔录(张三)")
        assert "张三" in result

    def test_empty_name(self):
        assert _normalize_name("") == ""
        assert _normalize_name(None) == ""

    def test_lowercase(self):
        assert _normalize_name("EvidenceABC") == "evidenceabc"

    def test_fullwidth_parens_normalized(self):
        # 全角括号内的日期也应去除
        assert _normalize_name("讯问笔录（2024.10.29）") == "讯问笔录"


class TestDedupKey:
    """去重键生成"""

    def test_same_evidence_same_key(self):
        ev1 = {"name": "讯问笔录", "type": "讯问笔录", "page_range": "1-10"}
        ev2 = {"name": "讯问笔录", "type": "讯问笔录", "page_range": "1-10"}
        assert _dedup_key(ev1) == _dedup_key(ev2)

    def test_different_page_range_different_key(self):
        ev1 = {"name": "讯问笔录", "type": "讯问笔录", "page_range": "1-10"}
        ev2 = {"name": "讯问笔录", "type": "讯问笔录", "page_range": "11-20"}
        assert _dedup_key(ev1) != _dedup_key(ev2)

    def test_empty_page_range_uses_key_facts(self):
        # page_range 为空时，用 key_facts 区分
        ev1 = {"name": "讯问笔录", "type": "讯问笔录", "page_range": "", "key_facts": "事实A内容"}
        ev2 = {"name": "讯问笔录", "type": "讯问笔录", "page_range": "", "key_facts": "事实B内容"}
        assert _dedup_key(ev1) != _dedup_key(ev2)

    def test_empty_page_range_same_facts_same_key(self):
        ev1 = {"name": "讯问笔录", "type": "讯问笔录", "page_range": "", "key_facts": "相同事实"}
        ev2 = {"name": "讯问笔录", "type": "讯问笔录", "page_range": "", "key_facts": "相同事实"}
        assert _dedup_key(ev1) == _dedup_key(ev2)

    def test_different_type_different_key(self):
        ev1 = {"name": "笔录", "type": "讯问笔录", "page_range": "1-10"}
        ev2 = {"name": "笔录", "type": "询问笔录", "page_range": "1-10"}
        assert _dedup_key(ev1) != _dedup_key(ev2)


class TestExtractInterrogateeAndDate:
    """被讯问人/日期/次数提取"""

    def test_extract_from_persons_field(self):
        interrogatee, date, seq = _extract_interrogatee_and_date(
            "讯问笔录", persons="张三", page_range=""
        )
        assert interrogatee == "张三"
        assert seq == 0

    def test_extract_from_persons_first_of_multiple(self):
        interrogatee, _, _ = _extract_interrogatee_and_date(
            "讯问笔录", persons="张三,李四", page_range=""
        )
        assert interrogatee == "张三"

    def test_extract_from_name_parens(self):
        # persons 为空时从名称括号取人名
        interrogatee, _, _ = _extract_interrogatee_and_date(
            "讯问笔录(张三)", persons="", page_range=""
        )
        assert interrogatee == "张三"

    def test_date_in_name_excluded_from_interrogatee(self):
        # 括号内是日期时不应作为人名
        interrogatee, date, _ = _extract_interrogatee_and_date(
            "讯问笔录(2024.10.29)", persons="", page_range=""
        )
        assert interrogatee == ""
        assert "2024.10.29" == date

    def test_sequence_extracted(self):
        _, _, seq = _extract_interrogatee_and_date(
            "讯问笔录第3次", persons="张三", page_range=""
        )
        assert seq == 3

    def test_sequence_chinese_num_extracted(self):
        _, _, seq = _extract_interrogatee_and_date(
            "讯问笔录第三次", persons="张三", page_range=""
        )
        assert seq == 3

    def test_date_from_page_range(self):
        _, date, _ = _extract_interrogatee_and_date(
            "讯问笔录", persons="张三", page_range="2024-10-29 第1-10页"
        )
        assert "2024-10-29" == date


class TestDedupAndLink:
    """去重标记与同人关联"""

    def test_empty_list_returns_empty(self):
        assert dedup_and_link([]) == []

    def test_marks_duplicates(self):
        """相同 dedup_key 的第二份标记为重复"""
        ev_list = [
            {"id": 1, "name": "讯问笔录", "type": "讯问笔录", "page_range": "1-10"},
            {"id": 2, "name": "讯问笔录", "type": "讯问笔录", "page_range": "1-10"},
        ]
        result = dedup_and_link(ev_list)
        assert result[0]["duplicate_of"] is None
        assert result[1]["duplicate_of"] == 1
        assert "重复" in result[1]["dedup_note"]

    def test_no_duplicate_when_different_page_range(self):
        ev_list = [
            {"id": 1, "name": "讯问笔录", "type": "讯问笔录", "page_range": "1-10"},
            {"id": 2, "name": "讯问笔录", "type": "讯问笔录", "page_range": "11-20"},
        ]
        result = dedup_and_link(ev_list)
        assert result[0]["duplicate_of"] is None
        assert result[1]["duplicate_of"] is None

    def test_empty_name_not_deduped(self):
        """空名称不参与去重（避免误判）"""
        ev_list = [
            {"id": 1, "name": "", "type": "讯问笔录", "page_range": "1-10"},
            {"id": 2, "name": "", "type": "讯问笔录", "page_range": "1-10"},
        ]
        result = dedup_and_link(ev_list)
        assert result[1]["duplicate_of"] is None

    def test_links_same_person_multiple_statements(self):
        """同人多份讯问笔录建立关联"""
        ev_list = [
            {"id": 1, "name": "讯问笔录(张三)第一次", "type": "犯罪嫌疑人供述和辩解",
             "persons": "张三", "page_range": "1-10"},
            {"id": 2, "name": "讯问笔录(张三)第二次", "type": "犯罪嫌疑人供述和辩解",
             "persons": "张三", "page_range": "11-20"},
        ]
        result = dedup_and_link(ev_list)
        # 两份笔录应互相关联
        assert 2 in result[0]["related_evidence_ids"]
        assert 1 in result[1]["related_evidence_ids"]

    def test_no_link_single_statement(self):
        """单份笔录不建立关联（需 >= 2 成员）"""
        ev_list = [
            {"id": 1, "name": "讯问笔录(张三)", "type": "犯罪嫌疑人供述和辩解",
             "persons": "张三", "page_range": "1-10"},
        ]
        result = dedup_and_link(ev_list)
        assert result[0]["related_evidence_ids"] == []

    def test_different_persons_not_linked(self):
        """不同人的笔录不关联"""
        ev_list = [
            {"id": 1, "name": "讯问笔录", "type": "犯罪嫌疑人供述和辩解",
             "persons": "张三", "page_range": "1-10"},
            {"id": 2, "name": "讯问笔录", "type": "犯罪嫌疑人供述和辩解",
             "persons": "李四", "page_range": "11-20"},
        ]
        result = dedup_and_link(ev_list)
        assert result[0]["related_evidence_ids"] == []
        assert result[1]["related_evidence_ids"] == []

    def test_all_evidence_get_dedup_fields(self):
        """每条证据都应初始化去重字段"""
        ev_list = [
            {"id": 1, "name": "讯问笔录", "type": "讯问笔录", "page_range": "1-10"},
        ]
        result = dedup_and_link(ev_list)
        assert "duplicate_of" in result[0]
        assert "related_evidence_ids" in result[0]
        assert "dedup_note" in result[0]


class TestGroupEvidenceByChain:
    """按链条分组"""

    def test_empty_list(self):
        assert group_evidence_by_chain([]) == []

    def test_groups_related_statements(self):
        """关联的讯问笔录应分到一组"""
        ev_list = [
            {"id": 1, "name": "讯问笔录", "type": "犯罪嫌疑人供述和辩解",
             "persons": "张三", "page_range": "1-10",
             "related_evidence_ids": [2]},
            {"id": 2, "name": "讯问笔录", "type": "犯罪嫌疑人供述和辩解",
             "persons": "张三", "page_range": "11-20",
             "related_evidence_ids": [1]},
        ]
        groups = group_evidence_by_chain(ev_list)
        assert len(groups) == 1
        assert set(groups[0]["member_refs"]) == {1, 2}
        assert groups[0]["group_type"] == "interrogation"
        assert groups[0]["anchor_evidence_id"] == 1

    def test_single_statement_no_group(self):
        """单份无关联的笔录不成组"""
        ev_list = [
            {"id": 1, "name": "讯问笔录", "type": "犯罪嫌疑人供述和辩解",
             "persons": "张三", "page_range": "1-10",
             "related_evidence_ids": []},
        ]
        groups = group_evidence_by_chain(ev_list)
        assert groups == []

    def test_group_label_contains_person(self):
        ev_list = [
            {"id": 1, "name": "讯问笔录", "type": "犯罪嫌疑人供述和辩解",
             "persons": "张三", "page_range": "1-10",
             "related_evidence_ids": [2]},
            {"id": 2, "name": "讯问笔录", "type": "犯罪嫌疑人供述和辩解",
             "persons": "张三", "page_range": "11-20",
             "related_evidence_ids": [1]},
        ]
        groups = group_evidence_by_chain(ev_list)
        assert "张三" in groups[0]["group_label"]
