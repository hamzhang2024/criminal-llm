import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { API_BASE } from '../api'

// 从 CDN 加载 pdf.js
const PDFJS_CDN = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174'

function loadPdfScript(): Promise<any> {
  if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib)

  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = `${PDFJS_CDN}/pdf.min.js`
    script.onload = () => {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = `${PDFJS_CDN}/pdf.worker.min.js`
      resolve(window.pdfjsLib)
    }
    script.onerror = () => reject(new Error('pdf.js 加载失败'))
    document.head.appendChild(script)
  })
}

interface PdfAnnotation {
  id: string
  pageNum?: number
  x: number
  y: number
  text: string
  color: string
  pdfFile?: string
  createdAt: string
}

interface PdfViewerProps {
  caseId: string
  pdfFilename: string
  annotations: PdfAnnotation[]
  onAddAnnotation: (annotation: PdfAnnotation) => void
  onUpdateAnnotation: (id: string, text: string) => void
  onDragAnnotation: (id: string, x: number, y: number) => void
  onDeleteAnnotation: (id: string) => void
  annotationMode: boolean
}

const NOTE_COLORS = ['#fff9c4', '#ffecb3', '#ffe0b2', '#ffcdd2', '#f8bbd0', '#e1bee7', '#d1c4e9', '#c5cae9', '#bbdefb', '#b3e5fc', '#b2ebf2', '#b2dfdb', '#c8e6c9', '#dcedc8', '#f0f4c3']
const randomColor = () => NOTE_COLORS[Math.floor(Math.random() * NOTE_COLORS.length)]
const generateId = () => Math.random().toString(36).substr(2, 9)

// 全局状态：是否有任何页面正在创建/编辑批注（跨页面互斥）
let isAnnotationEditing = false

// ========== 批注覆盖层 ==========

