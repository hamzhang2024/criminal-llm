"""
Case Manager 辅助函数模块

包含：
- _parse_evidence_blocks：解析 LLM 输出的证据块
- _extract_field：从文本提取字段
- _sanitize_filename：文件名净化
- _is_non_evidence_document：判断是否为非证据类文书（封面/目录等）
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# 非证据文书关键词——这类文书是案卷组织性材料，不含案件事实，不应纳入证据分析范围
_NON_EVIDENCE_NAME_KEYWORDS = (
    "卷内文书目录",
    "卷内目录",
    "案卷封面",
    "案卷封皮",
    "卷皮",
    "扉页",
    "卷宗封面",
    "卷宗目录",
    "卷内备考表",
    "备考表",
    "卷内封面",
    "卷内封底",
    "案卷封底",
    "卷底",
    "卷首",
    "卷尾",
    "目录页",
    "空白页",
)


def _is_non_evidence_document(name: str, evidence_type: str = "") -> bool:
    """判断文书是否为非证据类（封面/目录等案卷组织性材料）

    Args:
        name: 证据名称
        evidence_type: 证据类型（可选，用于辅助判断）

    Returns:
        True 表示该文书不应纳入证据分析范围
    """
    if not name:
        return False

    # 名称明确匹配封面/目录关键词
    for kw in _NON_EVIDENCE_NAME_KEYWORDS:
        if kw in name:
            return True

    # 程序性文书 + 名称含案卷组织性材料关键词 → 非证据
    if evidence_type and "程序性文书" in evidence_type:
        aux_keywords = ["目录", "封面", "封底", "封皮", "备考", "卷首", "卷尾", "卷皮", "扉页"]
        if any(kw in name for kw in aux_keywords):
            return True

    return False





def _parse_evidence_blocks(llm_output: str, source_file: str) -> list:
    """
    解析 LLM 返回的证据块。

    优先尝试 JSON 数组解析（LLM 返回 JSON 格式），
    失败后回退到文本格式解析（兼容已有的输出格式）。
    """
    import json
    import re
    blocks = []

    # ── 第1优先：JSON 数组解析 ──
    # 匹配 ```json ... ``` 代码块，或直接查找 JSON 数组
    json_text = llm_output
    code_block = re.search(r'```json\s*(\[.*?\])\s*```', llm_output, re.DOTALL)
    if code_block:
        json_text = code_block.group(1)
    elif llm_output.strip().startswith('['):
        # 尝试直接解析整个输出为 JSON 数组
        json_text = llm_output.strip()

    if json_text.startswith('['):
        try:
            # 尝试截断到最后一个 ] 以处理 LLM 多余输出
            bracket_end = json_text.rfind(']')
            if bracket_end > 0:
                json_text = json_text[:bracket_end + 1]

            # JSON 清理：修复常见的 LLM 输出格式问题
            import re as _re
            # 1. 修复非法转义字符（\ 后跟非法字符）
            json_text = _re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', json_text)
            # 2. 移除控制字符（除了 \n \r \t）
            json_text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', json_text)

            data = json.loads(json_text)
            if isinstance(data, list) and len(data) > 0:
                for item in data:
                    if isinstance(item, dict):
                        blocks.append({
                            "name": item.get("name", item.get("证据名称", "未命名证据")),
                            "type": item.get("type", item.get("证据类型", "其他证据")),
                            "source": source_file,
                            "page_range": item.get("page_range", item.get("页码范围", "")),
                            "persons": item.get("persons", item.get("涉案人员", "")),
                            "key_facts": item.get("key_facts", item.get("关键事实", "")),
                            "summary": item.get("summary", item.get("详细摘要", "")),
                            "original_quotes": item.get("original_quotes", item.get("原文摘录", "")),
                            "contradiction_hints": item.get("contradiction_hints", item.get("矛盾提示", "无")),
                            "related_entities": item.get("related_entities", item.get("关联信息", "")),
                            "images": item.get("images", []),
                            "raw_text": json.dumps(item, ensure_ascii=False, indent=2),
                        })
                if blocks:
                    logger.info(f"[证据解析] {source_file}: JSON 模式解析成功，{len(blocks)} 份证据")
                    return blocks
        except (json.JSONDecodeError, Exception) as e:
            logger.info(f"[证据解析] {source_file}: JSON 解析失败，回退到文本模式: {e}")
            blocks = []

    # ── 第2优先：文本格式解析（兼容原有格式）──
    patterns = [
        r'#{1,3}\s*证据(\d+)[：:]\s*(.+)$',           # ### 证据1：名称
        r'\*\*【证据\s*(\d+)】\*\*',                   # **【证据 1】**
        r'\*\*证据(\d+)\*\*[：:]\s*(.+)',              # **证据1**：名称
        r'#{1,3}\s*证据\s*(\d+)\s*$',                  # ### 证据 1
    ]

    sections = None

    for pat in patterns:
        p = re.compile(pat, re.MULTILINE)
        matches = list(p.finditer(llm_output))
        if matches:
            # 按匹配位置拆分
            sections = []
            last_end = 0
            for m in matches:
                sections.append(llm_output[last_end:m.start()])  # 前一份证据的内容
                sections.append(m.group(0))  # 证据标题
                last_end = m.end()
            sections.append(llm_output[last_end:])  # 最后一份证据的内容
            break

    if sections is None or len(sections) < 3:
        # 没找到证据块标记，整个输出作为一份证据
        logger.info(f"[证据解析] {source_file}: 未找到证据标记，整个输出作为一份证据（LLM 可能未按格式输出）")
        blocks.append({
            "name": source_file.replace(".md", ""),
            "type": "其他证据",
            "source": source_file,
            "page_range": "",
            "persons": "",
            "key_facts": "",
            "summary": llm_output,
            "original_quotes": "",
            "contradiction_hints": "",
            "related_entities": "",
            "raw_text": llm_output,
        })
        return blocks

    # sections: [intro, title1, content1, title2, content2, ...]
    # sections[0] = intro, sections[1] = title1, sections[2] = content1+title2, etc.
    # 重新整理：奇数索引是标题，偶数索引（>=2）是内容
    for i in range(1, len(sections), 2):
        title = sections[i].strip()
        content = sections[i + 1] if i + 1 < len(sections) else ""

        # 提取证据名：优先从内容的"证据名称"字段获取，其次从标题提取
        ev_name = _extract_field(content, "证据名称")
        if ev_name:
            name = ev_name
        else:
            # 从标题中去掉格式标记
            name = re.sub(r'#+\s*', '', title).strip()
            name = re.sub(r'\*\*【|】\*\*|\*\*', '', name).strip()
            if not name:
                name = f"证据{i // 2 + 1}"

        # 去掉 LLM 输出的"证据N："前缀（如"证据1：《受案登记表》" → "《受案登记表》"）
        name = re.sub(r'^证据\s*\d+\s*[：:]\s*', '', name).strip()
        if not name:
            name = f"证据{i // 2 + 1}"

        # 提取各字段
        ev_type = _extract_field(content, "证据类型") or "其他证据"
        page_range = _extract_field(content, "页码范围") or ""
        persons = _extract_field(content, "涉案人员") or ""
        key_facts = _extract_field(content, "关键事实") or ""
        summary = _extract_field(content, "详细摘要") or content
        original_quotes = _extract_field(content, "原文摘录") or ""
        contradiction = _extract_field(content, "矛盾提示") or "无"
        related_entities = _extract_field(content, "关联信息") or ""

        # 提取图片引用（从 Markdown 中匹配 ![]() 语法）
        images = re.findall(r'!\[.*?\]\([^)]+\)', content)
        if not images:
            # 也尝试从关联信息中提取
            images = re.findall(r'!\[.*?\]\([^)]+\)', related_entities)

        blocks.append({
            "name": name,
            "type": ev_type,
            "source": source_file,
            "page_range": page_range.strip(),
            "persons": persons.strip(),
            "key_facts": key_facts.strip(),
            "summary": summary.strip(),
            "original_quotes": original_quotes.strip(),
            "contradiction_hints": contradiction.strip(),
            "related_entities": related_entities.strip(),
            "images": images,
            "raw_text": content.strip(),
        })

    return blocks


def _extract_field(text: str, field_name: str) -> Optional[str]:
    """从证据文本中提取指定字段"""
    import re
    # 匹配 "字段名"：值 或 **字段名**：值 或 | 字段名 | 值 |
    patterns = [
        rf'\*\*{field_name}\*\*\s*[：:]\s*(.+)',
        rf'{field_name}\s*[：:]\s*(.+)',
        rf'\|\s*{field_name}\s*\|\s*(.+?)\s*\|',
    ]
    for p in patterns:
        match = re.search(p, text)
        if match:
            return match.group(1).strip()
    return None


def _sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    import re
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', '_', name)
    return name[:80]



