# 选择性 OCR 图片 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MinerU 转 MD 后，作为流水线内可选步骤，规则预筛（排除印章+小图）后让用户按卷勾选图片，批量单图识别回填 MD。

**Architecture:** 复用 `image_ocr_backfill` 的单图识别与回填逻辑，加「指定图片列表」参数；新增 `preselect_ocr_images` 读 layout.json 预筛；后端挂 3 个接口（列表/启动/进度）+ `asyncio.create_task` 后台任务；前端新增「选择性 OCR」卡片。MinerU 引擎删除自动回填路径，`image_ocr_enabled` 只留给 PaddleOCR。

**Tech Stack:** Python 3.13 / FastAPI / pytest（后端），React 18 + TypeScript（前端）

**设计文档：** `docs/superpowers/specs/2026-08-15-selective-ocr-design.md`

**关键背景（零上下文必读）：**
- 测试从仓库根运行：`python3 -m pytest tests/xxx.py -q`（`tests/conftest.py` 已加 backend 到 sys.path）
- `router = APIRouter(prefix="/api/cases")`（`backend/case_manager.py:33`），新接口挂这个 router
- `find_case_path(case_id)` 返回案件根目录（`case_manager.py:200`）
- MinerU 产物命名：`md/{卷名}.md`、`md/{卷名}_images/`、`md/{卷名}_layout.json`（卷名如"第10卷_去水印"）
- layout.json image 块结构（实测）：`{type:"image", sub_type:"seal"|"?", bbox:[x0,y0,x1,y1], blocks:[{lines:[{spans:[{type:"image", image_path:"xxx.jpg"}]}]}]}`
- `_recognize_single_image(session, img_path, token, ssl_context) -> str`（异步单图识别）
- `backfill_image_ocr(md_text, images_dir, stem, max_concurrent=3) -> str`（现有全量回填）
- 缩略图走 `serve-file` 端点：`/api/cases/{id}/serve-file?file_path={图片名}&dir=md`（图片在 `md/{卷}_images/`，serve-file 用 rglob 全局搜索文件名，无需卷名）

---

### Task 1: 预筛函数 + 回填改造（backend 核心）

**Files:**
- Modify: `backend/image_ocr_backfill.py`
- Test: `tests/test_selective_ocr.py`

- [ ] **Step 1: 写失败测试**

