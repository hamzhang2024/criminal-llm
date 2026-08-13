"""paddleocr_async 后处理统一复用 paddleocr_remote（本地副本双重转义失效修复）"""
import paddleocr_async
import paddleocr_remote


def test_async_uses_remote_postprocessing():
    """异步模块不再保留本地副本，直接共享 remote 的实现"""
    assert paddleocr_async._apply_postprocessing is paddleocr_remote._apply_postprocessing
    assert not hasattr(paddleocr_async, "_clean_latex_markup")
    assert not hasattr(paddleocr_async, "_fix_case_ocr_errors")


def test_latex_wrappers_removed():
    """LaTeX 包裹（$ \\underline{\\text{...}} $）被清除（修复前批量路径 MD 全是这种残留）"""
    sample = ' $ \\underline{\\text{万，月利息是1毛}} $ '
    result = paddleocr_async._apply_postprocessing(sample)
    assert '\\underline' not in result
    assert '月利息是1毛' in result


def test_case_fixes_full_list():
    """案卷纠错用全量表（remote 版，async 本地副本只有 21 条）"""
    result = paddleocr_async._apply_postprocessing("取保侯审决定书")
    assert "取保候审" in result
