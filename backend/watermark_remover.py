"""
PDF 水印移除工具 - 集成到后端，不再依赖外部脚本

支持三种水印类型：
1. 全局 Form XObject 水印
2. 每页旋转文本水印（45°/30°）
3. 直接文本水印（嵌入页面内容流）
"""

import fitz
import re
import os
import shutil
import subprocess
import tempfile
from typing import Optional, Dict, Any


def _try_fix_with_qpdf(input_path: str) -> Optional[str]:
    """尝试使用 qpdf 修复损坏的 PDF"""
    try:
        # 检查 qpdf 是否可用
        result = subprocess.run(["which", "qpdf"], capture_output=True)
        if result.returncode != 0:
            print("[水印移除] qpdf 未安装，无法修复")
            return None

        # 创建临时输出文件
        fd, output_path = tempfile.mkstemp(suffix='.pdf')
        os.close(fd)

        # 使用 qpdf 修复
        cmd = [
            "qpdf",
            "--ignore-xref-streams",  # 忽略损坏的交叉引用流
            "--qdf",                   # 重新生成 PDF
            input_path,
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)

        if result.returncode == 0 and os.path.exists(output_path):
            return output_path
        else:
            print(f"[水印移除] qpdf 修复失败: {result.stderr.decode()}")
            if os.path.exists(output_path):
                os.remove(output_path)
            return None
    except Exception as e:
        print(f"[水印移除] 修复异常: {e}")
        return None


def _safe_xref_stream(doc, xref):
    """安全读取 xref 流，跳过损坏的压缩对象"""
    try:
        return doc.xref_stream(xref)
    except Exception as e:
        # 记录损坏的流，但不中断处理
        print(f"[水印移除] 跳过损坏的流对象 xref={xref}: {e}")
        return None


def find_watermark_xobj(doc):
    """查找全局水印 XObject"""
    watermark_patterns = ['watermark', 'wm', 'KSPE', 'KSPX', 'BG', 'BACKGROUND']

    for i in range(1, doc.xref_length()):
        try:
            obj_type = doc.xref_get_key(i, "Type")
            if "XObject" not in str(obj_type):
                continue
            subtype = doc.xref_get_key(i, "Subtype")
            if "Form" not in str(subtype):
                continue

            try:
                obj_def = doc.xref_object(i, compressed=False)
            except Exception:
                continue
            obj_def_upper = obj_def.upper()

            is_watermark = False
            method = 'bbox'

            if "WATERMARK" in obj_def_upper and "PRIVATE" in obj_def_upper:
                is_watermark = True
                method = 'empty'
            else:
                for pattern in watermark_patterns:
                    if pattern.upper() in obj_def_upper:
                        bbox_match = re.search(r'/BBox\s+\[([^\]]+)\]', obj_def)
                        if bbox_match:
                            parts = bbox_match.group(1).split()
                            if len(parts) >= 4:
                                try:
                                    width = float(parts[2]) - float(parts[0])
                                    height = float(parts[3]) - float(parts[1])
                                    if width > 400 and height > 400:
                                        is_watermark = True
                                        break
                                except (ValueError, IndexError):
                                    continue

            if is_watermark:
                return i, method
        except Exception as e:
            # 跳过任何错误，继续检查下一个对象
            print(f"[水印检测] 跳过对象 {i}: {e}")
            continue

    return None, None


def detect_rotation_watermark(doc):
    """检测旋转水印（45°或30°）"""
    sample_pages = [0, 1, 10, 100]

    for p in sample_pages:
        if p < len(doc):
            try:
                page = doc[p]
                contents = page.get_contents()
                for cxref in contents:
                    try:
                        stream = _safe_xref_stream(doc, cxref)
                        if stream is None:
                            continue
                        content = stream.decode('latin-1', errors='ignore')

                        has_rotation = "0.70711" in content or "0.86603" in content
                        has_hex = bool(re.search(r'<[0-9a-fA-F]{40,}>', content))
                        has_tj = "Tj" in content and len(content) > 200

                        if has_rotation and (has_hex or has_tj):
                            return True
                    except Exception as e:
                        print(f"[旋转水印检测] 跳过内容流 {cxref}: {e}")
                        continue
            except Exception as e:
                print(f"[旋转水印检测] 跳过页面 {p}: {e}")
                continue

    return False


