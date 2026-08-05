# 工作流重构 阶段 4 实现计划（改动 B：4.75 辩护思路确认卡点 + 界面）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 辩护思路由律师确认后驱动辩护意见生成：系统生成结构化建议 → 流水线进入待确认状态 → 前端确认面板（勾选/编辑/补充）→ 确认稿注入步骤 5；报告页新增辩护思路 tab；案件级检索关键词编辑区

**设计文档:** `docs/superpowers/specs/2026-07-31-analysis-workflow-redesign-design.md` 第 4、9 节

**分支:** `feat/analysis-workflow-redesign`（沿用）

**探查结论：**
- 状态机：`analysis_state.json` DEFAULT_STATE steps 1-5 + 4.5；`_get_next_unfinished_step`（:464）顺序 1→2→3→4→4.5→5
- 端点：`pipeline_api.py` 的 run_pipeline_step（:85，valid_steps={1,2,3,4,4.5,5}）和 resume（:219）
- step5（:1705）：从 Wiki 读结论/矛盾/法律依据/证据摘要 + 控辩对抗，逐节生成（01-06）存 `05-辩护意见/`
- 4.5 产物：`analysis/04.5-控辩对抗/对抗分析.md`

---

### Task 1: 后端 step475 + 状态机 + API 端点

**Files:**
- Modify: `backend/analysis_pipeline.py`（DEFAULT_STATE + _get_next_unfinished_step + step475_defense_strategy + confirm_defense_strategy）
- Modify: `backend/pipeline_api.py`（valid_steps 加 4.75 + 两个新端点）
- Test: `tests/test_defense_strategy.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_defense_strategy.py
"""改动 B：4.75 辩护思路确认卡点"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import analysis_pipeline
from analysis_pipeline import AnalysisPipeline


def _make_pipeline(tmp_path: Path) -> AnalysisPipeline:
    """构造带 Wiki 与控辩对抗产物的 pipeline"""
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    wiki = analysis_dir / "indictment_wiki"
    wiki.mkdir(parents=True)
    (wiki / "06-综合结论.md").write_text("综合结论：证据链存在重大缺口", encoding="utf-8")
    (wiki / "05-矛盾记录.md").write_text("矛盾：供述前后不一", encoding="utf-8")
    debate = analysis_dir / "04.5-控辩对抗"
    debate.mkdir(parents=True)
    (debate / "对抗分析.md").write_text("法官裁决：自首认定存疑", encoding="utf-8")
    # step1/2 产物（step5 前置检查用）
    (analysis_dir / "step_1_result.json").write_text(json.dumps({"merged_files": []}), encoding="utf-8")
    return AnalysisPipeline("case_001", case_path)


def test_generates_suggestion_and_awaits_confirmation(tmp_path):
    """生成结构化建议 + 状态置为待确认（非 completed）"""
    pipe = _make_pipeline(tmp_path)
    pipe.llm.chat = AsyncMock(return_value=json.dumps({
        "directions": [
            {"type": "主攻", "direction": "证据链断裂，事实不清证据不足", "basis": "供述矛盾且无物证印证", "risk": "法院可能采纳补强证据"},
            {"type": "备选", "direction": "自首情节", "basis": "自动投案", "risk": "供述不完整"},
        ]
    }, ensure_ascii=False))

    result = asyncio.run(pipe.step475_defense_strategy("张三", "盗窃罪"))

    assert result["awaiting_confirmation"] is True
    assert len(result["suggestion"]["directions"]) == 2
    # 系统建议落盘（JSON + MD 各一份）
    d = tmp_path / "case_001" / "analysis" / "04.75-辩护思路"
    assert (d / "系统建议.json").exists()
    assert (d / "系统建议.md").exists()
    assert "证据链断裂" in (d / "系统建议.md").read_text(encoding="utf-8")
    # 状态为待确认而非完成
    state = json.loads((tmp_path / "case_001" / "analysis" / "analysis_state.json").read_text(encoding="utf-8"))
    assert state["steps"]["4.75"]["status"] == "awaiting_confirmation"


def test_resume_skips_regeneration_when_awaiting(tmp_path):
    """待确认状态下重跑 4.75：直接返回已有建议，不重复调 LLM"""
    pipe = _make_pipeline(tmp_path)
    pipe.llm.chat = AsyncMock(return_value=json.dumps({"directions": [
        {"type": "主攻", "direction": "无罪辩护", "basis": "b", "risk": "r"}]}, ensure_ascii=False))
    asyncio.run(pipe.step475_defense_strategy("张三", "盗窃罪"))

    pipe.llm.chat = AsyncMock(side_effect=AssertionError("不应再调 LLM"))
    result = asyncio.run(pipe.step475_defense_strategy("张三", "盗窃罪"))
    assert result["awaiting_confirmation"] is True
    assert result["suggestion"]["directions"][0]["direction"] == "无罪辩护"


def test_confirm_writes_confirmation_and_marks_done(tmp_path):
    """确认：写思路确认.md（含用户补充与修改痕迹）+ 状态 completed"""
    pipe = _make_pipeline(tmp_path)
    pipe.llm.chat = AsyncMock(return_value=json.dumps({"directions": [
        {"type": "主攻", "direction": "无罪辩护", "basis": "b1", "risk": "r1"},
        {"type": "备选", "direction": "罪轻辩护", "basis": "b2", "risk": "r2"},
    ]}, ensure_ascii=False))
    asyncio.run(pipe.step475_defense_strategy("张三", "盗窃罪"))

    result = asyncio.run(pipe.confirm_defense_strategy(
        selected=[0],
        user_additions=["我认为排非是突破口（讯问超时）"],
        use_system_default=False,
    ))
    assert result["success"] is True

    content = (tmp_path / "case_001" / "analysis" / "04.75-辩护思路" / "思路确认.md").read_text(encoding="utf-8")
    assert "无罪辩护" in content           # 选中的系统建议
    assert "罪轻辩护" not in content.split("律师补充")[0]  # 未选中的不在（在补充之前）
    assert "排非是突破口" in content         # 律师补充
    assert "律师补充" in content

    state = json.loads((tmp_path / "case_001" / "analysis" / "analysis_state.json").read_text(encoding="utf-8"))
    assert state["steps"]["4.75"]["status"] == "completed"


def test_use_system_default(tmp_path):
    """一键采纳：全部建议 + 无补充"""
    pipe = _make_pipeline(tmp_path)
    pipe.llm.chat = AsyncMock(return_value=json.dumps({"directions": [
        {"type": "主攻", "direction": "无罪辩护", "basis": "b", "risk": "r"}]}, ensure_ascii=False))
    asyncio.run(pipe.step475_defense_strategy("张三", "盗窃罪"))
    result = asyncio.run(pipe.confirm_defense_strategy(use_system_default=True))
    content = (tmp_path / "case_001" / "analysis" / "04.75-辩护思路" / "思路确认.md").read_text(encoding="utf-8")
    assert "无罪辩护" in content
    assert result["success"] is True


def test_next_unfinished_step_includes_475(tmp_path):
    """4.5 完成后、4.75 未完成时，下一个步骤是 4.75"""
    pipe = _make_pipeline(tmp_path)
    # 标记 1-4、4.5 完成
    state = json.loads(json.dumps(analysis_pipeline.DEFAULT_STATE))
    for k in ["1", "2", "3", "4", "4.5"]:
        state["steps"][k]["status"] = "completed"
    (tmp_path / "case_001" / "analysis" / "analysis_state.json").write_text(
        json.dumps(state), encoding="utf-8")
    assert pipe._get_next_unfinished_step() == 4.75
    # 4.75 完成后下一个是 5
    state["steps"]["4.75"]["status"] = "completed"
    (tmp_path / "case_001" / "analysis" / "analysis_state.json").write_text(
        json.dumps(state), encoding="utf-8")
    assert pipe._get_next_unfinished_step() == 5
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/zhanghan/.openclaw/workspace/criminal-llm && python3 -m pytest tests/test_defense_strategy.py -v`
Expected: FAIL（step475_defense_strategy 不存在）

