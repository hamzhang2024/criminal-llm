import { ReactNode } from 'react'

/**
 * 统一的 Section 标题组件
 * 消除 SettingsPage 等页面中重复的 inline style 标题
 */
export function SectionHeader({
  title,
  subtitle,
  action,
}: {
  title: string
  subtitle?: string
  action?: ReactNode
}) {
  return (
    <div style={{
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: '16px',
    }}>
      <div>
        <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--macos-text-primary)' }}>
          {title}
        </div>
        {subtitle && (
          <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)', marginTop: '2px' }}>
            {subtitle}
          </div>
        )}
      </div>
      {action && <div>{action}</div>}
    </div>
  )
}

/**
 * 卡片式 Section 内部的小标题（带左侧装饰条��
 */
export function SectionSubtitle({ title, icon: Icon }: {
  title: string
  icon?: React.ComponentType<{ className?: string; size?: number }>
}) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
      fontSize: '13px',
      fontWeight: 500,
      color: 'var(--macos-text-primary)',
      borderLeft: '3px solid var(--macos-accent)',
      paddingLeft: '10px',
      marginBottom: '12px',
    }}>
      {Icon && <Icon size={16} />}
      {title}
    </div>
  )
}