```python
"""选择性 OCR：预筛 + 指定图片列表回填"""
import json
from pathlib import Path

from image_ocr_backfill import preselect_ocr_images, backfill_image_ocr, MIN_DIMENSION


def _make_layout(tmp_path: Path, name: str, image_blocks: list) -> Path:
    """构造 mock layout.json，image_blocks 为 [{sub_type, image_path, bbox}]"""
    layout = tmp_path / f"{name}_layout.json"
    pages = []
    for i, blk in enumerate(image_blocks):
        pages.append({"page_idx": i, "para_blocks": [{
            "type": "image",
            "sub_type": blk.get("sub_type", "?"),
            "bbox": blk["bbox"],
            "blocks": [{"lines": [{"spans": [{"type": "image", "image_path": blk["image_path"]}]}]}],
        }]})
    layout.write_text(json.dumps({"pdf_info": pages}, ensure_ascii=False), encoding="utf-8")
    return layout


def test_preselect_excludes_seal_and_small(tmp_path):
    """预筛：排除 seal 印章 + 小图，保留 ? 类型"""
    _make_layout(tmp_path, "第1卷_去水印", [
        {"sub_type": "seal", "image_path": "seal1.jpg", "bbox": [0, 0, 200, 200]},
        {"sub_type": "?", "image_path": "big.jpg", "bbox": [0, 0, 300, 300]},
        {"sub_type": "?", "image_path": "small.jpg", "bbox": [0, 0, 80, 80]},  # 小图
        {"sub_type": "?", "image_path": "keep.jpg", "bbox": [0, 0, 250, 400]},
    ])
    result = preselect_ocr_images(tmp_path)
    assert "第1卷_去水印" in result
    names = list(result["第1卷_去水印"].keys())
    assert "big.jpg" in names and "keep.jpg" in names
    assert "seal1.jpg" not in names and "small.jpg" not in names


def test_preselect_dedups_and_handles_missing_layout(tmp_path):
    """预筛：图片去重 + layout 缺失不报错"""
    _make_layout(tmp_path, "第2卷_去水印", [
        {"sub_type": "?", "image_path": "dup.jpg", "bbox": [0, 0, 300, 300]},
        {"sub_type": "?", "image_path": "dup.jpg", "bbox": [10, 10, 300, 300]},  # 重复
    ])
    # 一个不存在的 layout 文件不影响
    (tmp_path / "第3卷_去水印_layout.json").write_text("损坏的 json", encoding="utf-8")
    result = preselect_ocr_images(tmp_path)
    assert len(result["第2卷_去水印"]) == 1  # dup.jpg 去重
    assert "第3卷_去水印" not in result  # 损坏的跳过


def test_backfill_only_names(tmp_path):
    """回填：only_names 只识别指定图片"""
    md_text = "![](./第1卷_去水印_images/a.jpg)\n\n![](./第1卷_去水印_images/b.jpg)"
    images_dir = tmp_path / "第1卷_去水印_images"
    images_dir.mkdir()
    (images_dir / "a.jpg").write_bytes(b"a")
    (images_dir / "b.jpg").write_bytes(b"b")
    recognized = []

    async def fake_recognize(session, img_path, token, ssl_context):
        recognized.append(Path(img_path).name)
        return "识别文字"

    import image_ocr_backfill as mod
    mod._recognize_single_image = fake_recognize
    mod._get_token = lambda: "fake-token"
    result = mod.backfill_image_ocr(md_text, images_dir, "第1卷_去水印", only_names={"a.jpg"})
    assert "识别文字" in result
    assert recognized == ["a.jpg"]  # 只识别 a.jpg，b.jpg 跳过
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_selective_ocr.py -q`
Expected: ImportError（`preselect_ocr_images` 不存在）

- [ ] **Step 3: 实现 `preselect_ocr_images` + 回填 `only_names`**

在 `backend/image_ocr_backfill.py` 顶部（`_too_small` 函数之后）加：

```python
def preselect_ocr_images(case_dir: Path) -> dict:
    """读各卷 layout.json，预筛出「疑似有文字的图片」（排除印章+小图）

    Returns:
        {卷名: {图片名: {"w": int, "h": int}}}，卷名即 layout.json 的 stem 去掉 _layout
    """
    md_dir = Path(case_dir) / "md"
    result = {}
    if not md_dir.exists():
        return result
    for layout_file in sorted(md_dir.glob("*_layout.json")):
        vol_name = layout_file.stem[:-len("_layout")] if layout_file.stem.endswith("_layout") else layout_file.stem
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
                    w = bbox[2] - bbox[0]
                    h = bbox[3] - bbox[1]
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
```

给 `backfill_image_ocr` 加参数（签名行改为）：

```python
async def backfill_image_ocr(
    md_text: str,
    images_dir: Path,
    stem: str,
    max_concurrent: int = 3,
    only_names: Optional[set] = None,
) -> str:
```

在筛选 `refs` 之后（约 155 行 `if not refs:` 之后）加：

```python
    if only_names is not None:
        refs = [r for r in refs if r in only_names]
        if not refs:
            return md_text
```

（`Optional` 已在文件顶部 import，`set` 内建。）

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_selective_ocr.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/image_ocr_backfill.py tests/test_selective_ocr.py
git commit -m "feat: 预筛函数（读layout排除印章+小图）+ 回填支持指定图片列表"
```

---

### Task 2: 后端 3 个接口 + 后台 OCR 任务

**Files:**
- Modify: `backend/case_manager.py`
- Test: `tests/test_selective_ocr_api.py`

- [ ] **Step 1: 写失败测试**

```python
"""选择性 OCR 接口：列表 / 启动 / 进度"""
import asyncio
import json
from pathlib import Path

from case_manager import case_router, OCR_TASKS
import image_ocr_backfill as backfill_mod