- [ ] **Step 3: 实现 analysis_pipeline.py**

（a）DEFAULT_STATE 的 steps 中 4.5 与 5 之间加：
```python
        "4.75": {"status": "idle", "completed_at": None},
```

（b）`_get_next_unfinished_step`：步骤 5 的检查逻辑（约 :474 的 `if step_num == 5` 分支）改为先查 4.5 再查 4.75：
```python
                if step_num == 5:
                    step4_data = state["steps"].get("4", {})
                    if step4_data.get("status") == "completed":
                        step45 = state["steps"].get("4.5", {})
                        if step45.get("status") != "completed":
                            return 4.5
                        step475 = state["steps"].get("4.75", {})
                        if step475.get("status") not in ("completed", "awaiting_confirmation"):
                            return 4.75
                        if step475.get("status") == "awaiting_confirmation":
                            return 4.75  # 返回以便 API 层提示待确认（step 方法内部直接返回已有建议）
                return step_num
```
注意末尾"全部完成后补 4.5"逻辑保持原样。

（c）新增两个方法（放在 step45_debate_simulation 之后、步骤 5 区之前）：

```python
    # ========== 步骤 4.75: 辩护思路确认 ==========

    def _strategy_dir(self) -> Path:
        return self.analysis_dir / "04.75-辩护思路"

    async def step475_defense_strategy(self, defendant: str, crime_type: Optional[str] = None, progress_cb=None) -> dict:
        """生成辩护思路建议并进入待确认状态。

        待确认状态下重跑直接返回已有建议（不重复调 LLM）。
        """
        strategy_dir = self._strategy_dir()
        strategy_dir.mkdir(parents=True, exist_ok=True)
        suggestion_json = strategy_dir / "系统建议.json"

        if suggestion_json.exists():
            try:
                suggestion = json.loads(suggestion_json.read_text(encoding="utf-8"))
                return {"awaiting_confirmation": True, "suggestion": suggestion}
            except Exception:
                pass  # 损坏则重新生成

        conclusion = self._load_wiki_page("", "06-综合结论.md")
        contradictions = self._load_wiki_page("", "05-矛盾记录.md")
        debate_file = self.analysis_dir / "04.5-控辩对抗" / "对抗分析.md"
        debate = debate_file.read_text(encoding="utf-8") if debate_file.exists() else ""

        raw = await self.llm.chat([
            {"role": "system", "content": """你是资深刑事辩护律师。基于案件分析结果提出辩护思路建议。
只输出严格 JSON：{"directions": [{"type": "主攻"|"备选", "direction": "方向简述", "basis": "依据（引用具体证据/矛盾点/裁判规则）", "risk": "风险点"}]}
主攻方向 1-2 个，备选方向 1-3 个。"""},
            {"role": "user", "content": f"""## 综合结论
{conclusion[:8000]}

## 矛盾记录
{contradictions[:5000]}

## 控辩对抗（法官裁决倾向）
{debate[:5000]}

被告人：{defendant}；罪名：{crime_type or '未知'}"""},
        ])

        m = re.search(r"\{.*\}", raw, re.S)
        suggestion = json.loads(m.group(0)) if m else {"directions": []}
        suggestion.setdefault("directions", [])

        suggestion_json.write_text(json.dumps(suggestion, ensure_ascii=False, indent=2), encoding="utf-8")
        (strategy_dir / "系统建议.md").write_text(self._render_suggestion_md(suggestion), encoding="utf-8")

        # 状态：待确认（不是 completed）
        state = self._load_analysis_state()
        state["steps"].setdefault("4.75", {})["status"] = "awaiting_confirmation"
        self._save_analysis_state(state)

        return {"awaiting_confirmation": True, "suggestion": suggestion}

    def _render_suggestion_md(self, suggestion: dict) -> str:
        lines = ["# 辩护思路建议（系统生成）\n"]
        for i, d in enumerate(suggestion.get("directions", [])):
            lines.append(f"## {i + 1}. [{d.get('type', '备选')}] {d.get('direction', '')}\n")
            lines.append(f"- 依据：{d.get('basis', '')}")
            lines.append(f"- 风险：{d.get('risk', '')}\n")
        return "\n".join(lines)

    async def confirm_defense_strategy(
        self,
        selected: list[int] | None = None,
        edited: dict | None = None,
        user_additions: list[str] | None = None,
        use_system_default: bool = False,
    ) -> dict:
        """确认辩护思路：写思路确认.md（含修改痕迹），状态置 completed。

        - selected: 选中的建议下标（从 0 开始）；None 且非 default 视为空选择
        - edited: {下标: 修改后的方向文本}
        - user_additions: 律师补充的思路列表
        - use_system_default: 一键采纳全部建议
        """
        suggestion_json = self._strategy_dir() / "系统建议.json"
        if not suggestion_json.exists():
            raise ValueError("尚未生成辩护思路建议，请先执行步骤 4.75")
        suggestion = json.loads(suggestion_json.read_text(encoding="utf-8"))
        directions = suggestion.get("directions", [])

        # 应用律师修改（先改后选）
        edited = edited or {}
        for idx, new_text in edited.items():
            i = int(idx)
            if 0 <= i < len(directions):
                directions[i] = {**directions[i], "direction": new_text, "_edited": True}

        if use_system_default:
            chosen = [(i, d) for i, d in enumerate(directions)]
        else:
            chosen = [(i, d) for i, d in enumerate(directions) if i in (selected or [])]

        additions = user_additions or []
        if not chosen and not additions:
            # 空确认视为采纳系统建议
            chosen = [(i, d) for i, d in enumerate(directions)]

        lines = ["# 辩护思路（律师已确认）\n", "## 采纳的方向\n"]
        for i, d in chosen:
            edited_mark = "（律师已修改）" if d.get("_edited") else ""
            lines.append(f"- **[{d.get('type', '备选')}] {d.get('direction', '')}**{edited_mark}")
            lines.append(f"  依据：{d.get('basis', '')}；风险：{d.get('risk', '')}")
        if additions:
            lines.append("\n## 律师补充\n")
            for a in additions:
                lines.append(f"- {a}")

                (self._strategy_dir() / "思路确认.md").write_text("\n".join(lines), encoding="utf-8")

        state = self._load_analysis_state()
        state["steps"].setdefault("4.75", {})["status"] = "completed"
        state["steps"]["4.75"]["completed_at"] = datetime.now().isoformat()
        self._save_analysis_state(state)
        return {"success": True, "chosen_count": len(chosen), "additions_count": len(additions)}

    def get_defense_strategy(self) -> dict:
        """供 API 读取：系统建议 + 确认稿 + 状态"""
        suggestion = {}
        suggestion_json = self._strategy_dir() / "系统建议.json"
        if suggestion_json.exists():
            try:
                suggestion = json.loads(suggestion_json.read_text(encoding="utf-8"))
            except Exception:
                pass
        confirmation_file = self._strategy_dir() / "思路确认.md"
        confirmation = confirmation_file.read_text(encoding="utf-8") if confirmation_file.exists() else None
        status = self._load_analysis_state()["steps"].get("4.75", {}).get("status", "idle")
        return {"suggestion": suggestion, "confirmation": confirmation, "status": status}
```

