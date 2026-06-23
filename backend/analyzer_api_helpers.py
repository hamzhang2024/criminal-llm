"""
Analyzer API 辅助函数模块

包含：
- apply_report_update：应用报告更新
- infer_evidence_type：推断证据类型
- get_pdf_pages / extract_pdf_text：PDF 信息提取
- build_analysis_prompt：构建分析提示词
- parse_report：解析报告 Markdown
"""
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def apply_report_update(original_markdown: str, update_result: dict[str, Any]) -> str:
    """
    应用增量更新到原报告（支持多个更新批量应用）
    
    Args:
        original_markdown: 原报告 Markdown
        update_result: LLM 返回的更新结果（包含 updates 数组）
    
    Returns:
        更新后的 Markdown
    """
    
    result = original_markdown
    updates = update_result.get("updates", [])
    
    # 如果没有 updates 数组，兼容旧的单更新格式
    if not updates:
        action = update_result.get("action", "replace")
        target_section = update_result.get("target_section", "")
        new_content = update_result.get("new_content", "")
        position = update_result.get("position", "")
        updates = [{"action": action, "target_section": target_section, "new_content": new_content, "position": position}]
    
    # 按顺序应用每个更新
    for update in updates:
        action = update.get("action", "replace")
        target_section = update.get("target_section", "")
        new_content = update.get("new_content", "")
        position = update.get("position", "")
        
        if action == "delete":
            # 删除章节
            if target_section:
                pattern = rf"###\s*{re.escape(target_section)}.*?(?=###\s|$)"
                result = re.sub(pattern, "", result, flags=re.DOTALL)
        
        elif action == "replace":
            # 替换章节
            if target_section and new_content:
                pattern = rf"(###\s*{re.escape(target_section)}).*?(?=###\s|$)"
                replacement = f"\\1\n\n{new_content}"
                new_result = re.sub(pattern, replacement, result, flags=re.DOTALL)
                if new_result != result:
                    result = new_result
                else:
                    # 如果正则匹配失败，追加到末尾
                    result = result + f"\n\n### {target_section}\n\n{new_content}"
        
        elif action == "insert":
            # 插入新章节
            if new_content:
                if position.startswith("after:"):
                    target = position[6:]
                    pattern = rf"(###\s*{re.escape(target)}.*?)(?=###\s|$)"
                    match = re.search(pattern, result, flags=re.DOTALL)
                    if match:
                        insert_pos = match.end()
                        result = result[:insert_pos] + f"\n\n### {target_section}\n\n{new_content}" + result[insert_pos:]
                        continue
                # 默认追加到末尾
                result = result + f"\n\n### {target_section}\n\n{new_content}"
    
    return result


def infer_evidence_type(filename: str) -> str:
    """从文件名推断证据类型"""
    if "起诉" in filename or "指控" in filename:
        return "起诉意见书"
    elif "讯问" in filename or "供述" in filename or "笔录" in filename:
        return "讯问笔录"
    elif "证言" in filename or "证人" in filename:
        return "证人证言"
    elif "鉴定" in filename:
        return "鉴定意见"
    elif "勘验" in filename or "检查" in filename:
        return "勘验笔录"
    elif "辨认" in filename:
        return "辨认笔录"
    elif "银行" in filename or "流水" in filename or "转账" in filename:
        return "书证-金融"
    elif "合同" in filename or "协议" in filename:
        return "书证-合同"
    elif "身份" in filename or "户籍" in filename:
        return "书证-身份"
    elif "拘留" in filename or "逮捕" in filename or "取保" in filename:
        return "程序性文书"
    else:
        return "其他证据"


def get_pdf_pages(pdf_path: str) -> int:
    """获取 PDF 页数"""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        pages = doc.page_count
        doc.close()
        return pages
    except Exception:
        return 0


def extract_pdf_text(pdf_path: str, max_pages: int = 50) -> str:
    """提取 PDF 文本"""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        text_parts = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            text_parts.append(page.get_text())
        doc.close()
        return "\n".join(text_parts)
    except Exception as e:
        return f"[文本提取失败: {e}]"


def build_analysis_prompt(defendant: str, evidence_texts: list[dict]) -> str:
    """构建分析提示词"""
    evidence_section = "\n\n".join([
        f"### {e['filename']} ({e['type']})\n{e['text']}"
        for e in evidence_texts
    ])
    
    return f"""你是一个专业的刑事辩护律师。请分析以下案卷材料，为被告人 **{defendant}** 提供辩护分析。

## 案卷材料

{evidence_section}

## 分析要求

请按照以下结构输出分析报告：

### 一、指控要素分析
基于起诉意见书，提取指控的核心要素：
- 罪名
- 涉案金额
- 涉案时间
- 涉案人员
- 指控行为

### 二、证据-要素映射表
列出每份证据证明的指控要素，格式：

| 证据名称 | 证明的要素 | 关键内容摘要 | 证明力评估 |

### 三、证据三性分析
对每份证据进行合法性、真实性、关联性分析：

| 证据名称 | 合法性 | 真实性 | 关联性 | 综合评价 |

### 四、矛盾识别
对比供述之间、供述与书证之间的矛盾点

### 五、辩护要点
为 {defendant} 提出具体的辩护要点

### 六、量刑情节
评估可能影响量刑的因素

请基于《刑事诉讼法》的规定，提供专业、准确的分析。
"""


def parse_report(markdown_text: str) -> dict[str, Any]:
    """
    解析 Markdown 报告为结构化数据
    
    Args:
        markdown_text: Markdown 格式的报告文本
    
    Returns:
        结构化的报告数据
    """
    
    report = {
        "indictment_summary": {},
        "evidence_map": [],
        "evidence_analysis": [],
        "contradictions": [],
        "defense_points": [],
        "sentencing_factors": {}
    }
    
    # 提取各章节内容
    sections = {}
    current_section = None
    current_content = []
    
    for line in markdown_text.split("\n"):
        # 检测章节标题
        section_match = re.match(r"^###\s*(一|二|三|四|五|六)[、.．]\s*(.+)$", line)
        if section_match:
            if current_section:
                sections[current_section] = "\n".join(current_content)
            current_section = section_match.group(2).strip()
            current_content = []
        else:
            current_content.append(line)
    
    if current_section:
        sections[current_section] = "\n".join(current_content)
    
    # 解析指控要素
    if "指控要素分析" in sections:
        content = sections["指控要素分析"]
        for key in ["罪名", "涉案金额", "涉案时间", "涉案人员", "指控行为"]:
            match = re.search(rf"[-*]\s*\*\*{key}[：:]\*\*\s*(.+)", content)
            if match:
                report["indictment_summary"][key] = match.group(1).strip()
    
    # 解析辩护要点
    if "辩护要点" in sections:
        content = sections["辩护要点"]
        for match in re.finditer(r"[-*]\s*(\d+[.．])?\s*(.+)", content):
            point = match.group(2).strip()
            if point and not point.startswith("|"):  # 排除表格行
                report["defense_points"].append(point)
    
    # 解析矛盾点
    if "矛盾识别" in sections:
        content = sections["矛盾识别"]
        for match in re.finditer(r"[-*]\s*(\d+[.．])?\s*(.+)", content):
            contradiction = match.group(2).strip()
            if contradiction and not contradiction.startswith("|"):
                report["contradictions"].append(contradiction)
    
    # 添加原始章节内容
    report["sections"] = sections
    
    return report
