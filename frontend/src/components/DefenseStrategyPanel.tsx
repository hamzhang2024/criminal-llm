// 辩护思路确认面板（步骤 4.75）
// awaiting_confirmation：律师可勾选/编辑系统建议、补充自己的思路，确认后自动触发步骤 5
// completed：以编辑态渲染（预填确认稿），可修改后重新确认（将重跑步骤 5）
// 步骤 5/6 运行中（含本会话刚确认）：只读状态条展示确认内容，避免误以为要重新决策

import { useCallback, useEffect, useRef, useState } from 'react'
import { Scale, Loader2, Plus, X } from 'lucide-react'
import {
  getDefenseStrategy,
  confirmDefenseStrategy,
  runPipelineStep,
} from '../api/pipeline'
import type { DefenseStrategy } from '../api/pipeline'
import { colors } from './report/reportColors'

interface DefenseStrategyPanelProps {
  caseId: string
  defendant: string
  charges: string[]
  onConfirmed?: () => void
  refreshKey?: number  // 变化时重新拉取辩护思路状态
  skipRunStep5?: boolean  // true 时确认后不跑流水线步骤5（stage 流由 onConfirmed 接管后续阶段）
  stageRunning?: boolean  // 步骤 5/6 正在运行（切界面回来后由父组件传入），面板改为只读状态条
}

// 类型徽标配色：主攻=金，备选=灰蓝
function typeBadgeStyle(type: string): { background: string; color: string; border: string } {
  if (type === '主攻') {
    return { background: colors.goldBg, color: colors.gold, border: `1px solid ${colors.goldBorder}` }
  }
  return { background: 'rgba(142,142,147,0.12)', color: '#8e8e93', border: '1px solid rgba(142,142,147,0.2)' }
}

