// 步骤 1：证据提取卡片

import React, { useState } from 'react'
import { MacOSCard, MacOSButton } from '../../../components/MacOSLayout'
import { showConfirm } from '../../../components/MacOSDialog'
import DocTypeBadge, { CompletenessDot } from '../../../components/DocTypeBadge'
import type { EvidenceIndexFile, CompletenessReport } from '../../../api'
import { SelectiveOCR } from './SelectiveOCR'

interface EvidenceItem {
  id: number | string
  name: string
  type?: string
  source?: string
  page_range?: string
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
}

export function Step1Extract({
  caseId, files, evidenceList, evidenceExtracted, evidenceFiles = [], completeness,
  processing, onExtract, onStop, onClear,
}: Step1ExtractProps) {
  const mdConversionComplete = files.length > 0 && files.every(f => f.status === 'done')
  // 未 OCR 图片数（SelectiveOCR 上报，用于提取前提醒）
  const [unocrCount, setUnocrCount] = useState(0)

  // 提取前提醒：还有未 OCR 图片时先让用户选择是否 OCR（转账凭证/流水截图文字对资金流分析重要）
  const handleExtractClick = async () => {
    if (unocrCount > 0) {
      const goOcr = await showConfirm({
        title: '还有图片未识别文字',
        message: `还有 ${unocrCount} 张图片（转账凭证/流水截图等）未 OCR。建议先在上方「选择性 OCR 图片」中选图识别，再提取证据，资金流数据会更完整。`,
        confirmText: '去选图 OCR',
        cancelText: '跳过继续提取',
        variant: 'warning',
      })
      if (goOcr) return // 留在页面让用户展开 OCR 卡片选图
    }
    onExtract()
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
      <SelectiveOCR caseId={caseId} onUnocrCountChange={setUnocrCount} />
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
                    <DocTypeBadge docType={ev.source ? docTypeMap[ev.source] : undefined} />
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--macos-text-secondary)' }}>
                    {ev.type} · {ev.source}{ev.page_range ? ' · ' + ev.page_range : ''}
                  </div>
                </div>
                <CompletenessDot
                  status={entry?.status}
                  missingCount={entry?.missing?.length || 0}
                  needsReview={entry?.needs_review}
                />
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
        <div style={{ fontSize: '12px', color: '#3b5998', padding: '12px 0' }}>
          已完成证据提取
        </div>
      )}
    </MacOSCard>
  )
}
