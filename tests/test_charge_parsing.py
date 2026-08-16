"""罪名解析测试：从案件文件夹名推断罪名时剥离"案件"前缀与日期后缀。"""
from case_manager import _parse_charges_from_name


def test_案件前缀和日期后缀均被剥离():
    # 真实案例：冯叶飞案文件夹名
    assert _parse_charges_from_name(
        "案件_冯叶飞涉嫌非法经营罪诈骗罪_20260815", "冯叶飞"
    ) == ["非法经营罪", "诈骗罪"]


def test_案件前缀加单罪名加日期():
    assert _parse_charges_from_name(
        "案件_张三涉嫌诈骗罪_20260101", "张三"
    ) == ["诈骗罪"]


def test_无前缀无日期的多罪名保持原行为():
    assert _parse_charges_from_name(
        "王某某涉嫌职务侵占罪挪用资金罪", "王某某"
    ) == ["职务侵占罪", "挪用资金罪"]


def test_案件前缀加危险驾驶罪加日期():
    assert _parse_charges_from_name(
        "案件_李四涉嫌危险驾驶罪_20251231", "李四"
    ) == ["危险驾驶罪"]
