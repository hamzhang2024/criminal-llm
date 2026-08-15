// 选择性 OCR 图片：按卷分组缩略图网格 + 勾选 + 批量识别
import { useState, useEffect, useCallback, useRef } from 'react'
import { API_BASE, getOcrImages, startOcrImages, getOcrStatus } from '../../../api'

interface Props {
  caseId: string
}

export function SelectiveOCR({ caseId }: Props) {
  const [groups, setGroups] = useState<Record<string, Record<string, { w: number; h: number }>>>({})
  const [selected, setSelected] = useState<Record<string, Set<string>>>({})
  const [loading, setLoading] = useState(false)
  const [ocrStatus, setOcrStatus] = useState<{ status: string; done: number; total: number; current?: string; failed?: string[] } | null>(null)
  const [error, setError] = useState('')

  // 轮询 timer 用 ref 保存，组件卸载时清理（避免定时器泄漏 + 卸载后 setState）
  const timerRef = useRef<number | null>(null)
  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current) }, [])

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
      if (s.has(name)) s.delete(name)
      else s.add(name)
      return { ...prev, [vol]: s }
    })
  }
  const toggleVol = (vol: string, names: string[]) => {
    setSelected(prev => {
      const s = new Set(prev[vol] || [])
      const allOn = names.every(n => s.has(n))
      const next = new Set(s)
      names.forEach(n => {
        if (allOn) next.delete(n)
        else next.add(n)
      })
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
      // 启动前清掉旧 timer，避免重复轮询
      if (timerRef.current) clearInterval(timerRef.current)
      let failCount = 0
      timerRef.current = window.setInterval(async () => {
        try {
          const st = await getOcrStatus(caseId)
          setOcrStatus(st)
          failCount = 0
          if (st.status !== 'running') { clearInterval(timerRef.current!); timerRef.current = null }
        } catch { failCount++; if (failCount >= 3) { clearInterval(timerRef.current!); timerRef.current = null } }
      }, 2000)
    } catch { setError('启动 OCR 失败') }
  }

  return (
    <div style={{ border: '1px solid var(--macos-border)', borderRadius: '8px', padding: '12px', marginBottom: '12px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '13px', fontWeight: '500' }}>选择性 OCR 图片</span>
        <button type="button" onClick={runOcr} disabled={selectedCount === 0 || ocrStatus?.status === 'running'}
          style={{ padding: '5px 12px', background: 'var(--macos-accent)', color: '#fff', border: 'none', borderRadius: '6px', cursor: 'pointer', fontSize: '12px' }}>
          OCR 选中图片（{selectedCount} 张）
        </button>
      </div>
      {ocrStatus && (
        <div style={{ fontSize: '12px', color: '#86868b', margin: '6px 0' }}>
          {ocrStatus.status === 'running' ? `识别中 ${ocrStatus.done}/${ocrStatus.total}` : ''}
          {ocrStatus.status === 'completed' ? '完成' : ''}
          {ocrStatus.status === 'failed' ? '识别失败，请重试' : ''}
          {ocrStatus.failed && ocrStatus.failed.length > 0 && <span style={{ color: '#c00' }}>（失败卷：{ocrStatus.failed.join('、')}）</span>}
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
              <button type="button" onClick={e => { e.preventDefault(); toggleVol(vol, names) }}
                style={{ marginLeft: '8px', fontSize: '11px', border: '1px solid var(--macos-border)', background: 'none', borderRadius: '4px', cursor: 'pointer' }}>
                {names.every(n => s.has(n)) ? '清空' : '全选'}
              </button>
            </summary>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(90px, 1fr))', gap: '6px', marginTop: '6px' }}>
              {names.map(name => (
                <button key={name} type="button" onClick={() => toggle(vol, name)} aria-pressed={s.has(name)}
                  style={{ border: s.has(name) ? '2px solid var(--macos-accent)' : '1px solid var(--macos-border)', borderRadius: '6px', padding: '3px', cursor: 'pointer', textAlign: 'center', background: '#fff', position: 'relative' }}>
                  <img src={`${API_BASE}/cases/${caseId}/serve-file?file_path=${encodeURIComponent(name)}&dir=md`}
                    alt={`${vol} 图片 ${name}`} style={{ width: '100%', height: '60px', objectFit: 'cover', borderRadius: '4px', display: 'block' }} loading="lazy" />
                  {s.has(name) && <span style={{ position: 'absolute', top: '2px', right: '4px', color: 'var(--macos-accent)', fontWeight: 'bold', fontSize: '14px' }}>✓</span>}
                  <div style={{ fontSize: '10px', color: '#86868b', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name.slice(0, 8)}</div>
                </button>
              ))}
            </div>
          </details>
        )
      })}
    </div>
  )
}
