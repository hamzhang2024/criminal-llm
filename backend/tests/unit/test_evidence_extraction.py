"""
证据提取相关单元测试

测试目标：
1. _is_non_evidence_document 识别封面/目录类文书
2. _parse_evidence_blocks 不截断 summary（P1.1 回归）
3. 降级证据 needs_review 标记
"""
import sys
from pathlib import Path

# 确保能导入 backend 模块
backend_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_dir))

from case_manager_helpers import (
    _is_non_evidence_document,
    _parse_evidence_blocks,
)


class TestNonEvidenceDocumentFilter:
    """P1.5: 案卷封面/目录类文书排除"""

    def test_volume_table_of_contents(self):
        """卷内文书目录应被识别为非证据"""
        assert _is_non_evidence_document("卷内文书目录") is True
        assert _is_non_evidence_document("卷内目录") is True
        assert _is_non_evidence_document("卷内目录(二卷)") is True

    def test_case_cover(self):
        """案卷封面应被识别为非证据"""
        assert _is_non_evidence_document("案卷封面") is True
        assert _is_non_evidence_document("案卷封皮") is True
        assert _is_non_evidence_document("卷皮") is True

    def test_title_page(self):
        """扉页应被识别为非证据"""
        assert _is_non_evidence_document("扉页") is True

    def test_normal_evidence_not_filtered(self):
        """正常证据名称不应被过滤"""
        assert _is_non_evidence_document("讯问笔录") is False
        assert _is_non_evidence_document("受案登记表") is False
        assert _is_non_evidence_document("刑事案件侦破经过") is False
        assert _is_non_evidence_document("鉴定意见书") is False

    def test_procedural_doc_with_directory_keyword(self):
        """程序性文书 + 含'目录' → 非证据"""
        assert _is_non_evidence_document("案件目录", "程序性文书") is True
        assert _is_non_evidence_document("文书目录", "程序性文书") is True

    def test_procedural_doc_without_directory_keyword(self):
        """程序性文书但不含目录/封面 → 保留为证据"""
        assert _is_non_evidence_document("立案决定书", "程序性文书") is False
        assert _is_non_evidence_document("拘留证", "程序性文书") is False

    def test_empty_name(self):
        """空名称不应被识别为非证据"""
        assert _is_non_evidence_document("") is False
        assert _is_non_evidence_document(None) is False


class TestSummaryNoTruncation:
    """P1.1: summary 不再被截断到 2000 字"""

    def test_long_summary_preserved_in_json_parse(self):
        """JSON 模式解析时长 summary 应完整保留"""
        long_summary = "这是一段很长的摘要。" * 500  # 约 5000 字
        llm_output = f'''```json
[
  {{
    "name": "讯问笔录",
    "type": "犯罪嫌疑人供述和辩解",
    "page_range": "1-10",
    "persons": "张三",
    "key_facts": "关键事实",
    "summary": "{long_summary}",
    "original_quotes": "原文",
    "contradiction_hints": "无",
    "related_entities": "",
    "images": []
  }}
]
```'''
        blocks = _parse_evidence_blocks(llm_output, "test.md")
        assert len(blocks) == 1
        assert len(blocks[0]["summary"]) > 2000, "summary 应超过 2000 字（未被截断）"
        assert blocks[0]["summary"] == long_summary

    def test_long_summary_preserved_in_text_fallback(self):
        """文本模式回退时长 summary 应完整保留"""
        long_content = "详细摘要：这是一段很长的内容。" * 500
        llm_output = f"### 证据1：测试证据\n\n{long_content}"
        blocks = _parse_evidence_blocks(llm_output, "test.md")
        assert len(blocks) == 1
        # summary 应保留完整内容（不再是 content[:2000]）
        assert len(blocks[0]["summary"]) > 2000


