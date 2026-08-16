# 页面旋转干预功能 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 去水印后的 PDF 预览页支持人工页面旋转（解决倒置扫描页），配套缩略图浏览、转换后乱码页自动提示、单页重转+md 拼接+证据失效的低成本修复闭环。

**Architecture:** 新建 `backend/page_rotation.py` 纯函数模块（缩略图生成/旋转/乱码检测/抽页/拼接/证据失效），`case_manager.py` 加 4 个薄端点；前端 Preview.tsx 为 PDF 增加「页面管理」模式（新组件 PdfPageManager：缩略图网格+旋转+保存+重转），FileList 加转换前提示与 ⚠️ 标记。

**Tech Stack:** Python 3.13 / FastAPI / PyMuPDF(fitz) / pytest；React 18 + TypeScript + Vite

**关键背景（零上下文必读）：**
- 测试从仓库根运行：`python3 -m pytest tests/xxx.py -q`（`tests/conftest.py` 已加 backend 到 sys.path）
- 前端构建验证：`cd frontend && npm run build && npx tsc --noEmit`
- 真实案例（倒置页）：冯叶飞案第2卷 `processed/第2卷_去水印.pdf` 第 154 页倒置 180°，导致 MinerU 将该页误判为 `<table>` 乱码块（`md/第2卷_去水印.md` 中 `<table><tr><td>第6页共11页</td></tr>...` 形式）
- MinerU API 无旋转参数；`page_ranges` 官方支持但本项目提交链路未接——单页重转用「PyMuPDF 抽页成临时 PDF 再整页提交」实现（已实测可行）
- 证据提取断点续传：`case_manager.py:1591-1601`，`processed_sources = {ev["source"] for ev in existing_evidence}`，index.json 中删除某 source 的条目后重新提取会自动重跑该卷
- 缩略图服务复用现有挂载：`main.py:80` `app.mount("/thumbnails", StaticFiles(directory=CACHE_DIR))`，CACHE_DIR=`DATA_DIR/cache`
- 前端已有未接线的 helper：`api/cases.ts:229` `getThumbnails(caseId, filePath, dir, width)` 期望 `GET /api/cases/{caseId}/pdf-thumbnails?file_path=&dir=&width=`（后端缺失，本计划实现）
- 案件文件定位：`case_manager.py` 的 `find_case_path(case_id)`；processed PDF 在 `{case_path}/processed/`
- Preview.tsx 目前 PDF 用 `<iframe>`（`components/Preview.tsx:108-115`），仅在 CaseDetailPage 使用（`CaseDetailPage.tsx:518`）
- MinerU 转换复用：`mineru_async.py` `AsyncMinerUConverter().convert_batch(pdf_paths, output_dir, ...)` 返回 `List[ConvertResult]`（含输出 md 路径）；测试时 monkeypatch 此方法避免真实 API 调用
- **不修改 `original/` 原件**，旋转只作用于 `processed/`

---

### Task 1: page_rotation 模块——缩略图生成 + 页面旋转

**Files:**
- Create: `backend/page_rotation.py`
- Test: `tests/test_page_rotation.py`

- [ ] **Step 1: 写失败测试**

```python
"""页面旋转干预：缩略图生成 + 页面旋转"""
import fitz
import pytest

from page_rotation import generate_pdf_thumbnails, rotate_pdf_page


def _make_pdf(path, pages=3):
    """生成带文字的多页 PDF（fitz）"""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"第{i+1}页内容 测试文字")
    doc.save(path)
    doc.close()


def test_thumbnails_generated_and_cached(tmp_path):
    """缩略图：逐页生成 PNG 并缓存（第二次调用不重复渲染）"""
    pdf = tmp_path / "test.pdf"
    _make_pdf(pdf, pages=3)
    cache = tmp_path / "thumb"

    result = generate_pdf_thumbnails(pdf, cache, width=200)
    assert len(result) == 3
    assert result[0]["page"] == 1
    assert (cache / "page_1.png").exists()
    assert (cache / "page_3.png").exists()

    # 第二次调用：删除 page_2.png 模拟缓存部分失效，只重渲该页
    (cache / "page_2.png").unlink()
    result2 = generate_pdf_thumbnails(pdf, cache, width=200)
    assert len(result2) == 3
    assert (cache / "page_2.png").exists()


def test_rotate_page_180(tmp_path):
    """旋转 180°：rotation 从 0 变 180，增量保存后重开仍生效"""
    pdf = tmp_path / "test.pdf"
    _make_pdf(pdf, pages=2)

    new_rot = rotate_pdf_page(pdf, 1, 180)
    assert new_rot == 180
    doc = fitz.open(pdf)
    assert doc[0].rotation == 180
    assert doc[1].rotation == 0  # 其他页不受影响
    doc.close()


def test_rotate_accumulates(tmp_path):
    """连续两次 90° = 180°"""
    pdf = tmp_path / "test.pdf"
    _make_pdf(pdf)
    rotate_pdf_page(pdf, 1, 90)
    assert rotate_pdf_page(pdf, 1, 90) == 180


def test_rotate_invalid_args(tmp_path):
    """非法角度/页码抛 ValueError"""
    pdf = tmp_path / "test.pdf"
    _make_pdf(pdf, pages=1)
    with pytest.raises(ValueError):
        rotate_pdf_page(pdf, 1, 45)
    with pytest.raises(ValueError):
        rotate_pdf_page(pdf, 99, 90)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_page_rotation.py -q`
