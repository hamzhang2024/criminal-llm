// 证据人工校对面板

import React, { useState, useEffect } from 'react'
import { api } from '../../../api'
import type { EvidenceReviewPayload } from '../../../api'
import { showAlert } from '../../../components/MacOSDialog'

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

interface EvidenceReviewPanelProps {
  caseId: string
  evidence: EvidenceItem
  onClose: () => void
  onSaved: () => void
}

const EVIDENCE_TYPES = [
  '物证',
  '书证',
  '证人证言',
  '被害人陈述',
  '犯罪嫌疑人供述和辩解',
  '鉴定意见',
  '勘验检查辨认笔录',
  '视听资料、电子数据',
  '程序性文书',
  '其他证据',
]

export function EvidenceReviewPanel({ caseId, evidence, onClose, onSaved }: EvidenceReviewPanelProps) {
  const [form, setForm] = useState<EvidenceReviewPayload>({
    name: evidence.name || '',
    type: evidence.type || '',
    persons: evidence.persons || '',
    key_facts: evidence.key_facts || '',
    contradiction_hints: evidence.contradiction_hints || '',
  })
  const [saving, setSaving] = useState(false)

  // 当切换证据时重置表单
  useEffect(() => {
    setForm({
      name: evidence.name || '',
      type: evidence.type || '',
      persons: evidence.persons || '',
      key_facts: evidence.key_facts || '',
      contradiction_hints: evidence.contradiction_hints || '',
    })
  }, [evidence.id])

  const handleSave = async () => {
    if (!form.name?.trim()) {
      showAlert({ title: '校对失败', message: '证据名称不能为空', variant: 'danger' })
      return
    }
    setSaving(true)
    try {
      await api.reviewEvidenceItem(caseId, Number(evidence.id), form)
      showAlert({ title: '校对成功', message: '证据信息已更新，下游分析将使用校对后的字段', variant: 'success' })
      onSaved()
      onClose()
    } catch (err) {
      showAlert({ title: '校对失败', message: err instanceof Error ? err.message : '未知错误', variant: 'danger' })
    } finally {
      setSaving(false)
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '8px 10px',
    border: '1px solid var(--macos-border)', borderRadius: '6px',
    fontSize: '13px', boxSizing: 'border-box',
    background: 'var(--macos-bg)',
  }
  const labelStyle: React.CSSProperties = {
    fontSize: '12px', fontWeight: 500, color: 'var(--macos-text-secondary)',
    marginBottom: '4px', display: 'block',
  }

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      background: 'rgba(0,0,0,0.4)', display: 'flex',
      alignItems: 'center', justifyContent: 'center', zIndex: 1000,
    }} onClick={onClose}>
      <div style={{
        background: 'var(--macos-bg)', borderRadius: '12px',
        padding: '24px', width: '560px', maxWidth: '90vw', maxHeight: '85vh',
        overflowY: 'auto', boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h3 style={{ fontSize: '16px', fontWeight: 600, margin: 0 }}>
            证据校对 #{evidence.id}
            {evidence.needs_review && (
              <span style={{ marginLeft: '8px', fontSize: '11px', color: '#ff9500', background: 'rgba(255,149,0,0.1)', padding: '2px 6px', borderRadius: '4px' }}>
                需复核
              </span>
            )}
            {evidence.reviewed && (
              <span style={{ marginLeft: '8px', fontSize: '11px', color: '#34c759', background: 'rgba(52,199,89,0.1)', padding: '2px 6px', borderRadius: '4px' }}>
                已校对
              </span>
            )}
          </h3>
          <button onClick={onClose} style={{ border: 'none', background: 'transparent', fontSize: '18px', cursor: 'pointer', color: 'var(--macos-text-secondary)' }}>×</button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div>
            <label style={labelStyle}>证据名称</label>
            <input
              style={inputStyle}
              value={form.name}
              onChange={e => setForm(prev => ({ ...prev, name: e.target.value }))}
              placeholder="证据名称"
            />
          </div>

          <div>
            <label style={labelStyle}>证据类型</label>
            <select
              style={inputStyle}
              value={form.type}
              onChange={e => setForm(prev => ({ ...prev, type: e.target.value }))}
            >
              {EVIDENCE_TYPES.map(t => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>

          <div>
            <label style={labelStyle}>涉案人员（逗号分隔）</label>
            <input
              style={inputStyle}
              value={form.persons}
              onChange={e => setForm(prev => ({ ...prev, persons: e.target.value }))}
              placeholder="如：张三,李四,王五"
            />
          </div>

          <div>
            <label style={labelStyle}>关键事实</label>
            <textarea
              style={{ ...inputStyle, minHeight: '60px', resize: 'vertical' }}
              value={form.key_facts}
              onChange={e => setForm(prev => ({ ...prev, key_facts: e.target.value }))}
              placeholder="该证据的关键事实摘要"
            />
          </div>

          <div>
            <label style={labelStyle}>矛盾提示</label>
            <textarea
              style={{ ...inputStyle, minHeight: '60px', resize: 'vertical' }}
              value={form.contradiction_hints}
              onChange={e => setForm(prev => ({ ...prev, contradiction_hints: e.target.value }))}
              placeholder='本证据内部或与其他证据的矛盾点，确无矛盾填"无"'
            />
          </div>

          <div style={{ fontSize: '11px', color: 'var(--macos-text-tertiary)', padding: '8px 10px', background: 'var(--macos-bg-secondary)', borderRadius: '6px' }}>
            校对后的字段将同步更新 index.json 和证据 MD 文件头部，下游分析（人物关系、矛盾分析、证据链）会使用校对后的值。
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '20px' }}>
          <button
            onClick={onClose}
            style={{ padding: '8px 16px', borderRadius: '6px', border: '1px solid var(--macos-border)', background: 'transparent', cursor: 'pointer', fontSize: '13px' }}
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            style={{
              padding: '8px 16px', borderRadius: '6px', border: 'none',
              background: saving ? 'var(--macos-text-tertiary)' : 'var(--macos-accent)',
              color: 'white', cursor: saving ? 'not-allowed' : 'pointer', fontSize: '13px',
            }}
          >
            {saving ? '保存中...' : '保存校对'}
          </button>
        </div>
      </div>
    </div>
  )
}
