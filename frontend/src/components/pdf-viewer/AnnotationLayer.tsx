// 批注覆盖层：便签 + 区域框选，创建/编辑弹窗、拖拽、删除（坐标为页面百分比）
import { useEffect, useRef, useState } from 'react'
import type { AnnotationTool, PdfAnnotation } from './types'

// 跨页面互斥：同一时间只允许一个批注处于创建/编辑状态
let isAnnotationEditing = false

const NOTE_COLORS = ['#fff9c4', '#ffecb3', '#ffe0b2', '#ffcdd2', '#f8bbd0', '#e1bee7', '#d1c4e9', '#c5cae9', '#bbdefb', '#b3e5fc', '#b2ebf2', '#b2dfdb', '#c8e6c9', '#dcedc8', '#f0f4c3']
export const randomNoteColor = () => NOTE_COLORS[Math.floor(Math.random() * NOTE_COLORS.length)]
export const generateAnnotationId = () => Math.random().toString(36).substr(2, 9)
const RECT_COLORS = ['#ffeb3b', '#8bff9e', '#7dd3fc', '#f0abfc', '#fda4af']
export const randomRectColor = () => RECT_COLORS[Math.floor(Math.random() * RECT_COLORS.length)]

interface AnnotationLayerProps {
  annotations: PdfAnnotation[]
  annotationMode: boolean
  tool: AnnotationTool
  pageWidth: number
  onCreateNote: (x: number, y: number, text: string) => void
  onCreateRect: (rect: { x: number; y: number; w: number; h: number }) => void
  onUpdateNote: (id: string, text: string) => void
  onDeleteNote: (id: string) => void
  onDragNote: (id: string, x: number, y: number) => void
}

// 创建/编辑共用的便签弹窗
function NotePopup({ left, top, initial, confirmText, onSave, onCancel }: {
  left: string
  top: string
  initial: string
  confirmText: string
  onSave: (text: string) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState(initial)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 50)
    return () => clearTimeout(t)
  }, [])

  const save = () => { if (draft.trim()) onSave(draft.trim()) }

  return (
    <div onClick={e => e.stopPropagation()} style={{
      position: 'absolute', left, top, transform: 'translate(-50%, -50%)',
      zIndex: 100, pointerEvents: 'auto',
    }}>
      <div style={{
        width: '220px', background: '#fff', borderRadius: '7px',
        boxShadow: '0 4px 16px rgba(0,0,0,0.22)', overflow: 'hidden', border: '1px solid #e0e0e0',
      }}>
        <textarea
          ref={inputRef}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey && draft.trim()) { e.preventDefault(); save() }
            if (e.key === 'Escape') { e.stopPropagation(); onCancel() }
          }}
          placeholder="输入批注..."
          rows={3}
          style={{
            width: '100%', padding: '10px 12px', fontSize: '12px',
            border: 'none', outline: 'none', resize: 'none',
            fontFamily: 'inherit', background: '#fffdf0',
            boxSizing: 'border-box', lineHeight: '1.5',
          }}
        />
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '6px', padding: '6px 10px', borderTop: '1px solid #eee' }}>
          <button onClick={onCancel} style={{
            padding: '3px 10px', fontSize: '11px', borderRadius: '4px',
            background: '#f5f5f5', border: '1px solid #ddd', cursor: 'pointer', color: '#666',
          }}>取消</button>
          <button onClick={save} disabled={!draft.trim()} style={{
            padding: '3px 10px', fontSize: '11px', borderRadius: '4px',
            background: draft.trim() ? 'var(--macos-accent)' : '#eee',
            color: draft.trim() ? '#fff' : '#999',
            border: 'none', cursor: draft.trim() ? 'pointer' : 'default',
          }}>{confirmText}</button>
        </div>
      </div>
    </div>
  )
}

