import { useState, useRef, useEffect, useCallback } from 'react'

interface Annotation {
  id: string
  x: number       // 像素坐标（相对于 scrollable content 左上角）
  y: number
  text: string
  color: string
  createdAt: string
  containerWidth?: number   // 创建时容器宽度，用于判断是否需要重新计算
  containerHeight?: number  // 创建时容器高度
}

interface StickyNoteOverlayProps {
  annotations: Annotation[]
  onAdd: (annotation: Annotation) => void
  onUpdate: (id: string, text: string) => void
  onUpdatePosition: (id: string, x: number, y: number) => void
  onDelete: (id: string) => void
  active: boolean
  /** 滚动的内容容器 ref，用于计算相对坐标 */
  contentRef: React.RefObject<HTMLDivElement | null>
}

const NOTE_COLORS = [
  '#fff9c4', '#ffecb3', '#ffe0b2', '#ffcdd2',
  '#f8bbd0', '#e1bee7', '#d1c4e9', '#c5cae9',
  '#bbdefb', '#b3e5fc', '#b2ebf2', '#b2dfdb',
  '#c8e6c9', '#dcedc8', '#f0f4c3',
]

const randomColor = () => NOTE_COLORS[Math.floor(Math.random() * NOTE_COLORS.length)]
const generateId = () => Math.random().toString(36).substr(2, 9)

