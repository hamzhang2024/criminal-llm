"""MinerU 转换产物的图片文字回填（PaddleOCR 单图识别通道）

MinerU vlm 对嵌入式截图/回单能转录，但照片类图片（如横置手机拍照的凭证/流水）
会原样保留为 ![]() 引用。本模块把这些残留图片逐张提交 PaddleOCR 单图识别，
将识别文字回填到 MD 对应图片引用之后。

- 缓存：{stem}_images/_ocr.json（文件名+大小为键），重复转换不重复识别
- 去重：同内容图片（MD5）只识别一次
- 过滤：小图（短边 < 120px，图标/签名条）跳过
"""
import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# 短边小于此值视为图标/签名条，跳过识别
MIN_DIMENSION = 120
# 单图任务轮询上限（秒）
RECOGNIZE_TIMEOUT = 600
POLL_INTERVAL = 3


def _get_token() -> str:
    """读取 PaddleOCR Token（回填通道用）"""
    try:
        from paddleocr_async import _get_paddleocr_token
        return _get_paddleocr_token()
    except Exception:
        return ""


async def _recognize_single_image(
    session: aiohttp.ClientSession,
    img_path: Path,
    token: str,
    ssl_context,
) -> str:
    """提交单图识别任务，返回识别文本（失败/无内容返回空串）"""
    from paddleocr_remote import PADDLEOCR_API_URL, PADDLEOCR_MODEL

    payload = {
        "useDocOrientationClassify": True,   # 横置照片自动纠正方向
        "useDocUnwarping": True,
        "useLayoutDetection": True,
        "useChartRecognition": False,
    }
    headers = {"Authorization": f"bearer {token}"}
    try:
        data = aiohttp.FormData()
        data.add_field("file", img_path.read_bytes(), filename=img_path.name,
                       content_type="image/jpeg")
        data.add_field("model", PADDLEOCR_MODEL)
        data.add_field("optionalPayload", json.dumps(payload))
        async with session.post(
            PADDLEOCR_API_URL, headers=headers, data=data,
            timeout=aiohttp.ClientTimeout(total=120), ssl=ssl_context,
        ) as resp:
            if resp.status != 200:
                logger.warning(f"[图片回填] 提交失败 HTTP {resp.status}: {img_path.name}")
                return ""
            job_id = (await resp.json())["data"]["jobId"]

        waited = 0
        while waited < RECOGNIZE_TIMEOUT:
            async with session.get(
                f"{PADDLEOCR_API_URL}/{job_id}", headers=headers,
                timeout=aiohttp.ClientTimeout(total=30), ssl=ssl_context,
            ) as resp:
                if resp.status != 200:
                    await asyncio.sleep(POLL_INTERVAL)
                    waited += POLL_INTERVAL
                    continue
                job = (await resp.json())["data"]
                state = job.get("state")
                if state == "done":
                    url = job.get("resultUrl", {}).get("jsonUrl", "")
                    if not url:
                        return ""
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=60), ssl=ssl_context,
                        skip_auto_headers=["User-Agent", "Accept", "Accept-Encoding"],
                    ) as r2:
                        raw = await r2.text()
                    texts = []
                    for line in raw.strip().split("\n"):
                        if not line.strip():
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        for pr in rec.get("result", {}).get("layoutParsingResults", []):
                            md = pr.get("markdown", {})
                            t = md.get("text", "") if isinstance(md, dict) else str(md)
                            # 去掉残留的图片引用（整图被当图片块时返回的是引用而非文字）
                            t = re.sub(r"<img[^>]*>|!\[\]\([^)]*\)", "", t).strip()
                            if t:
                                texts.append(t)
                    return "\n\n".join(texts)
                if state == "failed":
                    logger.warning(f"[图片回填] 识别失败: {img_path.name}: {job.get('errorMsg')}")
                    return ""
            await asyncio.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL
        logger.warning(f"[图片回填] 识别超时: {img_path.name}")
    except Exception as e:
        logger.warning(f"[图片回填] 识别异常 {img_path.name}: {e}")
    return ""


def _too_small(img_path: Path) -> bool:
    """短边 < MIN_DIMENSION 视为图标/签名条"""
    try:
        from PIL import Image
        with Image.open(img_path) as im:
            return min(im.width, im.height) < MIN_DIMENSION
    except Exception:
        return False