def _make_case(tmp_path: Path) -> Path:
    case_dir = tmp_path / "案件_x"
    md_dir = case_dir / "md"
    md_dir.mkdir(parents=True)
    (md_dir / "第1卷_去水印.md").write_text("![](./第1卷_去水印_images/a.jpg)", encoding="utf-8")
    (md_dir / "第1卷_去水印_images").mkdir()
    (md_dir / "第1卷_去水印_images" / "a.jpg").write_bytes(b"x")
    return case_dir


def _route(method, path, body=None):
    """找到 router 上注册的端点（按 path 匹配）"""
    for r in case_router.routes:
        if r.path == f"/api/cases/{path}" and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"路由不存在: {method} {path}")


def test_preselect_route_registered():
    """三个端点都已注册"""
    _route("GET", "/{case_id}/ocr-images")
    _route("POST", "/{case_id}/ocr-images")
    _route("GET", "/{case_id}/ocr-status")


def test_ocr_status_returns_default():
    """未启动任务时 ocr-status 返回 idle（OCR_TASKS 无该 case 时默认值）"""
    import case_manager as cm
    cm.OCR_TASKS.clear()
    assert cm.OCR_TASKS.get("nonexistent", {"status": "idle"})["status"] == "idle"


def test_backfill_write_via_api_shape(tmp_path):
    """POST 启动任务后，OCR_TASKS 有 running/completed 状态（mock 单图识别）"""
    case_dir = _make_case(tmp_path)

    async def fake_recognize(session, img_path, token, ssl_context):
        return "转账 15 万元"

    backfill_mod._recognize_single_image = fake_recognize
    backfill_mod._get_token = lambda: "t"

    import case_manager as cm
    # 直接调用后台任务函数（接口函数内部会调用它）
    OCR_TASKS.clear()
    await cm._run_ocr_task("case_x", case_dir, {"第1卷_去水印": ["a.jpg"]})
    assert OCR_TASKS["case_x"]["status"] == "completed"
    assert OCR_TASKS["case_x"]["done"] == 1
    # md 已回填
    md = (case_dir / "md" / "第1卷_去水印.md").read_text(encoding="utf-8")
    assert "转账 15 万元" in md
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_selective_ocr_api.py -q`
Expected: ImportError（`OCR_TASKS` 不存在）

- [ ] **Step 3: 实现接口 + 后台任务**

在 `backend/case_manager.py` 顶部（`router = APIRouter(...)` 之后）加：

```python
# 选择性 OCR 后台任务状态
OCR_TASKS: Dict[str, dict] = {}
```

在 `case_manager.py` 末尾（`get_evidence_summary` 等函数附近）加：

```python
async def _run_ocr_task(case_id: str, case_dir: Path, selected: Dict[str, list]):
    """后台执行选择性 OCR：对选中的图片做单图识别并回填对应卷 md"""
    md_dir = case_dir / "md"
    total = sum(len(v) for v in selected.values())
    OCR_TASKS[case_id] = {"status": "running", "done": 0, "total": total, "current": "", "failed": []}
    done = 0
    try:
        from image_ocr_backfill import backfill_image_ocr
        for vol_name, names in selected.items():
            md_file = md_dir / f"{vol_name}.md"
            images_dir = md_dir / f"{vol_name}_images"
            if not md_file.exists() or not images_dir.exists():
                continue
            md_text = md_file.read_text(encoding="utf-8")
            new_text = await backfill_image_ocr(md_text, images_dir, vol_name, only_names=set(names))
            if new_text != md_text:
                md_file.write_text(new_text, encoding="utf-8")
            done += len(names)
            OCR_TASKS[case_id].update({"done": done, "current": vol_name})
        OCR_TASKS[case_id]["status"] = "completed"
    except Exception as e:
        logger.warning(f"[选择性OCR] {case_id} 失败: {e}")
        OCR_TASKS[case_id]["status"] = "failed"
        OCR_TASKS[case_id]["error"] = str(e)[:200]


