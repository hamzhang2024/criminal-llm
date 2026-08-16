// 步骤 2：案卷分析 — 被告人信息 + 5 阶段分析按钮

import React from 'react'
import { Loader2 } from 'lucide-react'
import { MacOSCard, MacOSButton } from '../../../components/MacOSLayout'
import DocTypeBadge, { CompletenessDot } from '../../../components/DocTypeBadge'
import { SearchKeywordsEditor } from './SearchKeywordsEditor'
import { STAGES } from '../hooks/useStageAnalysis'
import type { CaseFile } from '../hooks/useCaseFiles'
import type { EvidenceIndexFile, CompletenessReport } from '../../../api'
import { getCacheStats } from '../../../api'
import type { CacheStats } from '../../../api'

interface EvidenceItem {
  id: number | string
  name: string
  type?: string
  source?: string
  page_range?: string
  md_file?: string
}

interface Step2AnalyzeProps {
  caseId: string
  defendant: string
  charges: string[]
  setCharges: (v: string[]) => void
  evidenceList: EvidenceItem[]
  evidenceExtracted: boolean
  evidenceFiles?: EvidenceIndexFile[]      // index.json files（文书分类）
  completeness?: CompletenessReport | null // 提取完整性报告
  stageStatus: Record<number, 'idle' | 'running' | 'completed' | 'error'>
  runningStage: number | null
  stageMessages: Record<number, string>
  stageErrors: Record<number, string>
  onRunStage: (num: number) => void
  onRunAll: () => void
  onStopStage: (num: number) => void
  onClearStage: (num: number) => void
  onViewStage: (num: number) => void
  onPreviewEvidence: (mdFile: string, evId: string | number) => void
  onRefreshEvidence: () => void
  onRefreshFiles: () => void
  pipelineStatus: Record<number | string, boolean>
  strategyAwaiting?: boolean  // 步骤 4.75 辩护思路待确认（高亮阶段 5）
}