def check_fm1_is_image(doc, page_xref):
    """检查 Fm1 是否为主要内容图片"""
    xobj = doc.xref_get_key(page_xref, "Resources/XObject")
    if not xobj:
        return False, None, "No XObject"

    fm1_match = re.search(r'/Fm1 (\d+) 0 R', str(xobj))
    if not fm1_match:
        return False, None, "No Fm1"

    fm1_xref = int(fm1_match.group(1))

    try:
        fm1_stream = _safe_xref_stream(doc, fm1_xref)
        if fm1_stream is None:
            return False, fm1_xref, "Corrupted stream"
        fm1_content = fm1_stream.decode('latin-1', errors='ignore')

        if '/Im' in fm1_content and 'Do' in fm1_content:
            return True, fm1_xref, "Fm1 contains image"

        bbox = doc.xref_get_key(fm1_xref, "BBox")
        if bbox and 'array' in str(bbox):
            parts = re.findall(r'-?\d+\.?\d*', str(bbox))
            if len(parts) >= 4:
                width = float(parts[2]) - float(parts[0])
                height = float(parts[3]) - float(parts[1])
                if width > 1000 and height > 1400:
                    return True, fm1_xref, f"Fm1 is full-page ({width}x{height})"

        return False, fm1_xref, "Fm1 appears to be watermark"
    except Exception as e:
        return False, fm1_xref, f"Error: {e}"


def remove_global_watermark(doc, xref, method='bbox'):
    """移除全局水印 XObject"""
    if method == 'empty':
        doc.xref_set_key(xref, "Type", "/XObject")
        doc.xref_set_key(xref, "Subtype", "/Form")
        doc.xref_set_key(xref, "BBox", "[ 0 0 1 1 ]")
        doc.xref_set_key(xref, "Resources", "<<>>")
        doc.xref_set_key(xref, "Length", "0")
        try:
            doc.xref_set_key(xref, "stream", "")
        except Exception:
            pass
    elif method == 'bbox':
        doc.xref_set_key(xref, "BBox", "[ 0 0 1 1 ]")


def filter_rotation_watermark_blocks(content):
    """过滤旋转水印块"""
    lines = content.split('\n')
    filtered_lines = []
    skip_block = False
    block_q_depth = 0
    removed_count = 0

    for line in lines:
        stripped = line.strip()

        if ('0.70711' in line or '0.86603' in line) and ('cm' in line or 'Tm' in line) and not line.strip().startswith('1 0 0 1'):
            skip_block = True
            block_q_depth = 1
            removed_count += 1
            continue

        if skip_block:
            if stripped == 'q':
                block_q_depth += 1
            elif stripped == 'Q':
                block_q_depth -= 1
                if block_q_depth <= 0:
                    skip_block = False
                    block_q_depth = 0
            continue

        filtered_lines.append(line)

    return '\n'.join(filtered_lines), len(content) - len('\n'.join(filtered_lines)), removed_count


def remove_rotation_watermark(doc):
    """移除每页旋转水印"""
    total_pages = len(doc)
    removed_count = 0
    filtered_streams = 0

    for i in range(total_pages):
        page = doc[i]
        page_xref = page.xref
        contents = page.get_contents()

        if i == 0:
            is_image, fm1_xref, reason = check_fm1_is_image(doc, page_xref)
            if is_image:
                pass  # Fm1 是主内容，保留

        good_refs = []

        for cxref in contents:
            try:
                stream = _safe_xref_stream(doc, cxref)
                if stream is None:
                    good_refs.append(cxref)
                    continue
                content = stream.decode('latin-1', errors='ignore')

                has_rotation = "0.70711" in content or "0.86603" in content
                has_hex = bool(re.search(r'<[0-9a-fA-F]{40,}>', content))
                has_fm1_call = "/Fm1 Do" in content
                has_tj = "Tj" in content

                if has_rotation and (has_hex or has_tj) and not has_fm1_call:
                    filtered, removed_bytes, blocks = filter_rotation_watermark_blocks(content)
                    if removed_bytes > 0:
                        try:
                            doc.update_stream(cxref, filtered.encode('latin-1'))
                            filtered_streams += 1
                        except Exception:
                            pass

                good_refs.append(cxref)
            except Exception:
                good_refs.append(cxref)

        if len(good_refs) != len(contents):
            if len(good_refs) == 0:
                doc.xref_set_key(page_xref, "Contents", "[]")
            elif len(good_refs) == 1:
                doc.xref_set_key(page_xref, "Contents", f"{good_refs[0]} 0 R")
            else:
                refs_str = "[" + " ".join(f"{x} 0 R" for x in good_refs) + "]"
                doc.xref_set_key(page_xref, "Contents", refs_str)
            removed_count += len(contents) - len(good_refs)

    return removed_count, filtered_streams


