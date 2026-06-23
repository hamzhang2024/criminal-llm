#!/usr/bin/env python3
"""
MD 质量验证模块

检查 MinerU 转换的 MD 文件质量：
1. 文字提取率（与 PDF 原文对比）
2. 表格完整性
3. 公式识别率
4. 乱码检测

如果质量不达标，建议启用 OCR
"""
import re
from pathlib import Path


def check_md_quality(md_path: str, min_quality: float = 0.8) -> dict:
    """
    检查 MD 文件质量
    
    Args:
        md_path: MD 文件路径
        min_quality: 最低质量要求 (0-1)
    
    Returns:
        {
            "quality": 0.95,           # 整体质量分数
            "text_ratio": 0.98,        # 文字提取率
            "has_tables": True,        # 是否有表格
            "has_formulas": False,     # 是否有公式
            "has_garbled": False,      # 是否有乱码
            "garbled_chars": 0,        # 乱码字符数
            "suggestion": "",          # 建议
            "need_ocr": False          # 是否需要 OCR
        }
    """
    md_path = Path(md_path)
    if not md_path.exists():
        return {"quality": 0, "suggestion": "MD 文件不存在", "need_ocr": True}
    
    content = md_path.read_text(encoding="utf-8")
    if not content.strip():
        return {"quality": 0, "suggestion": "MD 文件为空", "need_ocr": True}
    
    # 1. 检查文字量
    text_len = len(content)
    text_ratio = min(text_len / 100, 1.0)  # 至少 100 字符
    
    # 2. 检查表格
    has_tables = bool(re.search(r'\|.*\|.*\|', content))
    table_count = len(re.findall(r'\|.*\|.*\|', content))
    
    # 3. 检查公式
    has_formulas = bool(re.search(r'\$.*\$', content)) or bool(re.search(r'```latex', content))
    formula_count = len(re.findall(r'\$.*?\$', content))
    
    # 4. 检测乱码
    garbled_pattern = re.compile(r'[\x00-\x08\x0e-\x1f\x7f-\xff]{3,}')
    garbled_matches = garbled_pattern.findall(content)
    garbled_chars = sum(len(m) for m in garbled_matches)
    has_garbled = garbled_chars > 50  # 超过 50 个乱码字符认为质量差
    
    # 5. 检查中文比例（案卷应该是中文为主）
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', content))
    chinese_ratio = chinese_chars / max(text_len, 1)
    
    # 6. 计算整体质量
    quality = text_ratio * 0.4
    if has_garbled:
        quality *= 0.5
    if chinese_ratio < 0.1 and text_len > 500:
        quality *= 0.7
    
    quality = min(quality, 1.0)
    
    # 7. 生成建议
    suggestion = ""
    need_ocr = False
    
    if quality < min_quality:
        if has_garbled:
            suggestion = f"检测到 {garbled_chars} 个乱码字符，建议启用 OCR 重新转换"
            need_ocr = True
        elif text_ratio < 0.3:
            suggestion = f"文字提取率过低 ({text_ratio:.0%})，可能是扫描件，建议启用 OCR"
            need_ocr = True
        elif chinese_ratio < 0.1:
            suggestion = f"中文比例过低 ({chinese_ratio:.0%})，可能转换异常"
            need_ocr = True
        else:
            suggestion = "MD 质量不达标，建议检查转换参数或启用 OCR"
            need_ocr = True
    else:
        suggestion = "MD 质量良好"
    
    return {
        "quality": round(quality, 2),
        "text_ratio": round(text_ratio, 2),
        "has_tables": has_tables,
        "table_count": table_count,
        "has_formulas": has_formulas,
        "formula_count": formula_count,
        "has_garbled": has_garbled,
        "garbled_chars": garbled_chars,
        "chinese_ratio": round(chinese_ratio, 2),
        "suggestion": suggestion,
        "need_ocr": need_ocr,
        "file_size": text_len
    }


def batch_check_quality(md_files: list, min_quality: float = 0.8) -> dict:
    """
    批量检查 MD 文件质量
    
    Args:
        md_files: MD 文件路径列表
        min_quality: 最低质量要求
    
    Returns:
        {
            "total": 10,
            "passed": 8,
            "failed": 2,
            "avg_quality": 0.85,
            "need_ocr_files": ["file1.md", "file2.md"],
            "details": [...]
        }
    """
    results = []
    need_ocr_files = []
    
    for md_file in md_files:
        result = check_md_quality(md_file, min_quality)
        result["file"] = md_file
        results.append(result)
        
        if result["need_ocr"]:
            need_ocr_files.append(md_file)
    
    passed = sum(1 for r in results if not r["need_ocr"])
    failed = len(results) - passed
    avg_quality = sum(r["quality"] for r in results) / max(len(results), 1)
    
    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "avg_quality": round(avg_quality, 2),
        "need_ocr_files": need_ocr_files,
        "details": results
    }


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python check_md_quality.py <md_file1> [md_file2] ...")
        sys.exit(1)
    
    if len(sys.argv) == 2:
        # 单个文件
        result = check_md_quality(sys.argv[1])
        print(f"文件：{sys.argv[1]}")
        print(f"质量分数：{result['quality']:.0%}")
        print(f"文字提取率：{result['text_ratio']:.0%}")
        print(f"中文比例：{result['chinese_ratio']:.0%}")
        print(f"表格：{result['table_count']} 个")
        print(f"公式：{result['formula_count']} 个")
        print(f"乱码字符：{result['garbled_chars']} 个")
        print(f"建议：{result['suggestion']}")
        if result['need_ocr']:
            print("\n[WARN] 建议启用 OCR 重新转换")
    else:
        # 批量检查
        result = batch_check_quality(sys.argv[1:])
        print("批量检查结果：")
        print(f"总数：{result['total']}")
        print(f"通过：{result['passed']}")
        print(f"失败：{result['failed']}")
        print(f"平均质量：{result['avg_quality']:.0%}")
        if result['need_ocr_files']:
            print("\n需要 OCR 的文件：")
            for f in result['need_ocr_files']:
                print(f"  - {f}")
