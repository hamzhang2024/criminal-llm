"""5B 拆分：供述类证据筛选（口供对比专用输入）"""
from analysis_engine import _select_confession_texts


def _t(type_="", filename=""):
    return {"type": type_, "filename": filename, "text": "x"}


def test_selects_confession_by_type():
    texts = [_t("犯罪嫌疑人供述和辩解", "117_唐某第五次讯问笔录")]
    assert _select_confession_texts(texts) == texts


def test_selects_confession_by_filename():
    texts = [_t("其他证据", "张某第三次讯问笔录")]
    assert len(_select_confession_texts(texts)) == 1


def test_excludes_testimony_and_physical():
    texts = [
        _t("证人证言", "136_陈某询问笔录"),
        _t("书证（银行交易流水）", "247_张某邮储银行交易明细"),
        _t("被害人陈述", "沈某询问笔录"),
        _t("起诉书", "起诉书"),
    ]
    assert _select_confession_texts(texts) == []


def test_mixed_case():
    texts = [
        _t("犯罪嫌疑人供述和辩解", "117_唐某第五次讯问笔录"),
        _t("证人证言", "136_陈某询问笔录"),
        _t("书证", "028_放贷情况统计表"),
    ]
    selected = _select_confession_texts(texts)
    assert len(selected) == 1
    assert selected[0]["filename"] == "117_唐某第五次讯问笔录"
