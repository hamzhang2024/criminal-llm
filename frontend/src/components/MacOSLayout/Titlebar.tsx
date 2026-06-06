export function MacOSTitlebar({ showBack = false, onBack }: { showBack?: boolean, onBack?: () => void }) {
  return (
    <div className="macOS-titlebar">
      <div style={{ display: 'flex', alignItems: 'center', flex: 1, justifyContent: 'center' }}>
        {showBack && (
          <button className="macOS-back-button" onClick={onBack}>
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <path d="M12.5 15L7.5 10L12.5 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
        )}
        <div className="macOS-title">刑事案卷分析系统</div>
      </div>
    </div>
  )
}