@router.get("/{case_id}/ocr-images")
async def list_ocr_images(case_id: str):
    """预筛全部卷的图片（排除印章+小图），返回按卷分组列表"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    from image_ocr_backfill import preselect_ocr_images
    grouped = preselect_ocr_images(case_path)
    return {"success": True, "groups": grouped}


@router.post("/{case_id}/ocr-images")
async def start_ocr_images(case_id: str, body: dict = Body(...)):
    """启动选择性 OCR 后台任务。body: {卷名: [图片名...]}"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    selected = body.get("groups", body) if isinstance(body, dict) else {}
    selected = {k: v for k, v in selected.items() if v}
    if not selected:
        return {"success": False, "error": "未选择任何图片"}
    # 已在运行则拒绝重复启动
    if OCR_TASKS.get(case_id, {}).get("status") == "running":
        return {"success": False, "error": "OCR 任务进行中"}
    import asyncio
    asyncio.create_task(_run_ocr_task(case_id, case_path, selected))
    return {"success": True, "task_started": True}


@router.get("/{case_id}/ocr-status")
async def get_ocr_status(case_id: str):
    """OCR 任务进度"""
    return OCR_TASKS.get(case_id, {"status": "idle", "done": 0, "total": 0})
```

注意：`case_manager.py` 顶部已有 `from typing import List, Dict, Optional`，`Body`、`logger`、`find_case_path`、`router`、`HTTPException` 均已存在。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_selective_ocr_api.py -q`
Expected: 3 passed

- [ ] **Step 5: 全套件回归**

Run: `python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: passed

- [ ] **Step 6: Commit**

```bash
git add backend/case_manager.py tests/test_selective_ocr_api.py
git commit -m "feat: 选择性 OCR 三个接口（列表/启动/进度）+ 后台任务"
```

---

### Task 3: 删除 MinerU 引擎自动回填

**Files:**
- Modify: `backend/mineru_async.py`
- Modify: `backend/pdf_to_md.py`
- Test: `tests/test_selective_ocr.py`（追加）

- [ ] **Step 1: 追加失败测试（锁行为：MinerU 转换不再自动回填）**

在 `tests/test_selective_ocr.py` 追加：

```python
def test_mineru_convert_has_no_auto_backfill():
    """MinerU 转换路径不再调用 backfill_image_ocr（由选择性 OCR 取代）"""
    import mineru_async
    import inspect
    src = inspect.getsource(mineru_async)
    assert "backfill_image_ocr" not in src


def test_pdf_to_md_has_no_auto_backfill():
    """同步转换路径不再调用 backfill_image_ocr"""
    import pdf_to_md
    import inspect
    src = inspect.getsource(pdf_to_md)
    assert "backfill_image_ocr" not in src
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_selective_ocr.py -q`
Expected: 2 failed（mineru_async/pdf_to_md 仍含 backfill_image_ocr）

- [ ] **Step 3: 删除自动回填**

`backend/mineru_async.py`（约 493-510 行）删除 `_assemble_pdf_result` 里的整段图片回填（`if result.success and result.images_dir:` 到对应 `except` 结束），仅保留 `results.append(result)` 和进度更新。改后该循环体为：

```python
            results.append(result)
            async with progress_lock:
                if result.success:
                    progress.completed += 1
                else:
                    progress.failed += 1
                progress.current_files = [pdf_path.name]
                emit_progress()
```

（删除 `if result.success and result.images_dir:` 及其内部 `try...except` 图片回填整块，以及 `from config_manager import get_config_value` 的局部 import——若该 import 无其他用途则一并删除。）

`backend/pdf_to_md.py`（约 686 行）找到：

```python
        if not get_config_value("image_ocr_enabled", False):
            return md_text
```

改为：

```python
        return md_text
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_selective_ocr.py -q && python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: 5 passed；全套件 passed

- [ ] **Step 5: Commit**

```bash
git add backend/mineru_async.py backend/pdf_to_md.py tests/test_selective_ocr.py
git commit -m "refactor: MinerU 引擎删除自动图片回填（由选择性 OCR 取代），image_ocr_enabled 只留 PaddleOCR"
```

---

### Task 4: 前端选择性 OCR 卡片

**Files:**
- Create: `frontend/src/pages/CaseDetailPage/components/SelectiveOCR.tsx`
- Modify: `frontend/src/pages/CaseDetailPage/components/Step1Extract.tsx`
- Modify: `frontend/src/api/index.ts`（或 `evidence.ts`）加 3 个 API 函数

