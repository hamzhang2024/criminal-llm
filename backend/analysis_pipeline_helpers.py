"""
Analysis Pipeline 辅助函数模块

包含：
- _extract_name_from_content：从文本提取人名
- _extract_person_from_filename：从文件名提取人名
- infer_evidence_type：推断证据类型
- _contains_indictment_title：判断是否含起诉书标题
- _split_sessions：拆分讯问/询问笔录会话
"""
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# 笔录时间分隔正则（支持两种格式：至15时32分 和 至2025年11月25日15时32分）
SESSION_TIME_PATTERN = re.compile(
    r'时间(\d{4}年\d{2}月\d{2}日\d{2}时\d{2}分.*?至(?:\d{4}年\d{2}月\d{2}日)?\d{2}时\d{2}分)'
)

# 笔录正文中提取人名的正则（被讯问人/被询问人后的姓名）
CONTENT_NAME_PATTERNS = [
    # 无冒号紧凑格式：被询问人项少甫性别 / 被讯问人张三年龄 / 被询问/讯问人李四出生日期
    re.compile(r'(?:被讯问|被询问)[/／]?(?:讯问|询问)?人\s*([一-鿿]{2,4})(?=性别|年龄|出生日期|出生[年月])'),
    # 冒号格式：被讯问人：XXX / 被询问人：XXX
    re.compile(r'(?:被讯问|被询问)[/／]?(?:讯问|询问)?人\s*[：:]\s*([一-鿿]{2,4})'),
    # 犯罪嫌疑人/被告人
    re.compile(r'(?:犯罪嫌疑人|被告人)\s*[：:]\s*([一-鿿]{2,4})'),
    re.compile(r'(?:犯罪嫌疑人|被告人)\s*([一-鿿]{2,4})(?=性别|年龄|出生日期|出生[年月])'),
    # 姓名标签
    re.compile(r'姓\s*名\s*[：:]\s*([一-鿿]{2,4})'),
    # 我叫 XXX（对话中提到）
    re.compile(r'(?:我叫|本人叫|名字是)\s*([一-鿿]{2,4})'),
]


def _extract_name_from_content(text: str, max_len: int = 5000) -> Optional[str]:
    """从笔录正文提取人名（匹配被讯问人/被询问人后的姓名）"""
    preview = text[:max_len]
    for pat in CONTENT_NAME_PATTERNS:
        m = pat.search(preview)
        if m:
            name = m.group(1).strip()
            # 过滤明显不是人名的词
            if name not in ('不知道', '不清楚', '没有', '以上', '以下', '是什么'):
                return name
    return None


def _extract_person_from_filename(filename: str) -> Optional[str]:
    """从文件名提取人名
    文件名格式示例：
    - 第2卷_处理_01_江涛讯问笔录.md  （_序号_人名+类型）
    - 张萍询问笔录.md                （人名直接+类型）
    - 王烁宇_询问笔录.md             （人名_类型）
    策略：找到"询问/讯问/辨认"后，取紧邻的 2-4 字中文作为人名
    """
    stem = Path(filename).stem if '.' in filename else filename
    for kw in ['讯问', '询问', '辨认']:
        idx = stem.find(kw)
        if idx == -1:
            continue
        prefix = stem[:idx]

        # 格式 1：_序号_人名（如 _01_江涛）
        regex_match = re.search(r'_(\d+)_([\u4e00-\u9fff]{2,4})$', prefix)
        if regex_match:
            return regex_match.group(2)

        # 格式 2：人名直接紧跟关键词（如 张萍询问 → 张萍）
        name_match = re.search(r'([\u4e00-\u9fff]{2,4})$', prefix)
        if name_match:
            return name_match.group(1)

        # 格式 3：人名_（如 顾君燕_讯问笔录 → 顾君燕）
        name_match2 = re.search(r'([\u4e00-\u9fff]{2,4})_+$', prefix)
        if name_match2:
            return name_match2.group(1)
    return None


def infer_evidence_type(filename: str) -> str:
    """从文件名推断证据类型"""
    if "起诉书" in filename and "意见" not in filename:
        return "起诉书"
    elif "起诉意见书" in filename or "指控" in filename:
        return "起诉意见书"
    elif "讯问" in filename or "供述" in filename:
        return "讯问笔录"
    elif "询问" in filename:
        return "询问笔录"
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


async def _classify_document_type(llm, text: str) -> str:
    """用 LLM 判断一段文书文本是「起诉书」还是「起诉意见书」还是「其他」"""
    try:
        result = await llm.chat([
            {"role": "system", "content": "你是刑事律师助手。请判断以下文书的类型。只回答：起诉书 / 起诉意见书 / 其他。不要解释。"},
            {"role": "user", "content": f"请判断以下文书的类型（只回答：起诉书 / 起诉意见书 / 其他）：\n\n{text[:3000]}"},
        ])
        result = result.strip()
        if "起诉意见书" in result:
            return "起诉意见书"
        if "起诉书" in result:
            return "起诉书"
    except Exception:
        pass
    return "其他"


def _contains_indictment_title(text: str) -> bool:
    """判断文本是否包含起诉书标题，排除「起诉意见书」的干扰。

    "起诉意见书"中包含"起诉书"三个字，直接用 `in` 匹配会误判。
    本函数使用负向前后查找，确保匹配的是独立的"起诉书"而非"起诉意见书"的一部分。
    """
    # 匹配独立的"起诉书"（前面不是"意见"，后面不是"意见"）
    pattern = r"(?<!意见)起诉书(?!意见)"
    if re.search(pattern, text):
        return True
    if "公诉书" in text:
        return True
    return False


def _split_sessions(text: str) -> list[dict]:
    """
    按时间分隔出单次笔录
    Returns: list of {time_range: str, content: str}
    """
    matches = list(SESSION_TIME_PATTERN.finditer(text))
    if not matches:
        return [{"time_range": "未知", "content": text}]

    sessions = []
    for i, m in enumerate(matches):
        time_range = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        sessions.append({
            "time_range": time_range,
            "content": content,
        })
    return sessions

