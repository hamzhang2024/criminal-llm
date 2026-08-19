// 当前页检测与页面跳转
import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'

interface UseCurrentPageOptions {
  containerRef: RefObject<HTMLDivElement>
  numPages: number
}

export function useCurrentPage({ containerRef, numPages }: UseCurrentPageOptions) {
  const [currentPage, setCurrentPage] = useState(1)
  const pageElsRef = useRef(new Map<number, HTMLElement>())

  const registerPage = useCallback((n: number, el: HTMLElement | null) => {
    if (el) pageElsRef.current.set(n, el)
    else pageElsRef.current.delete(n)
  }, [])

  const getPageEl = useCallback(
    (n: number) => pageElsRef.current.get(n) ?? null,
    []
  )

  // 视口中心线穿过的页即为当前页（上下各收缩 45%，通常只剩一页命中）
  useEffect(() => {
    const root = containerRef.current
    if (!root || numPages === 0) return
    const visible = new Set<number>()
    const obs = new IntersectionObserver(entries => {
      for (const e of entries) {
        const n = Number((e.target as HTMLElement).dataset.pdfPage)
        if (!n) continue
        if (e.isIntersecting) visible.add(n)
        else visible.delete(n)
      }
      if (visible.size > 0) setCurrentPage(Math.min(...visible))
    }, { root, rootMargin: '-45% 0px -45% 0px' })
    pageElsRef.current.forEach(el => obs.observe(el))
    return () => obs.disconnect()
  }, [containerRef, numPages])

  const scrollToPage = useCallback((n: number, highlight = false) => {
    const el = pageElsRef.current.get(n)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setCurrentPage(n)
    if (highlight) {
      el.classList.remove('pdf-page-flash')
      // 强制重排以重启动画
      void el.offsetWidth
      el.classList.add('pdf-page-flash')
      setTimeout(() => el.classList.remove('pdf-page-flash'), 1600)
    }
  }, [])

  return { currentPage, registerPage, getPageEl, scrollToPage }
}
