// 选择性 OCR 图片：按卷分组缩略图网格 + 点击放大预览 + 勾选 + 批量识别
import { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
import { API_BASE, getOcrImages, startOcrImages, getOcrStatus } from '../../../api'
import type { OcrImageGroup } from '../../../api/evidence'

interface Props {
  caseId: string
  // 未 OCR 图片数变化回调（父组件提取前提醒用）
  onUnocrCountChange?: (count: number) => void
  // 转换完成信号：false→true 时重新加载图片列表（转换后才生成 layout.json）
  conversionDone?: boolean
}

// 图片 URL（serve-file 按 basename 递归匹配，dir=md 命中 _images 子目录）
function imgUrl(caseId: string, name: string): string {
  return `${API_BASE}/cases/${caseId}/serve-file?file_path=${encodeURIComponent(name)}&dir=md`
}

export function SelectiveOCR({ caseId, onUnocrCountChange, conversionDone }: Props) {
  const [groups, setGroups] = useState<OcrImageGroup>({})
  const [selected, setSelected] = useState<Record<string, Set<string>>>({})
  const [loading, setLoading] = useState(false)
  const [ocrStatus, setOcrStatus] = useState<{ status: string; done: number; total: number; current?: string; failed?: string[] } | null>(null)
  const [error, setError] = useState('')
  // 放大预览：点击缩略图打开大图，在 lightbox 里决定是否勾选
  const [preview, setPreview] = useState<{ vol: string; name: string; url: string } | null>(null)

  // 轮询 timer 用 ref 保存，组件卸载时清理（避免定时器泄漏 + 卸载后 setState）
  const timerRef = useRef<number | null>(null)
  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current) }, [])

  // 放大预览打开时：Esc 关闭 + 锁定背景滚动
  useEffect(() => {
    if (!preview) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setPreview(null) }
    window.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [preview])

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

  // 转换完成后重新加载（卡片挂载时 layout.json 可能还没生成，转换完成才有图片可筛）
  const prevConversionDone = useRef(conversionDone)
  useEffect(() => {
    if (conversionDone && !prevConversionDone.current) loadImages()
    prevConversionDone.current = conversionDone
  }, [conversionDone, loadImages])

  // 未 OCR 图片数（ocr=false 的候选图），上报父组件用于「提取前提醒」
  useEffect(() => {
    if (!onUnocrCountChange) return
    let n = 0
    for (const imgs of Object.values(groups)) {
      for (const meta of Object.values(imgs)) {
        if (!meta.ocr) n++
      }
    }
    onUnocrCountChange(n)
  }, [groups, onUnocrCountChange])

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
                <div key={name} style={{ position: 'relative' }}>
                  {/* 点击图片 → 放大看原图（纯查看，勾选走角上 checkbox） */}
                  <button type="button" onClick={() => setPreview({ vol, name, url: imgUrl(caseId, name) })}
                    title="点击放大查看原图"
                    style={{ width: '100%', border: s.has(name) ? '2px solid var(--macos-accent)' : '1px solid var(--macos-border)', borderRadius: '6px', padding: '3px', cursor: 'zoom-in', textAlign: 'center', background: '#fff', display: 'block' }}>
                    <img src={imgUrl(caseId, name)}
                      alt={`${vol} 图片 ${name}`} style={{ width: '100%', height: '60px', objectFit: 'cover', borderRadius: '4px', display: 'block' }} loading="lazy" />
                    <div style={{ fontSize: '10px', color: '#86868b', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name.slice(0, 8)}</div>
                  </button>
                  {/* 独立勾选复选框（不与放大冲突） */}
                  <label title={s.has(name) ? '取消勾选' : '勾选此图'}
                    style={{ position: 'absolute', top: '5px', left: '5px', background: 'rgba(255,255,255,0.9)', borderRadius: '3px', display: 'flex', alignItems: 'center', cursor: 'pointer', padding: '1px 3px', boxShadow: '0 1px 3px rgba(0,0,0,0.2)' }}>
                    <input type="checkbox" checked={s.has(name)} onChange={() => toggle(vol, name)} style={{ cursor: 'pointer' }} />
                  </label>
                </div>
              ))}
            </div>
          </details>
        )
      })}

      {/* 放大预览：Portal 到 body（脱离卡片层叠上下文，fixed 必然覆盖全屏），
          点击任意处（含图片本身）或 Esc 关闭 */}
      {preview && createPortal(
        <div onClick={() => setPreview(null)}
          role="dialog" aria-label={`图片预览 ${preview.name}`}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)', zIndex: 10000, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', cursor: 'zoom-out' }}>
          <img src={preview.url} alt={preview.name}
            style={{ maxWidth: '92vw', maxHeight: '88vh', objectFit: 'contain', borderRadius: '4px', background: '#fff', pointerEvents: 'none' }} />
          <div style={{ marginTop: '10px', fontSize: '12px', color: '#ccc', pointerEvents: 'none' }}>
            {preview.vol} · {preview.name}（点击任意处或按 Esc 关闭）
          </div>
        </div>,
        document.body
      )}
    </div>
  )
}