Expected: ImportError

- [ ] **Step 3: 实现 `backend/page_rotation.py`**

```python
"""页面旋转干预：缩略图生成、页面旋转、乱码页检测、单页抽取与 md 拼接

背景：案卷扫描件偶有倒置页（如整页旋转 180° 扫描），MinerU 对倒置页会误判
版面（笔录页识别为 <table> 乱码块），产生"泻叶无/次嘉豪"级乱码。MinerU API
无旋转参数，须在本地 PDF 上修正页面方向后再转换。
本模块全部为纯函数，FastAPI 端点在 case_manager.py 中做薄封装。
"""
import logging
import re
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# 缩略图默认宽度（供预览页网格浏览，能辨认页面朝向即可）
THUMB_DEFAULT_WIDTH = 200


def generate_pdf_thumbnails(pdf_path: Path, cache_dir: Path, width: int = THUMB_DEFAULT_WIDTH) -> list[dict]:
    """逐页生成缩略图 PNG 到 cache_dir（已存在则跳过，断点续渲）

    Returns: [{"page": 1, "file": "page_1.png"}, ...]（url 由端点层拼接）
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        zoom = width / 595  # A4 宽度约 595pt
        mat = fitz.Matrix(zoom, zoom)
        result = []
        for i, page in enumerate(doc):
            png = cache_dir / f"page_{i + 1}.png"
            if not png.exists():
                pix = page.get_pixmap(matrix=mat)
                pix.save(png)
            result.append({"page": i + 1, "file": png.name})
        return result
    finally:
        doc.close()


def rotate_pdf_page(pdf_path: Path, page_no: int, degrees: int,
                    thumb_cache_dir: Path | None = None) -> int:
    """将 page_no（1 基）顺时针旋转 degrees（90/180/270），增量保存

    Returns: 旋转后的新 rotation 值（0/90/180/270）
    """
    if degrees not in (90, 180, 270):
        raise ValueError("degrees 只支持 90/180/270")
    doc = fitz.open(pdf_path)
    try:
        if not 1 <= page_no <= len(doc):
            raise ValueError(f"页码 {page_no} 超出范围（共 {len(doc)} 页）")
        page = doc[page_no - 1]
        new_rot = (page.rotation + degrees) % 360
        page.set_rotation(new_rot)
        doc.saveIncr()  # 增量保存：只追加 rotation 变更，大文件秒级完成
    finally:
        doc.close()
    # 旋转后该页缩略图缓存失效
    if thumb_cache_dir:
        stale = thumb_cache_dir / f"page_{page_no}.png"
        if stale.exists():
            stale.unlink()
    logger.info(f"[页面旋转] {pdf_path.name} 第 {page_no} 页旋转 {degrees}° → {new_rot}°")
    return new_rot
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_page_rotation.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/page_rotation.py tests/test_page_rotation.py
git commit -m "feat: 页面旋转模块（缩略图生成+fitz增量旋转）"
```

---

### Task 2: 缩略图与旋转端点

**Files:**
- Modify: `backend/case_manager.py`（文件服务路由区，约 2828-2900 行 `serve_file` 附近）
- Test: `tests/test_page_rotation.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_thumb_cache_dir_naming():
    """缩略图缓存目录命名：cache/thumb/{case_id}/{pdf_stem}/（供端点与前端 URL 一致）"""
    from page_rotation import thumb_cache_dir_for
    from pathlib import Path
    d = thumb_cache_dir_for(Path("/data/cache"), "case_abc", "第2卷_去水印.pdf")
    assert d == Path("/data/cache/thumb/case_abc/第2卷_去水印")
```

- [ ] **Step 2: 运行确认失败** — ImportError

- [ ] **Step 3: 实现**

`backend/page_rotation.py` 追加：

```python
def thumb_cache_dir_for(cache_root: Path, case_id: str, pdf_name: str) -> Path:
    """案件 PDF 的缩略图缓存目录（与 /thumbnails 静态挂载下的 URL 一一对应）"""
    return cache_root / "thumb" / case_id / Path(pdf_name).stem
```

`backend/case_manager.py` 在 `serve_file` 路由附近追加两个端点（先 Read 确认该区域代码风格与 `find_case_path` 用法，保持一致）：

```python
@router.get("/{case_id}/pdf-thumbnails")
async def pdf_thumbnails(case_id: str, file_path: str, dir: str = "processed", width: int = 200):
    """生成并返回案件 PDF 的逐页缩略图（缓存于 DATA_DIR/cache/thumb/，复用 /thumbnails 挂载）"""
    from config import CACHE_DIR
    from page_rotation import generate_pdf_thumbnails, thumb_cache_dir_for
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    pdf = (case_path / dir / file_path).resolve()
    if not str(pdf).startswith(str(case_path.resolve())) or not pdf.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    width = max(100, min(width, 800))
    cache_dir = thumb_cache_dir_for(CACHE_DIR, case_id, pdf.name)
    thumbs = generate_pdf_thumbnails(pdf, cache_dir, width)
    base = f"/thumbnails/thumb/{case_id}/{pdf.stem}"
    return {"thumbnails": [{"page": t["page"], "url": f"{base}/{t['file']}"} for t in thumbs],
            "total_pages": len(thumbs)}


class RotatePageRequest(BaseModel):
    file_path: str
    dir: str = "processed"
    page: int
    degrees: int  # 90/180/270，顺时针累加


@router.post("/{case_id}/rotate-page")
async def rotate_page(case_id: str, req: RotatePageRequest):
    """旋转 processed/ 下 PDF 的指定页（只改显示朝向，不动 original/ 原件）"""
    from config import CACHE_DIR
    from page_rotation import rotate_pdf_page, thumb_cache_dir_for
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    if req.dir != "processed":
        raise HTTPException(status_code=400, detail="仅支持旋转 processed/ 下的文件")
    pdf = (case_path / req.dir / req.file_path).resolve()
    if not str(pdf).startswith(str(case_path.resolve())) or not pdf.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        new_rot = rotate_pdf_page(pdf, req.page, req.degrees,
                                  thumb_cache_dir_for(CACHE_DIR, case_id, pdf.name))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "page": req.page, "rotation": new_rot}
```

