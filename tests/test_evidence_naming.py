"""证据命名测试：pending_files 按卷号自然排序 + 通用笔录名从 persons 回填人名。"""
from case_manager import natural_sort_key, _enrich_transcript_name


class _FakeFile:
    """模拟 Path 的 name 属性，用于 natural_sort_key"""

    def __init__(self, name: str):
        self.name = name


def test_pending_files按卷号自然排序():
    # 乱序（模拟 glob 返回顺序）：第5→4→6→14→7→10→2→1→3→9→11
    names = [
        "第5卷_证据.md", "第4卷_证据.md", "第6卷_证据.md", "第14卷_证据.md",
        "第7卷_证据.md", "第10卷_证据.md", "第2卷_证据.md", "第1卷_证据.md",
        "第3卷_证据.md", "第9卷_证据.md", "第11卷_证据.md",
    ]
    sorted_names = [f.name for f in sorted((_FakeFile(n) for n in names), key=natural_sort_key)]
    expected = [f"第{i}卷_证据.md" for i in [1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 14]]
    assert sorted_names == expected


def test_通用讯问笔录回填被讯问人():
    persons = "讯问人：夏海峰；记录人：张福如；被讯问人：冯叶飞"
    assert _enrich_transcript_name("讯问笔录", persons, set()) == "讯问笔录（冯叶飞）"


def test_通用询问笔录回填被询问人():
    assert _enrich_transcript_name("询问笔录", "被询问人：唐雨", set()) == "询问笔录（唐雨）"


def test_被讯问人优先于被询问人():
    persons = "被询问人：李四；被讯问人：张三"
    assert _enrich_transcript_name("讯问笔录", persons, set()) == "讯问笔录（张三）"


def test_人名截止于分隔符():
    # 人名后跟随其他信息（逗号/句号/括号/分号）时只取人名
    assert _enrich_transcript_name("询问笔录", "被询问人：李四，男，35岁", set()) == "询问笔录（李四）"
    assert _enrich_transcript_name("讯问笔录", "被讯问人：王五。其他", set()) == "讯问笔录（王五）"
    assert _enrich_transcript_name("讯问笔录", "被讯问人：赵六（化名）", set()) == "讯问笔录（赵六）"


def test_重名时加序号():
    existing = {"讯问笔录（冯叶飞）"}
    assert _enrich_transcript_name("讯问笔录", "被讯问人：冯叶飞", existing) == "讯问笔录（冯叶飞，第2份）"


def test_重名序号递增():
    existing = {"讯问笔录（冯叶飞）", "讯问笔录（冯叶飞，第2份）"}
    assert _enrich_transcript_name("讯问笔录", "被讯问人：冯叶飞", existing) == "讯问笔录（冯叶飞，第3份）"


def test_persons为空保持原名():
    assert _enrich_transcript_name("讯问笔录", "", set()) == "讯问笔录"


def test_persons无人名保持原名():
    persons = "讯问人：夏海峰；记录人：张福如"
    assert _enrich_transcript_name("讯问笔录", persons, set()) == "讯问笔录"


def test_非通用名不动():
    persons = "被讯问人：冯叶飞"
    assert _enrich_transcript_name("冯叶飞第二次讯问笔录", persons, set()) == "冯叶飞第二次讯问笔录"
