"""案例文本工具：中文 bigram 切分、文件名解析、MD 章节截取"""
import re

_TOKEN_RE = re.compile(r"[0-9A-Za-z]+|[一-鿿]+")
_FILENAME_RE = re.compile(r"^【(第\d+号)】(.+)\.md$")
_ISSUE_RE = re.compile(r"##\s*二、主要问题\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL)
_REASON_RE = re.compile(r"##\s*三、裁判理由\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL)

REASONING_EXCERPT_MAX = 500


def to_bigrams(text: str) -> str:
    """把文本切为 FTS 可索引的 token 串：中文 bigram，英文/数字整词（小写）"""
    out = []
    for tok in _TOKEN_RE.findall(text):
        if tok.isascii():
            out.append(tok.lower())
        elif len(tok) == 1:
            out.append(tok)
        else:
            out.extend(a + b for a, b in zip(tok, tok[1:]))
    return " ".join(out)


def parse_case_filename(filename: str) -> tuple[str, str] | None:
    """'【第1000号】李某甲等寻衅滋事案——….md' -> ('第1000号', '李某甲等寻衅滋事案——…')；不规范返回 None"""
    m = _FILENAME_RE.match(filename)
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def extract_sections(md_text: str) -> dict:
    """按 MD 标题结构截取「二、主要问题」与「三、裁判理由」（原文不改写）"""
    issue_m = _ISSUE_RE.search(md_text)
    reason_m = _REASON_RE.search(md_text)
    return {
        "issue": issue_m.group(1).strip() if issue_m else None,
        "reasoning_excerpt": reason_m.group(1).strip()[:REASONING_EXCERPT_MAX] if reason_m else "",
    }