- [ ] **Step 4: 运行测试 + 回归**

Run: `python3 -m pytest tests/test_page_rotation.py -q && python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: 5 passed；全量无失败

- [ ] **Step 5: Commit**

```bash
git add backend/page_rotation.py backend/case_manager.py tests/test_page_rotation.py
git commit -m "feat: 案件PDF缩略图与页面旋转端点（/pdf-thumbnails、/rotate-page）"
```

---

### Task 3: md 乱码页检测 + md-issues 端点

**Files:**
- Modify: `backend/page_rotation.py`（追加检测函数）
- Modify: `backend/case_manager.py`（追加端点）
- Test: `tests/test_page_rotation.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_detect_table_wrapped_transcript(tmp_path):
    """检测：含页码标记/问答的 <table> 块（MinerU 把倒置笔录页误判为表格）"""
    md_dir = tmp_path / "md"
    md_dir.mkdir()
    # 真实乱码样本（冯叶飞案第2卷，已脱敏简化）
    (md_dir / "第2卷_去水印.md").write_text(
        "# 讯问笔录\n\n正常内容。\n\n"
        "<table><tr><td>第6页共11页</td></tr><tr><td>装打了个电话讲了一下这个事情,之后泻叶无也给期拟打了电话</td></tr>"
        "<tr><td>问:(向其展示次嘉豪在收取团购五万块钱抵押款后的明细)通过明细来看,</td></tr></table>\n\n"
        "后续正常内容。\n", encoding="utf-8")
    (md_dir / "正常卷.md").write_text("# 卷内文书目录\n\n<table><tr><td>序号</td><td>标题</td></tr></table>\n", encoding="utf-8")

    from page_rotation import detect_md_issues
    issues = detect_md_issues(md_dir)
    assert len(issues) == 1
    assert issues[0]["md_file"] == "第2卷_去水印.md"
    assert issues[0]["page_label"] == "第6页共11页"
    assert issues[0]["start_line"] >= 0 and issues[0]["end_line"] >= issues[0]["start_line"]
    assert "泻叶无" in issues[0]["preview"]


def test_detect_ignores_normal_tables(tmp_path):
    """正常表格（卷内目录等）不误报"""
    md_dir = tmp_path / "md"
    md_dir.mkdir()
    (md_dir / "a.md").write_text("<table><tr><td>序号</td><td>责任者</td></tr><tr><td>1</td><td>告知书</td></tr></table>\n", encoding="utf-8")
    from page_rotation import detect_md_issues
    assert detect_md_issues(md_dir) == []
```

- [ ] **Step 2: 运行确认失败** — ImportError

- [ ] **Step 3: 实现**

`backend/page_rotation.py` 追加：

```python
_PAGE_LABEL_RE = re.compile(r"第\s*\d+\s*页\s*共\s*\d+\s*页")


def detect_md_issues(md_dir: Path) -> list[dict]:
    """扫描 md/*.md，找出被 MinerU 误判为表格的笔录页（倒置/异常扫描页的特征）

    判定：整块 <table>...</table> 内含「第N页共M页」页码标记或 ≥1 个「问：」。
    正常表格（卷内目录等）不含这些特征，不误报。
    Returns: [{"md_file", "page_label", "start_line", "end_line", "preview"}]
    （行号为 0 基，end_line 含；供重转拼接定位）
    """
    issues = []
    for md in sorted(md_dir.glob("*.md")):
        lines = md.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            if lines[i].strip().startswith("<table>"):
                start = i
                while i < len(lines) and "</table>" not in lines[i]:
                    i += 1
                end = min(i, len(lines) - 1)
                text = "\n".join(lines[start:end + 1])
                if _PAGE_LABEL_RE.search(text) or "问：" in text or "问:" in text:
                    m = _PAGE_LABEL_RE.search(text)
                    issues.append({
                        "md_file": md.name,
                        "page_label": m.group(0) if m else "",
                        "start_line": start,
                        "end_line": end,
                        "preview": re.sub(r"<[^>]+>", " ", text)[:120].strip(),
                    })
            i += 1
    return issues
```

`backend/case_manager.py` 追加端点：

```python
@router.get("/{case_id}/md-issues")
async def md_issues(case_id: str):
    """扫描案件 md/ 下的识别异常页（MinerU 把倒置/异常页误判为表格的乱码块）"""
    from page_rotation import detect_md_issues
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    md_dir = case_path / "md"
    if not md_dir.exists():
        return {"issues": []}
    return {"issues": detect_md_issues(md_dir)}
```

- [ ] **Step 4: 运行测试 + 回归**

Run: `python3 -m pytest tests/test_page_rotation.py -q && python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: 7 passed；全量无失败