export function AnnotationLayer({ annotations, annotationMode, tool, pageWidth, onCreateNote, onCreateRect, onUpdateNote, onDeleteNote, onDragNote }: AnnotationLayerProps) {
  const [creating, setCreating] = useState<{ x: number; y: number } | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  // 框选拖拽预览（百分比坐标）
  const [rectDraft, setRectDraft] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const dragStateRef = useRef<{
    noteId: string; startX: number; startY: number; origX: number; origY: number; el: HTMLElement
  } | null>(null)

  const closeAll = () => {
    isAnnotationEditing = false
    setCreating(null)
    setEditing(null)
  }

  const toPercent = (el: HTMLElement, clientX: number, clientY: number) => {
    const r = el.getBoundingClientRect()
    return {
      x: ((clientX - r.left) / r.width) * 100,
      y: ((clientY - r.top) / r.height) * 100,
    }
  }

  // 框选模式：mousedown 开始拖拽画矩形
  const handleLayerMouseDown = (e: React.MouseEvent) => {
    if (!annotationMode || tool !== 'rect' || isAnnotationEditing) return
    if (e.target !== e.currentTarget) return
    e.preventDefault()
    const layer = e.currentTarget as HTMLElement
    const start = toPercent(layer, e.clientX, e.clientY)

    const onMove = (ev: MouseEvent) => {
      const cur = toPercent(layer as HTMLElement, ev.clientX, ev.clientY)
      setRectDraft({
        x: Math.min(start.x, cur.x),
        y: Math.min(start.y, cur.y),
        w: Math.abs(cur.x - start.x),
        h: Math.abs(cur.y - start.y),
      })
    }
    const onUp = (ev: MouseEvent) => {
      document.removeEventListener('mousemove', onMove)
      const cur = toPercent(layer as HTMLElement, ev.clientX, ev.clientY)
      const rect = {
        x: Math.min(start.x, cur.x),
        y: Math.min(start.y, cur.y),
        w: Math.abs(cur.x - start.x),
        h: Math.abs(cur.y - start.y),
      }
      setRectDraft(null)
      // 误触过滤：小于页面 1% 的框不创建
      if (rect.w > 1 && rect.h > 1) onCreateRect(rect)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp, { once: true })
  }

  // 便签模式：点击空白处创建
  const handleLayerClick = (e: React.MouseEvent) => {
    if (!annotationMode || tool !== 'note' || creating || editing || isAnnotationEditing) return
    if (e.target !== e.currentTarget) return
    isAnnotationEditing = true
    setCreating(toPercent(e.currentTarget as HTMLElement, e.clientX, e.clientY))
  }

  // 拖拽便签（拖拽中直接改 DOM，松手才回调持久化）
  const handleNoteMouseDown = (e: React.MouseEvent, noteId: string) => {
    if (!annotationMode) return
    e.stopPropagation()
    e.preventDefault()
    const el = e.currentTarget as HTMLElement
    const note = annotations.find(a => a.id === noteId)
    if (!note) return
    dragStateRef.current = { noteId, startX: e.clientX, startY: e.clientY, origX: note.x, origY: note.y, el }

    const onMove = (ev: MouseEvent) => {
      const d = dragStateRef.current
      const rect = el.parentElement?.getBoundingClientRect()
      if (!d || !rect) return
      d.el.style.left = `${((d.origX / 100) * rect.width + ev.clientX - d.startX) / rect.width * 100}%`
      d.el.style.top = `${((d.origY / 100) * rect.height + ev.clientY - d.startY) / rect.height * 100}%`
    }
    const onUp = (ev: MouseEvent) => {
      const d = dragStateRef.current
      dragStateRef.current = null
      document.removeEventListener('mousemove', onMove)
      const rect = el.parentElement?.getBoundingClientRect()
      if (!d || !rect) return
      onDragNote(
        d.noteId,
        ((d.origX / 100) * rect.width + ev.clientX - d.startX) / rect.width * 100,
        ((d.origY / 100) * rect.height + ev.clientY - d.startY) / rect.height * 100,
      )
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp, { once: true })
  }

  const notes = annotations.filter(a => (a.type ?? 'note') === 'note')
  const rects = annotations.filter(a => a.type === 'rect' && a.rect)
  const editingNote = editing ? annotations.find(a => a.id === editing) : null

  return (
    <div
      onClick={handleLayerClick}
      onMouseDown={handleLayerMouseDown}
      style={{
        position: 'absolute', inset: 0,
        pointerEvents: annotationMode ? 'auto' : 'none',
        cursor: annotationMode ? (tool === 'rect' ? 'crosshair' : 'copy') : 'default',
        zIndex: 50,
      }}
    >
      {/* 区域框选高亮块 */}
      {rects.map(a => (
        <div
          key={a.id}
          onMouseEnter={() => setHoveredId(a.id)}
          onMouseLeave={() => setHoveredId(null)}
          style={{
            position: 'absolute',
            left: `${a.rect!.x}%`, top: `${a.rect!.y}%`,
            width: `${a.rect!.w}%`, height: `${a.rect!.h}%`,
            background: `${a.color}59`,   // hex + 35% 透明度
            borderRadius: 2,
            pointerEvents: annotationMode ? 'auto' : 'none',
          }}
        >
          {hoveredId === a.id && annotationMode && (
            <div
              onClick={e => { e.stopPropagation(); onDeleteNote(a.id) }}
              style={{
                position: 'absolute', top: '-6px', right: '-6px',
                width: '16px', height: '16px', borderRadius: '50%',
                background: '#ef4444', color: '#fff',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '12px', fontWeight: 700, cursor: 'pointer',
                boxShadow: '0 1px 3px rgba(0,0,0,0.2)', lineHeight: 1,
              }}
            >×</div>
          )}
        </div>
      ))}

      {/* 框选拖拽预览 */}
      {rectDraft && (
        <div style={{
          position: 'absolute',
          left: `${rectDraft.x}%`, top: `${rectDraft.y}%`,
          width: `${rectDraft.w}%`, height: `${rectDraft.h}%`,
          background: 'rgba(255, 235, 59, 0.3)',
          border: '1px dashed rgba(200, 160, 0, 0.8)',
          pointerEvents: 'none',
        }} />
      )}

      {/* 便签 */}
      {notes.map(note => (
        <div
          key={note.id}
          onMouseEnter={() => setHoveredId(note.id)}
          onMouseLeave={() => setHoveredId(null)}
          onClick={e => {
            if (!annotationMode) return
            e.stopPropagation()
            if (isAnnotationEditing) return
            isAnnotationEditing = true
            setEditing(note.id)
          }}
          onMouseDown={e => handleNoteMouseDown(e, note.id)}
          style={{
            position: 'absolute',
            left: `${note.x}%`, top: `${note.y}%`,
            transform: 'translate(-50%, -50%)',
            width: `${Math.max(120, pageWidth * 0.22)}px`,
            maxWidth: `${pageWidth * 0.5}px`,
            minHeight: '32px', maxHeight: '120px', overflow: 'auto',
            padding: '6px 8px',
            background: `${note.color}cc`,
            borderRadius: '4px',
            boxShadow: '0 2px 6px rgba(0,0,0,0.1)',
            fontSize: '10px', lineHeight: '1.5', color: '#333',
            cursor: annotationMode ? 'grab' : 'default',
            pointerEvents: 'auto',
          }}
        >
          <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{note.text}</div>
          {hoveredId === note.id && annotationMode && (
            <div
              onClick={e => { e.stopPropagation(); onDeleteNote(note.id) }}
              style={{
                position: 'absolute', top: '-5px', right: '-5px',
                width: '16px', height: '16px', borderRadius: '50%',
                background: '#ef4444', color: '#fff',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '12px', fontWeight: 700, cursor: 'pointer',
                boxShadow: '0 1px 3px rgba(0,0,0,0.2)', lineHeight: 1,
              }}
            >×</div>
          )}
        </div>
      ))}

      {creating && (
        <NotePopup
          left={`${creating.x}%`}
          top={`${creating.y}%`}
          initial=""
          confirmText="添加"
          onSave={text => { onCreateNote(creating.x, creating.y, text); closeAll() }}
          onCancel={closeAll}
        />
      )}
      {editingNote && (
        <NotePopup
          left={`${editingNote.x}%`}
          top={`${editingNote.y}%`}
          initial={editingNote.text}
          confirmText="保存"
          onSave={text => { onUpdateNote(editingNote.id, text); closeAll() }}
          onCancel={closeAll}
        />
      )}
    </div>
  )
}