def save_with_qpdf(doc, output_path):
    """使用 PyMuPDF 直接保存（跳过 qpdf 线性化）"""
    try:
        # garbage=4 等价于 garbage collection + 对象去重 + 压缩
        doc.save(output_path, garbage=4, deflate=True)
        return True
    except Exception as e:
        # 如果保存失败，尝试更宽松的选项
        print(f"[水印移除] 标准保存失败，尝试宽松模式: {e}")
        try:
            doc.save(output_path, garbage=1, deflate=False)
            return True
        except Exception as e2:
            print(f"[水印移除] 宽松模式也失败: {e2}")
            # 最后尝试：不压缩，不垃圾回收
            try:
                doc.save(output_path)
                return True
            except Exception as e3:
                print(f"[水印移除] 所有保存方式都失败: {e3}")
                raise


def auto_detect_repeating_text(doc, sample_count=5):
    """自动检测每页重复出现的文本（可能是水印）"""
    from collections import Counter
    line_counts = Counter()

    for i in range(min(sample_count, len(doc))):
        try:
            page = doc[i]
            text = page.get_text()
            # 降低长度阈值到 2，避免漏掉短水印（如"江阴市院"只有4字）
            lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) >= 2]
            for line in lines:
                line_counts[line] += 1
        except Exception as e:
            print(f"[文本检测] 跳过页面 {i}: {e}")
            continue

    total_pages = min(sample_count, len(doc))
    for line, count in line_counts.most_common(10):
        # 必须在几乎所有采样页都出现，且长度适中（2-50字符）
        if count >= total_pages - 1 and 2 <= len(line) <= 50:
            return line
    return None


def filter_text_watermark_from_stream(content, watermark_text):
    """过滤包含水印文本的 BT...ET 块"""
    removed_blocks = 0

    # Pattern 1: 字面文本
    watermark_escaped = re.escape(watermark_text)
    pattern1 = r'BT\s+(?:(?!BT|ET).)*?\(' + watermark_escaped + r'\)\s*Tj(?:(?!BT|ET).)*?ET'
    matches1 = re.findall(pattern1, content, re.DOTALL)
    removed_blocks += len(matches1)
    content = re.sub(pattern1, '', content, flags=re.DOTALL)

    # Pattern 2: hex 编码文本（重复出现视为水印）
    hex_pattern = r'BT\s+(?:(?!BT|ET).)*?<[0-9a-fA-F]{8,}>\s*Tj(?:(?!BT|ET).)*?ET'
    hex_blocks = re.findall(hex_pattern, content, re.DOTALL)

    hex_values = {}
    for block in hex_blocks:
        hex_match = re.search(r'<([0-9a-fA-F]+)>\s*Tj', block)
        if hex_match:
            hex_val = hex_match.group(1)
            hex_values[hex_val] = hex_values.get(hex_val, 0) + 1

    for hex_val, count in hex_values.items():
        if count >= 5:
            specific_pattern = r'BT\s+(?:(?!BT|ET).)*?<' + hex_val + r'>\s*Tj(?:(?!BT|ET).)*?ET'
            matches = re.findall(specific_pattern, content, re.DOTALL)
            removed_blocks += len(matches)
            content = re.sub(specific_pattern, '', content, flags=re.DOTALL)

    content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
    return content, removed_blocks


