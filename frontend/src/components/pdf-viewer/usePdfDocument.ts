// PDF 文档加载 hook：本地 pdfjs-dist（替代原 CDN 动态注入，Tauri 离线可用）
// pdfjs-dist 体积较大，按需动态加载（首次打开 PDF 时才拉取该 chunk）
import { useEffect, useState } from 'react'
import { getPdfjs } from './pdfjs'
import type { PDFDocumentProxy, PageSize } from './types'

interface UsePdfDocumentResult {
  pdfDoc: PDFDocumentProxy | null
  error: string | null
  firstPageSize: PageSize | null   // 第一页尺寸，作为未渲染页占位尺寸的兜底
}

export function usePdfDocument(url: string | null): UsePdfDocumentResult {
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [firstPageSize, setFirstPageSize] = useState<PageSize | null>(null)

  useEffect(() => {
    if (!url) return
    let cancelled = false
    setPdfDoc(null)
    setError(null)
    setFirstPageSize(null)

    const load = async () => {
      try {
        const pdfjsLib = await getPdfjs()
        if (cancelled) return
        const task = pdfjsLib.getDocument(url)
        const pdf = await task.promise
        if (cancelled) { pdf.destroy(); return }
        const page = await pdf.getPage(1)
        if (cancelled) { pdf.destroy(); return }
        const vp = page.getViewport({ scale: 1 })
        setFirstPageSize({ width: vp.width, height: vp.height })
        setPdfDoc(pdf)
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'PDF 加载失败')
      }
    }
    load()

    return () => { cancelled = true }
  }, [url])

  return { pdfDoc, error, firstPageSize }
}
