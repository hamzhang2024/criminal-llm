// 全文搜索：后台分批构建文本索引 + 查询 + 命中定位
import { useCallback, useEffect, useRef, useState } from 'react'
import type { PDFDocumentProxy } from './types'

export interface SearchHit {
  pageNum: number
  context: string   // 命中前后 ±15 字上下文
}

const INDEX_BATCH = 10       // 每批索引页数，批间让出主线程
const MAX_HITS_PER_PAGE = 5  // 单页命中上限，避免结果爆炸

interface UsePdfSearchResult {
  search: (query: string) => SearchHit[]
  registerText: (pageNum: number, text: string) => void   // 文本层就绪时回填（跳过重复索引）
  indexedPages: number
  indexDone: boolean
  hasAnyText: boolean       // 索引完成后仍无文本 → 纯扫描件
}

export function usePdfSearch(pdfDoc: PDFDocumentProxy | null, numPages: number): UsePdfSearchResult {
  const indexRef = useRef(new Map<number, string>())
  const [indexedPages, setIndexedPages] = useState(0)
  const [indexDone, setIndexDone] = useState(false)

  const registerText = useCallback((pageNum: number, text: string) => {
    indexRef.current.set(pageNum, text)
  }, [])

  // 文档切换时重置索引
  useEffect(() => {
    indexRef.current.clear()
    setIndexedPages(0)
    setIndexDone(false)
  }, [pdfDoc])

  // 后台分批索引（文本层已覆盖的页跳过）
  useEffect(() => {
    if (!pdfDoc || numPages === 0) return
    let cancelled = false
    const run = async () => {
      for (let n = 1; n <= numPages; n++) {
        if (cancelled) return
        if (!indexRef.current.has(n)) {
          try {
            const page = await pdfDoc.getPage(n)
            const tc = await page.getTextContent()
            indexRef.current.set(n, tc.items.map((it: any) => it.str).join(''))
          } catch {
            indexRef.current.set(n, '')
          }
        }
        if (n % INDEX_BATCH === 0) {
          setIndexedPages(n)
          await new Promise(r => setTimeout(r, 0))
        }
      }
      if (!cancelled) {
        setIndexedPages(numPages)
        setIndexDone(true)
      }
    }
    run()
    return () => { cancelled = true }
  }, [pdfDoc, numPages])

  const search = useCallback((query: string): SearchHit[] => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    const hits: SearchHit[] = []
    indexRef.current.forEach((text, pageNum) => {
      const lower = text.toLowerCase()
      let idx = 0
      let count = 0
      while (count < MAX_HITS_PER_PAGE && (idx = lower.indexOf(q, idx)) !== -1) {
        hits.push({
          pageNum,
          context: text.slice(Math.max(0, idx - 15), idx + q.length + 15).trim(),
        })
        idx += q.length
        count++
      }
    })
    return hits.sort((a, b) => a.pageNum - b.pageNum)
  }, [])

  const hasAnyText = !indexDone || [...indexRef.current.values()].some(t => t.length > 0)

  return { search, registerText, indexedPages, indexDone, hasAnyText }
}