def remove_direct_text_watermark(doc, watermark_text):
    """移除直接文本水印"""
    total_pages = len(doc)
    total_removed = 0
    modified_streams = 0

    for i in range(total_pages):
        page = doc[i]
        contents = page.get_contents()
        page_modified = False

        for cxref in contents:
            try:
                stream = _safe_xref_stream(doc, cxref)
                if stream is None:
                    continue
                content = stream.decode('latin-1', errors='ignore')

                has_watermark = False
                hex_blocks = re.findall(r'BT\s+(?:(?!BT|ET).)*?<[0-9a-fA-F]{8,}>\s*Tj(?:(?!BT|ET).)*?ET', content, re.DOTALL)
                if hex_blocks:
                    hex_values = {}
                    for block in hex_blocks:
                        hex_match = re.search(r'<([0-9a-fA-F]+)>\s*Tj', block)
                        if hex_match:
                            hex_val = hex_match.group(1)
                            hex_values[hex_val] = hex_values.get(hex_val, 0) + 1
                    for hex_val, count in hex_values.items():
                        if count >= 5:
                            has_watermark = True
                            break

                if not has_watermark and watermark_text in content:
                    has_watermark = True

                if has_watermark:
                    filtered, removed = filter_text_watermark_from_stream(content, watermark_text)
                    if removed > 0:
                        doc.update_stream(cxref, filtered.encode('latin-1'))
                        page_modified = True
                        total_removed += removed
            except Exception:
                continue

        if page_modified:
            modified_streams += 1

    return total_removed, modified_streams


def remove_watermark(
    input_path: str,
    output_path: str,
    watermark_text: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    移除 PDF 水印

    Args:
        input_path: 输入 PDF 路径
        output_path: 输出 PDF 路径
        watermark_text: 水印文字（可选）
        password: PDF 密码（可选）

    Returns:
        {"success": bool, "output": str, "watermark_type": str, "error": str}
    """
    if not os.path.exists(input_path):
        return {"success": False, "error": f"文件不存在: {input_path}"}

    doc = None
    try:
        # 打开文档
        try:
            doc = fitz.open(input_path)
        except Exception as e:
            error_msg = str(e)
            # 检测是否是解压错误
            if "decompress" in error_msg.lower() or "header check" in error_msg.lower():
                # 尝试使用 qpdf 修复后重新打开
                print(f"[水印移除] PDF 流损坏，尝试修复: {os.path.basename(input_path)}")
                fixed_path = _try_fix_with_qpdf(input_path)
                if fixed_path and os.path.exists(fixed_path):
                    try:
                        doc = fitz.open(fixed_path)
                        print(f"[水印移除] 修复成功")
                    except Exception as e2:
                        return {"success": False, "error": f"PDF 文件损坏，无法修复: {error_msg}"}
                else:
                    return {"success": False, "error": f"PDF 文件损坏（压缩流错误），请联系开发者: {error_msg}"}
            else:
                return {"success": False, "error": f"无法打开 PDF: {error_msg}"}

        if doc.needs_pass:
            if password and password.strip():
                if not doc.authenticate(password.strip()):
                    return {"success": False, "error": "密码错误，无法解密 PDF"}
            else:
                return {"success": False, "error": f"PDF 已加密，需要先输入密码（{os.path.basename(input_path)}）"}

        total_pages = len(doc)
        watermark_type = None

        # 检测水印类型
        watermark_xref, method = find_watermark_xobj(doc)

        if watermark_xref:
            watermark_type = 'global_xobj'
        elif detect_rotation_watermark(doc):
            watermark_type = 'rotation'
        elif watermark_text:
            # 尝试检测直接文本水印
            detected_text = auto_detect_repeating_text(doc)
            if detected_text and watermark_text in detected_text:
                watermark_type = 'direct_text'

        # 自动检测重复文本
        if not watermark_type and not watermark_text:
            detected_text = auto_detect_repeating_text(doc)
            if detected_text:
                watermark_text = detected_text
                watermark_type = 'direct_text'

        # 移除水印
        if watermark_type == 'global_xobj':
            remove_global_watermark(doc, watermark_xref, method)
        elif watermark_type == 'rotation':
            remove_rotation_watermark(doc)
        elif watermark_type == 'direct_text':
            remove_direct_text_watermark(doc, watermark_text)

        # 保存
        save_with_qpdf(doc, output_path)

        if not os.path.exists(output_path):
            return {"success": False, "error": "输出文件未生成"}

        return {
            "success": True,
            "output": output_path,
            "watermark_type": watermark_type,
            "pages": total_pages,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if doc is not None:
            doc.close()
