// 步骤 1：证据提取卡片

import React, { useState } from 'react'
import { MacOSCard, MacOSButton } from '../../../components/MacOSLayout'
import { showConfirm } from '../../../components/MacOSDialog'
import DocTypeBadge, { CompletenessDot } from '../../../components/DocTypeBadge'
import { getCacheStats } from '../../../api'
import type { EvidenceIndexFile, CompletenessReport, CacheStats } from '../../../api'
import type { ExtractProgress } from '../hooks/useEvidenceExtraction'
import { SelectiveOCR } from './SelectiveOCR'

interface EvidenceItem {
  id: number | string
  name: string
  type?: string
  source?: string
  page_range?: string
  md_file?: string  // 证据全文文件名（预览用）
  failed?: boolean  // 按份提取失败的空壳条目（后端 index.json 标记）
  summary_preview?: string  // 摘要预览；失败占位块此处携带人性化失败原因
}

interface Step1ExtractProps {
  caseId: string
  files: Array<{ status: string }>
  evidenceList: EvidenceItem[]
  evidenceExtracted: boolean
  evidenceFiles?: EvidenceIndexFile[]      // index.json files（文书分类）
  completeness?: CompletenessReport | null // 提取完整性报告
  processing: boolean
  onExtract: () => void
  onStop: () => void
  onClear: () => void
  onRefreshEvidence: () => void
  onPreviewEvidence?: (mdFile: string, evId: string | number) => void  // 证据预览（摘要/全文切换，与案卷分析界面一致）
  extractProgress?: ExtractProgress | null  // 提取/摘要实时进度（进度条）
}

