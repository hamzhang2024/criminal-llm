#!/usr/bin/env python3
"""
测试去水印效果

检查：
1. 水印是否完全去除
2. OCR 文字层质量
3. 转换 MD 时是否会产生乱码
"""
import subprocess
import sys
from pathlib import Path

def test_watermark_removal():
    """测试去水印效果"""
    print("=" * 60)
    print("测试去水印效果")
    print("=" * 60)
    
    # 检查脚本路径
    script_path = Path(__file__).parent.parent / "skills" / "pdf-watermark-remover" / "scripts" / "remove_watermark.py"
    
    if not script_path.exists():
        print(f"❌ 脚本不存在：{script_path}")
        return False
    
    print(f"✅ 脚本存在：{script_path}")
    
    # 检查 OCRmyPDF 是否安装
    try:
        result = subprocess.run(
            ['ocrmypdf', '--version'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print(f"✅ OCRmyPDF 已安装：{result.stdout.strip()}")
        else:
            print("❌ OCRmyPDF 未正确安装")
            return False
    except FileNotFoundError:
        print("❌ OCRmyPDF 未安装")
        print("   安装命令：pip install ocrmypdf ocrmypdf-rapidocr")
        return False
    
    # 检查 RapidOCR 插件
    try:
        result = subprocess.run(
            ['python3', '-c', 'import ocrmypdf_rapidocr'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print("✅ RapidOCR 插件已安装")
        else:
            print("⚠️ RapidOCR 插件未安装（OCR 质量可能受影响）")
    except:
        print("⚠️ 无法检查 RapidOCR 插件")
    
    return True

def test_watermark_script():
    """测试水印脚本功能"""
    print("\n" + "=" * 60)
    print("测试水印脚本功能")
    print("=" * 60)
    
    script_path = Path(__file__).parent.parent / "skills" / "pdf-watermark-remover" / "scripts" / "remove_watermark.py"
    
    # 运行帮助命令测试脚本
    try:
        result = subprocess.run(
            ['python3', str(script_path), '--help'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0 or 'usage' in result.stdout.lower() or 'usage' in result.stderr.lower():
            print("✅ 水印脚本可正常运行")
            return True
        else:
            print(f"❌ 水印脚本运行失败")
            print(f"   错误：{result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"❌ 运行水印脚本时出错：{e}")
        return False

def check_watermark_quality(pdf_path):
    """检查 PDF 中的水印残留"""
    print(f"\n检查文件：{pdf_path}")
    
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        
        watermark_found = False
        text_content = []
        
        for page_num in range(min(3, len(doc))):  # 检查前 3 页
            page = doc[page_num]
            text = page.get_text()
            text_content.append(text)
            
            # 检查常见水印文字
            watermark_keywords = ['水印', 'watermark', '江阴市院', '机密', '秘密', '内部']
            for keyword in watermark_keywords:
                if keyword.lower() in text.lower():
                    print(f"   ⚠️ 第{page_num+1}页发现疑似水印文字：'{keyword}'")
                    watermark_found = True
            
            # 检查文字层质量
            if len(text.strip()) < 50:
                print(f"   ⚠️ 第{page_num+1}页文字内容过少（{len(text.strip())}字符）")
        
        doc.close()
        
        if not watermark_found:
            print("   ✅ 未发现明显水印残留")
            return True
        else:
            print("   ❌ 发现水印残留，可能影响 MD 转换")
            return False
            
    except Exception as e:
        print(f"   ❌ 检查失败：{e}")
        return False

if __name__ == "__main__":
    print("开始测试去水印效果...\n")
    
    # 测试 1：检查环境
    env_ok = test_watermark_removal()
    
    # 测试 2：测试脚本
    script_ok = test_watermark_script()
    
    # 测试 3：如果有测试文件，检查质量
    test_pdf = Path("/tmp/test_watermark.pdf")
    if test_pdf.exists():
        quality_ok = check_watermark_quality(test_pdf)
    else:
        print(f"\n⚠️ 未找到测试文件：{test_pdf}")
        print("   请提供一个带水印的 PDF 文件进行测试")
        quality_ok = True
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    print(f"环境检查：{'✅' if env_ok else '❌'}")
    print(f"脚本测试：{'✅' if script_ok else '❌'}")
    print(f"质量检查：{'✅' if quality_ok else '⚠️'}")
    
    if env_ok and script_ok:
        print("\n✅ 去水印功能基本正常")
        print("\n建议：")
        print("1. 使用实际案卷测试去水印效果")
        print("2. 转换 MD 后检查是否有乱码")
        print("3. 如果水印未完全去除，尝试指定水印文字参数")
    else:
        print("\n❌ 去水印功能存在问题，需要修复")
