export function MacOSInput({
  type = 'text', value, onChange, placeholder, style,
  showToggle, onToggleShow, disabled, wrapperStyle
}: {
  type?: string
  value: string
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void
  placeholder?: string
  style?: React.CSSProperties
  showToggle?: boolean
  onToggleShow?: () => void
  disabled?: boolean
  wrapperStyle?: React.CSSProperties
}) {
  return (
    <div style={{ position: 'relative', ...wrapperStyle }}>
      <input
        type={type}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        disabled={disabled}
        className="macOS-input"
        style={showToggle ? { ...style, paddingRight: 50 } : style}
      />
      {showToggle && (
        <button
          type="button"
          onClick={onToggleShow}
          className="macOS-input-toggle"
        >
          {type === 'password' ? '显示' : '隐藏'}
        </button>
      )}
    </div>
  )
}