- [ ] **Step 5: Commit**

```bash
git add backend/page_rotation.py backend/case_manager.py tests/test_page_rotation.py
git commit -m "feat: md乱码页检测（笔录页误判为表格）+ /md-issues 端点"
```

---

### Task 4: 单页抽取 + md 拼接 + 证据失效

**Files:**
- Modify: `backend/page_rotation.py`（追加三函数）
- Test: `tests/test_page_rotation.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_extract_single_page(tmp_path):
    """抽取单页为新 PDF"""
    from page_rotation import extract_single_page, _make_pdf_unused  # 占位，实际用本地 helper
```

（实际测试如下，替换上面占位）

```python
def test_extract_single_page(tmp_path):
    """抽取单页为新 PDF（供单页重转提交 MinerU）"""
    from page_rotation import extract_single_page
    pdf = tmp_path / "test.pdf"
    _make_pdf(pdf, pages=5)
    out = tmp_path / "page3.pdf"
    extract_single_page(pdf, 3, out)
    doc = fitz.open(out)
    assert len(doc) == 1
    assert "第3页内容" in doc[0].get_text()
    doc.close()


def test_splice_md_block(tmp_path):
    """md 拼接：新文本替换 [start_line, end_line] 行区间"""
    from page_rotation import splice_md_block
    md = tmp_path / "a.md"
    md.write_text("行0\n行1垃圾开始\n行2垃圾结束\n行3\n", encoding="utf-8")
    splice_md_block(md, 1, 2, "修复后的内容")
    assert md.read_text(encoding="utf-8") == "行0\n修复后的内容\n行3\n"


def test_invalidate_evidence_for_source(tmp_path):
    """证据失效：删除指定 source 的证据条目+证据md+摘要缓存，保留其他卷"""
    import json
    from page_rotation import invalidate_evidence_for_source
    case_path = tmp_path / "case"
    ev = case_path / "evidence"
    (ev / "summaries").mkdir(parents=True)
    idx = {"evidence": [
        {"name": "笔录A", "source": "第2卷_去水印.md", "md_file": "005_笔录A.md"},
        {"name": "笔录B", "source": "第3卷_去水印.md", "md_file": "030_笔录B.md"},
    ]}
    (ev / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    (ev / "005_笔录A.md").write_text("内容A", encoding="utf-8")
    (ev / "030_笔录B.md").write_text("内容B", encoding="utf-8")
    (ev / "summaries" / "005_笔录A.md").write_text("摘要A", encoding="utf-8")
    (ev / "summaries" / "005_笔录A.meta.json").write_text("{}", encoding="utf-8")

    removed = invalidate_evidence_for_source(case_path, "第2卷_去水印.md")
    assert removed == ["笔录A"]
    assert not (ev / "005_笔录A.md").exists()
    assert not (ev / "summaries" / "005_笔录A.md").exists()
    assert not (ev / "summaries" / "005_笔录A.meta.json").exists()
    assert (ev / "030_笔录B.md").exists()
    kept = json.loads((ev / "index.json").read_text(encoding="utf-8"))["evidence"]
    assert len(kept) == 1 and kept[0]["name"] == "笔录B"
```

- [ ] **Step 2: 运行确认失败** — ImportError

- [ ] **Step 3: 实现**

`backend/page_rotation.py` 追加：

```python
def extract_single_page(pdf_path: Path, page_no: int, out_path: Path) -> Path:
    """抽取 PDF 单页（1 基）为独立 PDF 文件（供单页重转提交 MinerU）"""
    doc = fitz.open(pdf_path)
    try:
        if not 1 <= page_no <= len(doc):
            raise ValueError(f"页码 {page_no} 超出范围（共 {len(doc)} 页）")
        out = fitz.open()
        out.insert_pdf(doc, from_page=page_no - 1, to_page=page_no - 1)
        out.save(out_path)
        out.close()
        return out_path
    finally:
        doc.close()


def splice_md_block(md_path: Path, start_line: int, end_line: int, new_text: str) -> None:
    """用新文本替换 md 的 [start_line, end_line] 行区间（0 基含两端）"""
    lines = md_path.read_text(encoding="utf-8").splitlines()
    if not (0 <= start_line <= end_line < len(lines)):
        raise ValueError(f"行区间 [{start_line}, {end_line}] 超出范围（共 {len(lines)} 行）")
    lines[start_line:end_line + 1] = [new_text]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"[md拼接] {md_path.name}: 行 {start_line}-{end_line} 已替换（{len(new_text)} 字符）")


def invalidate_evidence_for_source(case_path: Path, md_filename: str) -> list[str]:
    """删除源自指定 md 的全部证据条目（含证据 md 与摘要缓存）

    断点续传机制（case_manager.py 提取入口按 index.json 的 source 跳过已完成文件）
    会在下次提取时自动重跑该卷。Returns: 被移除的证据名列表。
    """
    import json
    evidence_dir = case_path / "evidence"
    index_file = evidence_dir / "index.json"
    if not index_file.exists():
        return []
    idx = json.loads(index_file.read_text(encoding="utf-8"))
    items = idx.get("evidence", [])
    removed = [e for e in items if e.get("source") == md_filename]
    for e in removed:
        md_file = evidence_dir / e.get("md_file", "")
        if md_file.exists():
            md_file.unlink()
        stem = Path(e.get("md_file", "")).stem
        for suffix in (".md", ".meta.json"):
            cache = evidence_dir / "summaries" / f"{stem}{suffix}"
            if cache.exists():
                cache.unlink()
    idx["evidence"] = [e for e in items if e.get("source") != md_filename]
    index_file.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[证据失效] {md_filename}: 移除 {len(removed)} 份证据，待重新提取")
    return [e.get("name", "") for e in removed]
```

