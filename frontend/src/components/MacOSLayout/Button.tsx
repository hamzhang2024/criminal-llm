export function MacOSButton({
  variant = 'secondary',
  children,
  onClick,
  icon: Icon,
  disabled = false,
  style
}: {
  variant?: 'primary' | 'secondary' | 'icon'
  children?: React.ReactNode
  onClick?: () => void
  icon?: React.ComponentType<{ className?: string }>
  disabled?: boolean
  style?: React.CSSProperties
}) {
  if (variant === 'icon' && Icon) {
    return (
      <button className="macOS-button macOS-button-icon" onClick={onClick} disabled={disabled} style={style}>
        <Icon className="w-4 h-4" />
      </button>
    )
  }

  return (
    <button
      className={`macOS-button ${variant === 'primary' ? 'macOS-button-primary' : 'macOS-button-secondary'}`}
      onClick={onClick}
      disabled={disabled}
      style={{ ...style, ...(disabled ? { opacity: 0.5, cursor: 'not-allowed' } : {}) }}
    >
      {Icon && <Icon className="w-4 h-4" />}
      {children}
    </button>
  )
}
