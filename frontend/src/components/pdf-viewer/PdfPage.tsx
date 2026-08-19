// 单页渲染：懒加载 + 缩放防抖重渲染（先模糊后清晰）+ 远距页位图释放
import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { AnnotationLayer } from './AnnotationLayer'
import { PdfTextLayer } from './PdfTextLayer'
import type { AnnotationTool, PageSize, PdfAnnotation, PDFDocumentProxy } from './types'

// 位图总像素上限（单边）：防止大缩放 × 高 DPR 导致内存爆炸
const MAX_BITMAP_EDGE = 4096
// 缩放变化后的重渲染防抖
const RERENDER_DEBOUNCE_MS = 300
// 提前渲染的视口外扩距离
const PREFETCH_MARGIN = '600px 0px'

interface PdfPageProps {
  pdfDoc: PDFDocumentProxy
  pageNum: number
  scale: number
  defaultSize: PageSize            // scale=1 兜底尺寸（首页），本页渲染后用真实尺寸
  annotations: PdfAnnotation[]
  annotationMode: boolean
  annotationTool: AnnotationTool
  registerPage: (n: number, el: HTMLElement | null) => void
  scrollContainer: RefObject<HTMLDivElement>
  onCreateNote: (x: number, y: number, text: string) => void
  onCreateRect: (rect: { x: number; y: number; w: number; h: number }) => void
  onUpdateNote: (id: string, text: string) => void
  onDeleteNote: (id: string) => void
  onDragNote: (id: string, x: number, y: number) => void
  onTextReady?: (pageNum: number, textDivs: HTMLElement[], text: string) => void
}

export function PdfPage({ pdfDoc, pageNum, scale, defaultSize, annotations, annotationMode, annotationTool, registerPage, scrollContainer, onCreateNote, onCreateRect, onUpdateNote, onDeleteNote, onDragNote, onTextReady }: PdfPageProps) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const renderTaskRef = useRef<{ cancel: () => void } | null>(null)
  const sizeRef = useRef<PageSize | null>(null)        // 本页 scale=1 真实尺寸
  const renderedScaleRef = useRef<number | null>(null) // canvas 当前位图对应的 scale
  const visibleRef = useRef(false)
  const scaleRef = useRef(scale)
  scaleRef.current = scale
  const [renderedScale, setRenderedScale] = useState<number | null>(null)

  // 占位尺寸始终跟随目标 scale，保证滚动锚点计算稳定（位图稍后跟清晰）
  const size1 = sizeRef.current ?? defaultSize
  const targetW = size1.width * scale
  const targetH = size1.height * scale

  const renderPage = useCallback(async (targetScale: number) => {
    const canvas = canvasRef.current
    if (!canvas) return
    renderTaskRef.current?.cancel()
    try {
      const page = await pdfDoc.getPage(pageNum)
      const vp1 = page.getViewport({ scale: 1 })
      sizeRef.current = { width: vp1.width, height: vp1.height }
      const viewport = page.getViewport({ scale: targetScale })
      let dpr = Math.min(window.devicePixelRatio || 1, 1.5)
      const maxEdge = Math.max(viewport.width, viewport.height)
      if (maxEdge * dpr > MAX_BITMAP_EDGE) dpr = MAX_BITMAP_EDGE / maxEdge

      canvas.width = Math.floor(viewport.width * dpr)
      canvas.height = Math.floor(viewport.height * dpr)
      canvas.style.width = `${viewport.width}px`
      canvas.style.height = `${viewport.height}px`
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      const task = page.render({ canvasContext: ctx, viewport })
      renderTaskRef.current = task
      await task.promise
      renderedScaleRef.current = targetScale
      setRenderedScale(targetScale)
    } catch { /* 取消/销毁异常忽略 */ }
  }, [pdfDoc, pageNum])

  // 释放位图（保留占位尺寸，百分比批注坐标不受影响）
  const releaseBitmap = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    renderTaskRef.current?.cancel()
    canvas.width = 0
    canvas.height = 0
    renderedScaleRef.current = null
    setRenderedScale(null)
  }, [])

  // 可见性观察：进入视口前预渲染，远离视口释放位图
  useEffect(() => {
    const el = wrapperRef.current
    if (!el) return
    const obs = new IntersectionObserver(([entry]) => {
      visibleRef.current = entry.isIntersecting
      if (entry.isIntersecting) {
        if (renderedScaleRef.current !== scaleRef.current) renderPage(scaleRef.current)
      } else if (renderedScaleRef.current !== null) {
        releaseBitmap()
      }
    }, { root: scrollContainer.current, rootMargin: PREFETCH_MARGIN })
    obs.observe(el)
    return () => {
      obs.disconnect()
      renderTaskRef.current?.cancel()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pdfDoc, pageNum])

  // 缩放变化：已渲染的可见页防抖重渲染（期间用 CSS transform 放大旧位图，先模糊后清晰）
  useEffect(() => {
    if (!visibleRef.current || renderedScaleRef.current === null) return
    if (renderedScaleRef.current === scale) return
    const timer = setTimeout(() => {
      if (visibleRef.current) renderPage(scale)
    }, RERENDER_DEBOUNCE_MS)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scale, renderPage])

  // 注册页面元素（供页码联动/缩放锚点使用）
  useEffect(() => {
    registerPage(pageNum, wrapperRef.current)
    return () => registerPage(pageNum, null)
  }, [pageNum, registerPage])

  // 位图比例补偿：缩放后、重渲染前用 CSS transform 撑满目标尺寸
  const bitmapRatio = renderedScale !== null ? scale / renderedScale : 1

  return (
    <div
      ref={wrapperRef}
      data-pdf-page={pageNum}
      className="pdf-page-wrapper"
      style={{ height: targetH, width: targetW }}
    >
      <canvas
        ref={canvasRef}
        className="pdf-page-canvas"
        style={{
          display: renderedScale !== null ? 'block' : 'none',
          transform: bitmapRatio !== 1 ? `scale(${bitmapRatio})` : undefined,
          transformOrigin: 'top left',
        }}
      />
      {renderedScale === null && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex',
          alignItems: 'center', justifyContent: 'center',
          fontSize: '12px', color: '#aaa', background: '#f0efe8',
        }}>
          第 {pageNum} 页
        </div>
      )}
      {/* 文本层随位图同生命周期（位图释放时一并卸载控制 DOM 规模），
          但按目标 scale 即时重渲染——不可见文字无需等位图防抖 */}
      {renderedScale !== null && (
        <PdfTextLayer
          pdfDoc={pdfDoc}
          pageNum={pageNum}
          scale={scale}
          enabled={!annotationMode}
          onTextReady={onTextReady}
        />
      )}
      <AnnotationLayer
        annotations={annotations}
        annotationMode={annotationMode}
        tool={annotationTool}
        pageWidth={targetW}
        onCreateNote={onCreateNote}
        onCreateRect={onCreateRect}
        onUpdateNote={onUpdateNote}
        onDeleteNote={onDeleteNote}
        onDragNote={onDragNote}
      />
    </div>
  )
}
