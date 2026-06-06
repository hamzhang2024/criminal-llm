import { Loader2, CheckCircle, AlertCircle, XCircle } from 'lucide-react'

export interface StatusBarProps {
  message: string | null
  variant?: 'processing' | 'success' | 'error'
  onDismiss?: () => void
  processing?: boolean
}

/**
 * 统一的状态/进度/错误栏
 * 消除每个页面中重复的错误横幅和进度条样式
 */
export function StatusBar({ message, variant = 'processing', onDismiss, processing }: StatusBarProps) {
  if (!message) return null

  const iconMap = {
    processing: processing !== false ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle className="w-4 h-4" />,
    success: <CheckCircle className="w-4 h-4" color="var(--macos-success)" />,
    error: <XCircle className="w-4 h-4" color="var(--macos-danger)" />,
  }

  const bgMap = {
    processing: 'rgba(59, 89, 152, 0.08)',
    success: 'rgba(59, 89, 152, 0.08)',
    error: 'rgba(102, 102, 102, 0.08)',
  }

  const borderMap = {
    processing: '1px solid rgba(59, 89, 152, 0.15)',
    success: '1px solid rgba(59, 89, 152, 0.2)',
    error: '1px solid rgba(102, 102, 102, 0.2)',
  }

  const colorMap = {
    processing: 'var(--macos-accent)',
    success: 'var(--macos-success)',
    error: 'var(--macos-danger)',
  }

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '10px',
      padding: '10px 16px',
      margin: '0 24px 16px',
      background: bgMap[variant],
      border: borderMap[variant],
      borderRadius: '8px',
      fontSize: '13px',
      color: colorMap[variant],
    }}>
      {iconMap[variant]}
      <span style={{ flex: 1 }}>{message}</span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            fontSize: '16px',
            color: colorMap[variant],
            padding: '0 4px',
            opacity: 0.6,
          }}
        >
          ×
        </button>
      )}
    </div>
  )
}