（`re`、`json`、`datetime` 顶部已导入。）

（d）`pipeline_api.py`：
- `run_pipeline_step` 和 `resume_pipeline` 的 `valid_steps` / `step_methods` 加 4.75：`4.75: lambda: pipeline.step475_defense_strategy(defendant, charges[0] if charges else None, progress_cb=...)`；docstring 步骤说明加 4.75
- 新端点：

```python
@router.get("/{case_id}/defense-strategy")
async def get_defense_strategy(case_id: str):
    """辩护思路：系统建议 + 确认稿 + 状态"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    pipeline = AnalysisPipeline(case_id, case_path)
    return pipeline.get_defense_strategy()


class ConfirmStrategyRequest(BaseModel):
    selected: Optional[List[int]] = None
    edited: Optional[dict] = None
    user_additions: Optional[List[str]] = None
    use_system_default: bool = False


@router.post("/{case_id}/defense-strategy/confirm")
async def confirm_defense_strategy(case_id: str, req: ConfirmStrategyRequest):
    """确认辩护思路（律师确认后驱动步骤 5）"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    pipeline = AnalysisPipeline(case_id, case_path)
    try:
        result = await pipeline.confirm_defense_strategy(
            selected=req.selected,
            edited=req.edited,
            user_additions=req.user_additions,
            use_system_default=req.use_system_default,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**result, "next_step": pipeline._get_next_unfinished_step()}
```

