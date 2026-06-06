import { ReactNode } from 'react'

export interface InlineDialogProps {
  open: boolean
  onClose?: () => void
  title?: string
  children: ReactNode
  width?: number
}

/**
 * 通用浮动弹窗组件
 * 消除每个页面中重复的 `position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center`
 */
export function InlineDialog({ open, onClose, title, children, width = 400 }: InlineDialogProps) {
  if (!open) return null

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.4)',
        backdropFilter: 'blur(12px) saturate(180%)',
        WebkitBackdropFilter: 'blur(12px) saturate(180%)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 10001,
      }}
      onClick={(e) => { if (e.target === e.currentTarget && onClose) onClose() }}
    >
      <div
        className="macOS-animate-in"
        style={{
          background: 'rgba(255, 255, 255, 0.72)',
          backdropFilter: 'blur(20px) saturate(180%)',
          WebkitBackdropFilter: 'blur(20px) saturate(180%)',
          borderRadius: '12px',
          padding: '24px',
          width: width,
          maxWidth: '90vw',
          maxHeight: '80vh',
          overflow: 'auto',
          boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
        }}
      >
        {title && (
          <h3 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '16px', color: 'var(--macos-text-primary)' }}>
            {title}
          </h3>
        )}
        {children}
      </div>
    </div>
  )
}
