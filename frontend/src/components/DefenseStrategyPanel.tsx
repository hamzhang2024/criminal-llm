// 辩护思路确认面板（步骤 4.75）
// 仅在流水线进入 awaiting_confirmation 状态时渲染：
// 律师可勾选/编辑系统建议、补充自己的思路，确认后自动触发步骤 5（辩护意见生成）

import { useCallback, useEffect, useState } from 'react'
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
}

// 类型徽标配色：主攻=金，备选=灰蓝
function typeBadgeStyle(type: string): { background: string; color: string; border: string } {
  if (type === '主攻') {
    return { background: colors.goldBg, color: colors.gold, border: `1px solid ${colors.goldBorder}` }
  }
  return { background: 'rgba(142,142,147,0.12)', color: '#8e8e93', border: '1px solid rgba(142,142,147,0.2)' }
}

export default function DefenseStrategyPanel({ caseId, defendant, charges, onConfirmed }: DefenseStrategyPanelProps) {
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

  useEffect(() => {
    let cancelled = false
    getDefenseStrategy(caseId)
      .then(data => {
        if (cancelled) return
        setStrategy(data)
        if (data.status === 'awaiting_confirmation') {
          // 默认全部勾选
          setSelected(new Set((data.suggestion?.directions || []).map((_, i) => i)))
        }
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoaded(true) })
    return () => { cancelled = true }
  }, [caseId])

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

  const handleConfirm = useCallback(async (useSystemDefault: boolean) => {
    if (confirming) return
    setConfirming(true)
    setError('')
    setProgressMsg('正在确认辩护思路...')
    try {
      if (useSystemDefault) {
        await confirmDefenseStrategy(caseId, { use_system_default: true })
      } else {
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
      }
      await runStep5()
      setProgressMsg('')
      onConfirmed?.()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '确认失败，请重试')
      setProgressMsg('')
    } finally {
      setConfirming(false)
    }
  }, [confirming, caseId, strategy, editedDirections, selected, additions, runStep5, onConfirmed])

  // 仅待确认状态渲染（其余状态由报告页/步骤指示器呈现）
  if (!loaded || !strategy || strategy.status !== 'awaiting_confirmation') return null

  const directions = strategy.suggestion?.directions || []

  return (
    <div style={{
      marginBottom: 12, padding: '16px', borderRadius: '10px',
      border: `1px solid ${colors.goldBorder}`, background: colors.goldBg,
    }}>
      {/* 标题 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <Scale className="w-4 h-4" style={{ color: colors.gold }} />
        <span style={{ fontSize: 14, fontWeight: 600, color: colors.textPrimary }}>辩护思路待确认</span>
        <span style={{ fontSize: 11, color: colors.textTertiary }}>系统已生成建议，请勾选、修改或补充后确认</span>
      </div>
      <div style={{ fontSize: 11, color: colors.textSecondary, marginBottom: 12 }}>
        确认后将自动开始生成辩护意见（步骤 5）；律师补充的思路优先级最高
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

      {/* 操作按钮 */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          onClick={() => handleConfirm(false)}
          disabled={confirming}
          style={{
            padding: '8px 16px', fontSize: 13, fontWeight: 500, borderRadius: 8, border: 'none',
            background: confirming ? colors.goldBorder : colors.gold, color: '#fff',
            cursor: confirming ? 'not-allowed' : 'pointer',
            display: 'flex', alignItems: 'center', gap: 6,
          }}
        >
          {confirming && <Loader2 className="w-3 h-3 animate-spin" />}
          确认并继续分析
        </button>
        <button
          onClick={() => handleConfirm(true)}
          disabled={confirming}
          style={{
            padding: '8px 16px', fontSize: 13, fontWeight: 500, borderRadius: 8,
            border: `1px solid ${colors.goldBorder}`, background: 'transparent',
            color: colors.gold, cursor: confirming ? 'not-allowed' : 'pointer',
          }}
        >
          全部采纳并继续
        </button>
      </div>
    </div>
  )
}
