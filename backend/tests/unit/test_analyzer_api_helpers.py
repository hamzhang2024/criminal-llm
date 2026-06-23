"""
analyzer_api_helpers 单元测试

测试目标：
1. apply_report_update - 报告章节增删改（replace/delete/insert）
2. infer_evidence_type - 文件名推断证据类型
3. build_analysis_prompt - 分析提示词构建
4. parse_report - Markdown 报告结构化解析

PDF 相关函数（get_pdf_pages/extract_pdf_text）依赖 fitz，不在此测。
"""

import pytest

from analyzer_api_helpers import (
    apply_report_update,
    build_analysis_prompt,
    infer_evidence_type,
    parse_report,
)


class TestInferEvidenceType:
    """文件名推断证据类型"""

    @pytest.mark.parametrize("filename,expected", [
        ("起诉意见书.pdf", "起诉意见书"),
        ("指控材料.pdf", "起诉意见书"),
        ("讯问笔录.pdf", "讯问笔录"),
        ("供述记录.pdf", "讯问笔录"),
        ("证人证言.pdf", "证人证言"),
        ("鉴定意见.pdf", "鉴定意见"),
        ("勘验笔录.pdf", "勘验笔录"),
        ("辨认笔录.pdf", "辨认笔录"),
        ("银行流水.pdf", "书证-金融"),
        ("转账记录.pdf", "书证-金融"),
        ("合同.pdf", "书证-合同"),
        ("协议书.pdf", "书证-合同"),
        ("身份证.pdf", "书证-身份"),
        ("户籍证明.pdf", "书证-身份"),
        ("拘留证.pdf", "程序性文书"),
        ("逮捕证.pdf", "程序性文书"),
        ("取保候审.pdf", "程序性文书"),
        ("未知文件.pdf", "其他证据"),
    ])
    def test_type_inference(self, filename, expected):
        assert infer_evidence_type(filename) == expected

    def test_empty_filename(self):
        assert infer_evidence_type("") == "其他证据"


class TestApplyReportUpdate:
    """报告章节更新"""

    def test_replace_section(self):
        original = "### 一、概述\n\n旧内容\n\n### 二、分析\n\n分析内容"
        result = apply_report_update(original, {
            "action": "replace",
            "target_section": "一、概述",
            "new_content": "新内容",
        })
        assert "新内容" in result
        assert "旧内容" not in result
        assert "### 二、分析" in result

    def test_replace_nonexistent_section_appends(self):
        """替换不存在的章节时追加到末尾"""
        original = "### 一、概述\n\n内容"
        result = apply_report_update(original, {
            "action": "replace",
            "target_section": "九、新增",
            "new_content": "新增内容",
        })
        assert "### 九、新增" in result
        assert "新增内容" in result

    def test_delete_section(self):
        original = "### 一、概述\n\n概述内容\n\n### 二、分析\n\n分析内容"
        result = apply_report_update(original, {
            "action": "delete",
            "target_section": "一、概述",
        })
        assert "概述内容" not in result
        assert "### 二、分析" in result

    def test_insert_after_section(self):
        original = "### 一、概述\n\n概述内容\n\n### 三、结尾\n\n结尾"
        result = apply_report_update(original, {
            "action": "insert",
            "target_section": "二、新增",
            "new_content": "新增内容",
            "position": "after:一、概述",
        })
        # 新章节应插入到一、概述之后、三、结尾之前
        idx_new = result.index("二、新增")
        idx_end = result.index("三、结尾")
        idx_overview = result.index("一、概述")
        assert idx_overview < idx_new < idx_end

    def test_insert_default_append(self):
        """insert 无 position 时追加到末尾"""
        original = "### 一、概述\n\n内容"
        result = apply_report_update(original, {
            "action": "insert",
            "target_section": "二、新增",
            "new_content": "新增内容",
        })
        assert "### 二、新增" in result
        assert result.index("二、新增") > result.index("一、概述")

    def test_batch_updates(self):
        """批量应用多个更新"""
        original = "### 一、概述\n\n旧内容"
        result = apply_report_update(original, {
            "updates": [
                {"action": "replace", "target_section": "一、概述", "new_content": "新内容"},
                {"action": "insert", "target_section": "二、补充", "new_content": "补充内容"},
            ]
        })
        assert "新内容" in result
        assert "### 二、补充" in result

    def test_empty_update_returns_original(self):
        original = "### 一\n\n内容"
        result = apply_report_update(original, {})
        assert result == original


class TestBuildAnalysisPrompt:
    """分析提示词构建"""

    def test_contains_defendant(self):
        prompt = build_analysis_prompt("张三", [{"filename": "f.md", "type": "讯问笔录", "text": "内容"}])
        assert "张三" in prompt

    def test_contains_evidence_content(self):
        prompt = build_analysis_prompt("张三", [
            {"filename": "讯问笔录.md", "type": "讯问笔录", "text": "供述内容"},
        ])
        assert "讯问笔录.md" in prompt
        assert "供述内容" in prompt

    def test_contains_analysis_structure(self):
        prompt = build_analysis_prompt("张三", [])
        assert "指控要素分析" in prompt
        assert "辩护要点" in prompt
        assert "量刑情节" in prompt

    def test_multiple_evidences_joined(self):
        prompt = build_analysis_prompt("张三", [
            {"filename": "a.md", "type": "讯问笔录", "text": "内容A"},
            {"filename": "b.md", "type": "书证", "text": "内容B"},
        ])
        assert "内容A" in prompt
        assert "内容B" in prompt


class TestParseReport:
    """报告结构化解析"""

    def test_parse_indictment_summary(self):
        markdown = """### 一、指控要素分析

- **罪名：** 诈骗罪
- **涉案金额：** 100万元
"""
        report = parse_report(markdown)
        assert report["indictment_summary"]["罪名"] == "诈骗罪"
        assert report["indictment_summary"]["涉案金额"] == "100万元"

    def test_parse_defense_points(self):
        markdown = """### 五、辩护要点

- 证据不足
- 程序违法
"""
        report = parse_report(markdown)
        assert "证据不足" in report["defense_points"]
        assert "程序违法" in report["defense_points"]

    def test_parse_contradictions(self):
        markdown = """### 四、矛盾识别

- 供述前后矛盾
- 供述与书证矛盾
"""
        report = parse_report(markdown)
        assert len(report["contradictions"]) == 2

    def test_table_rows_excluded_from_points(self):
        """表格行不计入辩护要点"""
        markdown = """### 五、辩护要点

| 证据 | 要点 |
| --- | --- |
| 笔录 | 矛盾 |
"""
        report = parse_report(markdown)
        # 表格行（以 | 开头）应被排除
        for point in report["defense_points"]:
            assert not point.startswith("|")

    def test_empty_report(self):
        report = parse_report("")
        assert report["indictment_summary"] == {}
        assert report["defense_points"] == []
        assert report["sections"] == {}

    def test_sections_captured(self):
        markdown = "### 一、概述\n\n内容"
        report = parse_report(markdown)
        assert "概述" in report["sections"]
