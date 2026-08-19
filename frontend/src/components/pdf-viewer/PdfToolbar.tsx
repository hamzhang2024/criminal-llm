// 工具栏：侧栏开关、页码跳转、缩放控件、适配宽度、搜索开关、批注工具切换
import { useEffect, useState } from 'react'
import { ZOOM_MAX, ZOOM_MIN, ZOOM_PRESETS } from './useZoom'
import type { AnnotationTool } from './types'

const btnStyle: React.CSSProperties = {
  padding: '3px 8px', fontSize: '11px', borderRadius: '4px',
  background: '#fff', border: '1px solid #ddd', cursor: 'pointer', color: '#666',
  minWidth: '26px',
}

const activeBtnStyle: React.CSSProperties = {
  ...btnStyle,
  background: 'var(--macos-accent)',
  color: '#fff',
  border: '1px solid var(--macos-accent)',
}

interface PdfToolbarProps {
  numPages: number
  currentPage: number
  scale: number
  fitMode: boolean
  sidebarOpen: boolean
  searchOpen: boolean
  annotationMode: boolean
  annotationTool: AnnotationTool
  onToggleSidebar: () => void
  onToggleSearch: () => void
  onToolChange: (tool: AnnotationTool) => void
  onJump: (page: number) => void
  onZoomIn: () => void
  onZoomOut: () => void
  onSetScale: (scale: number) => void
  onFitWidth: () => void
}

export function PdfToolbar({ numPages, currentPage, scale, fitMode, sidebarOpen, searchOpen, annotationMode, annotationTool, onToggleSidebar, onToggleSearch, onToolChange, onJump, onZoomIn, onZoomOut, onSetScale, onFitWidth }: PdfToolbarProps) {
  const [jumpPage, setJumpPage] = useState('')

  // 当前页联动到输入框（仅当输入框未聚焦时，避免打扰用户输入）
  useEffect(() => {
    if (document.activeElement?.id !== 'pdf-jump-input') {
      setJumpPage(String(currentPage))
    }
  }, [currentPage])

  const doJump = () => {
    const n = parseInt(jumpPage)
    if (n >= 1 && n <= numPages) onJump(n)
  }

  const pct = Math.round(scale * 100)

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '6px 10px', background: '#f5f4ef',
      borderBottom: '1px solid #e8e6df', flexShrink: 0, gap: '8px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <button onClick={onToggleSidebar} title="缩略图侧栏" style={sidebarOpen ? activeBtnStyle : btnStyle}>
          ☰
        </button>
        <input
          id="pdf-jump-input"
          type="number"
          min={1}
          max={numPages}
          value={jumpPage}
          onChange={e => setJumpPage(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { doJump(); e.currentTarget.blur() } }}
          style={{
            width: '52px', padding: '3px 6px', fontSize: '12px',
            border: '1px solid #ddd', borderRadius: '3px',
            textAlign: 'center', background: '#fff', outline: 'none',
          }}
        />
        <span style={{ fontSize: '12px', color: '#666', whiteSpace: 'nowrap' }}>/ {numPages} 页</span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        {annotationMode && (
          <>
            <button
              onClick={() => onToolChange('note')}
              title="便签批注：点击页面添加"
              style={annotationTool === 'note' ? activeBtnStyle : btnStyle}
            >
              便签
            </button>
            <button
              onClick={() => onToolChange('rect')}
              title="框选批注：拖拽圈选区域高亮"
              style={annotationTool === 'rect' ? activeBtnStyle : btnStyle}
            >
              框选
            </button>
          </>
        )}
        <button onClick={onToggleSearch} title="全文搜索" style={searchOpen ? activeBtnStyle : btnStyle}>
          🔍
        </button>
        <button onClick={onZoomOut} disabled={scale <= ZOOM_MIN} title="缩小" style={btnStyle}>−</button>
        <select
          value={fitMode ? 'fit' : String(pct)}
          onChange={e => {
            if (e.target.value === 'fit') onFitWidth()
            else onSetScale(Number(e.target.value) / 100)
          }}
          style={{
            fontSize: '11px', padding: '2px 4px', borderRadius: '4px',
            border: '1px solid #ddd', background: '#fff', color: '#666', outline: 'none',
          }}
        >
          {!fitMode && !ZOOM_PRESETS.includes(scale) && <option value={String(pct)}>{pct}%</option>}
          {ZOOM_PRESETS.map(p => (
            <option key={p} value={String(p * 100)}>{p * 100}%</option>
          ))}
          <option value="fit">适配宽度</option>
        </select>
        <button onClick={onZoomIn} disabled={scale >= ZOOM_MAX} title="放大" style={btnStyle}>+</button>
      </div>
    </div>
  )
}