def preselect_ocr_images(case_dir: Path) -> dict[str, dict[str, dict[str, int]]]:
    """读各卷 layout.json，预筛出「疑似有文字的图片」（排除印章+小图）

    Returns:
        {卷名: {图片名: {"w": int, "h": int}}}，卷名即 layout.json 的 stem 去掉 _layout
    """
    md_dir = Path(case_dir) / "md"
    result = {}
    if not md_dir.exists():
        return result
    for layout_file in sorted(md_dir.glob("*_layout.json")):
        vol_name = layout_file.stem[:-len("_layout")]
        try:
            data = json.loads(layout_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        images = {}
        for page in data.get("pdf_info", []):
            for blk in page.get("para_blocks", []):
                if blk.get("type") != "image":
                    continue
                if blk.get("sub_type") == "seal":
                    continue
                bbox = blk.get("bbox", [])
                if len(bbox) >= 4:
                    w = int(bbox[2] - bbox[0])
                    h = int(bbox[3] - bbox[1])
                    if min(w, h) < MIN_DIMENSION:
                        continue
                else:
                    w = h = 0
                # 取图片文件名（blocks[0].lines[0].spans[0].image_path）
                name = ""
                for b in blk.get("blocks", []):
                    for line in b.get("lines", []):
                        for span in line.get("spans", []):
                            name = span.get("image_path", "")
                            if name:
                                break
                        if name:
                            break
                    if name:
                        break
                if name and name not in images:
                    images[name] = {"w": w, "h": h}
        if images:
            result[vol_name] = images
    return result


async def backfill_image_ocr(
    md_text: str,
    images_dir: Path,
    stem: str,
    max_concurrent: int = 3,
    only_names: Optional[set[str]] = None,
) -> str:
    """把 images_dir 中未转录图片的识别文字回填到 md_text 的图片引用之后

    Args:
        md_text: MinerU 转换产物 Markdown
        images_dir: {stem}_images 目录
        stem: 文件名 stem（用于匹配 MD 中的 ./{stem}_images/ 引用）
        max_concurrent: 单图识别并发数
        only_names: 仅回填这些图片名（None 表示全部），用于选择性 OCR
    """
    token = _get_token()
    if not token:
        logger.info("[图片回填] 未配置 PaddleOCR Token，跳过（MinerU 图片回填需要 PaddleOCR 通道）")
        return md_text
    if not images_dir or not Path(images_dir).exists():
        return md_text
    images_dir = Path(images_dir)

    # 找 MD 中引用的本地图片（![]() 与 <img> 两种形式）
    img_dir_ref = re.escape(f"./{stem}_images/")
    md_refs = re.findall(r"!\[\]\(" + img_dir_ref + r"([^)]+)\)", md_text)
    img_refs = re.findall(r"<img[^>]*src=\"" + img_dir_ref + r"([^\"]+)\"", md_text)
    refs = list(dict.fromkeys(md_refs + img_refs))  # 保序去重
    if not refs:
        return md_text

    if only_names is not None:
        refs = [r for r in refs if r in only_names]
        if not refs:
            return md_text

    # 读缓存
    cache_path = images_dir / "_ocr.json"
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    # 筛选待识别：缓存命中跳过、小图跳过、同内容去重
    tasks = {}
    hash_map = {}  # 待回填名 -> 同内容的已识别名
    seen_hash = {}
    for name in refs:
        img_path = images_dir / name
        if not img_path.exists():
            continue
        size = img_path.stat().st_size
        cached = cache.get(name)
        if cached and cached.get("size") == size:
            continue
        if _too_small(img_path):
            cache[name] = {"size": size, "text": "", "skipped": "small"}
            continue
        h = hashlib.md5(img_path.read_bytes()).hexdigest()
        if h in seen_hash:
            hash_map[name] = seen_hash[h]
            continue
        seen_hash[h] = name
        tasks[name] = img_path

    # 并发识别
    if tasks:
        logger.info(f"[图片回填] {stem}: {len(tasks)} 张图片待识别（并发 {max_concurrent}）")
        from paddleocr_async import _SSL_CONTEXT
        sem = asyncio.Semaphore(max_concurrent)

        async with aiohttp.ClientSession() as session:
            async def _one(name: str, path: Path):
                async with sem:
                    text = await _recognize_single_image(session, path, token, _SSL_CONTEXT)
                    cache[name] = {"size": path.stat().st_size, "text": text}
                    logger.info(f"[图片回填] {name}: {len(text)} 字符")

            await asyncio.gather(*(_one(n, p) for n, p in tasks.items()))

    # 去重图片共享识别结果
    for name, src in hash_map.items():
        src_entry = cache.get(src)
        if src_entry:
            cache[name] = {"size": (images_dir / name).stat().st_size,
                           "text": src_entry.get("text", ""), "dedup_from": src}

    if tasks or hash_map:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    # 回填：识别文字插到引用之后（引用块格式，保留原图）
    def _quote(text: str) -> str:
        return "\n> ".join(text.split("\n"))

    def _repl_md(m: re.Match) -> str:
        name = m.group(1)
        if name not in refs:
            return m.group(0)
        text = (cache.get(name) or {}).get("text", "").strip()
        if not text:
            return m.group(0)
        return f"{m.group(0)}\n\n> 📄 图片内容识别：\n> {_quote(text)}"

    def _repl_img(m: re.Match) -> str:
        name = m.group(1)
        if name not in refs:
            return m.group(0)
        text = (cache.get(name) or {}).get("text", "").strip()
        if not text:
            return m.group(0)
        return f"{m.group(0)}\n\n> 📄 图片内容识别：\n> {_quote(text)}"

    md_text = re.sub(r"!\[\]\(" + img_dir_ref + r"([^)]+)\)", _repl_md, md_text)
    md_text = re.sub(r"<img[^>]*src=\"" + img_dir_ref + r"([^\"]+)\"", _repl_img, md_text)
    return md_text
