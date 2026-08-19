// 文本层：renderTextLayer 封装，支持文字选择/复制；扫描件自动降级（不挂载）
import { useEffect, useRef, useState } from 'react'
import { getPdfjs } from './pdfjs'
import type { PDFDocumentProxy } from './types'

interface PdfTextLayerProps {
  pdfDoc: PDFDocumentProxy
  pageNum: number
  scale: number
  enabled: boolean            // 批注模式下禁用（避免遮挡批注点击）
  onTextReady?: (pageNum: number, textDivs: HTMLElement[], text: string) => void  // 供搜索索引/高亮
}

export function PdfTextLayer({ pdfDoc, pageNum, scale, enabled, onTextReady }: PdfTextLayerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [hasText, setHasText] = useState(true)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let cancelled = false

    const render = async () => {
      const pdfjsLib = await getPdfjs()
      if (cancelled) return
      const page = await pdfDoc.getPage(pageNum)
      if (cancelled) return
      const viewport = page.getViewport({ scale })
      const textContent = await page.getTextContent()
      if (cancelled) return

      // 扫描件无文字层：不挂载，避免空容器遮挡
      if (textContent.items.length === 0) {
        setHasText(false)
        return
      }
      setHasText(true)

      container.innerHTML = ''
      // pdf.js 3.x textLayer 依赖 --scale-factor CSS 变量
      container.style.setProperty('--scale-factor', String(viewport.scale))
      const textDivs: HTMLElement[] = []
      const task = pdfjsLib.renderTextLayer({
        textContentSource: textContent,
        container,
        viewport,
        textDivs,
      })
      await task.promise
      if (cancelled) return
      onTextReady?.(pageNum, textDivs, textContent.items.map((it: any) => it.str).join(''))
    }
    render()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pdfDoc, pageNum, scale])

  if (!hasText) return null

  return (
    <div
      ref={containerRef}
      className="textLayer"
      style={{ pointerEvents: enabled ? 'auto' : 'none' }}
    />
  )
}
