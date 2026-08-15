"""MinerU 真批量进度折算测试"""
from mineru_async import _estimate_completed


def test_estimate_completed_partial():
    """17 spec 中 8 个 done，14 PDF → 折算 int(8*14/17)=6"""
    assert _estimate_completed(done_count=8, total_count=17, total_pdf=14) == 6


def test_estimate_completed_full():
    """全部 done → 折算为 total_pdf"""
    assert _estimate_completed(done_count=17, total_count=17, total_pdf=14) == 14


def test_estimate_completed_zero_guards():
    """total_count 为 0 或空 → 0"""
    assert _estimate_completed(done_count=5, total_count=0, total_pdf=14) == 0
    assert _estimate_completed(done_count=0, total_count=0, total_pdf=0) == 0


def test_estimate_completed_caps_at_total():
    """折算结果不超 total_pdf"""
    assert _estimate_completed(done_count=20, total_count=17, total_pdf=14) == 14