// 进度条（macOS 风格）
function ProgressBar({ percent, label, subLabel }: { percent: number; label: string; subLabel?: string }) {
  return (
    <div style={{ margin: '10px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '5px' }}>
        <span style={{ color: 'var(--macos-text-secondary)' }}>{label}</span>
        <span style={{ color: 'var(--macos-accent)', fontWeight: 600 }}>{percent}%</span>
      </div>
      <div style={{ height: '6px', background: 'var(--macos-bg-secondary)', borderRadius: '3px', overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: `${percent}%`, borderRadius: '3px',
          background: 'var(--macos-accent)', transition: 'width 0.5s ease',
        }} />
      </div>
      {subLabel && <div style={{ fontSize: '11px', color: '#86868b', marginTop: '4px' }}>{subLabel}</div>}
    </div>
  )
}

export function Step1Extract({
  caseId, files, evidenceList, evidenceExtracted, evidenceFiles = [], completeness,
  processing, onExtract, onStop, onClear, onPreviewEvidence, extractProgress,
}: Step1ExtractProps) {
  const mdConversionComplete = files.length > 0 && files.every(f => f.status === 'done')
  // 未 OCR 图片数（SelectiveOCR 上报，用于提取前提醒）
  const [unocrCount, setUnocrCount] = useState(0)

  // 提取运行中轮询 LLM 用量统计（每 15s），进度条下方一行小字实时展示
  const [llmStats, setLlmStats] = useState<CacheStats | null>(null)
  React.useEffect(() => {
    if (!processing) return
    let cancelled = false
    const load = () => getCacheStats().then(s => { if (!cancelled) setLlmStats(s) }).catch(() => {})
    load()
    const timer = setInterval(load, 15000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [processing])

  // 提取前提醒：还有未 OCR 图片时先让用户选择是否 OCR（转账凭证/流水截图文字对资金流分析重要）
  const handleExtractClick = async () => {
    if (unocrCount > 0) {
      const goOcr = await showConfirm({
        title: '还有图片未识别文字',
        message: `还有 ${unocrCount} 张图片未 OCR。\n\n这些图片（转账凭证/流水截图等）里的文字不会进入证据文本，可能让资金流分析不完整。\n\n· 点「去选图 OCR」：先识别再提取（推荐）\n· 点「直接提取」：跳过这些图片文字，现在就开始`,
        confirmText: '去选图 OCR',
        cancelText: '直接提取',
        variant: 'warning',
      })
      if (goOcr) return // 留在页面让用户展开 OCR 卡片选图
    }
    onExtract()
  }

  // 修复失败证据：明确告知只重提失败份（成功证据按名跳过、摘要只补缺的），语义比"重新提取"直观
  const handleRepairFailed = async () => {
    const ok = await showConfirm({
      title: `修复 ${failedCount} 份提取失败的证据`,
      message: `将重新提取 ${failedCount} 份失败证据（带 ⚠️ 标记的空壳）。\n\n· 已成功的 ${evidenceList.length - failedCount} 份不会重复提取\n· 摘要层只补失败份，其余不受影响\n· 预计耗时取决于模型速度，失败份会逐轮自动收敛`,
      confirmText: '开始修复',
      cancelText: '取消',
      variant: 'info',
    })
    if (ok) onExtract()
  }

  // 文书分类映射：来源文件名 → doc_type
  const docTypeMap: Record<string, string> = {}
  for (const f of evidenceFiles) docTypeMap[f.name] = f.doc_type
  // 非证据文件（封面/目录等，不参与提取）
  const nonEvidenceFiles = evidenceFiles.filter(f => f.doc_type.startsWith('non_evidence'))

  // 完整性摘要：N 份证据完整 · N 份疑似遗漏 · N 份非证据
  const summary = completeness?.summary || {}
  const okCount = summary.ok || 0
  const suspectCount = (summary.suspect || 0) + (summary.failed || 0)
  const summaryParts: string[] = []
  if (okCount > 0) summaryParts.push(`${okCount} 份证据完整`)
  if (suspectCount > 0) summaryParts.push(`${suspectCount} 份疑似遗漏`)
  if (nonEvidenceFiles.length > 0) summaryParts.push(`${nonEvidenceFiles.length} 份非证据`)

  // 按份提取失败份数（空壳条目，下次提取自动重试）
  const failedCount = evidenceList.filter(ev => ev.failed).length

  // 从失败条目的 summary_preview 提取人性化失败原因
  // （后端占位块格式：「⚠️ 本文书提取失败：<原因>，请重新提取。…」；旧数据无原因则返回空）
  const failReasonOf = (ev: any): string => {
    const m = (ev.summary_preview || '').match(/提取失败：(.+?)(?:，请重新提取|$)/)
    return m ? m[1] : ''
  }
  // 主导失败原因（第一条有原因的失败条目）：完成行下方展示，用户不用再猜
  const dominantReason = failReasonOf(evidenceList.find(ev => ev.failed) || {})

  return (
    <MacOSCard style={{ marginTop: 12 }}>
      <div className="flex-between mb-md">
        <div className="flex-row gap-md">
          <h4 className="text-md font-semibold">证据提取</h4>
          {evidenceList.length > 0 && <span className="text-xs text-secondary">{evidenceList.length} 份</span>}
        </div>
        <div className="flex-row gap-sm">
          {processing ? (
            <MacOSButton variant="secondary" onClick={onStop} style={{ color: '#ff9500', borderColor: '#ff9500' }}>停止</MacOSButton>
          ) : evidenceExtracted ? (
            <>
              {failedCount > 0 && (
                <MacOSButton variant="primary" onClick={handleRepairFailed}
                  style={{ background: '#b7791f', borderColor: '#b7791f' }}>
                  修复失败证据（{failedCount}）
                </MacOSButton>
              )}
              <MacOSButton variant="secondary" onClick={onExtract}>重新提取</MacOSButton>
              <MacOSButton variant="secondary" onClick={onClear} style={{ color: 'var(--macos-danger)', borderColor: 'var(--macos-danger)' }}>清除</MacOSButton>
            </>
          ) : mdConversionComplete ? (
            <MacOSButton variant="primary" onClick={handleExtractClick}>提取证据</MacOSButton>
          ) : (
            <MacOSButton variant="secondary" disabled>请先转换 PDF</MacOSButton>
          )}
        </div>
      </div>
      {/* 提取/摘要进度条（processing 时显示） */}
      {processing && extractProgress && (
        extractProgress.phase === 'summarizing' ? (
          <ProgressBar
            percent={extractProgress.summaryTotal > 0 ? Math.round(extractProgress.summaryDone / extractProgress.summaryTotal * 100) : 0}
            label={`正在生成证据摘要（${extractProgress.summaryDone}/${extractProgress.summaryTotal} 份）`}
            subLabel={extractProgress.currentFile ? `当前：${extractProgress.currentFile}` : undefined}
          />
        ) : (
          <ProgressBar
            percent={extractProgress.totalFiles > 0 ? Math.round(extractProgress.processedFiles / extractProgress.totalFiles * 100) : 0}
            label={`正在提取证据（${extractProgress.processedFiles}/${extractProgress.totalFiles} 卷）`}
            subLabel={extractProgress.currentFile
              ? `当前：${extractProgress.currentFile}${
                  extractProgress.currentFileTotal > 0
                    ? ` · ${extractProgress.currentFileDone}/${extractProgress.currentFileTotal} 份笔录`
                    : ` · ${extractProgress.currentFileStage || '处理中'}`  // 目录清点阶段显示阶段名，不再误显 0/0 份
                }${
                  extractProgress.llmLatencyMs > 30000
                    ? `（等待模型 ${Math.round(extractProgress.llmLatencyMs / 1000)}s）`  // 慢模型下标明"在等模型"而非卡死
                    : ''
                }`
              : undefined}
          />
        )
      )}
      {/* 提取运行中的实时 LLM 用量（进度条下方一行小字） */}
      {processing && llmStats && llmStats.calls > 0 && (
        <div style={{ fontSize: '11px', color: '#86868b', marginTop: '-4px', marginBottom: '8px' }}>
          LLM 用量：{llmStats.calls} 次调用 · 输入 {llmStats.input_tokens.toLocaleString()} tokens（缓存命中 {Math.round(llmStats.hit_rate * 100)}%）· 输出 {llmStats.output_tokens.toLocaleString()} tokens
        </div>
      )}
      <SelectiveOCR caseId={caseId} onUnocrCountChange={setUnocrCount} conversionDone={mdConversionComplete} />
      {summaryParts.length > 0 && (
        <div style={{
          fontSize: '11px', color: suspectCount > 0 ? '#ff9500' : 'var(--macos-text-secondary)',
          padding: '6px 10px', marginBottom: '8px',
          background: suspectCount > 0 ? 'rgba(255,149,0,0.06)' : 'var(--macos-bg-secondary)',
          borderRadius: '6px',
          border: `1px solid ${suspectCount > 0 ? 'rgba(255,149,0,0.2)' : 'var(--macos-border)'}`,
        }}>
          {summaryParts.join(' · ')}
        </div>
      )}
      {(evidenceList.length > 0 || nonEvidenceFiles.length > 0) && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '300px', overflowY: 'auto' }}>
          {evidenceList.map(ev => {
            const entry = ev.source ? completeness?.files?.[ev.source] : undefined
            return (
              <div key={ev.id} style={{
                display: 'flex', alignItems: 'center', gap: '12px',
                padding: '10px 12px',
                background: 'var(--macos-bg-secondary)',
                borderRadius: '8px',
                border: '1px solid var(--macos-border)'
              }}>
                <div style={{
                  width: '28px', height: '28px', borderRadius: '6px',
                  background: 'var(--macos-accent-light)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '12px', fontWeight: '600', color: 'var(--macos-accent)'
                }}>{ev.id}</div>
                <div style={{ flex: 1, overflow: 'hidden' }}>
                  <div style={{ fontSize: '13px', fontWeight: '500', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {ev.name}
                    {ev.failed && (
                      <span title={ev.summary_preview || '提取失败，下次提取将自动重试'} style={{ color: '#b7791f' }}> ⚠️</span>
                    )}
                    <DocTypeBadge docType={ev.source ? docTypeMap[ev.source] : undefined} />
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--macos-text-secondary)' }}>
                    {ev.type} · {ev.source}{ev.page_range ? ' · ' + ev.page_range : ''}
                  </div>
                  {/* 失败原因直接显示（不用悬停也能看到，如"请求超出模型上下文窗口…"） */}
                  {ev.failed && failReasonOf(ev) && (
                    <div style={{ fontSize: '11px', color: '#b7791f', marginTop: '2px', lineHeight: 1.4 }}>
                      {failReasonOf(ev)}
                    </div>
                  )}
                </div>
                <CompletenessDot
                  status={entry?.status}
                  missingCount={entry?.missing?.length || 0}
                  needsReview={entry?.needs_review}
                />
                {/* 证据预览（摘要/全文切换，与案卷分析界面一致） */}
                {ev.md_file && onPreviewEvidence && (
                  <button
                    onClick={() => onPreviewEvidence(ev.md_file!, ev.id)}
                    style={{
                      padding: '4px 8px', background: 'var(--macos-accent-light)',
                      border: 'none', borderRadius: '4px', cursor: 'pointer',
                      fontSize: '11px', color: 'var(--macos-accent)', flexShrink: 0,
                    }}
                  >预览</button>
                )}
              </div>
            )
          })}
          {nonEvidenceFiles.map(f => (
            <div key={`non-${f.name}`} style={{
              display: 'flex', alignItems: 'center', gap: '12px',
              padding: '10px 12px',
              background: 'var(--macos-bg-secondary)',
              borderRadius: '8px',
              border: '1px solid var(--macos-border)',
              opacity: 0.55,
            }}>
              <div style={{
                width: '28px', height: '28px', borderRadius: '6px',
                background: 'rgba(142,142,147,0.12)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '12px', fontWeight: '600', color: '#8e8e93'
              }}>—</div>
              <div style={{ flex: 1, overflow: 'hidden' }}>
                <div style={{ fontSize: '13px', fontWeight: '500', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {f.name.replace(/\.md$/, '')}
                  <DocTypeBadge docType={f.doc_type} />
                </div>
                <div style={{ fontSize: '11px', color: 'var(--macos-text-secondary)' }}>
                  {f.name} · 不参与证据提取
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      {!evidenceExtracted && !mdConversionComplete && (
        <div style={{ fontSize: '12px', color: 'var(--macos-text-tertiary)', padding: '12px 0' }}>
          请先完成全部文件的转换，然后再提取证据
        </div>
      )}
      {evidenceExtracted && (
        <div style={{ fontSize: '12px', color: failedCount > 0 ? '#b7791f' : '#3b5998', padding: '12px 0' }}>
          已完成证据提取{failedCount > 0 && `（${failedCount} 份失败待重提，下次提取自动重试）`}
          {/* 主导失败原因：修复环境问题后再重提，否则只会再次失败 */}
          {failedCount > 0 && dominantReason && (
            <div style={{ marginTop: '6px', lineHeight: 1.5 }}>
              失败原因：{dominantReason}
            </div>
          )}
        </div>
      )}
    </MacOSCard>
  )
}
