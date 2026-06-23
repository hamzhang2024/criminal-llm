"""
case_manager_helpers 单元测试

测试目标：
1. _is_non_evidence_document - 判断封面/目录等非证据文书
2. _parse_evidence_blocks - 解析 LLM 证据输出（JSON 优先 + 文本回退）
3. _extract_field - 从证据文本提取字段
4. _sanitize_filename - 文件名非法字符清理
"""

import pytest

from case_manager_helpers import (
    _extract_field,
    _is_non_evidence_document,
    _parse_evidence_blocks,
    _sanitize_filename,
)


class TestIsNonEvidenceDocument:
    """非证据文书判断"""

    @pytest.mark.parametrize("name", [
        "案卷封面",
        "卷内文书目录",
        "卷皮",
        "备考表",
        "卷首",
        "目录页",
        "空白页",
    ])
    def test_cover_and_catalog_keywords(self, name):
        assert _is_non_evidence_document(name) is True

    def test_normal_evidence_name(self):
        assert _is_non_evidence_document("讯问笔录") is False
        assert _is_non_evidence_document("受案登记表") is False

    def test_empty_name(self):
        assert _is_non_evidence_document("") is False

    def test_programmatic_doc_with_aux_keyword(self):
        """程序性文书 + 案卷组织关键词 → 非证据"""
        assert _is_non_evidence_document("某文件", evidence_type="程序性文书") is False
        assert _is_non_evidence_document("目录", evidence_type="程序性文书") is True
        assert _is_non_evidence_document("封面", evidence_type="程序性文书") is True

    def test_programmatic_doc_without_aux_keyword_not_filtered(self):
        """程序性文书但无组织关键词 → 仍为证据"""
        assert _is_non_evidence_document("讯问笔录", evidence_type="程序性文书") is False

    def test_non_programmatic_type_ignores_aux_keyword(self):
        """非程序性文书类型时，辅助关键词不生效（仅名称关键词生效）"""
        # "目录" 单独不在名称关键词里（名称关键词是"目录页"等），程序性文书才触发
        # 但 "卷内目录" 在名称关键词里，任何类型都触发
        assert _is_non_evidence_document("卷内目录", evidence_type="讯问笔录") is True


class TestExtractField:
    """字段提取"""

    def test_plain_field(self):
        assert _extract_field("证据名称：讯问笔录", "证据名称") == "讯问笔录"

    def test_bold_field(self):
        assert _extract_field("**证据名称**：讯问笔录", "证据名称") == "讯问笔录"

    def test_table_field(self):
        assert _extract_field("| 证据名称 | 讯问笔录 |", "证据名称") == "讯问笔录"

    def test_field_not_found(self):
        assert _extract_field("无此字段", "证据名称") is None

    def test_field_with_fullwidth_colon(self):
        assert _extract_field("证据类型:书证", "证据类型") == "书证"

    def test_empty_text(self):
        assert _extract_field("", "证据名称") is None


class TestSanitizeFilename:
    """文件名清理"""

    def test_replace_illegal_chars(self):
        assert _sanitize_filename('a<b>c:"d') == "a_b_c__d"

    def test_replace_whitespace(self):
        assert _sanitize_filename("a b\tc") == "a_b_c"

    def test_truncate_to_80(self):
        long_name = "x" * 100
        assert len(_sanitize_filename(long_name)) == 80

    def test_normal_name_unchanged(self):
        assert _sanitize_filename("讯问笔录.pdf") == "讯问笔录.pdf"


class TestParseEvidenceBlocks:
    """证据块解析（JSON 优先 + 文本回退）"""

    def test_parse_json_array(self):
        """JSON 数组格式优先解析"""
        llm_output = """```json
[
  {"name": "讯问笔录", "type": "讯问笔录", "page_range": "1-10"},
  {"name": "询问笔录", "type": "询问笔录"}
]
```"""
        blocks = _parse_evidence_blocks(llm_output, "source.md")
        assert len(blocks) == 2
        assert blocks[0]["name"] == "讯问笔录"
        assert blocks[0]["type"] == "讯问笔录"
        assert blocks[0]["source"] == "source.md"
        assert blocks[0]["page_range"] == "1-10"
        assert blocks[1]["name"] == "询问笔录"

    def test_parse_json_chinese_keys(self):
        """支持中文键名（证据名称/证据类型等）"""
        llm_output = '[{"证据名称": "笔录A", "证据类型": "书证"}]'
        blocks = _parse_evidence_blocks(llm_output, "s.md")
        assert len(blocks) == 1
        assert blocks[0]["name"] == "笔录A"
        assert blocks[0]["type"] == "书证"

    def test_json_with_invalid_escape_fallback(self):
        """JSON 含非法转义字符时自动修复"""
        # \x 非法转义，应被修复为 \\x 后解析
        llm_output = '[{"name": "test\\xvalue", "type": "书证"}]'
        blocks = _parse_evidence_blocks(llm_output, "s.md")
        # 应能解析（修复后），不应崩溃
        assert len(blocks) == 1

    def test_parse_text_format_markdown_headers(self):
        """文本格式：### 证据1：名称"""
        llm_output = """### 证据1：讯问笔录
证据类型：讯问笔录
页码范围：1-10
关键事实：供述内容

### 证据2：询问笔录
证据类型：询问笔录
"""
        blocks = _parse_evidence_blocks(llm_output, "source.md")
        assert len(blocks) == 2
        assert blocks[0]["name"] == "讯问笔录"
        assert blocks[0]["type"] == "讯问笔录"
        assert blocks[0]["page_range"] == "1-10"

    def test_no_markers_whole_output_as_one_block(self):
        """无证据标记时，整个输出作为一份证据"""
        llm_output = "这是没有证据标记的纯文本输出"
        blocks = _parse_evidence_blocks(llm_output, "source.md")
        assert len(blocks) == 1
        assert blocks[0]["name"] == "source"  # .md 被去掉
        assert blocks[0]["summary"] == llm_output

    def test_extract_images_from_content(self):
        """从内容提取图片引用"""
        llm_output = """### 证据1：书证
证据类型：书证
![图片1](image1.png)
"""
        blocks = _parse_evidence_blocks(llm_output, "s.md")
        assert len(blocks[0]["images"]) >= 1

    def test_default_contradiction_hints(self):
        """矛盾提示默认值为'无'"""
        llm_output = '[{"name": "笔录", "type": "讯问笔录"}]'
        blocks = _parse_evidence_blocks(llm_output, "s.md")
        assert blocks[0]["contradiction_hints"] == "无"

    def test_empty_output(self):
        """空输出返回单条证据"""
        blocks = _parse_evidence_blocks("", "source.md")
        assert len(blocks) == 1
        assert blocks[0]["name"] == "source"

    def test_json_array_with_extra_text(self):
        """JSON 数组后有多余文本，截断到最后一个 ]"""
        llm_output = '[{"name": "笔录", "type": "书证"}] 这是多余的解释文本'
        blocks = _parse_evidence_blocks(llm_output, "s.md")
        assert len(blocks) == 1
        assert blocks[0]["name"] == "笔录"
