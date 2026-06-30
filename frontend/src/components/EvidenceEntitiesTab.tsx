// 证据关联信息展示组件
// 读取 evidence/related_entities.json，展示结构化关联信息

import React, { useState, useEffect } from 'react'
import { getEvidenceEntities } from '../api/evidence'

interface EntityInfo {
  type: string
  value: string
  persons: string[]
  evidence_count: number
}

interface EvidenceEntitiesData {
  total_entities: number
  summary: Record<string, EntityInfo[]>
  entities: Array<{
    type: string
    value: string
    persons: string[]
    evidence_ids: number[]
    evidence_count: number
  }>
}

interface Props {
  caseId: string
}

export function EvidenceEntitiesTab({ caseId }: Props) {
  const [data, setData] = useState<EvidenceEntitiesData | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeType, setActiveType] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    getEvidenceEntities(caseId)
      .then((res) => {
        setData(res)
        if (Object.keys(res.summary || {}).length > 0) {
          setActiveType(Object.keys(res.summary)[0])
        }
      })
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [caseId])

  if (loading) {
    return (
      <div style={{ padding: '20px', textAlign: 'center', color: '#8e8e93', fontSize: '13px' }}>
        加载关联信息...
      </div>
    )
  }

  if (!data || data.total_entities === 0) {
    return (
      <div style={{ padding: '20px', textAlign: 'center', color: '#8e8e93', fontSize: '13px' }}>
        暂无结构化关联信息（需重新提取证据生成）
      </div>
    )
  }

  const types = Object.keys(data.summary || {})
  const activeEntries = activeType ? (data.summary[activeType] || []) : []

  const typeLabels: Record<string, string> = {
    '手机号': '📱',
    '微信号': '💬',
    'QQ号': '💬',
    '银行卡': '💳',
    '身份证': '🆔',
    '车牌号': '🚗',
  }

  const typeBgColors: Record<string, string> = {
    '手机号': '#e8f5e9',
    '微信号': '#e3f2fd',
    'QQ号': '#e3f2fd',
    '银行卡': '#fff3e0',
    '身份证': '#fce4ec',
    '车牌号': '#f3e5f5',
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 类型选择栏 */}
      <div style={{ display: 'flex', gap: '4px', padding: '8px 0', flexWrap: 'wrap', borderBottom: '1px solid #e5e5ea' }}>
        {types.map((t) => {
          const count = (data.summary[t] || []).length
          const icon = typeLabels[t] || '📄'
          const isActive = t === activeType
          return (
            <button
              key={t}
              onClick={() => setActiveType(t)}
              style={{
                padding: '4px 10px', borderRadius: '14px', border: 'none',
                fontSize: '12px', cursor: 'pointer',
                background: isActive ? '#007aff' : typeBgColors[t] || '#f2f2f7',
                color: isActive ? '#fff' : '#1c1c1e',
                fontWeight: isActive ? 600 : 400,
                transition: 'all 0.15s',
              }}
            >
              {icon} {t} ({count})
            </button>
          )
        })}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: '11px', color: '#8e8e93', alignSelf: 'center' }}>
          共 {data.total_entities} 条
        </span>
      </div>

      {/* 实体列表 */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
        {activeEntries.length === 0 ? (
          <div style={{ textAlign: 'center', color: '#8e8e93', fontSize: '12px', padding: '20px' }}>
            该类暂无关联信息
          </div>
        ) : (
          activeEntries.map((entry, i) => (
            <div
              key={i}
              style={{
                padding: '8px 10px', marginBottom: '4px',
                background: '#f9f9fb', borderRadius: '8px',
                borderLeft: `3px solid ${typeBgColors[activeType!] || '#007aff'}`,
              }}
            >
              <div style={{ fontSize: '14px', fontWeight: 500, color: '#1c1c1e', fontFamily: 'monospace' }}>
                {entry.value}
              </div>
              <div style={{ display: 'flex', gap: '8px', marginTop: '3px', fontSize: '11px', color: '#8e8e93' }}>
                {entry.persons.length > 0 && (
                  <span>👤 {entry.persons.join('、')}</span>
                )}
                <span>📎 {entry.evidence_count} 份证据</span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