（pipeline_api 的 `BaseModel`/`List`/`Optional` import 按文件现有情况补。）

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_defense_strategy.py -v` → `python3 -m pytest tests/ -q`（无回归）

- [ ] **Step 5: 提交**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm
git add backend/analysis_pipeline.py backend/pipeline_api.py tests/test_defense_strategy.py
git commit -m "feat: 4.75 辩护思路确认卡点（建议生成+待确认状态机+确认端点）"
```

---

### Task 2: 步骤 5 注入已确认辩护思路

**Files:**
- Modify: `backend/analysis_pipeline.py`（step5_defense_opinion）
- Test: `tests/test_defense_strategy.py`（追加）

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_step5_injects_confirmed_strategy(tmp_path):
    """步骤 5 各节 prompt 头部注入已确认辩护思路"""
    pipe = _make_pipeline(tmp_path)
    # 预置 Wiki 与确认稿
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki"
    (wiki / "01-指控要素.md").write_text("指控要素", encoding="utf-8")
    strategy_dir = tmp_path / "case_001" / "analysis" / "04.75-辩护思路"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "思路确认.md").write_text("# 辩护思路（律师已确认）\n\n主攻：排非", encoding="utf-8")

    captured = []

    async def fake_chat(messages, **kw):
        captured.append(messages[-1]["content"])
        return "小节内容"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step5_defense_opinion("张三", "盗窃罪"))

    assert captured, "步骤 5 应有 LLM 调用"
    for prompt in captured:
        assert "辩护思路（律师已确认" in prompt
        assert "主攻：排非" in prompt
        assert "律师补充的思路优先级最高" in prompt


