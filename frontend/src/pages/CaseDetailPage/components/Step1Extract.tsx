// 步骤 1：证据提取卡片

import React, { useState } from 'react'
import { MacOSCard, MacOSButton } from '../../../components/MacOSLayout'
import { EvidenceReviewPanel } from './EvidenceReviewPanel'

interface EvidenceItem {
  id: number | string
  name: string
  type?: string
  source?: string
  page_range?: string
  persons?: string
  key_facts?: string
  contradiction_hints?: string
  needs_review?: boolean
  reviewed?: boolean
}

interface Step1ExtractProps {
  caseId?: string
  files: Array<{ status: string }>
  evidenceList: EvidenceItem[]
  evidenceExtracted: boolean
  processing: boolean
  stopping?: boolean
  onExtract: () => void
  onStop: () => void
  onClear: () => void
  onRefreshEvidence: () => void
}

export function Step1Extract({
  caseId, files, evidenceList, evidenceExtracted, processing, stopping,
  onExtract, onStop, onClear, onRefreshEvidence,
}: Step1ExtractProps) {
  const mdConversionComplete = files.length > 0 && files.every(f => f.status === 'done')
  const [reviewingEv, setReviewingEv] = useState<EvidenceItem | null>(null)

  return (
    <MacOSCard style={{ marginTop: 12 }}>
      <div className="flex-between mb-md">
        <div className="flex-row gap-md">
          <h4 className="text-md font-semibold">证据提取</h4>
          {evidenceList.length > 0 && <span className="text-xs text-secondary">{evidenceList.length} 份</span>}
        </div>
        <div className="flex-row gap-sm">
          {processing ? (
            <MacOSButton variant="secondary" disabled={!!stopping} onClick={onStop} style={{ color: '#ff9500', borderColor: '#ff9500' }}>
              {stopping ? '正在停止...' : '停止'}
            </MacOSButton>
          ) : evidenceExtracted ? (
            <>
              <MacOSButton variant="secondary" onClick={onExtract}>重新提取</MacOSButton>
              <MacOSButton variant="secondary" onClick={onClear} style={{ color: 'var(--macos-danger)', borderColor: 'var(--macos-danger)' }}>清除</MacOSButton>
            </>
          ) : mdConversionComplete ? (
            <MacOSButton variant="primary" onClick={onExtract}>提取证据</MacOSButton>
          ) : (
            <MacOSButton variant="secondary" disabled>请先转换 PDF</MacOSButton>
          )}
        </div>
      </div>
      {evidenceList.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '300px', overflowY: 'auto' }}>
          {evidenceList.map(ev => (
            <div key={ev.id} style={{
              display: 'flex', alignItems: 'center', gap: '12px',
              padding: '10px 12px',
              background: 'var(--macos-bg-secondary)',
              borderRadius: '8px',
              border: `1px solid ${ev.needs_review ? 'rgba(255,149,0,0.3)' : 'var(--macos-border)'}`
            }}>
              <div style={{
                width: '28px', height: '28px', borderRadius: '6px',
                background: 'var(--macos-accent-light)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '12px', fontWeight: '600', color: 'var(--macos-accent)'
              }}>{ev.id}</div>
              <div style={{ flex: 1, overflow: 'hidden' }}>
                <div style={{ fontSize: '13px', fontWeight: '500', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  {ev.name}
                  {ev.needs_review && (
                    <span style={{ fontSize: '10px', color: '#ff9500', background: 'rgba(255,149,0,0.1)', padding: '1px 5px', borderRadius: '3px', flexShrink: 0 }}>需复核</span>
                  )}
                  {ev.reviewed && (
                    <span style={{ fontSize: '10px', color: '#34c759', background: 'rgba(52,199,89,0.1)', padding: '1px 5px', borderRadius: '3px', flexShrink: 0 }}>已校对</span>
                  )}
                </div>
                <div style={{ fontSize: '11px', color: 'var(--macos-text-secondary)' }}>
                  {ev.type} · {ev.source}{ev.page_range ? ' · ' + ev.page_range : ''}
                </div>
              </div>
              {caseId && (
                <button
                  onClick={() => setReviewingEv(ev)}
                  style={{
                    padding: '4px 10px', borderRadius: '5px',
                    border: '1px solid var(--macos-border)', background: 'transparent',
                    color: 'var(--macos-accent)', fontSize: '11px', cursor: 'pointer',
                    flexShrink: 0,
                  }}
                >
                  校对
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      {reviewingEv && caseId && (
        <EvidenceReviewPanel
          caseId={caseId}
          evidence={reviewingEv}
          onClose={() => setReviewingEv(null)}
          onSaved={onRefreshEvidence}
        />
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