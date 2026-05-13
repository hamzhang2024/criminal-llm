// macOS 风格对话框组件

import { useState, useEffect, useCallback } from 'react'
import { AlertTriangle, Info, CheckCircle, XCircle } from 'lucide-react'

type DialogType = 'confirm' | 'alert'
type DialogVariant = 'danger' | 'warning' | 'info' | 'success'

interface DialogOptions {
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  variant?: DialogVariant
  onConfirm?: () => void
  onCancel?: () => void
}

interface DialogState extends DialogOptions {
  type: DialogType
  visible: boolean
}

let globalSetDialog: React.Dispatch<React.SetStateAction<DialogState | null>> | undefined

// 导出给外部使用的 confirm/alert 替代函数
export function showConfirm(options: DialogOptions): Promise<boolean> {
  return new Promise((resolve) => {
    if (globalSetDialog) {
      globalSetDialog(() => ({
        ...options,
        type: 'confirm',
        visible: true,
        onConfirm: () => { resolve(true); globalSetDialog?.(() => null) },
        onCancel: () => { resolve(false); globalSetDialog?.(() => null) },
      }))
    } else {
      resolve(window.confirm(options.message))
    }
  })
}

export function showAlert(options: Omit<DialogOptions, 'onConfirm' | 'onCancel' | 'cancelText'>): Promise<void> {
  return new Promise((resolve) => {
    if (globalSetDialog) {
      globalSetDialog(() => ({
        ...options,
        type: 'alert',
        visible: true,
        onConfirm: () => { resolve(); globalSetDialog?.(() => null) },
      }))
    } else {
      window.alert(options.message)
      resolve()
    }
  })
}

// 导出 Hook 供根组件使用
export function useDialogProvider() {
  const [dialog, setDialog] = useState<DialogState | null>(null)

  useEffect(() => {
    globalSetDialog = setDialog
    return () => { globalSetDialog = undefined as any }
  }, [])

  const closeDialog = useCallback(() => {
    if (dialog?.onCancel) dialog.onCancel()
    setDialog(null)
  }, [dialog])

  const DialogComponent = dialog ? (
    <DialogRenderer dialog={dialog} onClose={closeDialog} />
  ) : null

  return DialogComponent
}

// 内部对话框渲染组件
function DialogRenderer({ dialog, onClose }: { dialog: DialogState; onClose: () => void }) {
  const variantConfig: Record<DialogVariant, { icon: typeof Info; color: string; bg: string }> = {
    danger: { icon: XCircle, color: '#ff3b30', bg: 'rgba(255, 59, 48, 0.1)' },
    warning: { icon: AlertTriangle, color: '#ff9500', bg: 'rgba(255, 149, 0, 0.1)' },
    info: { icon: Info, color: '#007aff', bg: 'rgba(0, 122, 255, 0.1)' },
    success: { icon: CheckCircle, color: '#34c759', bg: 'rgba(52, 199, 89, 0.1)' },
  }

  const { icon: Icon, color, bg } = variantConfig[dialog.variant || 'info']

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100001 }}
      onClick={dialog.type === 'alert' ? onClose : undefined}
    >
      <div
        style={{
          background: '#ffffff',
          borderRadius: '14px',
          padding: '24px',
          maxWidth: '400px',
          width: '90vw',
          boxShadow: '0 24px 80px rgba(0,0,0,0.25)',
          animation: 'dialogIn 0.15s ease-out'
        }}
        onClick={e => e.stopPropagation()}
      >
        <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start', marginBottom: '16px' }}>
          <div style={{
            width: '40px',
            height: '40px',
            borderRadius: '50%',
            background: bg,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0
          }}>
            <Icon className="w-5 h-5" color={color} />
          </div>
          <div style={{ flex: 1 }}>
            <h3 style={{ fontSize: '15px', fontWeight: '600', marginBottom: '6px', color: '#1d1d1f' }}>
              {dialog.title}
            </h3>
            <p style={{ fontSize: '13px', color: '#6e6e73', lineHeight: '1.5', margin: 0 }}>
              {dialog.message}
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
          {dialog.type === 'confirm' && (
            <button
              onClick={onClose}
              style={{
                padding: '8px 18px',
                borderRadius: '8px',
                border: '1px solid #d1d1d6',
                background: 'transparent',
                color: '#86868b',
                fontSize: '13px',
                fontWeight: '500',
                cursor: 'pointer'
              }}
            >
              {dialog.cancelText || '取消'}
            </button>
          )}
          <button
            onClick={dialog.onConfirm || onClose}
            onMouseDown={e => {
              if (dialog.onConfirm) {
                e.stopPropagation()
              }
            }}
            autoFocus
            style={{
              padding: '8px 18px',
              borderRadius: '8px',
              border: 'none',
              background: dialog.variant === 'danger' ? '#ff3b30' : '#007aff',
              color: '#fff',
              fontSize: '13px',
              fontWeight: '500',
              cursor: 'pointer'
            }}
          >
            {dialog.confirmText || (dialog.variant === 'danger' ? '删除' : '确定')}
          </button>
        </div>
      </div>
    </div>
  )
}