def test_step5_without_strategy_unchanged(tmp_path):
    """无确认稿：prompt 不含辩护思路段（向后兼容）"""
    pipe = _make_pipeline(tmp_path)
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki"
    (wiki / "01-指控要素.md").write_text("指控要素", encoding="utf-8")

    captured = []

    async def fake_chat(messages, **kw):
        captured.append(messages[-1]["content"])
        return "小节内容"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step5_defense_opinion("张三", "盗窃罪"))
    for prompt in captured:
        assert "辩护思路（律师已确认" not in prompt
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_defense_strategy.py::test_step5_injects_confirmed_strategy -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`step5_defense_opinion` 中 Wiki 读取区之后加：
```python
        # 辩护思路（4.75 律师确认稿，存在则注入每节 prompt）
        strategy_file = self.analysis_dir / "04.75-辩护思路" / "思路确认.md"
        strategy_context = ""
        if strategy_file.exists():
            strategy_context = (
                "辩护思路（律师已确认，必须遵循；律师补充的思路优先级最高，与系统建议冲突时以律师为准）：\n"
                + strategy_file.read_text(encoding="utf-8")[:3000]
            )
```
然后找到各节 prompt 构建处（先读代码确认每节 user prompt 的组装方式），在每节 user prompt 的最前面加 `{strategy_context}\n\n`（strategy_context 为空时与原 prompt 逐字一致——用 f-string 条件拼接，确保空串时不留多余空行）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_defense_strategy.py -v` → `python3 -m pytest tests/ -q`（无回归）

- [ ] **Step 5: 提交**

```bash
git add backend/analysis_pipeline.py tests/test_defense_strategy.py
git commit -m "feat: 步骤5注入律师确认的辩护思路"
```

---

### Task 3: CaseDetailPage 确认面板 + 关键词编辑区

**Files:**
- Create: `frontend/src/components/DefenseStrategyPanel.tsx`
- Modify: `frontend/src/pages/CaseDetailPage`（或其子组件/ hooks，挂载确认面板 + 关键词编辑区）
- Modify: `frontend/src/api/pipeline.ts`（新 API 函数）

- [ ] **Step 1: API 函数（pipeline.ts）**

```ts
export interface StrategyDirection {
  type: string
  direction: string
  basis: string
  risk: string
}

export interface DefenseStrategy {
  suggestion: { directions: StrategyDirection[] }
  confirmation: string | null
  status: string  // idle | awaiting_confirmation | completed
}

export async function getDefenseStrategy(caseId: string): Promise<DefenseStrategy> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/defense-strategy`)
  return res.json()
}

export async function confirmDefenseStrategy(
  caseId: string,
  body: { selected?: number[]; edited?: Record<string, string>; user_additions?: string[]; use_system_default?: boolean },
): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/defense-strategy/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `请求失败（${res.status}）`)
  return res.json()
}

export async function runPipelineStep(caseId: string, stepNum: number, defendant: string, charges: string[]): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/step/${stepNum}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ defendant, charges }),
  })
  return res.json()
}
```
（先读 pipeline.ts 现有函数，runPipelineStep 若已有则复用不重复。）

- [ ] **Step 2: DefenseStrategyPanel.tsx**

