export function MacOSEmptyState({
  icon: Icon,
  title,
  description,
  action
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
  action?: React.ReactNode
}) {
  return (
    <div className="macOS-empty-state macOS-animate-in">
      <Icon className="macOS-empty-state-icon" />
      <div className="macOS-empty-state-title">{title}</div>
      <div className="macOS-empty-state-description">{description}</div>
      {action && <div style={{ marginTop: 20 }}>{action}</div>}
    </div>
  )
}