export function StickyNoteOverlay({ annotations, onAdd, onUpdate, onUpdatePosition, onDelete, active, contentRef }: StickyNoteOverlayProps) {
  const [creating, setCreating] = useState<{ x: number; y: number } | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const dragStateRef = useRef<{
    noteId: string
    startX: number
    startY: number
    origX: number
    origY: number
    el: HTMLElement
  } | null>(null)

  useEffect(() => {
    if (creating || editing) {
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [creating, editing])

  // 获取相对于 content 的像素坐标
  const getRelativePos = useCallback((e: React.MouseEvent) => {
    const content = contentRef.current
    if (!content) return null
    const rect = content.getBoundingClientRect()
    return {
      x: e.clientX - rect.left + content.scrollLeft,
      y: e.clientY - rect.top + content.scrollTop,
    }
  }, [contentRef])

  // 点击创建
  const handleClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!active) return
    if (e.target !== e.currentTarget) return
    const pos = getRelativePos(e)
    if (!pos) return
    setCreating({ x: pos.x, y: pos.y })
    setDraft('')
  }, [active, getRelativePos])

  // 拖拽移动便签 — mousemove 直接操作 DOM 避免闪烁，mouseup 保存
  const handleMouseDown = useCallback((e: React.MouseEvent, noteId: string) => {
    if (!active) return
    e.stopPropagation()
    e.preventDefault()

    const el = e.currentTarget as HTMLElement
    const note = annotations.find(a => a.id === noteId)
    if (!note) return

    const startX = e.clientX
    const startY = e.clientY
    const origX = note.x
    const origY = note.y

    dragStateRef.current = { noteId, startX, startY, origX, origY, el }

    const onMove = (ev: MouseEvent) => {
      const d = dragStateRef.current
      if (!d) return
      const dx = ev.clientX - d.startX
      const dy = ev.clientY - d.startY
      d.el.style.left = `${d.origX + dx}px`
      d.el.style.top = `${d.origY + dy}px`
    }
    const onUp = (ev: MouseEvent) => {
      const d = dragStateRef.current
      if (!d) return
      dragStateRef.current = null
      const dx = ev.clientX - d.startX
      const dy = ev.clientY - d.startY
      onUpdatePosition(d.noteId, d.origX + dx, d.origY + dy)
    }

    // 用 document.body 监听以确保 mouseup 也能捕获
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp, { once: true })
  }, [active, annotations, onAdd])

  // 保存新建
  const saveNew = useCallback(() => {
    if (!creating || !draft.trim()) {
      setCreating(null)
      return
    }
    onAdd({
      id: generateId(),
      x: creating.x,
      y: creating.y,
      text: draft.trim(),
      color: randomColor(),
      createdAt: new Date().toISOString(),
    })
    setCreating(null)
    setDraft('')
  }, [creating, draft, onAdd])

  // 保存编辑
  const saveEdit = useCallback(() => {
    if (!editing || !draft.trim()) {
      setEditing(null)
      return
    }
    onUpdate(editing, draft.trim())
    setEditing(null)
    setDraft('')
  }, [editing, draft, onUpdate])

  // 开始编辑
  const startEdit = useCallback((note: Annotation) => {
    if (dragStateRef.current) return // 拖拽中不触发编辑
    setEditing(note.id)
    setDraft(note.text)
  }, [])

  return (
    <div
      ref={overlayRef}
      onClick={handleClick}
      style={{
        position: 'absolute',
        top: 0, left: 0, right: 0, bottom: 0,
        pointerEvents: active ? 'auto' : 'none',
        zIndex: 50,
      }}
    >
      {/* 已存在的便签 */}
      {annotations.map(note => (
        <div
          key={note.id}
          onMouseEnter={() => setHoveredId(note.id)}
          onMouseLeave={() => setHoveredId(null)}
          onClick={e => { e.stopPropagation(); startEdit(note) }}
          onMouseDown={e => handleMouseDown(e, note.id)}
          style={{
            position: 'absolute',
            left: note.x,
            top: note.y,
            transform: 'translate(-50%, -50%)',
            width: '180px',
            minHeight: '40px',
            maxHeight: '120px',
            overflow: 'auto',
            padding: '8px 10px',
            background: `${note.color}99`,
            borderRadius: '6px',
            boxShadow: '0 2px 8px rgba(0,0,0,0.12)',
            fontSize: '11px',
            lineHeight: '1.5',
            color: '#333',
            cursor: active ? 'grab' : 'default',
            pointerEvents: active ? 'auto' : 'none',
          }}
        >
          <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {note.text}
          </div>
          {hoveredId === note.id && active && (
            <div
              onClick={e => { e.stopPropagation(); onDelete(note.id) }}
              style={{
                position: 'absolute', top: '-6px', right: '-6px',
                width: '18px', height: '18px', borderRadius: '50%',
                background: '#ef4444', color: '#fff',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '11px', fontWeight: 700, cursor: 'pointer',
                boxShadow: '0 1px 4px rgba(0,0,0,0.2)',
              }}
            >
              ×
            </div>
          )}
        </div>
      ))}

      {/* 正在创建/编辑的便签输入框 */}
      {(creating || editing) && (
        <div
          onClick={e => e.stopPropagation()}
          style={{
            position: 'absolute',
            left: creating ? creating.x : undefined,
            top: creating ? creating.y : undefined,
            ...(editing ? (() => {
              const note = annotations.find(a => a.id === editing)
              return note ? { left: note.x, top: note.y } : {}
            })() : {}),
            transform: 'translate(-50%, -50%)',
            zIndex: 100,
          }}
        >
          <div style={{
            width: '220px', background: '#fff',
            borderRadius: '8px', boxShadow: '0 4px 16px rgba(0,0,0,0.2)',
            overflow: 'hidden',
          }}>
            <textarea
              ref={inputRef}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey && draft.trim()) {
                  e.preventDefault()
                  if (editing) saveEdit()
                  else saveNew()
                }
                if (e.key === 'Escape') {
                  e.stopPropagation()
                  setCreating(null)
                  setEditing(null)
                  setDraft('')
                }
              }}
              placeholder={editing ? '修改批注...' : '输入批注...'}
              rows={3}
              style={{
                width: '100%', padding: '10px 12px', fontSize: '12px',
                border: 'none', outline: 'none', resize: 'none',
                fontFamily: 'inherit', background: '#fffdf0',
                boxSizing: 'border-box', lineHeight: '1.6',
              }}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '6px', padding: '6px 10px', borderTop: '1px solid #eee' }}>
              <button
                onClick={() => { setCreating(null); setEditing(null); setDraft('') }}
                style={{
                  padding: '3px 10px', fontSize: '11px', borderRadius: '4px',
                  background: '#f5f5f5', border: '1px solid #ddd', cursor: 'pointer',
                  color: '#666',
                }}
              >
                取消
              </button>
              <button
                onClick={editing ? saveEdit : saveNew}
                disabled={!draft.trim()}
                style={{
                  padding: '3px 10px', fontSize: '11px', borderRadius: '4px',
                  background: draft.trim() ? 'var(--macos-accent)' : '#eee',
                  color: draft.trim() ? '#fff' : '#999',
                  border: 'none', cursor: draft.trim() ? 'pointer' : 'default',
                }}
              >
                {editing ? '保存' : '添加'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
