"""_OCR_FIXES 去重：mineru_async 共享 pdf_to_md 的完整纠错表"""
import mineru_async
import pdf_to_md


def test_shared_same_object():
    """两模块的 _OCR_FIXES 是同一对象（根除漂移）"""
    assert mineru_async._OCR_FIXES is pdf_to_md._OCR_FIXES


def test_full_list_coverage():
    """异步路径享有完整纠错表（修复前只有 18 条）"""
    assert len(mineru_async._OCR_FIXES) == len(pdf_to_md._OCR_FIXES)
    assert len(mineru_async._OCR_FIXES) > 50


def test_async_path_fixes_case_terms():
    """异步路径现在能纠正案卷高频错字（修复前不能）"""
    result = mineru_async._fix_ocr_errors("嫌疑人投案自手，归案后坦自")
    assert "投案自首" in result
    assert "坦白" in result


def test_parity_fix_ocr_errors():
    """两模块 _fix_ocr_errors 输出一致"""
    sample = "讯问人：某某\n投案自手\n监视居佗\n甲の乙"
    assert mineru_async._fix_ocr_errors(sample) == pdf_to_md._fix_ocr_errors(sample)
