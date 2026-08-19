// 搜索面板：关键词输入 + 结果列表 + 索引进度
import { useState } from 'react'
import type { SearchHit } from './usePdfSearch'

interface SearchPanelProps {
  hits: SearchHit[]
  searching: boolean
  indexDone: boolean
  indexedPages: number
  numPages: number
  hasAnyText: boolean
  onSearch: (query: string) => void
  onGoto: (hit: SearchHit) => void
  onClose: () => void
}

export function SearchPanel({ hits, searching, indexDone, indexedPages, numPages, hasAnyText, onSearch, onGoto, onClose }: SearchPanelProps) {
  const [query, setQuery] = useState('')

  return (
    <div style={{
      width: 220, flexShrink: 0, display: 'flex', flexDirection: 'column',
      background: '#f5f4ef', borderLeft: '1px solid #e8e6df',
    }}>
      <div style={{ display: 'flex', gap: 6, padding: '8px 10px', borderBottom: '1px solid #e8e6df' }}>
        <input
          autoFocus
          value={query}
          onChange={e => { setQuery(e.target.value); onSearch(e.target.value) }}
          placeholder="全文搜索..."
          style={{
            flex: 1, padding: '4px 8px', fontSize: 12, minWidth: 0,
            border: '1px solid #ddd', borderRadius: 4, outline: 'none',
          }}
        />
        <button onClick={onClose} style={{
          padding: '3px 8px', fontSize: 11, borderRadius: 4,
          background: '#fff', border: '1px solid #ddd', cursor: 'pointer', color: '#666',
        }}>×</button>
      </div>

      {!indexDone && (
        <div style={{ padding: '6px 10px', fontSize: 11, color: '#999' }}>
          索引中 {indexedPages}/{numPages} 页（可先搜索已索引部分）
        </div>
      )}
      {indexDone && !hasAnyText && (
        <div style={{ padding: '6px 10px', fontSize: 11, color: '#999' }}>
          该文档为扫描件，无法全文搜索
        </div>
      )}

      <div style={{ flex: 1, overflowY: 'auto' }}>
        {query.trim() && !searching && hits.length === 0 && indexDone && hasAnyText && (
          <div style={{ padding: 10, fontSize: 12, color: '#999' }}>无匹配结果</div>
        )}
        {hits.map((h, i) => (
          <div
            key={`${h.pageNum}-${i}`}
            onClick={() => onGoto(h)}
            style={{
              padding: '6px 10px', cursor: 'pointer', fontSize: 12,
              borderBottom: '1px solid #eceae3', lineHeight: 1.5,
            }}
            onMouseEnter={e => (e.currentTarget.style.background = '#eceae3')}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
          >
            <span style={{ color: 'var(--macos-accent)', fontWeight: 600, marginRight: 6 }}>
              第 {h.pageNum} 页
            </span>
            <span style={{ color: '#555' }}>…{h.context}…</span>
          </div>
        ))}
      </div>
    </div>
  )
}
