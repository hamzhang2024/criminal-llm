// PDF 页面管理：缩略图网格 + 页面旋转 + 乱码页修复闭环

import React, { useEffect, useState } from 'react'
import { API_BASE } from '../../../api/client'
import { getThumbnails, rotatePage, reconvertBlock } from '../../../api/cases'
import type { MdIssue } from '../../../api/cases'

interface PdfPageManagerProps {
  caseId: string
  pdfFilename: string            // processed/ 下文件名
  issues: MdIssue[]              // 该 PDF 对应 md 的乱码块（可能为空）
  onFixed: () => void            // 修复完成回调（父组件刷新状态）
}

// 缩略图 URL 是后端返回的根路径（/thumbnails/...），需拼上后端源：
// 开发模式 API_BASE='/api' → 同源相对路径（走 Vite 代理）；生产模式为 http://localhost:PORT
// 注意：API_BASE 是可变的 let，BACKEND_ORIGIN 在组件体内每次渲染计算，避免拿到陈旧快照

export function PdfPageManager({ caseId, pdfFilename, issues, onFixed }: PdfPageManagerProps) {
  const BACKEND_ORIGIN = API_BASE.replace(/\/api$/, '')
  const [thumbs, setThumbs] = useState<Array<{ page: number; url: string }>>([])
  const [loading, setLoading] = useState(true)
  const [rotations, setRotations] = useState<Map<number, number>>(new Map())  // page → 累计角度
  const [saving, setSaving] = useState(false)
  const [fixing, setFixing] = useState(false)
  const [message, setMessage] = useState('')
  const [cacheBust, setCacheBust] = useState(0)
  // 行内修复输入：当前展开的 issue 下标（null = 未展开）及其页码输入值
  // 注：macOS WKWebView 不实现 window.prompt（调用即抛 TypeError），故用行内输入代替
  const [fixingIssue, setFixingIssue] = useState<number | null>(null)
  const [fixPageInput, setFixPageInput] = useState('')

  useEffect(() => {
    setLoading(true)
    getThumbnails(caseId, pdfFilename, 'processed', 200)
      .then(r => { setThumbs(r.thumbnails || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [caseId, pdfFilename, cacheBust])

  const addRotation = (page: number, deg: number) => {
    setRotations(prev => {
      const next = new Map(prev)
      next.set(page, ((next.get(page) || 0) + deg) % 360)
      return next
    })
  }

  const saveRotations = async () => {
    setSaving(true)
    setMessage('')
    try {
      for (const [page, deg] of rotations) {
        if (deg !== 0) {
          await rotatePage(caseId, pdfFilename, page, deg)
          // 每成功一页即从 Map 出队：中途失败时重试不会重复累加已成功页的角度
          setRotations(prev => { const n = new Map(prev); n.delete(page); return n })
        }
      }
      setRotations(new Map())  // 兜底：循环全部成功时整体清空
      setCacheBust(b => b + 1)  // 旋转后缩略图缓存已失效，重新拉取
      setMessage('旋转已保存')
    } catch (e: any) {
      setMessage(`保存失败：${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  // 点击「重转并修复」：行内展开页码输入（替代 window.prompt，Tauri macOS 无此 API）
  const openFixInput = (idx: number) => {
    setFixingIssue(idx)
    setFixPageInput('')
    setMessage('')
  }

  // 确认行内输入的页码并执行重转修复
  const confirmFixIssue = async (issue: MdIssue) => {
    const page = parseInt(fixPageInput, 10)
    if (!page || page < 1) {
      setMessage('请输入有效的页码（从 1 开始的数字）')
      return
    }
    if (page > thumbs.length) {
      setMessage(`页码超出范围（共 ${thumbs.length} 页）`)
      return
    }
    setFixing(true)
    setMessage('')
    try {
      const r = await reconvertBlock(caseId, {
        file_path: pdfFilename, page,
        md_file: pdfFilename.replace(/\.pdf$/i, '.md'),
        start_line: issue.start_line, end_line: issue.end_line,
        invalidate_evidence: true,
      })
      setMessage(`修复完成${r.invalidated?.length ? `，${r.invalidated.length} 份相关证据已标记重提取（请回案件页点「提取证据」）` : ''}`)
      setFixingIssue(null)
      onFixed()
    } catch (e: any) {
      setMessage(`修复失败：${e.message}`)
    } finally {
      setFixing(false)
    }
  }

  if (loading) return <div style={{ padding: 24, color: '#86868b' }}>生成缩略图中...</div>

  return (
    <div style={{ flex: 1, overflow: 'auto', background: '#1a1a1e', padding: 16 }}>
      {issues.length > 0 && (
        <div style={{ background: '#fff3cd', color: '#664d03', borderRadius: 8, padding: '10px 14px', marginBottom: 12, fontSize: 13 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>⚠️ 检测到 {issues.length} 处识别异常（可能页面倒置或扫描异常）</div>
          {issues.map((iss, i) => (
            <div key={i} style={{ marginTop: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {iss.page_label || '未知页'}：{iss.preview.slice(0, 50)}…
                </span>
                <button onClick={() => (fixingIssue === i ? setFixingIssue(null) : openFixInput(i))} disabled={fixing}
                  style={{ padding: '4px 10px', fontSize: 12, border: 'none', borderRadius: 4, background: '#0d6efd', color: '#fff', cursor: 'pointer' }}>
                  {fixing ? '修复中…' : fixingIssue === i ? '收起' : '重转并修复'}
                </button>
              </div>
              {fixingIssue === i && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, paddingLeft: 4 }}>
                  <span style={{ fontSize: 12 }}>页码：</span>
                  <input
                    type="number" min={1} max={thumbs.length} value={fixPageInput}
                    onChange={e => setFixPageInput(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); confirmFixIssue(iss) } }}
                    placeholder={`1-${thumbs.length}`}
                    aria-label="输入该内容在 PDF 中的页码"
                    autoFocus
                    style={{ width: 72, padding: '3px 6px', fontSize: 12, borderRadius: 4, border: '1px solid #c3a955', background: '#fff' }}
                  />
                  <button onClick={() => confirmFixIssue(iss)} disabled={fixing || !fixPageInput.trim()}
                    style={{ padding: '3px 10px', fontSize: 12, border: 'none', borderRadius: 4, background: '#0d6efd', color: '#fff', cursor: 'pointer' }}>
                    {fixing ? '修复中…' : '确认'}
                  </button>
                  <button onClick={() => setFixingIssue(null)} disabled={fixing}
                    style={{ padding: '3px 10px', fontSize: 12, border: '1px solid #adb5bd', borderRadius: 4, background: 'transparent', color: '#664d03', cursor: 'pointer' }}>
                    取消
                  </button>
                  <span style={{ fontSize: 11, opacity: 0.8 }}>在下方缩略图中确认过的数字页码</span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {message && <div style={{ color: '#c8c8ce', fontSize: 13, marginBottom: 10 }}>{message}</div>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
        {thumbs.map(t => {
          const deg = rotations.get(t.page) || 0
          return (
            <div key={t.page} style={{ background: '#2c2c30', borderRadius: 8, padding: 8, textAlign: 'center' }}>
              <div style={{ overflow: 'hidden', borderRadius: 4, marginBottom: 6 }}>
                <img src={`${BACKEND_ORIGIN}${t.url}${cacheBust ? `?t=${cacheBust}` : ''}`} alt={`第${t.page}页`} loading="lazy"
                  style={{ width: '100%', transform: `rotate(${deg}deg)`, transition: 'transform 0.2s' }} />
              </div>
              <div style={{ fontSize: 12, color: '#c8c8ce', marginBottom: 6 }}>第 {t.page} 页{deg ? `（待保存 ${deg}°）` : ''}</div>
              <div style={{ display: 'flex', justifyContent: 'center', gap: 8 }}>
                <button onClick={() => addRotation(t.page, 270)} title="逆时针90°"
                  style={{ padding: '2px 10px', fontSize: 14, border: 'none', borderRadius: 4, background: '#48484e', color: '#fff', cursor: 'pointer' }}>↺</button>
                <button onClick={() => addRotation(t.page, 90)} title="顺时针90°"
                  style={{ padding: '2px 10px', fontSize: 14, border: 'none', borderRadius: 4, background: '#48484e', color: '#fff', cursor: 'pointer' }}>↻</button>
              </div>
            </div>
          )
        })}
      </div>
      {rotations.size > 0 && (
        <div style={{ position: 'sticky', bottom: 0, padding: '12px 0', textAlign: 'center' }}>
          <button onClick={saveRotations} disabled={saving}
            style={{ padding: '8px 24px', fontSize: 14, border: 'none', borderRadius: 8, background: 'var(--macos-accent)', color: '#fff', cursor: 'pointer' }}>
            {saving ? '保存中…' : `保存旋转（${rotations.size} 页）`}
          </button>
        </div>
      )}
    </div>
  )
}