确认面板组件（Props: caseId, defendant, charges, onConfirmed）。行为：
- mount 时 getDefenseStrategy；status === 'awaiting_confirmation' 才渲染（否则 null）
- 展示系统建议列表：每条 [类型徽标] 方向文本（可点击编辑为 textarea）、依据、风险、勾选框（aria-label）
- "补充自己的思路"：文本框 + [添加] 按钮 → additions 列表（可删）
- 底部：[确认并继续分析]（confirmDefenseStrategy(selected/edited/user_additions) → 成功后调 runPipelineStep(caseId, 5, ...) 或直接调 resume——先读现有代码确认步骤 5 的触发方式，与其保持一致）/ [全部采纳并继续]（use_system_default: true）
- 确认中 loading 态；失败用页面既有错误提示方式

样式参照 CaseSearchPanel/DocTypeBadge 的 macOS 令牌（从 components/report/reportColors.ts 导入 colors）。

- [ ] **Step 3: CaseDetailPage 挂载 + 关键词编辑区**

（a）挂载面板：分析工作流区域（读 CaseDetailPage 找步骤指示器/进度展示处），在步骤指示器下方插入 `<DefenseStrategyPanel ... />`（条件渲染由组件内部按 status 控制）。

（b）关键词编辑区（spec 9.1）：分析启动区附近加"类案检索关键词"tag 编辑器：
- mount 时读案件 case.json（GET 案件详情接口里的 charges 等字段——先读现有案件详情 API 返回，确认 charges/search_keywords/suggested_keywords 是否已透出；没有则在 case_manager 案件详情端点补返回）
- 预填：search_keywords ?? suggested_keywords ?? charges
- tag 增删，保存：写 case.json search_keywords（用现有案件更新接口或新端点，先查 case_manager 有无 PATCH/PUT case 接口）

- [ ] **Step 4: 验证**

```bash
cd frontend && npx tsc --noEmit && npm run build
```

- [ ] **Step 5: 提交**

```bash
git add frontend/src/
git commit -m "feat: 辩护思路确认面板 + 案件检索关键词编辑区"
```

---

### Task 4: ReportPage 辩护思路 tab + 标签顺序调整

**Files:**
- Modify: `frontend/src/pages/ReportPage.tsx`

- [ ] **Step 1: 标签调整**

TABS 数组（约 :55-65）当前顺序：指控要素、人物关系、事件拆解、法律法规、证据列表、矛盾分析、控辩对抗、三阶层分析、完整报告。在控辩对抗与三阶层分析之间插入：
```tsx
  { key: 'defense_strategy', label: '辩护思路', icon: Target, color: '#8e5a2a', bgColor: 'rgba(142,90,42,0.08)' },
```
（icon 从 lucide-react 选现有的；颜色与现有调色板协调。）

- [ ] **Step 2: 内容渲染**

新增渲染分支（activeTab === 'defense_strategy'）：调 getDefenseStrategy(caseId)，展示确认稿（markdown 渲染走现有 marked+DOMPurify 路径或纯文本 pre-wrap）；确认稿不存在且 status 为 awaiting_confirmation 时显示"待确认"提示 + 跳转案件详情页链接；completed 时提供"重新编辑"按钮（点击调 getDefenseStrategy 拿到 suggestion，内嵌 DefenseStrategyPanel 的编辑态或跳转案件详情页确认面板——取实现简单的：跳案件详情页）。

- [ ] **Step 3: 验证 + 提交**

```bash
cd frontend && npx tsc --noEmit && npm run build
git add frontend/src/pages/ReportPage.tsx
git commit -m "feat: 报告页新增辩护思路 tab"
```

---

### Task 5: 端到端冒烟（人工检查点）

- [ ] **Step 1:** 钱江案（已有全部分析）：删除 `analysis/04.75-辩护思路`（如有）并将 analysis_state.json 的 4.75 置 idle，跑 step 4.75 生成建议 → 确认（加一条律师补充）→ 检查思路确认.md → 只重跑步骤 5 的 01-案件概述 一节验证注入（不重跑全部六节，控制成本）
- [ ] **Step 2:** 前端确认面板交互 + ReportPage 辩护思路 tab 展示

---

## 执行顺序

Task 1（后端卡点）→ Task 2（步骤 5 注入）→ Task 3（确认面板+关键词）→ Task 4（报告页 tab）→ Task 5（冒烟）
