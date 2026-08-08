#!/usr/bin/env python3
"""PaddleOCR 图片识别参数实测脚本（一次性验证工具，不进产品代码）

用法：
    python3 scripts/test_paddle_image_ocr.py <案卷PDF路径> [--baseline]

    默认：开启 useOcrForImageBlock + useSealRecognition 转换并分析产物
    --baseline：用原参数转换，用于耗时对比

验收对照（设计文档 6.1）：
1. MD 中图片位置出现识别文字
2. 抽 10 张资金类凭证人工比对，金额/账号/日期准确率 ≥ 90%
3. <img> 标签是否保留（决定折叠正则修复是否保留）
4. 与 --baseline 耗时对比，增幅 ≤ 约 50%
"""
import re
import sys
import time
import tempfile
from pathlib import Path

# 让脚本可 import backend 模块
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import paddleocr_remote


def analyze_md(md_path: Path) -> None:
    """分析产物 MD：统计 <img> 标签数量，并打印每个标签后的识别文字片段"""
    text = md_path.read_text(encoding="utf-8")
    img_tags = re.findall(r"<img\s[^>]*>", text)
    print(f"\n===== 产物分析 =====")
    print(f"MD 文件: {md_path}")
    print(f"总字符数: {len(text)}")
    print(f"<img> 标签数: {len(img_tags)}（>0 说明图片引用保留，折叠修复有意义）")

    # 打印每个 <img> 标签后 300 字符，供人工比对识别质量
    for i, m in enumerate(re.finditer(r"<img\s[^>]*>", text)):
        after = text[m.end():m.end() + 300].strip()
        print(f"\n--- 图片 {i + 1} 后续内容（前 300 字符）---")
        print(after if after else "（无识别文字）")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    baseline = "--baseline" in sys.argv
    if not pdf_path.exists():
        print(f"文件不存在: {pdf_path}")
        sys.exit(1)

    if not baseline:
        # 在原 payload 基础上追加两个新参数（不改产品代码）
        paddleocr_remote.PADDLEOCR_OPTIONAL_PAYLOAD = {
            **paddleocr_remote.PADDLEOCR_OPTIONAL_PAYLOAD,
            "useOcrForImageBlock": True,
            "useSealRecognition": True,
        }
        print("[实测] 已开启 useOcrForImageBlock + useSealRecognition")
    else:
        print("[实测] baseline 模式（原参数）")

    out_dir = Path(tempfile.mkdtemp(prefix="paddle_image_ocr_"))
    start = time.time()
    result = paddleocr_remote.paddleocr_convert(pdf_path, out_dir)
    elapsed = time.time() - start

    if not result or not result[0]:
        print("[实测] 转换失败")
        sys.exit(1)

    print(f"\n[实测] 转换耗时: {elapsed:.0f} 秒")
    analyze_md(out_dir / f"{pdf_path.stem}.md")
    print(f"\n产物目录: {out_dir}（图片在 {{stem}}_images/，可对照原图人工核验）")


if __name__ == "__main__":
    main()
