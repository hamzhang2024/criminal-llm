// PDF 页面管理：缩略图网格 + 页面旋转 + 乱码页修复闭环

import React, { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { API_BASE } from '../../../api/client'
import { getThumbnails, rotatePage, reconvertBlock, reconvertVolume } from '../../../api/cases'
import type { MdIssue } from '../../../api/cases'
import { showConfirm } from '../../../components/MacOSDialog'

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
  const [loadError, setLoadError] = useState('')
  const [rotations, setRotations] = useState<Map<number, number>>(new Map())  // page → 累计角度
  const [saving, setSaving] = useState(false)
  const [fixing, setFixing] = useState(false)
  // 整卷重转中（多页倒置的根治路径：重建 md + 失效该卷证据）
  const [reconverting, setReconverting] = useState(false)
  const [message, setMessage] = useState('')
  const [cacheBust, setCacheBust] = useState(0)
  // 行内修复输入：当前展开的 issue 下标（null = 未展开）及其页码输入值
  // 注：macOS WKWebView 不实现 window.prompt（调用即抛 TypeError），故用行内输入代替
  const [fixingIssue, setFixingIssue] = useState<number | null>(null)
  const [fixPageInput, setFixPageInput] = useState('')
  // 「定位」高亮：当前高亮的缩略图页码（短暂闪烁后自动取消）
  const [highlightPage, setHighlightPage] = useState<number | null>(null)
  // 刚保存旋转的页码：重转输入框预填优先取它（旋转→重转闭环），其次 issue 的估算页
  const [lastRotatedPage, setLastRotatedPage] = useState<number | null>(null)
  // 缩略图 DOM 引用（页码 → 容器），供「定位」scrollIntoView 用
  const thumbRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  // 查看原图的页码（null = 关闭）
  const [viewingPage, setViewingPage] = useState<number | null>(null)
  // 网格页码跳转输入（几百页的 PDF 滚动定位不现实）
  const [jumpInput, setJumpInput] = useState('')
  // 放大查看模态框内的页码跳转输入
  const [viewJumpInput, setViewJumpInput] = useState('')

  // 放大查看：Esc 关闭 + ←/→ 翻页
  useEffect(() => {
    if (viewingPage === null) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setViewingPage(null)
      else if (e.key === 'ArrowLeft') setViewingPage(p => (p !== null && p > 1 ? p - 1 : p))
      else if (e.key === 'ArrowRight') setViewingPage(p => (p !== null && p < thumbs.length ? p + 1 : p))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [viewingPage, thumbs.length])

  // 高亮闪烁动画结束后自动取消高亮
  useEffect(() => {
    if (highlightPage == null) return
    const t = window.setTimeout(() => setHighlightPage(null), 2000)
    return () => clearTimeout(t)
  }, [highlightPage])

  useEffect(() => {
    setLoading(true)
    setLoadError('')
    // 400px：需能辨认文字朝向（200px 看不清），端点上限 800
    getThumbnails(caseId, pdfFilename, 'processed', 400)
      .then(r => { setThumbs(r.thumbnails || []); setLoading(false) })
      // 失败须明示（文件不存在/生成失败），否则空网格看起来像"PDF 无页面"
      .catch((e) => { setLoadError(e instanceof Error ? e.message : String(e)); setLoading(false) })
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
    // 记录本次旋转的页码（供「重转并修复」预填：旋转的页通常就是要重转的页）
    const rotatedPages = [...rotations.entries()].filter(([, d]) => d !== 0).map(([p]) => p)
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
      if (rotatedPages.length > 0) setLastRotatedPage(rotatedPages[0])
      // 旋转只是改了 PDF 页面方向，md 乱码块仍在——仍有未修复异常时引导用户接着重转
      setMessage(issues.length > 0
        ? '旋转已保存。点击上方「重转并修复」重新转换该页'
        : '旋转已保存')
    } catch (e: any) {
      setMessage(`保存失败：${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  // 「定位」：滚动到估算页缩略图并金色高亮闪烁（估算误差 ±2 页，用户视觉上确认即可）
  const locatePage = (page: number | null) => {
    if (page == null) return
    const el = thumbRefs.current.get(page)
    if (!el) {
      setMessage(`未找到第 ${page} 页缩略图`)
      return
    }
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setHighlightPage(page)
  }

  // 点击「重转并修复」：行内展开页码输入（替代 window.prompt，Tauri macOS 无此 API）
  // 预填页码：优先刚旋转保存的页（旋转→重转闭环），其次后端估算页，都没有则留空手输
  const openFixInput = (idx: number) => {
    setFixingIssue(idx)
    const prefill = lastRotatedPage ?? issues[idx]?.estimated_page
    setFixPageInput(prefill != null ? String(prefill) : '')
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
    // 二次确认：填错页码会把错误页文字写进乱码块，且该块不再被扫描识别、无法再次修复
    const confirmed = await showConfirm({
      title: '确认修复',
      message: `将提取 PDF 第 ${page} 页的文字，替换 ${issue.md_file} 中的识别异常块（第 ${issue.start_line + 1}-${issue.end_line + 1} 行）。\n\n原 md 会自动备份为 ${issue.md_file}.fix-bak；替换后该块不再被异常扫描识别，请确认页码无误。`,
      confirmText: '确认修复',
      cancelText: '取消',
    })
    if (!confirmed) return
    setFixing(true)
    setMessage('')
    try {
      const r = await reconvertBlock(caseId, {
        file_path: pdfFilename, page,
        // md 名直接取扫描结果（PDF 与 md 的 _去水印 后缀可能不一致，按 PDF 名推导会失配）
        md_file: issue.md_file,
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
  if (loadError) return <div style={{ padding: 24, color: '#d66' }}>缩略图生成失败或文件不存在：{loadError}</div>

  return (
    <div style={{ flex: 1, overflow: 'auto', background: '#1a1a1e', padding: 16 }}>
      {/* 定位高亮的闪烁动画（金色光晕，与横幅警告色系一致） */}
      <style>{`@keyframes pdfPageFlash { 0%,100% { box-shadow: 0 0 0 0 rgba(245,197,24,0) } 50% { box-shadow: 0 0 14px 4px rgba(245,197,24,0.85) } }`}</style>
      {issues.length > 0 && (
        <div style={{ background: '#fff3cd', color: '#664d03', borderRadius: 8, padding: '10px 14px', marginBottom: 12, fontSize: 13 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>⚠️ 检测到 {issues.length} 处识别异常（可能页面倒置或扫描异常）</div>
          {issues.map((iss, i) => (
            <div key={i} style={{ marginTop: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {iss.page_label || '未知页'}
                  {iss.estimated_page != null && `（估算约第 ${iss.estimated_page} 页，可能偏差较大）`}
                  ：{iss.preview.slice(0, 50)}…
                </span>
                {iss.estimated_page != null && (
                  <button onClick={() => locatePage(iss.estimated_page)}
                    title="滚动到估算页缩略图并高亮"
                    style={{ padding: '4px 10px', fontSize: 12, border: 'none', borderRadius: 4, background: '#c3a955', color: '#fff', cursor: 'pointer' }}>
                    定位
                  </button>
                )}
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
      {/* 页码跳转条：几百页的 PDF 滚动定位不现实 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <span style={{ fontSize: 12, color: '#c8c8ce' }}>共 {thumbs.length} 页，跳到第</span>
        <input
          type="number" min={1} max={thumbs.length} value={jumpInput}
          onChange={e => setJumpInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') {
              const n = parseInt(jumpInput, 10)
              if (n >= 1 && n <= thumbs.length) locatePage(n)
            }
          }}
          aria-label="跳转到指定页缩略图"
          style={{ width: 64, padding: '3px 6px', fontSize: 12, borderRadius: 4, border: '1px solid #48484e', background: '#2c2c30', color: '#fff', textAlign: 'center' }}
        />
        <button
          onClick={() => {
            const n = parseInt(jumpInput, 10)
            if (n >= 1 && n <= thumbs.length) locatePage(n)
          }}
          style={{ padding: '3px 12px', fontSize: 12, border: 'none', borderRadius: 4, background: '#48484e', color: '#fff', cursor: 'pointer' }}>
          跳转
        </button>
        <span style={{ fontSize: 11, color: '#86868b' }}>点击缩略图可放大查看（←→ 翻页）</span>
        <button
          onClick={async () => {
            const confirmed = await showConfirm({
              title: '整卷重转',
              message: `将重建「${pdfFilename}」的 md 文件（转换质量差/多页倒置的根治方法）。\n\n· 该卷已提取的证据会失效，需要重新提取\n· 大卷耗时较长（取决于 PDF 引擎配额）\n\n确认继续？`,
              confirmText: '开始重转',
              variant: 'warning',
            })
            if (!confirmed) return
            setReconverting(true)
            setMessage('整卷重转中，请耐心等待（大卷可能需要几十分钟）…')
            try {
              const r = await reconvertVolume(caseId, pdfFilename)
              if (r.success) {
                setMessage(`整卷重转完成：${r.md_file}（${r.chars} 字符${r.invalidated?.length ? `，已失效 ${r.invalidated.length} 份证据` : ''}）。请回案件页重新「提取证据」`)
                setCacheBust(b => b + 1)
                onFixed()
              } else {
                setMessage(`整卷重转失败：${r.error || '未知错误'}`)
              }
            } catch (e: any) {
              setMessage(`整卷重转失败：${e.message || e}`)
            } finally {
              setReconverting(false)
            }
          }}
          disabled={reconverting}
          title="重建该卷 md（多页倒置/转换质量差时用）"
          style={{
            marginLeft: 'auto', padding: '3px 12px', fontSize: 12, border: '1px solid #c3a955',
            borderRadius: 4, background: 'transparent', color: '#c3a955', cursor: reconverting ? 'not-allowed' : 'pointer',
            opacity: reconverting ? 0.6 : 1,
          }}>
          {reconverting ? '重转中…' : '整卷重转'}
        </button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
        {thumbs.map(t => {
          const deg = rotations.get(t.page) || 0
          const highlighted = highlightPage === t.page
          return (
            <div key={t.page}
              ref={el => { if (el) thumbRefs.current.set(t.page, el); else thumbRefs.current.delete(t.page) }}
              style={{
                background: '#2c2c30', borderRadius: 8, padding: 8, textAlign: 'center',
                // 定位高亮：金色边框 + 短暂闪烁动画（2s 后由 effect 自动取消）
                ...(highlighted ? { outline: '2px solid #f5c518', animation: 'pdfPageFlash 0.65s ease-in-out 3' } : {}),
              }}>
              <div style={{ overflow: 'hidden', borderRadius: 4, marginBottom: 6 }}>
                <img
                  src={`${BACKEND_ORIGIN}${t.url}${cacheBust ? `?t=${cacheBust}` : ''}`}
                  alt={`第${t.page}页`}
                  loading="lazy"
                  style={{ width: '100%', transform: `rotate(${deg}deg)`, transition: 'transform 0.2s', cursor: 'zoom-in' }}
                  onClick={() => setViewingPage(t.page)}
                />
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
      {rotations.size > 0 && createPortal(
        // 悬浮保存按钮：sticky 在嵌套滚动容器里会沉到几百张缩略图底部（用户找不到），
        // 改 fixed 右下角常驻
        <div style={{ position: 'fixed', bottom: '24px', right: '24px', zIndex: 9999, textAlign: 'center' }}>
          <button onClick={saveRotations} disabled={saving}
            style={{
              padding: '10px 22px', fontSize: 14, border: 'none', borderRadius: 10,
              background: 'var(--macos-accent)', color: '#fff', cursor: 'pointer',
              boxShadow: '0 4px 16px rgba(0,0,0,0.35)',
            }}>
            {saving ? '保存中…' : `保存旋转（${rotations.size} 页）`}
          </button>
        </div>,
        document.body
      )}

      {/* 原图查看模态框：Portal 到 body（脱离卡片 transform 层叠上下文，防闪烁/跳动）；
          用后端渲染的高清 PNG 而非 iframe 内嵌 PDF（WKWebView iframe 不支持 PDF，显示乱码） */}
      {viewingPage !== null && createPortal(
        <div
          onClick={() => setViewingPage(null)}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.92)',
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            zIndex: 10000,
          }}
        >
          {/* 工具条：翻页 + 页码跳转 + 选为修复页 + 关闭（阻止冒泡，避免点工具条关模态） */}
          <div
            onClick={e => e.stopPropagation()}
            style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}
          >
            <button
              onClick={() => setViewingPage(p => (p !== null && p > 1 ? p - 1 : p))}
              disabled={viewingPage <= 1}
              style={{ padding: '6px 14px', fontSize: 16, border: 'none', borderRadius: 6, background: 'rgba(255,255,255,0.2)', color: '#fff', cursor: 'pointer' }}>
              ‹
            </button>
            <span style={{ color: '#fff', fontSize: 14 }}>第 {viewingPage} / {thumbs.length} 页</span>
            <button
              onClick={() => setViewingPage(p => (p !== null && p < thumbs.length ? p + 1 : p))}
              disabled={viewingPage >= thumbs.length}
              style={{ padding: '6px 14px', fontSize: 16, border: 'none', borderRadius: 6, background: 'rgba(255,255,255,0.2)', color: '#fff', cursor: 'pointer' }}>
              ›
            </button>
            {/* 就地旋转：发现倒置当场改，不用回网格找小按钮 */}
            <button
              onClick={() => addRotation(viewingPage, 270)}
              title="逆时针90°"
              style={{ padding: '6px 12px', fontSize: 15, border: 'none', borderRadius: 6, background: 'rgba(255,255,255,0.2)', color: '#fff', cursor: 'pointer' }}>
              ↺
            </button>
            <button
              onClick={() => addRotation(viewingPage, 90)}
              title="顺时针90°"
              style={{ padding: '6px 12px', fontSize: 15, border: 'none', borderRadius: 6, background: 'rgba(255,255,255,0.2)', color: '#fff', cursor: 'pointer' }}>
              ↻
            </button>
            {(rotations.get(viewingPage) || 0) !== 0 && (
              <span style={{ color: '#f5c518', fontSize: 12 }}>待保存 {rotations.get(viewingPage)}°</span>
            )}
            <input
              type="number" min={1} max={thumbs.length} value={viewJumpInput}
              onChange={e => setViewJumpInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') {
                  const n = parseInt(viewJumpInput, 10)
                  if (n >= 1 && n <= thumbs.length) setViewingPage(n)
                }
              }}
              placeholder="页码"
              aria-label="放大查看跳转到指定页"
              style={{ width: 60, padding: '5px 6px', fontSize: 13, borderRadius: 4, border: '1px solid rgba(255,255,255,0.3)', background: 'rgba(255,255,255,0.12)', color: '#fff', textAlign: 'center' }}
            />
            {fixingIssue !== null && (
              <button
                onClick={() => {
                  setFixPageInput(String(viewingPage))
                  setViewingPage(null)
                  setMessage(`已选择第 ${viewingPage} 页，确认无误后点「确认」执行修复`)
                }}
                title="把当前查看的页码填入修复输入框"
                style={{ padding: '6px 14px', fontSize: 13, border: 'none', borderRadius: 6, background: '#0d6efd', color: '#fff', cursor: 'pointer' }}>
                选为修复页
              </button>
            )}
            <button
              onClick={() => setViewingPage(null)}
              style={{ padding: '6px 16px', fontSize: 14, border: 'none', borderRadius: 6, background: 'rgba(255,255,255,0.2)', color: '#fff', cursor: 'pointer' }}>
              ✕ 关闭
            </button>
          </div>
          <img
            key={`${viewingPage}-${rotations.get(viewingPage) || 0}`}
            src={`${BACKEND_ORIGIN}/api/cases/${caseId}/pdf-page-image?file_path=${encodeURIComponent(pdfFilename)}&dir=processed&page=${viewingPage}&dpi=150`}
            alt={`第 ${viewingPage} 页`}
            onClick={e => e.stopPropagation()}
            style={{
              maxWidth: '92vw', maxHeight: '82vh', objectFit: 'contain', background: '#fff', borderRadius: 4,
              transform: `rotate(${rotations.get(viewingPage) || 0}deg)`,
            }}
          />
        </div>,
        document.body
      )}
    </div>
  )
}