- [ ] **Step 4: 运行测试 + 回归**

Run: `python3 -m pytest tests/test_page_rotation.py -q && python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: 10 passed；全量无失败

- [ ] **Step 5: Commit**

```bash
git add backend/page_rotation.py tests/test_page_rotation.py
git commit -m "feat: 单页抽取+md拼接+证据失效（重转修复闭环的基础件）"
```

---

### Task 5: 单页重转端点 reconvert-block

**Files:**
- Modify: `backend/case_manager.py`（追加端点）
- Test: `tests/test_page_rotation.py`（追加，monkeypatch MinerU）

- [ ] **Step 1: 追加失败测试**

```python
def test_reconvert_block_flow(tmp_path, monkeypatch):
    """重转闭环：抽页→转换→拼接→证据失效（MinerU 打桩，验证编排顺序与产物）"""
    import asyncio, json
    import case_manager
    from page_rotation import splice_md_block

    case_path = tmp_path / "case"
    (case_path / "processed").mkdir(parents=True)
    (case_path / "md").mkdir()
    (case_path / "evidence").mkdir()
    _make_pdf(case_path / "processed" / "第2卷_去水印.pdf", pages=3)
    (case_path / "md" / "第2卷_去水印.md").write_text(
        "前文\n<table><tr><td>第6页共11页</td></tr><tr><td>乱码</td></tr></table>\n后文\n", encoding="utf-8")
    (case_path / "evidence" / "index.json").write_text(json.dumps({"evidence": [
        {"name": "笔录A", "source": "第2卷_去水印.md", "md_file": "005_A.md"}]}, ensure_ascii=False), encoding="utf-8")

    # MinerU 打桩：convert_batch 直接产出"修复文本"md
    class FakeResult:
        def __init__(self, md): self.md_path = md
    async def fake_convert_batch(self, pdf_paths, output_dir, **kw):
        md = output_dir / f"{pdf_paths[0].stem}.md"
        md.write_text("修复后的笔录内容", encoding="utf-8")
        return [FakeResult(md)]
    monkeypatch.setattr("mineru_async.AsyncMinerUConverter.convert_batch", fake_convert_batch)
    monkeypatch.setattr(case_manager, "find_case_path", lambda cid: case_path)

    req = case_manager.ReconvertBlockRequest(
        file_path="第2卷_去水印.pdf", page=2, md_file="第2卷_去水印.md",
        start_line=1, end_line=1, invalidate_evidence=True)
    resp = asyncio.run(case_manager.reconvert_block("c", req))

    assert resp["success"] is True
    assert "修复后的笔录内容" in (case_path / "md" / "第2卷_去水印.md").read_text(encoding="utf-8")
    assert resp["invalidated"] == ["笔录A"]
    # 证据已失效（index.json 清空）
    assert json.loads((case_path / "evidence" / "index.json").read_text(encoding="utf-8"))["evidence"] == []
```

- [ ] **Step 2: 运行确认失败** — AttributeError（ReconvertBlockRequest 不存在）

- [ ] **Step 3: 实现**

`backend/case_manager.py` 追加（注意先 Read 确认文件顶部 import 区与端点风格）：

```python
class ReconvertBlockRequest(BaseModel):
    file_path: str          # processed/ 下 PDF 文件名
    page: int               # 需重转的 PDF 页码（1 基）
    md_file: str            # 待拼接的 md 文件名
    start_line: int         # 乱码块起始行（md-issues 返回）
    end_line: int           # 乱码块结束行（含）
    invalidate_evidence: bool = False  # 是否同时失效该卷证据（供重新提取）


@router.post("/{case_id}/reconvert-block")
async def reconvert_block(case_id: str, req: ReconvertBlockRequest):
    """单页重转修复：抽取旋转后的单页 → MinerU 转换 → 替换 md 乱码块 → 可选证据失效

    成本：MinerU 1 页额度 + 0 次 LLM 调用（对比整卷重转 170 页+全卷重提取）。
    """
    import tempfile
    from mineru_async import AsyncMinerUConverter
    from page_rotation import extract_single_page, splice_md_block, invalidate_evidence_for_source

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    pdf = (case_path / "processed" / req.file_path).resolve()
    md_path = (case_path / "md" / req.md_file).resolve()
    if not str(pdf).startswith(str(case_path.resolve())) or not pdf.exists():
        raise HTTPException(status_code=404, detail="PDF 不存在")
    if not str(md_path).startswith(str(case_path.resolve())) or not md_path.exists():
        raise HTTPException(status_code=404, detail="MD 文件不存在")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        single = extract_single_page(pdf, req.page, tmp / "page.pdf")
        converter = AsyncMinerUConverter()
        results = await converter.convert_batch([single], tmp, max_concurrent=1)
        new_md = tmp / "page.md"
        if not results or not new_md.exists():
            raise HTTPException(status_code=502, detail="单页转换失败，请稍后重试")
        new_text = new_md.read_text(encoding="utf-8").strip()

    splice_md_block(md_path, req.start_line, req.end_line, new_text)

    invalidated = []
    if req.invalidate_evidence:
        invalidated = invalidate_evidence_for_source(case_path, req.md_file)

    return {"success": True, "spliced_chars": len(new_text), "invalidated": invalidated}
