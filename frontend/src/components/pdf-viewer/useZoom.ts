// 缩放管理：连续缩放、预设、Ctrl+滚轮、视觉锚点保持
import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'

export const ZOOM_MIN = 0.25
export const ZOOM_MAX = 4
export const ZOOM_PRESETS = [0.5, 0.75, 1, 1.5, 2]
const ZOOM_STEP = 1.2

const clamp = (v: number) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, v))

interface ZoomAnchor {
  pageNum: number   // 视口顶部所在页
  ratio: number     // 页内偏移比例（0-1）
}

interface UseZoomOptions {
  containerRef: RefObject<HTMLDivElement>
  numPages: number
  pdfPageWidth: number          // scale=1 时的页宽（用于适配宽度计算）
  getPageEl: (n: number) => HTMLElement | null
}

export function useZoom({ containerRef, numPages, pdfPageWidth, getPageEl }: UseZoomOptions) {
  const [scale, setScale] = useState(1)
  const [fitMode, setFitMode] = useState(true)
  const anchorRef = useRef<ZoomAnchor | null>(null)

  const computeFitScale = useCallback(() => {
    const el = containerRef.current
    if (!el || pdfPageWidth <= 0) return 1
    return clamp((el.clientWidth - 20) / pdfPageWidth)
  }, [containerRef, pdfPageWidth])

  // 捕获当前视觉锚点：视口顶部落在哪一页的什么位置
  const captureAnchor = useCallback((): ZoomAnchor | null => {
    const container = containerRef.current
    if (!container) return null
    const cTop = container.getBoundingClientRect().top
    for (let n = 1; n <= numPages; n++) {
      const el = getPageEl(n)
      if (!el) continue
      const r = el.getBoundingClientRect()
      if (r.bottom > cTop && r.height > 0) {
        return { pageNum: n, ratio: Math.max(0, (cTop - r.top) / r.height) }
      }
    }
    return null
  }, [containerRef, numPages, getPageEl])

  const applyScale = useCallback((next: number, fit = false) => {
    anchorRef.current = captureAnchor()
    setFitMode(fit)
    setScale(clamp(next))
  }, [captureAnchor])

  const zoomIn = useCallback(() => applyScale(scale * ZOOM_STEP), [scale, applyScale])
  const zoomOut = useCallback(() => applyScale(scale / ZOOM_STEP), [scale, applyScale])
  const fitWidth = useCallback(() => applyScale(computeFitScale(), true), [applyScale, computeFitScale])

  // 缩放后恢复锚点位置（占位尺寸随 scale 即时变化，一帧后布局已稳定）
  useEffect(() => {
    const anchor = anchorRef.current
    if (!anchor) return
    anchorRef.current = null
    requestAnimationFrame(() => {
      const container = containerRef.current
      const el = getPageEl(anchor.pageNum)
      if (!container || !el) return
      const r = el.getBoundingClientRect()
      const cTop = container.getBoundingClientRect().top
      container.scrollTop += (r.top + anchor.ratio * r.height) - cTop
    })
  }, [scale, containerRef, getPageEl])

  // 适配模式下窗口尺寸变化时重算
  useEffect(() => {
    if (!fitMode) return
    const onResize = () => {
      anchorRef.current = captureAnchor()
      setScale(computeFitScale())
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [fitMode, captureAnchor, computeFitScale])

  // Ctrl/Cmd + 滚轮缩放（macOS WKWebView 双指捏合映射为 wheel+ctrlKey）
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let raf = 0
    let pending = 0
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return
      e.preventDefault()
      pending += e.deltaY
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        const delta = pending
        pending = 0
        setScale(prev => {
          anchorRef.current = captureAnchor()
          setFitMode(false)
          return clamp(prev * (delta < 0 ? ZOOM_STEP : 1 / ZOOM_STEP))
        })
      })
    }
    container.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      container.removeEventListener('wheel', onWheel)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [containerRef, captureAnchor])

  return { scale, fitMode, zoomIn, zoomOut, fitWidth, applyScale, computeFitScale }
}