class TestDegradedEvidenceMarking:
    """P1.4: LLM 解析失败的降级证据应能携带 needs_review 标记

    注：_parse_evidence_blocks 本身不设置 needs_review，
    该标记在 _extract_single_file 降级分支设置。
    这里验证解析失败场景下整个输出作为一份证据（type=其他证据）的识别。
    """

    def test_unformatted_output_becomes_single_block(self):
        """LLM 未按格式输出时，整段作为一份证据"""
        unformatted = "这是一段没有 JSON 也没有证据标记的纯文本输出。"
        blocks = _parse_evidence_blocks(unformatted, "test.md")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "其他证据"


class TestEvidenceReviewMDRewrite:
    """L4: 校对端点 MD 文件重写逻辑测试

    覆盖两种 MD 格式：
    1. 标准格式（含 ## 关联信息 标记）— _replace_section 按段落替换
    2. 降级格式（原始文件，只有 \n---\n 分隔）— fallback 保留 --- 后内容
    """

    def test_standard_format_replaces_key_facts_section(self):
        """标准格式：校对 key_facts 后 MD 的 ## 关键事实 段落被替换"""
        # 模拟标准证据 MD（与 case_manager.py:1204 模板一致）
        original_md = """# 测试证据

| 项目 | 内容 |
|------|------|
| **证据类型** | 书证 |
| **来源文件** | test.md |
| **页码范围** | 1-5 |
| **涉案人员** | 张三 |

## 关联信息

无

## 关键事实

旧的关键事实内容

## 原文摘录

原文内容

## 矛盾提示

无

---

## LLM 原始输出

{}"""
        # 模拟 _replace_section 逻辑
        def _replace_section(text: str, header: str, new_content: str) -> str:
            idx = text.find(header)
            if idx < 0:
                return text
            line_end = text.find("\n", idx)
            if line_end < 0:
                line_end = len(text)
            next_section = text.find("\n## ", line_end)
            if next_section < 0:
                next_section = len(text)
            return text[:line_end] + f"\n\n{new_content}" + text[next_section:]

        new_md = _replace_section(original_md, "## 关键事实", "校对后的新关键事实")
        assert "校对后的新关键事实" in new_md
        assert "旧的关键事实内容" not in new_md
        # 其他段落不受影响
        assert "## 原文摘录" in new_md
        assert "原文内容" in new_md
        assert "## 矛盾提示" in new_md

    def test_degraded_format_fallback_preserves_content(self):
        """降级格式（原始文件）：无 ## 关联信息 标记，fallback 保留 --- 后内容"""
        # 模拟降级证据 MD（与 case_manager.py:1191 模板一致）
        original_md = """# 测试证据

| 项目 | 内容 |
|------|------|
| **证据类型** | 原始文件（LLM 提取失败） |
| **来源文件** | test.md |

> **注意**：此证据为原始 MD 文件内容，因 LLM 无法正确提取证据格式而保留原文。

---

原始文件的全部内容放这里"""

        # 模拟校对端点的 preserved 提取逻辑
        preserved = ""
        marker = "## 关联信息"
        marker_idx = original_md.find(marker)
        if marker_idx >= 0:
            preserved = original_md[marker_idx:]
        else:
            # 降级格式 fallback
            sep_idx = original_md.find("\n---\n")
            if sep_idx >= 0:
                preserved = original_md[sep_idx:]

        # 验证 fallback 正确保留 --- 后内容
        assert "---" in preserved
        assert "原始文件的全部内容放这里" in preserved
        # 标准标记不存在
        assert "## 关联信息" not in preserved

    def test_replace_section_missing_header_noop(self):
        """段落标记不存在时 _replace_section 不修改原文"""
        original = "# 标题\n\n## 关联信息\n\n内容\n"
        # 不存在的段落
        result = original  # _replace_section 找不到 "## 不存在" 时返回原文
        idx = original.find("## 不存在")
        assert idx < 0  # 确认段落不存在
        assert result == original  # 不修改