```

注意：
- `FakeResult`/`ConvertResult` 的实际字段名以 `mineru_async.py` 的 `ConvertResult` 定义为准（先 Read 确认输出 md 的实际路径规则：chunk 机制下单页文件是否会生成 `{stem}_part1.md` 之类的名字——若是，端点里按 `tmp.glob("*.md")` 取第一个更稳）。实现时先 Read `convert_batch` 返回结构与 `_save_to_dir`。
- 测试里的 `FakeResult` 和端点取值方式要对齐（端点若改用 glob 取 md，测试打桩也相应只写 md 文件即可，返回值用真实 ConvertResult 或简单对象均可）。

- [ ] **Step 4: 运行测试 + 全套件回归**

Run: `python3 -m pytest tests/test_page_rotation.py -q && python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: 11 passed；全量无失败

- [ ] **Step 5: Commit**

```bash
git add backend/case_manager.py tests/test_page_rotation.py
git commit -m "feat: 单页重转端点（抽页→MinerU→md拼接→证据失效，1页额度低成本修复）"
```

---

### Task 6: 前端 PdfPageManager 组件 + Preview 集成

**Files:**
- Create: `frontend/src/pages/CaseDetailPage/components/PdfPageManager.tsx`
- Modify: `frontend/src/pages/CaseDetailPage/components/Preview.tsx`
- Modify: `frontend/src/api/cases.ts`（追加 3 个 API 方法）
- Modify: `frontend/src/pages/CaseDetailPage.tsx`（Preview 调用点传 caseId/dir，约 518 行）

- [ ] **Step 1: API 方法**（`api/cases.ts` 追加）

```typescript
export async function rotatePage(caseId: string, filePath: string, page: number, degrees: number, dir = 'processed'): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/rotate-page`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_path: filePath, dir, page, degrees })
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || res.statusText)
  }
  return res.json()
}

export async function getMdIssues(caseId: string): Promise<{ issues: Array<{ md_file: string; page_label: string; start_line: number; end_line: number; preview: string }> }> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/md-issues`)
  return res.json()
}

export async function reconvertBlock(caseId: string, params: {
  file_path: string; page: number; md_file: string;
  start_line: number; end_line: number; invalidate_evidence?: boolean
}): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/reconvert-block`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params)
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || res.statusText)
  }
  return res.json()
}
```

- [ ] **Step 2: PdfPageManager 组件**

```tsx
// PDF 页面管理：缩略图网格 + 页面旋转 + 乱码页修复闭环
import React, { useEffect, useState } from 'react'
import { getThumbnails, rotatePage, reconvertBlock } from '../../../api/cases'

interface MdIssue {
  md_file: string
  page_label: string
  start_line: number
  end_line: number
  preview: string
}

interface PdfPageManagerProps {
  caseId: string
  pdfFilename: string            // processed/ 下文件名
  issues: MdIssue[]              // 该 PDF 对应 md 的乱码块（可能为空）
  onFixed: () => void            // 修复完成回调（父组件刷新状态）
}

