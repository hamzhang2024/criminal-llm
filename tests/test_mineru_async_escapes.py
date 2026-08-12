"""mineru_async 双重转义失效修复测试（签名保护/OCR纠错在异步路径从未生效）"""
import mineru_async
import pdf_to_md


def test_signature_protection_basic():
    """签名区替换为占位符（修复前：永不匹配）"""
    text = "讯问人：张三\n记录人：李四\n正文内容"
    result = mineru_async._protect_signatures_as_images(text)
    assert "[手写签名]" in result
    assert "张三" not in result and "李四" not in result
    assert "讯问人：" in result and "记录人：" in result
    assert "正文内容" in result


def test_signature_protection_all_roles():
    """15 种签名角色全覆盖（与 pdf_to_md 对齐）"""
    roles = ["询问人", "讯问人", "记录人", "被询问人", "被讯问人", "捺印人",
             "翻译人", "法定代理人", "办案单位", "办案人", "侦查人员",
             "见证人", "持有人", "交出人", "接收人"]
    for role in roles:
        text = f"{role}：某某签名\n下一行"
        result = mineru_async._protect_signatures_as_images(text)
        assert "[手写签名]" in result, f"{role} 未被保护"
        assert "某某签名" not in result


def test_kana_fix_no_literal_backslash():
    """の 修复：甲の乙 → 甲的乙（修复前：变成字面 \\1的\\2）"""
    result = mineru_async._fix_ocr_errors("甲の乙")
    assert result == "甲的乙"
    assert "\\1" not in result


def test_parity_with_pdf_to_md():
    """两模块的签名保护/OCR 纠错输出完全一致（防再次漂移）"""
    sample = "讯问人：王五\n被讯问人：赵六\n甲の乙\n正文"
    assert mineru_async._protect_signatures_as_images(sample) == pdf_to_md._protect_signatures_as_images(sample)
    assert mineru_async._fix_ocr_errors(sample) == pdf_to_md._fix_ocr_errors(sample)