export function Step2Analyze({
  caseId, defendant, charges, setCharges,
  evidenceList, evidenceExtracted, evidenceFiles = [], completeness,
  stageStatus, runningStage, stageMessages, stageErrors,
  onRunStage, onRunAll, onStopStage, onClearStage, onViewStage,
  onPreviewEvidence, onRefreshEvidence, onRefreshFiles,
  pipelineStatus, strategyAwaiting = false,
}: Step2AnalyzeProps) {
  // 文书分类映射：来源文件名 → doc_type
  const docTypeMap: Record<string, string> = {}
  for (const f of evidenceFiles) docTypeMap[f.name] = f.doc_type
  // 非证据文件（封面/目录等，不参与提取）
  const nonEvidenceFiles = evidenceFiles.filter(f => f.doc_type.startsWith('non_evidence'))

  // 分析运行中轮询 LLM 缓存命中率（每 8s），空闲时隐藏徽标
  const [cacheStats, setCacheStats] = React.useState<CacheStats | null>(null)
  React.useEffect(() => {
    if (runningStage === null) return
    let cancelled = false
    const load = () => getCacheStats().then(s => { if (!cancelled) setCacheStats(s) }).catch(() => {})
    load()
    const timer = setInterval(load, 8000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [runningStage])

  return (
    <>
      {/* 证据文件列表 */}
      {(evidenceList.length > 0 || nonEvidenceFiles.length > 0) && (
        <MacOSCard style={{ marginBottom: 12 }}>
          <div className="flex-between mb-sm">
            <div className="flex-row gap-sm">
              <h4 className="text-md font-semibold">证据文件</h4>
              <span className="text-xs text-secondary">{evidenceList.length} 份</span>
            </div>
            <button
              onClick={onRefreshEvidence}
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
              title="刷新证据列表"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#86868b" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
            </button>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '180px', overflowY: 'auto' }}>
            {evidenceList.map((ev: any) => {
              const entry = ev.source ? completeness?.files?.[ev.source] : undefined
              return (
              <div key={ev.id} style={{
                display: 'flex', alignItems: 'center', gap: '10px',
                padding: '8px 10px',
                background: 'var(--macos-bg-secondary)',
                borderRadius: '6px',
                fontSize: '12px'
              }}>
                <div style={{
                  width: '24px', height: '24px', borderRadius: '6px',
                  background: 'var(--macos-accent-light)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '11px', fontWeight: '600', color: 'var(--macos-accent)'
                }}>{ev.id}</div>
                <div style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {ev.name}
                  <DocTypeBadge docType={ev.source ? docTypeMap[ev.source] : undefined} />
                </div>
                <div style={{ color: 'var(--macos-text-tertiary)', fontSize: '11px' }}>
                  {ev.type}
                </div>
                <CompletenessDot
                  status={entry?.status}
                  missingCount={entry?.missing?.length || 0}
                  needsReview={entry?.needs_review}
                />
                {ev.md_file && (
                  <button
                    onClick={() => onPreviewEvidence(ev.md_file, ev.id)}
                    style={{
                      padding: '4px 8px',
                      background: 'var(--macos-accent-light)',
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontSize: '11px',
                      color: 'var(--macos-accent)'
                    }}
                  >预览</button>
                )}
              </div>
              )
            })}
            {nonEvidenceFiles.map(f => (
              <div key={`non-${f.name}`} style={{
                display: 'flex', alignItems: 'center', gap: '10px',
                padding: '8px 10px',
                background: 'var(--macos-bg-secondary)',
                borderRadius: '6px',
                fontSize: '12px',
                opacity: 0.55,
              }}>
                <div style={{
                  width: '24px', height: '24px', borderRadius: '6px',
                  background: 'rgba(142,142,147,0.12)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '11px', fontWeight: '600', color: '#8e8e93'
                }}>—</div>
                <div style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {f.name.replace(/\.md$/, '')}
                  <DocTypeBadge docType={f.doc_type} />
                </div>
                <div style={{ color: 'var(--macos-text-tertiary)', fontSize: '11px' }}>
                  不参与提取
                </div>
              </div>
            ))}
          </div>
        </MacOSCard>
      )}

      {/* 被告人信息 + 罪名输入 */}
      <MacOSCard style={{ marginBottom: '12px' }}>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', marginBottom: '12px' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: '11px', color: 'var(--macos-text-tertiary)', marginBottom: '4px' }}>被告人</div>
            <div style={{ fontSize: '14px', color: 'var(--macos-text-primary)', fontWeight: 500 }}>{defendant || '未指定'}</div>
          </div>
          <div style={{ flex: 2 }}>
            <div style={{ fontSize: '11px', color: 'var(--macos-text-tertiary)', marginBottom: '4px' }}>指控罪名（回车添加，可多个）</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', padding: '6px 10px', border: '1px solid var(--macos-border)', borderRadius: '8px', minHeight: '36px', alignItems: 'center', background: 'var(--macos-bg-secondary)' }}>
              {charges.map((c, i) => (
                <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', padding: '2px 8px', background: 'var(--macos-accent)', color: '#fff', borderRadius: '10px', fontSize: '12px', fontWeight: 500 }}>
                  {c}
                  <button onClick={() => setCharges(charges.filter((_, j) => j !== i))} style={{ background: 'none', border: 'none', color: '#fff', cursor: 'pointer', padding: '0 2px', fontSize: '14px', lineHeight: 1 }}>×</button>
                </span>
              ))}
              <input
                type="text"
                placeholder={charges.length === 0 ? "如：诈骗罪" : "继续添加..."}
                style={{ border: 'none', outline: 'none', fontSize: '13px', flex: 1, minWidth: '80px', background: 'transparent' }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    const v = (e.target as HTMLInputElement).value.trim()
                    if (v && !charges.includes(v)) setCharges([...charges, v])
                    ;(e.target as HTMLInputElement).value = ''
                  }
                }}
              />
            </div>
          </div>
        </div>
      </MacOSCard>

      {/* 类案检索关键词（与罪名同属分析输入，紧邻放置） */}
      <SearchKeywordsEditor caseId={caseId} charges={charges} />

      {/* 5 阶段独立按钮 */}
      <MacOSCard style={{ marginBottom: '12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h4 style={{ fontSize: '14px', fontWeight: '600', margin: 0 }}>分析阶段（不建议并行处理）</h4>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            {/* 分析运行中的实时缓存命中率徽标 */}
            {runningStage !== null && cacheStats && cacheStats.calls > 0 && (
              <span style={{ fontSize: '11px', color: 'var(--macos-text-tertiary)' }} title={`缓存命中 ${cacheStats.hit_tokens.toLocaleString()} / ${(cacheStats.hit_tokens + cacheStats.miss_tokens).toLocaleString()} tokens`}>
                ⚡ 缓存 <span style={{ fontWeight: 600, color: cacheStats.hit_rate >= 0.5 ? '#34c759' : 'var(--macos-accent)' }}>{Math.round(cacheStats.hit_rate * 100)}%</span>
              </span>
            )}
            <MacOSButton
              variant="primary"
              disabled={!evidenceExtracted || runningStage !== null}
              onClick={onRunAll}
            >
              全部分析
            </MacOSButton>
          </div>
        </div>
        {!evidenceExtracted && (
          <div style={{
            padding: '8px 12px', borderRadius: '8px', marginBottom: '12px',
            background: 'rgba(255,149,0,0.08)', border: '1px solid rgba(255,149,0,0.2)',
            fontSize: '12px', color: '#ff9500',
            display: 'flex', alignItems: 'center', gap: '8px'
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
            请先完成证据提取，再进行案卷分析
          </div>
        )}
        {evidenceExtracted && !pipelineStatus[4] && (
          <div style={{
            padding: '8px 12px', borderRadius: '8px', marginBottom: '12px',
            background: 'rgba(0,122,255,0.08)', border: '1px solid rgba(0,122,255,0.2)',
            fontSize: '12px', color: 'var(--macos-accent)',
            display: 'flex', alignItems: 'center', gap: '8px'
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
            证据已提取，请等待阶段 4（法律法规）分析完成后即可查看报告
          </div>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {STAGES.map(stage => {
            const status = stageStatus[stage.num] || 'idle'
            const isRunning = runningStage === stage.num
            const msg = stageMessages[stage.num]
            const errMsg = stageErrors[stage.num]

            const canStartStage = (num: number) => {
              const idx = STAGES.findIndex(s => s.num === num)
              if (idx <= 0) return true
              for (let i = 0; i < idx; i++) {
                if (stageStatus[STAGES[i].num] !== 'completed') return false
              }
              return true
            }

            const evidenceDisabled = !evidenceExtracted
            const seqDisabled = !canStartStage(stage.num)
            const analysisDisabled = evidenceDisabled || seqDisabled
            // 阶段 5（综合辩护）前置卡点：步骤 4.75 辩护思路待确认时高亮提示
            const awaitingStrategy = stage.num === 5 && strategyAwaiting && status !== 'completed' && status !== 'running'

            return (
              <div key={stage.num} style={{
                display: 'flex', alignItems: 'center', gap: '12px',
                padding: '12px',
                borderRadius: '8px',
                border: awaitingStrategy ? '1px solid rgba(255,149,0,0.45)' : `1px solid ${status === 'completed' ? 'rgba(59,89,152,0.2)' : status === 'error' ? 'rgba(102,102,102,0.15)' : 'var(--macos-border)'}`,
                background: awaitingStrategy ? 'rgba(255,149,0,0.07)' : status === 'completed' ? 'rgba(59,89,152,0.04)' : status === 'error' ? 'rgba(102,102,102,0.03)' : 'var(--macos-bg-secondary)',
                opacity: analysisDisabled ? 0.5 : 1,
                transition: 'opacity 0.2s'
              }}>
                <div style={{
                  width: '28px', height: '28px', borderRadius: '50%',
                  background: status === 'completed' ? 'rgba(59,89,152,0.1)' : isRunning ? 'var(--macos-accent-light)' : status === 'error' ? 'rgba(102,102,102,0.1)' : 'var(--macos-bg-tertiary)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '14px', fontWeight: '600', flexShrink: 0,
                  color: status === 'completed' ? '#3b5998' : isRunning ? 'var(--macos-accent)' : status === 'error' ? '#666666' : '#86868b'
                }}>
                  {status === 'completed' ? '✓' : isRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : stage.num}
                </div>

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '13px', fontWeight: '500' }}>{stage.name}</div>
                  {awaitingStrategy && (
                    <div style={{ fontSize: '11px', color: '#ff9500', fontWeight: 500 }}>
                      辩护思路待确认 — 请在上方确认面板中处理后再继续
                    </div>
                  )}
                  {msg && <div style={{ fontSize: '11px', color: 'var(--macos-accent)' }}>{msg}</div>}
                  {errMsg && <div style={{ fontSize: '11px', color: '#666666' }}>{errMsg}</div>}
                  {!msg && !errMsg && seqDisabled && (
                    <div style={{ fontSize: '11px', color: '#86868b' }}>等待前序阶段完成</div>
                  )}
                  {!msg && !errMsg && !seqDisabled && !isRunning && <div style={{ fontSize: '11px', color: 'var(--macos-text-tertiary)' }}>{stage.desc}</div>}
                </div>

                <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
                  {status === 'completed' && (
                    <>
                      <button onClick={() => onViewStage(stage.num)} disabled={analysisDisabled} style={{
                        padding: '4px 10px', borderRadius: '6px',
                        border: '1px solid var(--macos-border)', background: 'transparent',
                        color: analysisDisabled ? '#d1d1d6' : 'var(--macos-accent)',
                        fontSize: '12px', cursor: analysisDisabled ? 'not-allowed' : 'pointer'
                      }}>查看</button>
                      <button onClick={() => onClearStage(stage.num)} disabled={analysisDisabled} style={{
                        padding: '4px 10px', borderRadius: '6px',
                        border: analysisDisabled ? '1px solid #d1d1d6' : '1px solid #666666', background: 'transparent',
                        color: analysisDisabled ? '#d1d1d6' : '#666666',
                        fontSize: '12px', cursor: analysisDisabled ? 'not-allowed' : 'pointer'
                      }}>清除</button>
                    </>
                  )}
                  {status === 'error' && (
                    <button onClick={() => onRunStage(stage.num)} disabled={analysisDisabled} style={{
                      padding: '4px 10px', borderRadius: '6px',
                      border: 'none', background: analysisDisabled ? '#d1d1d6' : '#666666',
                      color: '#fff', fontSize: '12px',
                      cursor: analysisDisabled ? 'not-allowed' : 'pointer'
                    }}>重试</button>
                  )}
                  {isRunning && (
                    <button onClick={() => onStopStage(stage.num)} style={{
                      padding: '4px 10px', borderRadius: '6px',
                      border: 'none', background: '#ff9500',
                      color: '#fff', fontSize: '12px', cursor: 'pointer'
                    }}>停止</button>
                  )}
                  {(status === 'idle' || status === 'error') && !isRunning && (
                    <button onClick={() => onRunStage(stage.num)} disabled={!defendant.trim() || analysisDisabled} style={{
                      padding: '4px 10px', borderRadius: '6px',
                      border: 'none',
                      background: (!defendant.trim() || analysisDisabled) ? '#d1d1d6' : 'var(--macos-accent)',
                      color: '#fff', fontSize: '12px',
                      cursor: (!defendant.trim() || analysisDisabled) ? 'not-allowed' : 'pointer'
                    }}>开始</button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </MacOSCard>
    </>
  )
}