export function PdfPageManager({ caseId, pdfFilename, issues, onFixed }: PdfPageManagerProps) {
  const [thumbs, setThumbs] = useState<Array<{ page: number; url: string }>>([])
  const [loading, setLoading] = useState(true)
  const [rotations, setRotations] = useState<Map<number, number>>(new Map())  // page → 累计角度
  const [saving, setSaving] = useState(false)
  const [fixing, setFixing] = useState(false)
  const [message, setMessage] = useState('')
  const [cacheBust, setCacheBust] = useState(0)

  useEffect(() => {
    setLoading(true)
    getThumbnails(caseId, pdfFilename, 'processed', 200)
      .then(r => { setThumbs(r.thumbnails || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [caseId, pdfFilename, cacheBust])

  const addRotation = (page: number, deg: number) => {
    setRotations(prev => {
      const next = new Map(prev)
      next.set(page, ((next.get(page) || 0) + deg) % 360)
      return next
    })
  }

  const saveRotations = async () => {
    setSaving(true)
    setMessage('')
    try {
      for (const [page, deg] of rotations) {
        if (deg !== 0) await rotatePage(caseId, pdfFilename, page, deg)
      }
      setRotations(new Map())
      setCacheBust(b => b + 1)  // 旋转后缩略图缓存已失效，重新拉取
      setMessage('旋转已保存')
    } catch (e: any) {
      setMessage(`保存失败：${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  const fixIssue = async (issue: MdIssue) => {
    const input = window.prompt(
      `将重转「${pdfFilename}」中识别异常的页面并替换乱码内容。\n` +
      `异常内容：${issue.page_label || issue.preview.slice(0, 40)}\n\n` +
      `请输入该内容在 PDF 中的页码（缩略图中确认过的数字页码）：`)
    const page = input ? parseInt(input, 10) : NaN
    if (!page || page < 1) return
    setFixing(true)
    setMessage('')
    try {
      const r = await reconvertBlock(caseId, {
        file_path: pdfFilename, page,
        md_file: pdfFilename.replace(/\.pdf$/i, '.md'),
        start_line: issue.start_line, end_line: issue.end_line,
        invalidate_evidence: true,
      })
      setMessage(`修复完成${r.invalidated?.length ? `，${r.invalidated.length} 份相关证据已标记重提取（请回案件页点「提取证据」）` : ''}`)
      onFixed()
    } catch (e: any) {
      setMessage(`修复失败：${e.message}`)
    } finally {
      setFixing(false)
    }
  }

  if (loading) return <div style={{ padding: 24, color: '#86868b' }}>生成缩略图中...</div>

  return (
    <div style={{ flex: 1, overflow: 'auto', background: '#1a1a1e', padding: 16 }}>
      {issues.length > 0 && (
        <div style={{ background: '#fff3cd', color: '#664d03', borderRadius: 8, padding: '10px 14px', marginBottom: 12, fontSize: 13 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>⚠️ 检测到 {issues.length} 处识别异常（可能页面倒置或扫描异常）</div>
          {issues.map((iss, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {iss.page_label || '未知页'}：{iss.preview.slice(0, 50)}…
              </span>
              <button onClick={() => fixIssue(iss)} disabled={fixing}
                style={{ padding: '4px 10px', fontSize: 12, border: 'none', borderRadius: 4, background: '#0d6efd', color: '#fff', cursor: 'pointer' }}>
                {fixing ? '修复中…' : '重转并修复'}
              </button>
            </div>
          ))}
        </div>
      )}
      {message && <div style={{ color: '#c8c8ce', fontSize: 13, marginBottom: 10 }}>{message}</div>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
        {thumbs.map(t => {
          const deg = rotations.get(t.page) || 0
          return (
            <div key={t.page} style={{ background: '#2c2c30', borderRadius: 8, padding: 8, textAlign: 'center' }}>
              <div style={{ overflow: 'hidden', borderRadius: 4, marginBottom: 6 }}>
                <img src={`${t.url}${cacheBust ? `?t=${cacheBust}` : ''}`} alt={`第${t.page}页`}
                  style={{ width: '100%', transform: `rotate(${deg}deg)`, transition: 'transform 0.2s' }} />
              </div>
              <div style={{ fontSize: 12, color: '#c8c8ce', marginBottom: 6 }}>第 {t.page} 页{deg ? `（待保存 ${deg}°）` : ''}</div>
              <div style={{ display: 'flex', justifyContent: 'center', gap: 8 }}>
                <button onClick={() => addRotation(t.page, 270)} title="逆时针90°"
                  style={{ padding: '2px 10px', fontSize: 14, border: 'none', borderRadius: 4, background: '#48484e', color: '#fff', cursor: 'pointer' }}>↺</button>
                <button onClick={() => addRotation(t.page, 90)} title="顺时针90°"
                  style={{ padding: '2px 10px', fontSize: 14, border: 'none', borderRadius: 4, background: '#48484e', color: '#fff', cursor: 'pointer' }}>↻</button>
              </div>
            </div>
          )
        })}
      </div>
      {rotations.size > 0 && (
        <div style={{ position: 'sticky', bottom: 0, padding: '12px 0', textAlign: 'center' }}>
          <button onClick={saveRotations} disabled={saving}
            style={{ padding: '8px 24px', fontSize: 14, border: 'none', borderRadius: 8, background: 'var(--macos-accent)', color: '#fff', cursor: 'pointer' }}>
            {saving ? '保存中…' : `保存旋转（${rotations.size} 页）`}
          </button>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Preview.tsx 集成**

PreviewFile 接口加 `caseId` 与 `dir`；PDF 分支头部加「页面管理」切换按钮，激活时渲染 PdfPageManager 替代 iframe：

```tsx
interface PreviewFile {
  id: string | number
  name: string
  path: string
  caseId?: string
  dir?: string
}

interface PreviewProps {
  file: PreviewFile
  onClose: () => void
  digest?: string
  digestWarning?: boolean
  mdIssues?: Array<{ md_file: string; page_label: string; start_line: number; end_line: number; preview: string }>
  onIssuesChanged?: () => void
}
```

组件内：

```tsx
const [pageManage, setPageManage] = useState(false)
const isPdf = !file.name.endsWith('.md')
const canManage = isPdf && file.caseId && file.dir === 'processed'
const pdfIssues = (mdIssues || []).filter(i => i.md_file === file.name.replace(/\.pdf$/i, '.md'))
```

头部按钮（放在「← 返回」右侧）：

```tsx
{canManage && (
  <button onClick={() => setPageManage(v => !v)} style={{ /* 与返回按钮同风格 */ }}>
    {pageManage ? '文档预览' : `页面管理${pdfIssues.length ? ` ⚠️${pdfIssues.length}` : ''}`}
  </button>
)}
```

PDF 内容区分支改为：

```tsx
) : pageManage && canManage ? (
  <PdfPageManager caseId={file.caseId!} pdfFilename={file.name} issues={pdfIssues}
    onFixed={() => onIssuesChanged?.()} />
) : (
  <div style={{ flex: 1, overflow: 'hidden', background: '#1a1a1e' }}>
    <iframe ... />
  </div>
)}
```

`CaseDetailPage.tsx` 518 行 Preview 调用点：

```tsx
{previewFile && <Preview file={{ ...previewFile, caseId, dir: previewDir }} onClose={...} digest={...} mdIssues={mdIssues} onIssuesChanged={refreshMdIssues} />}
```

（`previewDir`：useCaseFiles.handleOpenFile 里已有 dir 决策逻辑——step 1 为 'processed'，需把 dir 透出到 previewFile 或并列 state；mdIssues 的获取见 Task 7。）

- [ ] **Step 4: 构建验证**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: 无类型错误，构建成功

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/CaseDetailPage/components/PdfPageManager.tsx frontend/src/pages/CaseDetailPage/components/Preview.tsx frontend/src/api/cases.ts frontend/src/pages/CaseDetailPage.tsx
git commit -m "feat: 预览页页面管理模式（缩略图网格+旋转保存+乱码页重转修复）"
```

---

### Task 7: 转换前提示 + 文件列表 ⚠️ 标记

**Files:**
- Modify: `frontend/src/pages/CaseDetailPage.tsx`（mdIssues state + 获取时机）
- Modify: `frontend/src/pages/CaseDetailPage/components/FileList.tsx`（提示条 + ⚠️ 标记）

- [ ] **Step 1: mdIssues state**

`CaseDetailPage.tsx`：

```tsx
const [mdIssues, setMdIssues] = useState<Array<{ md_file: string; page_label: string; start_line: number; end_line: number; preview: string }>>([])

const refreshMdIssues = useCallback(async () => {
  try {
    const r = await getMdIssues(caseId!)
    setMdIssues(r.issues || [])
  } catch { setMdIssues([]) }
}, [caseId])
```

获取时机：currentStep 变为 1 时 + 批量转换轮询完成时（`handleConvertAllToMd` 的轮询收尾处，约 273-291 行）调用 `refreshMdIssues()`。

- [ ] **Step 2: FileList 提示与标记**

FileList props 加 `mdIssues` 与 `showRotationHint`：

```tsx
// 步骤1且存在未转换（done=false）的 PDF 时，列表上方显示：
{showRotationHint && (
  <div style={{ background: '#e8f0fe', color: '#1a56db', borderRadius: 8, padding: '8px 14px', margin: '8px 0', fontSize: 13 }}>
    提示：转换前建议先「预览」→「页面管理」浏览缩略图，确认无倒置页面（倒置页会导致识别乱码）
  </div>
)}

// 文件行：若该文件已转换（done）且 mdIssues 含对应 md（{stem}.md），文件名旁显示：
<span title="检测到识别异常页，请在「预览 → 页面管理」中处理" style={{ color: '#b7791f' }}> ⚠️</span>
```

`showRotationHint` 计算（CaseDetailPage 传入）：`currentStep === 1 && step1Files.some(f => !f.done)`。
⚠️ 判断：`step1Files` 行 `f.done && mdIssues.some(i => i.md_file === f.name.replace(/\.pdf$/i, '.md'))`（注意 step 1 行文件名可能是 processedPath，先 Read FileList.tsx:76-77 的命名逻辑对齐）。

- [ ] **Step 3: 构建验证**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: 通过

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/CaseDetailPage.tsx frontend/src/pages/CaseDetailPage/components/FileList.tsx
git commit -m "feat: 转换前页面方向提示 + 文件列表识别异常⚠️标记"
```

---

### Task 8: 冯叶飞案真实验证（手动）

**非代码任务**，在前述任务完成后执行：

- [ ] **Step 1: 后端起服** `cd backend && python3 main.py`
- [ ] **Step 2: 验证 md-issues** `curl -s http://localhost:8080/api/cases/case_27e576ef/md-issues | python3 -m json.tool` — 应返回第2卷的「第6页共11页」乱码块
- [ ] **Step 3: 验证旋转** 对 `第2卷_去水印.pdf` 第 154 页调 rotate-page（180°）→ 重新拉缩略图确认该页方向已正
- [ ] **Step 4: 验证重转闭环** 调 reconvert-block（page=154 + issue 的 start/end_line + invalidate_evidence=true）→ 确认 md 乱码块被干净文本替换、第2卷 29 份证据已失效
- [ ] **Step 5: 重新提取** 前端点「提取证据」，确认断点续传只重跑第2卷（其余卷跳过），027 笔录人名应为「赵君杰」（原乱码为「赵若杰」）
- [ ] **Step 6: 前端走查** 预览 → 页面管理：缩略图网格、旋转预览、⚠️ 横幅、重转按钮全流程

---

## Self-Review 记录

- **范围覆盖**：缩略图（T1/T2）、旋转（T1/T2）、乱码检测（T3）、重转闭环（T4/T5）、前端页面管理（T6）、提示与标记（T7）、真实验证（T8）✓
- **明确不做**：original/ 原件编辑（证据完整性）；通用 PDF 编辑（删页/调序，YAGNI）；单页 `page_ranges` 直连提交（抽页成临时 PDF 已等效）；生僻字率检测（v1 只覆盖已观测的表格误判模式）；ReportPage 的 PdfViewer 不加页面管理（用户场景在去水印后预览）
- **类型一致**：`generate_pdf_thumbnails` 返回 `[{page, file}]`，端点层拼 url；`MdIssue` 字段前后端一致（md_file/page_label/start_line/end_line/preview）；`ReconvertBlockRequest` 字段与前端 `reconvertBlock` 参数一致
- **风险点**：①证据失效后重新提取的**编号连续性**——实现 Task 5 前先 Read case_manager.py 提取入口的编号分配逻辑（约 1900-2010 行），若新提取证据编号与保留条目冲突/乱序，需在 invalidate 后做编号规整或在端点文档中说明；②saveIncr 要求 PDF 未被加密且可写——processed/ 文件为本应用产出，满足；③单页转换产物文件名按 chunk 机制可能与 `page.md` 不同，端点用 glob 兜底