// 从确认稿 Markdown 解析已采纳的方向文本和律师补充（用于 completed 编辑态预填）
function parseConfirmation(md: string | null): { directions: string[]; additions: string[] } {
  if (!md) return { directions: [], additions: [] }
  const directions: string[] = []
  const additions: string[] = []
  let inAdditions = false
  for (const line of md.split('\n')) {
    if (/^##\s*律师补充/.test(line)) { inAdditions = true; continue }
    if (/^##\s/.test(line)) { inAdditions = false; continue }
    const mDir = line.match(/^- \*\*\[(?:主攻|备选)\]\s*(.+?)\*\*/)
    if (!inAdditions && mDir) { directions.push(mDir[1].trim()); continue }
    const mAdd = line.match(/^- (.+)/)
    if (inAdditions && mAdd) additions.push(mAdd[1].trim())
  }
  return { directions, additions }
}

// 草稿：未确认的勾选/编辑/补充持久化到 localStorage（key 含 caseId），防止切界面丢失
interface StrategyDraft {
  selected?: number[]  // 勾选的建议下标；旧版草稿无此字段（兼容缺失）
  editedDirections: Record<number, string>
  additions: string[]
  newAddition: string
}

// 读取草稿；空草稿视为无草稿（避免覆盖确认稿预填）
function readDraft(key: string): StrategyDraft | null {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const d = JSON.parse(raw) as Partial<StrategyDraft>
    const draft: StrategyDraft = {
      selected: Array.isArray(d.selected) ? d.selected : undefined,
      editedDirections: d.editedDirections || {},
      additions: d.additions || [],
      newAddition: d.newAddition || '',
    }
    const hasSelection = draft.selected !== undefined && draft.selected.length > 0
    if (!hasSelection && Object.keys(draft.editedDirections).length === 0 && draft.additions.length === 0 && !draft.newAddition.trim()) return null
    return draft
  } catch { return null }
}

// 写入草稿；内容为空时清除 key
function writeDraft(key: string, draft: StrategyDraft) {
  try {
    const hasSelection = draft.selected !== undefined && draft.selected.length > 0
    if (!hasSelection && Object.keys(draft.editedDirections).length === 0 && draft.additions.length === 0 && !draft.newAddition.trim()) {
      localStorage.removeItem(key)
    } else {
      localStorage.setItem(key, JSON.stringify(draft))
    }
  } catch { /* ignore */ }
}

export default function DefenseStrategyPanel({ caseId, defendant, charges, onConfirmed, refreshKey, skipRunStep5, stageRunning }: DefenseStrategyPanelProps) {
  const [strategy, setStrategy] = useState<DefenseStrategy | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  // 编辑后的方向文本（下标 → 新文本），未编辑的不出现
  const [editedDirections, setEditedDirections] = useState<Record<number, string>>({})
  const [additions, setAdditions] = useState<string[]>([])
  const [newAddition, setNewAddition] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [progressMsg, setProgressMsg] = useState('')
  const [error, setError] = useState('')
  // 本次会话内刚确认过：隐藏面板，避免步骤 5 运行期间重复确认
  const [justConfirmed, setJustConfirmed] = useState(false)

  const draftKey = `defense_strategy_draft_${caseId}`
  // 标记后端数据已加载到哪个案件，防止 caseId 切换瞬间把旧草稿写入新案件
  const loadedCaseRef = useRef('')

  useEffect(() => {
    let cancelled = false
    getDefenseStrategy(caseId)
      .then(data => {
        if (cancelled) return
        setStrategy(data)
        loadedCaseRef.current = caseId  // 仅成功路径标记：失败时保持旧值，避免草稿 effect 走 removeItem 分支误删既有草稿
        const directions = data.suggestion?.directions || []
        const draft = readDraft(draftKey)  // 本地草稿（未确认的编辑成果），优先于后端解析值
        if (data.status === 'awaiting_confirmation') {
          setJustConfirmed(false)
          // 默认全部勾选；草稿含勾选状态时以草稿为准
          setSelected(draft?.selected !== undefined ? new Set(draft.selected) : new Set(directions.map((_, i) => i)))
          if (draft) {
            setEditedDirections(draft.editedDirections)
            setAdditions(draft.additions)
            setNewAddition(draft.newAddition)
          } else {
            setEditedDirections({})
            setAdditions([])
          }
        } else if (data.status === 'completed') {
          // 已确认 → 编辑态预填：勾选状态与编辑文本从确认稿恢复
          const parsed = parseConfirmation(data.confirmation)
          const origs = directions.map(d => d.direction)
          const consumed = new Set<number>()                    // 已被确认稿命中的建议下标
          const pairs: Array<{ idx: number; text: string }> = [] // 建议下标 ↔ 确认稿方向文本
          // 第一轮：文本精确匹配，记录每个确认稿位置命中的建议下标（-1 = 未命中，即被律师改写过）
          const matchIdx = parsed.directions.map(t => {
            const j = origs.findIndex((o, k) => !consumed.has(k) && o === t)
            if (j >= 0) { consumed.add(j); pairs.push({ idx: j, text: t }) }
            return j
          })
          if (parsed.directions.length === 0) {
            // 确认稿为空（null 或无方向）→ 回退全选
            // 与后端"空确认视为采纳系统建议"语义对齐，避免 UI 呈现全不勾、提交却全采纳的矛盾
            directions.forEach((_, i) => consumed.add(i))
          } else if (pairs.length === 0) {
            // 一条都匹配不上（全被改写）→ 回退全选，按位置一一对应
            directions.forEach((_, i) => consumed.add(i))
            parsed.directions.forEach((t, n) => { if (n < directions.length) pairs.push({ idx: n, text: t }) })
          } else {
            // 第二轮：未命中（改写）文本按"递增约束"归并到剩余建议下标
            // 后端按勾选下标升序写入确认稿，故确认稿第 N 条的真实下标必大于第 N-1 条。
            // 旧实现把未命中文本按序配给 rest 升序下标，在"反选+改写"交错时错配：
            //   建议 [A,B,C,D]，反选 A、改写 C→C'，确认稿 [B,C',D] → C' 被错配给下标 0（A）。
            // 归并规则：遍历确认稿位置，遇精确匹配位置时丢弃 rest 中小于该下标的项；
            // 未命中位置取 rest 中满足递增约束的最小下标（rest 升序，队首即所求）。
            // 同上例：B 精确命中下标 1 → 丢弃 rest 中 <1 的 0 → C' 正确配给下标 2（C）。
            const rest = origs.map((_, k) => k).filter(k => !consumed.has(k))
            parsed.directions.forEach((t, n) => {
              const j = matchIdx[n]
              if (j >= 0) {
                while (rest.length > 0 && rest[0] < j) rest.shift()  // 小于已确定下标的项不可能再被命中
              } else if (rest.length > 0) {
                const idx = rest.shift()!
                consumed.add(idx)
                pairs.push({ idx, text: t })
              }
            })
          }
          // 勾选状态：草稿含 selected 时优先（用户最新未保存的勾选），否则用确认稿推导值
          setSelected(draft?.selected !== undefined ? new Set(draft.selected) : new Set(consumed))
          // 编辑文本恢复：确认稿文本与对应建议原文不同即为律师修改（不要求长度相等）
          const edited: Record<number, string> = {}
          for (const p of pairs) { if (p.text !== origs[p.idx]) edited[p.idx] = p.text }
          // 草稿优先于确认稿解析值（草稿是用户最新未保存的劳动成果）
          setEditedDirections(draft ? draft.editedDirections : edited)
          setAdditions(draft ? draft.additions : parsed.additions)
          if (draft) setNewAddition(draft.newAddition)
        }
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoaded(true) })
    return () => { cancelled = true }
  }, [caseId, refreshKey])

  // 草稿持久化：勾选/编辑方向/补充/新补充输入变化即写入 localStorage
  useEffect(() => {
    if (loadedCaseRef.current !== caseId) return  // 当前案件数据未加载完，不写
    writeDraft(draftKey, { selected: Array.from(selected).sort((a, b) => a - b), editedDirections, additions, newAddition })
  }, [caseId, draftKey, selected, editedDirections, additions, newAddition])

  const toggle = useCallback((idx: number) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }, [])

  const addAddition = useCallback(() => {
    const v = newAddition.trim()
    if (!v) return
    setAdditions(prev => [...prev, v])
    setNewAddition('')
  }, [newAddition])

  // 确认成功后触发步骤 5（与 useStageAnalysis.executePipelineStep 保持一致：runPipelineStep）
  const runStep5 = useCallback(async () => {
    setProgressMsg('正在生成辩护意见（步骤 5），耗时较长请耐心等待...')
    const result = await runPipelineStep(caseId, 5, defendant, charges)
    if (!result.success) throw new Error(result.detail || result.error || '步骤 5 执行失败')
  }, [caseId, defendant, charges])

  // 确认：始终提交律师的勾选/修改/补充（不再提供"全部采纳"捷径，避免丢失编辑成果）
  const handleConfirm = useCallback(async () => {
    if (confirming) return
    setConfirming(true)
    setError('')
    setProgressMsg('正在确认辩护思路...')
    try {
      const directions = strategy?.suggestion?.directions || []
      // 只提交真正被修改过的方向文本
      const edited: Record<string, string> = {}
      directions.forEach((d, i) => {
        const v = editedDirections[i]
        if (v !== undefined && v.trim() && v !== d.direction) edited[String(i)] = v.trim()
      })
      await confirmDefenseStrategy(caseId, {
        selected: Array.from(selected).sort((a, b) => a - b),
        edited,
        user_additions: additions,
      })
      // 确认成功：草稿已落库为确认稿，清除本地草稿
      try { localStorage.removeItem(draftKey) } catch { /* ignore */ }
      // stage 流：确认后由 onConfirmed 接管后续阶段（5/6），不跑流水线步骤 5
      if (!skipRunStep5) {
        await runStep5()
      }
      setProgressMsg('')
      // 本地置为已确认并隐藏面板，防止步骤 5 完成后重复确认
      setStrategy(prev => prev ? { ...prev, status: 'completed' } : prev)
      setJustConfirmed(true)
      onConfirmed?.()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '确认失败，请重试')
      setProgressMsg('')
    } finally {
      setConfirming(false)
    }
  }, [confirming, caseId, draftKey, strategy, editedDirections, selected, additions, runStep5, onConfirmed])

  // 待确认 / 已确认（可重新编辑）状态渲染
  if (!loaded || !strategy) return null
  if (strategy.status !== 'awaiting_confirmation' && strategy.status !== 'completed') return null

  const isCompleted = strategy.status === 'completed'
  const directions = strategy.suggestion?.directions || []

  // 步骤 5/6 运行中（含本会话刚确认）：只读状态条展示确认内容，不可编辑
  // 避免切界面回来后面板呈现"重新确认"编辑态，误导用户以为要重新决策
  if (justConfirmed || (isCompleted && stageRunning)) {
    // 已采纳方向的最终文本（勾选下标 + 律师修改后的文本）
    const confirmedDirections = directions
      .map((d, i) => ({ d, i }))
      .filter(({ i }) => selected.has(i))
      .map(({ d, i }) => ({ type: d.type, text: editedDirections[i] ?? d.direction }))
    return (
      <div style={{
        marginBottom: 12, padding: '16px', borderRadius: '10px',
        border: `1px solid ${colors.goldBorder}`, background: colors.goldBg,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <Scale className="w-4 h-4" style={{ color: colors.gold }} />
          <span style={{ fontSize: 14, fontWeight: 600, color: colors.textPrimary }}>辩护思路已确认</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: colors.gold, marginBottom: 12 }}>
          <Loader2 className="w-3 h-3 animate-spin" /> 辩护思路已确认，辩护意见生成中…
        </div>
        {confirmedDirections.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: additions.length > 0 ? 8 : 0 }}>
            {confirmedDirections.map((item, k) => {
              const badge = typeBadgeStyle(item.type)
              return (
                <div key={k} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: 12, color: colors.textPrimary, lineHeight: 1.6 }}>
                  <span style={{
                    flexShrink: 0, fontSize: 10, padding: '1px 6px', borderRadius: 4,
                    background: badge.background, color: badge.color, border: badge.border,
                    whiteSpace: 'nowrap', marginTop: 2,
                  }}>{item.type || '备选'}</span>
                  <span>{item.text}</span>
                </div>
              )
            })}
          </div>
        )}
        {additions.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: colors.textSecondary }}>律师补充</div>
            {additions.map((a, k) => (
              <div key={k} style={{ fontSize: 12, color: colors.textPrimary, lineHeight: 1.6 }}>{a}</div>
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{
      marginBottom: 12, padding: '16px', borderRadius: '10px',
      border: `1px solid ${colors.goldBorder}`, background: colors.goldBg,
    }}>
      {/* 标题 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <Scale className="w-4 h-4" style={{ color: colors.gold }} />
        <span style={{ fontSize: 14, fontWeight: 600, color: colors.textPrimary }}>
          {isCompleted ? '辩护思路已确认' : '辩护思路待确认'}
        </span>
        <span style={{ fontSize: 11, color: colors.textTertiary }}>
          {isCompleted ? '可修改后重新确认' : '系统已生成建议，请勾选、修改或补充后确认'}
        </span>
      </div>
      <div style={{ fontSize: 11, color: colors.textSecondary, marginBottom: 12 }}>
        {isCompleted
          ? '已确认 · 可修改后重新确认（将重跑步骤 5）'
          : '确认后将自动开始生成辩护意见（步骤 5）；律师补充的思路优先级最高'}
      </div>

      {/* 系统建议列表 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 12 }}>
        {directions.map((d, i) => {
          const badge = typeBadgeStyle(d.type)
          return (
            <div key={i} style={{
              padding: '10px 12px', borderRadius: 8,
              border: `1px solid ${selected.has(i) ? colors.goldBorder : colors.border}`,
              background: colors.surface,
            }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                <input
                  type="checkbox"
                  checked={selected.has(i)}
                  onChange={() => toggle(i)}
                  aria-label={`选择辩护思路 ${i + 1}：${d.direction}`}
                  style={{ marginTop: 3 }}
                />
                <span style={{
                  flexShrink: 0, fontSize: 10, padding: '1px 6px', borderRadius: 4,
                  background: badge.background, color: badge.color, border: badge.border,
                  whiteSpace: 'nowrap', marginTop: 2,
                }}>{d.type || '备选'}</span>
                <textarea
                  value={editedDirections[i] ?? d.direction}
                  onChange={e => setEditedDirections(prev => ({ ...prev, [i]: e.target.value }))}
                  aria-label={`编辑辩护思路 ${i + 1} 方向`}
                  rows={2}
                  style={{
                    flex: 1, fontSize: 12, color: colors.textPrimary, lineHeight: 1.6,
                    border: `1px solid ${colors.border}`, borderRadius: 6,
                    padding: '6px 8px', resize: 'vertical', background: colors.surfaceAlt,
                  }}
                />
              </div>
              <div style={{ fontSize: 11, color: colors.textSecondary, marginTop: 6, paddingLeft: 22 }}>
                依据：{d.basis}
              </div>
              <div style={{ fontSize: 11, color: colors.textTertiary, marginTop: 4, paddingLeft: 22 }}>
                风险：{d.risk}
              </div>
            </div>
          )
        })}
        {directions.length === 0 && (
          <div style={{ fontSize: 12, color: colors.textTertiary }}>系统未生成建议，可直接补充自己的思路后确认</div>
        )}
      </div>

      {/* 律师补充 */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: colors.textSecondary, marginBottom: 6 }}>补充自己的思路</div>
        {additions.map((a, i) => (
          <div key={i} style={{
            display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6,
            padding: '6px 10px', borderRadius: 6, background: colors.surface,
            border: `1px solid ${colors.border}`, fontSize: 12, color: colors.textPrimary,
          }}>
            <span style={{ flex: 1 }}>{a}</span>
            <button
              onClick={() => setAdditions(prev => prev.filter((_, j) => j !== i))}
              aria-label={`删除补充思路 ${i + 1}`}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: colors.textTertiary, padding: 2 }}
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        ))}
        <div style={{ display: 'flex', gap: 6 }}>
          <input
            value={newAddition}
            onChange={e => setNewAddition(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addAddition() } }}
            placeholder="如：排非是突破口（讯问超时）"
            aria-label="新增补充思路"
            style={{
              flex: 1, padding: '7px 10px', fontSize: 12, borderRadius: 6,
              border: `1px solid ${colors.border}`, color: colors.textPrimary, background: colors.surface,
            }}
          />
          <button
            onClick={addAddition}
            disabled={!newAddition.trim()}
            style={{
              display: 'flex', alignItems: 'center', gap: 4, padding: '6px 12px', fontSize: 12,
              borderRadius: 6, border: `1px solid ${colors.goldBorder}`, background: 'transparent',
              color: colors.gold, cursor: newAddition.trim() ? 'pointer' : 'not-allowed',
            }}
          >
            <Plus className="w-3 h-3" /> 添加
          </button>
        </div>
      </div>

      {/* 错误提示 */}
      {error && <div style={{ fontSize: 12, color: '#c62828', marginBottom: 10 }}>{error}</div>}
      {progressMsg && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: colors.gold, marginBottom: 10 }}>
          <Loader2 className="w-3 h-3 animate-spin" /> {progressMsg}
        </div>
      )}

      {/* 操作按钮：单一主按钮，始终提交勾选 + 修改 + 补充 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <button
          onClick={handleConfirm}
          disabled={confirming}
          style={{
            padding: '8px 16px', fontSize: 13, fontWeight: 500, borderRadius: 8, border: 'none',
            background: confirming ? colors.goldBorder : colors.gold, color: '#fff',
            cursor: confirming ? 'not-allowed' : 'pointer',
            display: 'flex', alignItems: 'center', gap: 6,
          }}
        >
          {confirming && <Loader2 className="w-3 h-3 animate-spin" />}
          {isCompleted ? '重新确认并重跑步骤 5' : '确认并继续分析'}
        </button>
        <span style={{ fontSize: 11, color: colors.textTertiary }}>
          默认全选，可反选不需要的方向；你的修改和补充都会被采纳
        </span>
      </div>
    </div>
  )
}
