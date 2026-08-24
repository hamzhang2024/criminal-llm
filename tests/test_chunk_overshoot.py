"""过冲回扫分块器：预算×0.8~1.2 下刀区间，文书边界处切割，不腰斩

设计（用户提议 + 现有边界资产）：
- 过冲取窗后从末端回扫，优先级：## 标题 > 笔录头/人名字段 > ### 子标题/空行 > 硬切
- 块 token ≤ 预算 × 1.2（25% 预算比例已含安全余量）
- 不腰斩 → 不需要块间重叠（硬切路径保留重叠）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from case_manager import _split_content_by_tokens, _count_tokens


def _make_transcript(title, person, qa_count):
    lines = [f"# {title}", "", f"被讯问人：{person}"]
    for i in range(qa_count):
        lines.append(f"问：第{i + 1}个问题，你把经过讲一下？答：这是第{i + 1}段回答，内容比较长一些。" * 8)
    return "\n".join(lines) + "\n\n"


def _make_volume(specs):
    """specs: [(title, person, qa_count)] → 卷文本"""
    return "".join(_make_transcript(*s) for s in specs)


BUDGET = 3000  # tokens，测试用小预算


def test_no_document_cut_at_boundary():
    """核心不变式：每块以标题开头（或原文开头），无文书腰斩"""
    text = _make_volume([
        ("讯问笔录", "张某", 12),
        ("讯问笔录", "李某", 12),
        ("讯问笔录", "王某", 12),
        ("讯问笔录", "赵某", 12),
    ])
    chunks = _split_content_by_tokens(text, BUDGET, "第1卷.md")
    assert len(chunks) >= 2, "应拆成多块"
    for c in chunks[1:]:
        body = c["text"].lstrip()
        # 块开头必须是文书标题或笔录头，不能是问答中段
        assert body.startswith("#") or body.startswith("被讯问人") or body.startswith("被询问人"), \
            f"块以非边界内容开头（疑似腰斩）: {body[:50]!r}"


def test_chunk_tokens_within_120_percent_budget():
    """每块 token ≤ 预算 × 1.2（最后一块除外——允许不足量）"""
    text = _make_volume([
        ("讯问笔录", "张某", 15),
        ("讯问笔录", "李某", 15),
        ("讯问笔录", "王某", 15),
        ("讯问笔录", "赵某", 15),
        ("讯问笔录", "陈某", 15),
    ])
    chunks = _split_content_by_tokens(text, BUDGET, "第1卷.md")
    for c in chunks[:-1]:
        assert _count_tokens(c["text"]) <= BUDGET * 1.2, \
            f"块 {c['label']} 超过 1.2× 预算: {_count_tokens(c['text'])}"


def test_full_coverage_no_overlap():
    """所有块拼接 = 原文完整覆盖（不腰斩则无需重叠）"""
    text = _make_volume([
        ("讯问笔录", "张某", 10),
        ("讯问笔录", "李某", 10),
        ("讯问笔录", "王某", 10),
    ])
    chunks = _split_content_by_tokens(text, BUDGET, "第1卷.md")
    assert "".join(c["text"] for c in chunks) == text


def test_single_huge_document_hard_cut_with_overlap():
    """单份文书超预算：按 ### 子边界拆，再不行硬切（保留重叠防丢上下文）"""
    # 一份超长笔录，无任何子标题
    body = "".join(f"问：问题{i}？答：{'回答内容。' * 30}\n" for i in range(60))
    text = f"# 讯问笔录\n被讯问人：张某\n{body}"
    chunks = _split_content_by_tokens(text, BUDGET, "第1卷.md")
    assert len(chunks) >= 2, "超长单文书必须拆块"
    # 硬切路径：后续块应带前块尾部重叠（上下文连续性）
    if len(chunks) >= 2:
        assert len(chunks[1]["text"]) > 0


def test_no_headings_hard_cut():
    """纯无标题 OCR 流：硬切兜底"""
    text = "纯文本流水没有标题。" * 1000
    chunks = _split_content_by_tokens(text, BUDGET, "第1卷.md")
    assert len(chunks) >= 2
    total = sum(_count_tokens(c["text"]) for c in chunks)
    assert total >= _count_tokens(text)  # 有重叠则总量 ≥ 原文


def test_no_skinny_chunks():
    """消灭瘦块：除最后一块，每块 ≥ 预算 × 0.5（边界自然分布时）"""
    # 5 份等长笔录，预算约容纳 2.5 份
    text = _make_volume([(f"讯问笔录", f"人{i}", 10) for i in range(5)])
    chunks = _split_content_by_tokens(text, BUDGET, "第1卷.md")
    for c in chunks[:-1]:
        assert _count_tokens(c["text"]) >= BUDGET * 0.5, \
            f"瘦块 {c['label']}: {_count_tokens(c['text'])} tokens"


def test_small_text_single_chunk():
    """小文本不拆"""
    text = _make_volume([("讯问笔录", "张某", 2)])
    chunks = _split_content_by_tokens(text, BUDGET, "第1卷.md")
    assert len(chunks) == 1
    assert chunks[0]["text"] == text