function PageAnnotationOverlay({ annotations, annotationMode, onUpdateNote, onDeleteNote, onDragNote, pageWidth }: {
  annotations: PdfAnnotation[]
  annotationMode: boolean
  onUpdateNote: (id: string, text: string) => void
  onDeleteNote: (id: string) => void
  onDragNote: (id: string, x: number, y: number) => void
  pageWidth: number
}) {
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const dragStateRef = useRef<{
    noteId: string
    startX: number
    startY: number
    origX: number
    origY: number
    el: HTMLElement
  } | null>(null)

  useEffect(() => {
    if (editing) {
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [editing])

  const saveEdit = () => {
    if (!editing || !draft.trim()) { setEditing(null); return }
    onUpdateNote(editing, draft.trim())
    setEditing(null)
    setDraft('')
  }

  // 拖拽批注
  const handleMouseDown = (e: React.MouseEvent, noteId: string) => {
    if (!annotationMode) return
    e.stopPropagation()
    e.preventDefault()
    const el = e.currentTarget as HTMLElement
    const note = annotations.find(a => a.id === noteId)
    if (!note) return

    const startX = e.clientX
    const startY = e.clientY
    const origX = note.x
    const origY = note.y
    dragStateRef.current = { noteId, startX, startY, origX, origY, el }

    const onMove = (ev: MouseEvent) => {
      const d = dragStateRef.current
      if (!d) return
      const dx = ev.clientX - d.startX
      const dy = ev.clientY - d.startY
      // 百分比坐标
      const rect = el.parentElement?.getBoundingClientRect()
      if (!rect) return
      const newX = ((d.origX / 100) * rect.width + dx) / rect.width * 100
      const newY = ((d.origY / 100) * rect.height + dy) / rect.height * 100
      d.el.style.left = `${newX}%`
      d.el.style.top = `${newY}%`
    }
    const onUp = (ev: MouseEvent) => {
      const d = dragStateRef.current
      if (!d) return
      dragStateRef.current = null
      const dx = ev.clientX - d.startX
      const dy = ev.clientY - d.startY
      const rect = el.parentElement?.getBoundingClientRect()
      if (!rect) return
      const newX = ((d.origX / 100) * rect.width + dx) / rect.width * 100
      const newY = ((d.origY / 100) * rect.height + dy) / rect.height * 100
      onDragNote(d.noteId, newX, newY)
      document.removeEventListener('mousemove', onMove)
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp, { once: true })
  }

  if (annotations.length === 0 && !editing) return null

  return (
    <div style={{
      position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
      pointerEvents: 'none', zIndex: 50,
    }}>
      {annotations.map(note => (
        <div
          key={note.id}
          onMouseEnter={() => setHoveredId(note.id)}
          onMouseLeave={() => setHoveredId(null)}
          onClick={e => {
            if (!annotationMode) return
            e.stopPropagation()
            setEditing(note.id)
            setDraft(note.text)
          }}
          onMouseDown={e => handleMouseDown(e, note.id)}
          style={{
            position: 'absolute',
            left: `${note.x}%`,
            top: `${note.y}%`,
            transform: 'translate(-50%, -50%)',
            width: `${Math.max(120, pageWidth * 0.22)}px`,
            maxWidth: `${pageWidth * 0.5}px`,
            minHeight: '32px',
            maxHeight: '120px',
            overflow: 'auto',
            padding: '6px 8px',
            background: `${note.color}cc`,
            borderRadius: '4px',
            boxShadow: '0 2px 6px rgba(0,0,0,0.1)',
            fontSize: '10px',
            lineHeight: '1.5',
            color: '#333',
            cursor: annotationMode ? 'grab' : 'default',
            pointerEvents: 'auto',
          }}
        >
          <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{note.text}</div>
          {hoveredId === note.id && annotationMode && (
            <div
              onClick={e => { e.stopPropagation(); onDeleteNote(note.id) }}
              style={{
                position: 'absolute', top: '-5px', right: '-5px',
                width: '16px', height: '16px', borderRadius: '50%',
                background: '#ef4444', color: '#fff',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '12px', fontWeight: 700, cursor: 'pointer',
                boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                lineHeight: 1,
              }}
            >
              ×
            </div>
          )}
        </div>
      ))}

      {editing && (
        <div onClick={e => e.stopPropagation()} style={{
          position: 'absolute',
          left: (() => {
            const note = annotations.find(a => a.id === editing)
            return note ? `${note.x}%` : undefined
          })(),
          top: (() => {
            const note = annotations.find(a => a.id === editing)
            return note ? `${note.y}%` : undefined
          })(),
          transform: 'translate(-50%, -50%)',
          zIndex: 100,
          pointerEvents: 'auto',
        }}>
          <div style={{
            width: '220px', background: '#fff',
            borderRadius: '7px', boxShadow: '0 4px 16px rgba(0,0,0,0.22)',
            overflow: 'hidden', border: '1px solid #e0e0e0',
          }}>
            <textarea
              ref={inputRef}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey && draft.trim()) {
                  e.preventDefault()
                  saveEdit()
                }
                if (e.key === 'Escape') {
                  e.stopPropagation()
                  setEditing(null)
                  setDraft('')
                }
              }}
              placeholder="修改批注..."
              rows={3}
              style={{
                width: '100%', padding: '10px 12px', fontSize: '12px',
                border: 'none', outline: 'none', resize: 'none',
                fontFamily: 'inherit', background: '#fffdf0',
                boxSizing: 'border-box', lineHeight: '1.5',
              }}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '6px', padding: '6px 10px', borderTop: '1px solid #eee' }}>
              <button
                onClick={() => { setEditing(null); setDraft('') }}
                style={{
                  padding: '3px 10px', fontSize: '11px', borderRadius: '4px',
                  background: '#f5f5f5', border: '1px solid #ddd', cursor: 'pointer',
                  color: '#666',
                }}
              >
                取消
              </button>
              <button
                onClick={saveEdit}
                disabled={!draft.trim()}
                style={{
                  padding: '3px 10px', fontSize: '11px', borderRadius: '4px',
                  background: draft.trim() ? '#1e3a5f' : '#eee',
                  color: draft.trim() ? '#fff' : '#999',
                  border: 'none', cursor: draft.trim() ? 'pointer' : 'default',
                }}
              >
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ========== 单页组件（懒加载渲染） ==========

function PdfPage({ pdfDoc, pageNum, scale, annotations, annotationMode, onCreateNote, onUpdateNote, onDeleteNote, onDragNote, scrollContainer }: {
  pdfDoc: any
  pageNum: number
  scale: number
  annotations: PdfAnnotation[]
  annotationMode: boolean
  onCreateNote: (x: number, y: number, text: string) => void
  onUpdateNote: (id: string, text: string) => void
  onDeleteNote: (id: string) => void
  onDragNote: (id: string, x: number, y: number) => void
  scrollContainer: React.RefObject<HTMLDivElement>
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const renderTaskRef = useRef<any>(null)
  const [pageHeight, setPageHeight] = useState(0)
  const [pageWidth, setPageWidth] = useState(612)
  const [creating, setCreating] = useState<{ x: number; y: number } | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // DPR 限制到 1.5 降低渲染开销
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5)

  useEffect(() => {
    if (creating || editing) {
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [creating, editing])

  // 点击页面创建批注（点击空处）
  const handlePageClick = (e: React.MouseEvent) => {
    if (!annotationMode || creating || editing || isAnnotationEditing) return
    if (e.target !== e.currentTarget && !(e.target as HTMLElement).closest('.pdf-page-bg')) return
    // 点击的是 canvas 本身，创建批注
    const rect = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * 100
    const y = ((e.clientY - rect.top) / rect.height) * 100
    isAnnotationEditing = true
    setCreating({ x, y })
    setDraft('')
  }

  const saveNew = () => {
    isAnnotationEditing = false
    if (!creating || !draft.trim()) { setCreating(null); return }
    onCreateNote(creating.x, creating.y, draft.trim())
    setCreating(null)
    setDraft('')
  }

  const saveEdit = () => {
    isAnnotationEditing = false
    if (!editing || !draft.trim()) { setEditing(null); return }
    onUpdateNote(editing, draft.trim())
    setEditing(null)
    setDraft('')
  }


  useEffect(() => {
    let cancelled = false

    const el = containerRef.current
    if (!el) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && pageHeight === 0) {
          renderPage()
        }
      },
      { root: scrollContainer.current, rootMargin: '300px 0px' }
    )

    observer.observe(el)
    return () => { cancelled = true; renderTaskRef.current?.cancel(); observer.disconnect() }
  }, [pdfDoc, pageNum, scale])

  // scale 变化时重置并重新渲染
  useEffect(() => {
    setPageHeight(0)
    if (canvasRef.current) {
      const ctx = canvasRef.current.getContext('2d')
      if (ctx) ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height)
    }
    const timer = setTimeout(() => renderPage(), 50)
    return () => clearTimeout(timer)
  }, [scale, pdfDoc])

  const renderPage = useCallback(async () => {
    try {
      const page = await pdfDoc.getPage(pageNum)
      const viewport = page.getViewport({ scale })
      setPageWidth(viewport.width)
      setPageHeight(viewport.height)
      const canvas = canvasRef.current
      if (!canvas) return
      canvas.width = viewport.width * dpr
      canvas.height = viewport.height * dpr
      const ctx = canvas.getContext('2d')!
      ctx.scale(dpr, dpr)
      renderTaskRef.current = page.render({ canvasContext: ctx, viewport })
      await renderTaskRef.current.promise
    } catch { /* ignore */ }
  }, [pdfDoc, pageNum, scale])

  return (
    <>
      <div
        ref={containerRef}
        className="pdf-page-bg"
        onClick={handlePageClick}
        style={{ position: 'relative', background: '#e8e6df', height: pageHeight || 300, display: 'flex', justifyContent: 'center' }}
      >
        {pageHeight === 0 && (
          <div style={{
            height: 300, display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: '#f0efe8', fontSize: '12px', color: '#aaa',
          }}>
            加载中...
          </div>
        )}
        <canvas ref={canvasRef} style={{ display: pageHeight > 0 ? 'block' : 'none' }} />
        {pageHeight > 0 && (
          <PageAnnotationOverlay
            annotations={annotations}
            annotationMode={annotationMode}
            onUpdateNote={onUpdateNote}
            onDeleteNote={onDeleteNote}
            onDragNote={onDragNote}
            pageWidth={pageWidth}
          />
        )}
      </div>

      {/* 正在创建/编辑的便签输入框（页面下方） */}
      {(creating || editing) && (
        <div onClick={e => e.stopPropagation()} style={{
          position: 'absolute',
          left: creating ? `${creating.x}%` : undefined,
          top: creating ? `${creating.y}%` : undefined,
          ...(editing ? (() => {
            const note = annotations.find(a => a.id === editing)
            return note ? { left: `${note.x}%`, top: `${note.y}%` } : {}
          })() : {}),
          transform: 'translate(-50%, -50%)',
          zIndex: 100,
        }}>
          <div style={{
            width: '220px', background: '#fff',
            borderRadius: '7px', boxShadow: '0 4px 16px rgba(0,0,0,0.22)',
            overflow: 'hidden', border: '1px solid #e0e0e0',
          }}>
            <textarea
              ref={inputRef}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey && draft.trim()) {
                  e.preventDefault()
                  if (editing) saveEdit()
                  else saveNew()
                }
                if (e.key === 'Escape') {
                  e.stopPropagation()
                  isAnnotationEditing = false
                  setCreating(null)
                  setEditing(null)
                  setDraft('')
                }
              }}
              placeholder={editing ? '修改批注...' : '输入批注...'}
              rows={3}
              style={{
                width: '100%', padding: '10px 12px', fontSize: '12px',
                border: 'none', outline: 'none', resize: 'none',
                fontFamily: 'inherit', background: '#fffdf0',
                boxSizing: 'border-box', lineHeight: '1.5',
              }}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '6px', padding: '6px 10px', borderTop: '1px solid #eee' }}>
              <button
                onClick={() => { isAnnotationEditing = false; setCreating(null); setEditing(null); setDraft('') }}
                style={{
                  padding: '3px 10px', fontSize: '11px', borderRadius: '4px',
                  background: '#f5f5f5', border: '1px solid #ddd', cursor: 'pointer',
                  color: '#666',
                }}
              >
                取消
              </button>
              <button
                onClick={editing ? saveEdit : saveNew}
                disabled={!draft.trim()}
                style={{
                  padding: '3px 10px', fontSize: '11px', borderRadius: '4px',
                  background: draft.trim() ? '#1e3a5f' : '#eee',
                  color: draft.trim() ? '#fff' : '#999',
                  border: 'none', cursor: draft.trim() ? 'pointer' : 'default',
                }}
              >
                {editing ? '保存' : '添加'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

// ========== 主组件 ==========

export function PdfViewer({ caseId, pdfFilename, annotations, onAddAnnotation, onUpdateAnnotation, onDragAnnotation, onDeleteAnnotation, annotationMode }: PdfViewerProps) {
  const [pdfDoc, setPdfDoc] = useState<any>(null)
  const [numPages, setNumPages] = useState(0)
  const [scale, setScale] = useState(1.0)
  const [error, setError] = useState<string | null>(null)
  const [fitMode, setFitMode] = useState<'fit' | 'original'>('fit')
  const [jumpPage, setJumpPage] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const pdfPageWidthRef = useRef(0)

  // 加载 PDF.js 和文档
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const pdfjsLib = await loadPdfScript()
        if (cancelled) return
        const url = `${API_BASE}/cases/${caseId}/serve-file?file_path=${encodeURIComponent(pdfFilename)}&dir=processed`
        const pdf = await pdfjsLib.getDocument(url).promise
        if (cancelled) { pdf.destroy(); return }
        const page = await pdf.getPage(1)
        const viewport = page.getViewport({ scale: 1 })
        if (cancelled) { pdf.destroy(); return }
        pdfPageWidthRef.current = viewport.width
        setPdfDoc(pdf)
        setNumPages(pdf.numPages)
        setError(null)

        // 加载完成后立即计算适配缩放
        requestAnimationFrame(() => {
          const el = containerRef.current
          if (el && el.clientWidth > 0) {
            const containerWidth = el.clientWidth - 20
            const fitScale = containerWidth / viewport.width
            setScale(fitScale)
          }
        })
      } catch (e: any) {
        if (!cancelled) setError(e.message || 'PDF 加载失败')
      }
    }
    load()
    return () => { cancelled = true }
  }, [caseId, pdfFilename])

  // 窗口大小变化时重新计算
  useEffect(() => {
    if (fitMode !== 'fit') return
    const el = containerRef.current
    if (!el || pdfPageWidthRef.current === 0) return

    const handleResize = () => {
      const w = el.clientWidth - 20
      if (w > 0) {
        setScale(w / pdfPageWidthRef.current)
      }
    }

    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [fitMode])

  // 切换适配模式
  const toggleFit = () => {
    const el = containerRef.current
    if (!el || pdfPageWidthRef.current === 0) return
    const w = el.clientWidth - 20
    if (w <= 0) return

    if (fitMode === 'fit') {
      setScale(1.0)
      setFitMode('original')
    } else {
      const newScale = w / pdfPageWidthRef.current
      setScale(newScale)
      setFitMode('fit')
    }
  }

  // 创建批注（弹出输入框）
  const handleCreateAnnotation = useCallback((pageNum: number, x: number, y: number, text: string) => {
    if (!text.trim()) return
    const id = generateId()
    onAddAnnotation({
      id, pageNum, x, y,
      text: text.trim(), color: randomColor(),
      pdfFile: pdfFilename,
      createdAt: new Date().toISOString(),
    })
  }, [pdfFilename, onAddAnnotation])

  // 拖拽批注
  const handleDragAnnotation = useCallback((id: string, x: number, y: number) => {
    onDragAnnotation(id, x, y)
  }, [onDragAnnotation])

  const handleJump = () => {
    const n = parseInt(jumpPage)
    if (n >= 1 && n <= numPages) {
      const el = document.getElementById(`pdf-page-${n}`)
      el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setJumpPage('')
    }
  }

  if (error) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#999', fontSize: '14px' }}>
        {error}
      </div>
    )
  }

  if (!pdfDoc) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#999', fontSize: '14px' }}>
        加载 PDF...
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* 顶部工具栏 */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '6px 10px',
        background: '#f5f4ef',
        borderBottom: '1px solid #e8e6df',
        flexShrink: 0, gap: '8px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '12px', color: '#666' }}>
            共 {numPages} 页
          </span>
          {/* 页码跳转 */}
          <input
            type="number"
            min={1}
            max={numPages}
            value={jumpPage}
            onChange={e => setJumpPage(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleJump() }}
            placeholder="页码"
            style={{
              width: '55px', padding: '3px 6px', fontSize: '12px',
              border: '1px solid #ddd', borderRadius: '3px',
              textAlign: 'center', background: '#fff', outline: 'none',
            }}
          />
          <button onClick={handleJump} style={{
            padding: '3px 8px', fontSize: '11px', borderRadius: '4px',
            background: '#fff', border: '1px solid #ddd', cursor: 'pointer', color: '#666',
          }}>
            跳转
          </button>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <button onClick={toggleFit} style={{
            padding: '3px 8px', fontSize: '11px', borderRadius: '4px',
            background: fitMode === 'fit' ? '#1e3a5f' : '#fff',
            color: fitMode === 'fit' ? '#fff' : '#666',
            border: `1px solid ${fitMode === 'fit' ? '#1e3a5f' : '#ddd'}`,
            cursor: 'pointer', whiteSpace: 'nowrap',
          }}>
            {fitMode === 'fit' ? '适配页面' : '原始大小'}
          </button>
        </div>
      </div>

      {/* PDF 页面列表 */}
      <div ref={containerRef} style={{ flex: 1, overflow: 'auto', background: '#e8e6df' }}>
        <div style={{ maxWidth: '800px', margin: '0 auto' }}>
          {Array.from({ length: numPages }, (_, i) => {
            const pageNum = i + 1
            const pageNotes = annotations.filter(a => a.pageNum === pageNum)
            return (
              <div key={pageNum} id={`pdf-page-${pageNum}`} style={{ marginBottom: '2px' }}>
                <PdfPage
                  pdfDoc={pdfDoc}
                  pageNum={pageNum}
                  scale={scale}
                  annotations={pageNotes}
                  annotationMode={annotationMode}
                  onCreateNote={(x, y, text) => handleCreateAnnotation(pageNum, x, y, text)}
                  onUpdateNote={onUpdateAnnotation}
                  onDeleteNote={onDeleteAnnotation}
                  onDragNote={handleDragAnnotation}
                  scrollContainer={containerRef}
                />
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
