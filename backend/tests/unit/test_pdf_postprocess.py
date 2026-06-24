"""
PDF→MD 后处理纯逻辑单元测试

测试目标：
1. _fix_ocr_errors - OCR 错误修复（日语假名、笔录误识）
2. _strip_hallucinated_tables - VLM 幻觉表格检测移除
3. _protect_signatures_as_images - 签名区替换为占位符
4. _fold_consecutive_images - 连续图片折叠为 details 块

这些函数直接决定 MD 产物质量，0% 覆盖风险高。
LLM/PDF IO 函数不在此测。
"""

import pytest

from pdf_to_md_postprocess import (
    _fix_ocr_errors,
    _fold_consecutive_images,
    _protect_signatures_as_images,
    _strip_hallucinated_tables,
)


class TestFixOcrErrors:
    """OCR 错误修复"""

    def test_fix_japanese_kana_misrecognition(self):
        """日语假名误识别为中文指标名"""
        assert _fix_ocr_errors("日本語の語") == "日平均额"
        assert _fix_ocr_errors("国語の語") == "增值税"

    def test_fix_interrogation_record_typos(self):
        """讯问/询问笔录常见误识"""
        assert _fix_ocr_errors("讯间笔录") == "讯问笔录"
        assert _fix_ocr_errors("询间笔录") == "询问笔录"
        assert _fix_ocr_errors("讯间人") == "讯问人"

    def test_fix_spaced_typos(self):
        """带空格的误识"""
        assert _fix_ocr_errors("讯 间 笔 录") == "讯问笔录"
        assert _fix_ocr_errors("询 间 笔 录") == "询问笔录"

    def test_isolated_no_between_chinese(self):
        """汉字间的孤立の替换为的"""
        assert _fix_ocr_errors("张三の笔录") == "张三的笔录"
        assert _fix_ocr_errors("案件の事实") == "案件的事实"

    def test_isolated_no_replaced(self):
        """行内孤立の替换为的"""
        result = _fix_ocr_errors("他 の 说")
        assert "の" not in result

    def test_normal_text_unchanged(self):
        """正常中文文本不受影响"""
        text = "这是正常的中文笔录内容，无 OCR 错误。"
        assert _fix_ocr_errors(text) == text

    def test_multiple_errors_in_one_text(self):
        """一段文本含多个错误，全部修复"""
        text = "讯间笔录の内容，国語の語统计"
        result = _fix_ocr_errors(text)
        assert "讯问笔录" in result
        assert "增值税" in result
        assert "の" not in result


class TestStripHallucinatedTables:
    """VLM 幻觉表格检测"""

    def test_strip_repeated_cell_table(self):
        """同一单元格内容重复 ≥5 次 → 移除整表"""
        # 构造 ≥10 个单元格的表格（"诈骗金额"重复 6 次 + 4 个数字）
        cells = "<td>诈骗金额</td>" * 6 + "<td>100万</td>" * 4
        table = f"<table><tr>{cells}</tr></table>"
        result = _strip_hallucinated_tables(table)
        assert "<table>" not in result
        assert "幻觉表格已移除" in result

    def test_keep_normal_table(self):
        """正常表格（无重复）保留"""
        cells = "".join(f"<td>内容{i}</td>" for i in range(10))
        table = f"<table><tr>{cells}</tr></table>"
        result = _strip_hallucinated_tables(table)
        assert "<table>" in result

    def test_small_table_not_checked(self):
        """小表格（<10 单元格）不检测，原样保留"""
        table = "<table><tr><td>重复</td><td>重复</td></tr></table>"
        result = _strip_hallucinated_tables(table)
        assert result == table

    def test_numeric_repeated_not_hallucination(self):
        """纯数字单元格重复不算幻觉（如年份、合计）"""
        cells = "<td>2024</td>" * 12
        table = f"<table><tr>{cells}</tr></table>"
        result = _strip_hallucinated_tables(table)
        assert "<table>" in result  # 数字重复保留

    def test_no_table_unchanged(self):
        """无表格文本不变"""
        text = "这是普通文本，没有表格"
        assert _strip_hallucinated_tables(text) == text


class TestProtectSignatures:
    """签名区保护"""

    @pytest.mark.parametrize("label", [
        "询问人", "讯问人", "记录人", "被询问人", "被讯问人",
        "捺印人", "翻译人", "法定代理人", "办案单位", "办案人",
        "侦查人员", "见证人", "持有人", "交出人", "接收人",
    ])
    def test_signature_label_replaced(self, label):
        """各签名标签后的手写内容替换为占位符"""
        text = f"{label}：张三手写签名"
        result = _protect_signatures_as_images(text)
        assert "[手写签名]" in result
        assert "张三手写签名" not in result
        # 标签本身保留
        assert label in result

    def test_fullwidth_colon(self):
        """全角冒号也匹配"""
        result = _protect_signatures_as_images("讯问人：李四")
        assert "[手写签名]" in result

    def test_no_signature_unchanged(self):
        """无签名标签的文本不变"""
        text = "讯问笔录内容，被告人张三供述..."
        assert _protect_signatures_as_images(text) == text


class TestFoldConsecutiveImages:
    """连续图片折叠"""

    def test_fold_three_images(self):
        """3+ 连续图片折叠为 details"""
        text = "![img](a.png)\n![img](b.png)\n![img](c.png)"
        result, count = _fold_consecutive_images(text, min_count=3)
        assert count == 1
        assert "<details>" in result
        assert "共 3 张" in result

    def test_no_fold_below_threshold(self):
        """低于阈值的图片不折叠"""
        text = "![img](a.png)\n![img](b.png)"
        result, count = _fold_consecutive_images(text, min_count=3)
        assert count == 0
        assert "<details>" not in result

    def test_fold_with_blank_lines(self):
        """图片间有空行仍视为同一块"""
        text = "![img](a.png)\n\n![img](b.png)\n\n![img](c.png)"
        result, count = _fold_consecutive_images(text, min_count=3)
        assert count == 1

    def test_text_between_images_breaks_block(self):
        """图片间有非空文本则断开成多块"""
        text = "![img](a.png)\n![img](b.png)\n中间文本\n![img](c.png)\n![img](d.png)"
        result, count = _fold_consecutive_images(text, min_count=2)
        assert count == 2

    def test_no_images_unchanged(self):
        """无图片文本不变"""
        text = "普通文本\n无图片"
        result, count = _fold_consecutive_images(text)
        assert count == 0
        assert result == text

    def test_returns_count(self):
        """返回折叠块数量"""
        text = "![img](a.png)\n![img](b.png)\n![img](c.png)"
        _, count = _fold_consecutive_images(text, min_count=3)
        assert count == 1
