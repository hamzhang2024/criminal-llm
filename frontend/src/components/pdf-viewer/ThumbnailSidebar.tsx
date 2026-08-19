// 缩略图侧栏：复用后端 pdf-thumbnails 端点，img 懒加载，当前页高亮跟随
import { useEffect, useRef, useState } from 'react'
import { API_BASE, getThumbnails } from '../../api'
import type { PageSize } from './types'

const THUMB_WIDTH = 120

interface ThumbnailSidebarProps {
  caseId: string
  pdfFilename: string
  currentPage: number
  defaultSize: PageSize
  onJump: (page: number) => void
}

interface Thumb {
  page: number
  url: string
}

export function ThumbnailSidebar({ caseId, pdfFilename, currentPage, defaultSize, onJump }: ThumbnailSidebarProps) {
  const [thumbs, setThumbs] = useState<Thumb[]>([])
  const [failed, setFailed] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  // 缩略图 URL 是后端根路径（/thumbnails/...），需拼后端源（同 PdfPageManager）
  const backendOrigin = API_BASE.replace(/\/api$/, '')
  const itemH = Math.round(THUMB_WIDTH * (defaultSize.height / defaultSize.width)) + 22

  useEffect(() => {
    let cancelled = false
    setThumbs([])
    setFailed(false)
    getThumbnails(caseId, pdfFilename, 'processed', THUMB_WIDTH * 2)
      .then(r => { if (!cancelled) setThumbs(r.thumbnails ?? []) })
      .catch(() => { if (!cancelled) setFailed(true) })
    return () => { cancelled = true }
  }, [caseId, pdfFilename])

  // 当前页变化时缩略图跟随滚动（ nearest 避免大幅跳动）
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-thumb-page="${currentPage}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [currentPage])

  if (failed) {
    return <div style={{ padding: 12, fontSize: 11, color: '#999' }}>缩略图不可用</div>
  }

  return (
    <div ref={listRef} style={{
      width: THUMB_WIDTH + 24, flexShrink: 0, overflowY: 'auto',
      background: '#f5f4ef', borderRight: '1px solid #e8e6df', padding: '8px 12px',
    }}>
      {thumbs.map(t => (
        <div
          key={t.page}
          data-thumb-page={t.page}
          onClick={() => onJump(t.page)}
          style={{
            marginBottom: 10, cursor: 'pointer', textAlign: 'center',
            borderRadius: 4, padding: 3,
            outline: t.page === currentPage ? '2px solid var(--macos-accent)' : '2px solid transparent',
            transition: 'outline-color 120ms',
          }}
        >
          <img
            src={`${backendOrigin}${t.url}`}
            loading="lazy"
            alt={`第 ${t.page} 页`}
            style={{
              width: THUMB_WIDTH, height: itemH - 22, objectFit: 'contain',
              background: '#fff', borderRadius: 2,
              boxShadow: '0 1px 3px rgba(0,0,0,0.15)', display: 'block',
            }}
          />
          <div style={{
            fontSize: 10, marginTop: 3,
            color: t.page === currentPage ? 'var(--macos-accent)' : '#888',
            fontWeight: t.page === currentPage ? 600 : 400,
          }}>
            {t.page}
          </div>
        </div>
      ))}
      {thumbs.length === 0 && (
        <div style={{ padding: 12, fontSize: 11, color: '#aaa', textAlign: 'center' }}>生成缩略图中...</div>
      )}
    </div>
  )
}