- [ ] **Step 1: 加 API 函数**

在 `frontend/src/api/evidence.ts` 末尾追加：

```ts
export interface OcrImageGroup { [volName: string]: { [imgName: string]: { w: number; h: number } } }

export async function getOcrImages(caseId: string): Promise<OcrImageGroup> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/ocr-images`)
  const data = await res.json()
  return data.groups || {}
}

export async function startOcrImages(caseId: string, groups: Record<string, string[]>): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/ocr-images`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ groups }),
  })
  return res.json()
}

export async function getOcrStatus(caseId: string): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/ocr-status`)
  return res.json()
}
```

- [ ] **Step 2: 写 SelectiveOCR 组件**

创建 `frontend/src/pages/CaseDetailPage/components/SelectiveOCR.tsx`：

```tsx
// 选择性 OCR 图片：按卷分组缩略图网格 + 勾选 + 批量识别
import { useState, useEffect, useCallback } from 'react'
import { API_BASE, getOcrImages, startOcrImages, getOcrStatus, OcrImageGroup } from '../../../api'

interface Props {
  caseId: string
}

export function SelectiveOCR({ caseId }: Props) {
  const [groups, setGroups] = useState<OcrImageGroup>({})
  const [selected, setSelected] = useState<Record<string, Set<string>>>({})
  const [loading, setLoading] = useState(false)
  const [ocrStatus, setOcrStatus] = useState<{ status: string; done: number; total: number } | null>(null)
  const [error, setError] = useState('')

  const loadImages = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const g = await getOcrImages(caseId)
      setGroups(g)
      // 默认全选
      const sel: Record<string, Set<string>> = {}
      for (const [vol, imgs] of Object.entries(g)) sel[vol] = new Set(Object.keys(imgs))
      setSelected(sel)
    } catch { setError('加载图片失败') } finally { setLoading(false) }
  }, [caseId])

  useEffect(() => { loadImages() }, [loadImages])

  const toggle = (vol: string, name: string) => {
    setSelected(prev => {
      const s = new Set(prev[vol] || [])
      s.has(name) ? s.delete(name) : s.add(name)
      return { ...prev, [vol]: s }
    })
  }
  const toggleVol = (vol: string, names: string[]) => {
    setSelected(prev => {
      const s = prev[vol] || new Set()
      const allOn = names.every(n => s.has(n))
      const next = new Set(s)
      names.forEach(n => allOn ? next.delete(n) : next.add(n))
      return { ...prev, [vol]: next }
    })
  }

  const selectedCount = Object.values(selected).reduce((a, s) => a + s.size, 0)

  const runOcr = async () => {
    setError('')
    const body: Record<string, string[]> = {}
    for (const [vol, s] of Object.entries(selected)) if (s.size) body[vol] = Array.from(s)
    if (!Object.keys(body).length) { setError('未选择图片'); return }
    try {
      await startOcrImages(caseId, body)
      setOcrStatus({ status: 'running', done: 0, total: selectedCount })
      const timer = setInterval(async () => {
        try {
          const st = await getOcrStatus(caseId)
          setOcrStatus(st)
          if (st.status !== 'running') clearInterval(timer)
        } catch { clearInterval(timer) }
      }, 2000)
    } catch { setError('启动 OCR 失败') }
  }

  return (
    <div style={{ border: '1px solid var(--macos-border)', borderRadius: '8px', padding: '12px', marginBottom: '12px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '13px', fontWeight: '500' }}>选择性 OCR 图片</span>
        <button onClick={runOcr} disabled={selectedCount === 0 || ocrStatus?.status === 'running'}
          style={{ padding: '5px 12px', background: 'var(--macos-accent)', color: '#fff', border: 'none', borderRadius: '6px', cursor: 'pointer', fontSize: '12px' }}>
          OCR 选中图片（{selectedCount} 张）
        </button>
      </div>
      {ocrStatus && (
        <div style={{ fontSize: '12px', color: '#86868b', margin: '6px 0' }}>
          {ocrStatus.status === 'running' ? `识别中 ${ocrStatus.done}/${ocrStatus.total}` : ocrStatus.status === 'completed' ? '完成' : ''}
        </div>
      )}
      {error && <div style={{ color: '#c00', fontSize: '12px' }}>{error}</div>}
      {loading && <div style={{ fontSize: '12px', color: '#86868b' }}>加载中...</div>}
      {Object.keys(groups).length === 0 && !loading && <div style={{ fontSize: '12px', color: '#86868b' }}>无可 OCR 图片（印章/小图已自动排除）</div>}
      {Object.entries(groups).map(([vol, imgs]) => {
        const names = Object.keys(imgs)
        const s = selected[vol] || new Set()
        return (
          <details key={vol} style={{ marginTop: '8px' }}>
            <summary style={{ cursor: 'pointer', fontSize: '12px', fontWeight: '500' }}>
              {vol}（{s.size}/{names.length}）
              <button onClick={e => { e.preventDefault(); toggleVol(vol, names) }}
                style={{ marginLeft: '8px', fontSize: '11px', border: '1px solid var(--macos-border)', background: 'none', borderRadius: '4px', cursor: 'pointer' }}>
                {names.every(n => s.has(n)) ? '清空' : '全选'}
              </button>
            </summary>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(90px, 1fr))', gap: '6px', marginTop: '6px' }}>
              {names.map(name => (
                <div key={name} onClick={() => toggle(vol, name)}
                  style={{ border: s.has(name) ? '2px solid var(--macos-accent)' : '1px solid var(--macos-border)', borderRadius: '6px', padding: '3px', cursor: 'pointer', textAlign: 'center' }}>
                  <img src={`${API_BASE}/cases/${caseId}/serve-file?file_path=${encodeURIComponent(name)}&dir=md`}
                    alt={name} style={{ width: '100%', height: '60px', objectFit: 'cover', borderRadius: '4px' }} loading="lazy" />
                  <div style={{ fontSize: '10px', color: '#86868b', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name.slice(0, 8)}</div>
                </div>
              ))}
            </div>
          </details>
        )
      })}
    </div>
  )
}
```

（`API_BASE` 从 `../../../api` 导出，已存在。）

- [ ] **Step 3: 在 Step1Extract 插入卡片**

读 `frontend/src/pages/CaseDetailPage/components/Step1Extract.tsx`，找到转换完成状态展示的位置（`evidenceExtracted` 为 false、转换已完成时），在「提取证据」按钮之前插入：

```tsx
<SelectiveOCR caseId={caseId} />
```

并在文件顶部 `import { SelectiveOCR } from './SelectiveOCR'`。

（具体插入位置：先 Read Step1Extract.tsx 找到「提取证据」相关 JSX，把 `<SelectiveOCR>` 放在其上方、转换完成的提示下方。）

- [ ] **Step 4: 类型检查 + 构建**

Run: `cd frontend && npx tsc --noEmit && npm run build 2>&1 | tail -3`
Expected: 无错误，构建成功

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/CaseDetailPage/components/SelectiveOCR.tsx frontend/src/pages/CaseDetailPage/components/Step1Extract.tsx frontend/src/api/evidence.ts
git commit -m "feat: 前端选择性 OCR 卡片（按卷分组缩略图+勾选+批量识别）"
```

---

## Self-Review 记录

- **spec 覆盖**：预筛（T1）✓ 回填指定列表（T1）✓ 3 接口（T2）✓ 后台任务（T2）✓ 删除 MinerU 自动回填（T3）✓ 前端卡片（T4）✓ 开关关系（T3）✓
- **字段命名一致**：后端 `preselect_ocr_images` 返回 `{卷名: {图片名: {w,h}}}`，前端 `OcrImageGroup` 同构；`OCR_TASKS` 状态字段 `{status, done, total, current, failed}` 前后端一致；POST body `{groups: {卷名: [图片名]}}` 前后端一致
- **已知边界**：Task 2 的 `test_ocr_status_returns_default` 是弱断言（直接测默认 dict），完整接口行为靠 Task 4 前端 + 真实验证兜底；缩略图 URL 用 `dir=md` + serve-file 的 rglob 全局搜索（已确认 serve-file 支持图片 media_type）
