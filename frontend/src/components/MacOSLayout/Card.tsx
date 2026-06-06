export function MacOSCard({ children, className = '', style, onClick, clickable }: {
  children: React.ReactNode
  className?: string
  style?: React.CSSProperties
  onClick?: (e: React.MouseEvent) => void
  clickable?: boolean
}) {
  return (
    <div
      className={`macOS-card ${clickable ? 'macOS-card-clickable' : ''} ${className}`}
      style={style}
      onClick={onClick}
    >
      {children}
    </div>
  )
}
