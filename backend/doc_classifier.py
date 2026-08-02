"""文书分类：证据 / 非证据（封面/目录/封底/备考表）

原则：
- 非证据文件**保留在原目录与文件列表**，不入提取流程（案卷完整性，永不删除）
- 宁可误提取，不误标非证据：规则需高置信，拿不准归证据
- 文件名规则优先，LLM 兜底（只看开头 500 字，一次调用）
"""
import re

NON_EVIDENCE_TYPES = ("封面", "目录", "封底", "备考表")

_FILENAME_RULES = [
    (re.compile(r"封面"), "封面"),
    (re.compile(r"卷内.*目录|文书目录|(?<!卷内)目录"), "目录"),
    (re.compile(r"封底"), "封底"),
    (re.compile(r"备考表"), "备考表"),
]

_CONTENT_HINT = re.compile(r"刑事侦查卷宗|卷内文书目录|备\s*考\s*表|封\s*底")


async def classify_document(filename: str, first_500_chars: str) -> str:
    """返回 "evidence" 或 "non_evidence:<subtype>"

    文件名规则优先；内容高度吻合封面特征才走 LLM 兜底，否则直接归证据。
    """
    for pattern, subtype in _FILENAME_RULES:
        if pattern.search(filename):
            return f"non_evidence:{subtype}"
    if not _CONTENT_HINT.search(first_500_chars or ""):
        return "evidence"
    return await _llm_classify(filename, first_500_chars)


async def _llm_classify(filename: str, first_500_chars: str) -> str:
    """LLM 兜底判定（只认高置信非证据，其余归证据）"""
    from llm_client import get_llm_client
    client = get_llm_client()
    try:
        result = await client.chat([
            {"role": "system", "content": "你是案卷整理员。判断文书类型，只回答一个词：封面 / 目录 / 封底 / 备考表 / 证据。只有非常确定是程序性封面、卷内目录、封底或备考表时才回答前四项，否则一律回答“证据”。"},
            {"role": "user", "content": f"文件名：{filename}\n\n开头内容：\n{first_500_chars[:500]}"},
        ])
        result = result.strip().strip("。.")
        if result in NON_EVIDENCE_TYPES:
            return f"non_evidence:{result}"
    except Exception:
        pass
    return "evidence"
