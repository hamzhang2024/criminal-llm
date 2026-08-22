// PDF 查看器主容器：工具栏 + 缩略图侧栏 + 搜索面板 + 连续滚动页面列表
import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE, getMdIssues } from '../../api'
import type { MdIssue } from '../../api/cases'
import { PdfPageManager } from '../../pages/CaseDetailPage/components/PdfPageManager'
import { generateAnnotationId, randomNoteColor, randomRectColor } from './AnnotationLayer'
import { PdfPage } from './PdfPage'
import { PdfToolbar } from './PdfToolbar'
import { SearchPanel } from './SearchPanel'
import { ThumbnailSidebar } from './ThumbnailSidebar'
import { useCurrentPage } from './useCurrentPage'
import { usePdfDocument } from './usePdfDocument'
import { usePdfSearch } from './usePdfSearch'
import type { SearchHit } from './usePdfSearch'
import { useZoom } from './useZoom'
import type { AnnotationTool, PdfViewerProps } from './types'
import './pdf-viewer.css'

export function PdfViewer({ caseId, pdfFilename, annotations, onAddAnnotation, onUpdateAnnotation, onDragAnnotation, onDeleteAnnotation, annotationMode }: PdfViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [numPages, setNumPages] = useState(0)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [searchOpen, setSearchOpen] = useState(false)
  const [annotationTool, setAnnotationTool] = useState<AnnotationTool>('note')
  // 页面管理模式（集成 PdfPageManager 功能）
  const [pageManageMode, setPageManageMode] = useState(false)
  const [mdIssues, setMdIssues] = useState<MdIssue[]>([])

  const url = `${API_BASE}/cases/${caseId}/serve-file?file_path=${encodeURIComponent(pdfFilename)}&dir=processed`
  const { pdfDoc, error, firstPageSize } = usePdfDocument(url)

  useEffect(() => {
    setNumPages(pdfDoc?.numPages ?? 0)
  }, [pdfDoc])

  // 加载乱码页检测（用于页面管理模式的异常标记）
  useEffect(() => {
    if (!caseId || !pdfFilename) return
    getMdIssues(caseId)
      .then(r => setMdIssues(r.issues || []))
      .catch(() => setMdIssues([]))
  }, [caseId, pdfFilename])

  const { currentPage, registerPage, getPageEl, scrollToPage } = useCurrentPage({ containerRef, numPages })
  const { scale, fitMode, zoomIn, zoomOut, fitWidth, applyScale, computeFitScale } = useZoom({
    containerRef,
    numPages,
    pdfPageWidth: firstPageSize?.width ?? 0,
    getPageEl,
  })
  const { search, registerText, indexedPages, indexDone, hasAnyText } = usePdfSearch(pdfDoc, numPages)

  // 搜索状态
  const [hits, setHits] = useState<SearchHit[]>([])
  const textDivsRef = useRef(new Map<number, HTMLElement[]>())
  const pendingHighlightRef = useRef<{ pageNum: number; query: string } | null>(null)
  const lastQueryRef = useRef('')

  // 文档加载完成后初始化为适配宽度
  useEffect(() => {
    if (!pdfDoc || !firstPageSize) return
    const raf = requestAnimationFrame(() => applyScale(computeFitScale(), true))
    return () => cancelAnimationFrame(raf)
  }, [pdfDoc, firstPageSize, applyScale, computeFitScale])

  // 键盘翻页：←/→/PgUp/PgDn（容器聚焦时生效，不劫持全局按键）
  const handleKeyDown = (e: React.KeyboardEvent) => {
    const prev = () => scrollToPage(Math.max(1, currentPage - 1))
    const next = () => scrollToPage(Math.min(numPages, currentPage + 1))
    if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); prev() }
    if (e.key === 'ArrowRight' || e.key === 'PageDown') { e.preventDefault(); next() }
  }

  const handleCreateNote = useCallback((pageNum: number, x: number, y: number, text: string) => {
    onAddAnnotation({
      id: generateAnnotationId(),
      type: 'note',
      pageNum, x, y,
      text, color: randomNoteColor(),
      pdfFile: pdfFilename,
      createdAt: new Date().toISOString(),
    })
  }, [pdfFilename, onAddAnnotation])

  const handleCreateRect = useCallback((pageNum: number, rect: { x: number; y: number; w: number; h: number }) => {
    onAddAnnotation({
      id: generateAnnotationId(),
      type: 'rect',
      pageNum,
      x: rect.x, y: rect.y,
      rect,
      text: '', color: randomRectColor(),
      pdfFile: pdfFilename,
      createdAt: new Date().toISOString(),
    })
  }, [pdfFilename, onAddAnnotation])

  // 在指定页的 textDivs 上应用搜索高亮
  const applyHighlight = useCallback((pageNum: number, query: string) => {
    const divs = textDivsRef.current.get(pageNum)
    if (!divs) return
    const q = query.toLowerCase()
    for (const div of divs) {
      div.classList.remove('search-hit', 'search-hit-current')
      if (q && div.textContent?.toLowerCase().includes(q)) {
        div.classList.add('search-hit')
      }
    }
  }, [])

  // 文本层就绪回调：注册搜索文本 + 补挂待处理高亮
  const handleTextReady = useCallback((pageNum: number, textDivs: HTMLElement[], text: string) => {
    textDivsRef.current.set(pageNum, textDivs)
    registerText(pageNum, text)
    const pending = pendingHighlightRef.current
    if (pending && pending.pageNum === pageNum) {
      applyHighlight(pageNum, pending.query)
    }
  }, [registerText, applyHighlight])

  const handleSearch = useCallback((query: string) => {
    lastQueryRef.current = query
    setHits(search(query))
  }, [search])

  const handleGotoHit = useCallback((hit: SearchHit) => {
    const query = lastQueryRef.current.trim()
    pendingHighlightRef.current = { pageNum: hit.pageNum, query }
    scrollToPage(hit.pageNum, true)
    // 文本层已挂载的页直接高亮（未挂载的由 handleTextReady 补挂）
    applyHighlight(hit.pageNum, query)
  }, [scrollToPage, applyHighlight])

  if (error) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#999', fontSize: '14px' }}>
        {error}
      </div>
    )
  }

  if (!pdfDoc || !firstPageSize) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#999', fontSize: '14px' }}>
        加载 PDF...
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <PdfToolbar
        numPages={numPages}
        currentPage={currentPage}
        scale={scale}
        fitMode={fitMode}
        sidebarOpen={sidebarOpen}
        searchOpen={searchOpen}
        annotationMode={annotationMode}
        annotationTool={annotationTool}
        pageManageMode={pageManageMode}
        issueCount={mdIssues.length}
        onToggleSidebar={() => setSidebarOpen(v => !v)}
        onToggleSearch={() => { setSearchOpen(v => !v); if (searchOpen) setHits([]) }}
        onTogglePageManage={() => setPageManageMode(v => !v)}
        onToolChange={setAnnotationTool}
        onJump={n => scrollToPage(n, true)}
        onZoomIn={zoomIn}
        onZoomOut={zoomOut}
        onSetScale={s => applyScale(s)}
        onFitWidth={fitWidth}
      />
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        {sidebarOpen && !pageManageMode && (
          <ThumbnailSidebar
            caseId={caseId}
            pdfFilename={pdfFilename}
            currentPage={currentPage}
            defaultSize={firstPageSize}
            onJump={n => scrollToPage(n, true)}
          />
        )}
        {pageManageMode ? (
          // 页面管理模式：显示缩略图网格 + 旋转 + 修复
          <div style={{ flex: 1, overflow: 'auto', background: '#1a1a1e', padding: 16 }}>
            <PdfPageManager
              caseId={caseId}
              pdfFilename={pdfFilename}
              issues={mdIssues}
              onFixed={() => {
                // 修复完成后刷新异常检测
                getMdIssues(caseId)
                  .then(r => setMdIssues(r.issues || []))
                  .catch(() => setMdIssues([]))
              }}
            />
          </div>
        ) : (
          <div
            ref={containerRef}
            tabIndex={0}
            onKeyDown={handleKeyDown}
            style={{ flex: 1, overflow: 'auto', background: '#e8e6df', outline: 'none' }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', padding: '8px 0' }}>
              {Array.from({ length: numPages }, (_, i) => i + 1).map(pageNum => (
                <PdfPage
                  key={pageNum}
                  pdfDoc={pdfDoc}
                  pageNum={pageNum}
                  scale={scale}
                  defaultSize={firstPageSize}
                  annotations={annotations.filter(a => a.pageNum === pageNum)}
                  annotationMode={annotationMode}
                  annotationTool={annotationTool}
                  registerPage={registerPage}
                  scrollContainer={containerRef}
                  onCreateNote={(x, y, text) => handleCreateNote(pageNum, x, y, text)}
                  onCreateRect={rect => handleCreateRect(pageNum, rect)}
                  onUpdateNote={onUpdateAnnotation}
                  onDeleteNote={onDeleteAnnotation}
                  onDragNote={onDragAnnotation}
                  onTextReady={handleTextReady}
                />
              ))}
            </div>
          </div>
        )}
        {searchOpen && !pageManageMode && (
          <SearchPanel
            hits={hits}
            searching={false}
            indexDone={indexDone}
            indexedPages={indexedPages}
            numPages={numPages}
            hasAnyText={hasAnyText}
            onSearch={handleSearch}
            onGoto={handleGotoHit}
            onClose={() => { setSearchOpen(false); setHits([]) }}
          />
        )}
      </div>
    </div>
  )
}
