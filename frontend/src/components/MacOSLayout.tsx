import { Link, useLocation } from 'react-router-dom'
import { FileText, Scale, Wand2, ChevronRight, FileDown, BookOpen } from 'lucide-react'
import '../styles/macOS.css'

interface SidebarItemProps {
  path: string
  label: string
  icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>
  active?: boolean
}

function SidebarItem({ path, label, icon: Icon, active }: SidebarItemProps) {
  return (
    <Link to={path} className={`macOS-sidebar-item ${active ? 'active' : ''}`}>
      <Icon className="w-4 h-4" />
      <span>{label}</span>
      <ChevronRight className="w-3 h-3 ml-auto opacity-0 group-hover:opacity-50" />
    </Link>
  )
}

export function MacOSSidebar() {
  const location = useLocation()
  
  const mainItems = [
    { path: '/process', label: '① PDF处理', icon: Wand2 },
    { path: '/convert', label: '② PDF转MD', icon: FileDown },
    { path: '/analyze', label: '③ 案卷分析', icon: Scale },
  ]

  const currentIndex = mainItems.findIndex(item => location.pathname === item.path)
  
  return (
    <aside className="macOS-sidebar">
      <div className="macOS-sidebar-section">
        <div className="macOS-sidebar-title">工作流进度</div>
        
        {/* 进度条 */}
        <div style={{ padding: '0 12px 12px' }}>
          <div style={{ height: '4px', background: 'rgba(0,0,0,0.1)', borderRadius: '2px', overflow: 'hidden' }}>
            <div style={{ 
              height: '100%', 
              width: `${currentIndex >= 0 ? ((currentIndex + 1) / mainItems.length) * 100 : 0}%`,
              background: currentIndex >= 0 ? '#007aff' : 'transparent',
              borderRadius: '2px',
              transition: 'width 0.3s ease'
            }} />
          </div>
          <div style={{ fontSize: '11px', color: '#86868b', marginTop: '4px' }}>
            {currentIndex >= 0 ? `步骤 ${currentIndex + 1}/${mainItems.length}` : '未开始'}
          </div>
        </div>
        
        <div className="group">
          {mainItems.map(({ path, label, icon: Icon }) => (
            <SidebarItem
              key={path}
              path={path}
              label={label}
              icon={Icon}
              active={location.pathname === path}
            />
          ))}
        </div>
      </div>
      
      <div className="macOS-sidebar-section">
        <div className="macOS-sidebar-title">工具</div>
        <div className="group">
          <SidebarItem
            path="/tauri-test"
            label="Tauri 测试"
            icon={FileText}
            active={location.pathname === '/tauri-test'}
          />
          <SidebarItem
            path="/manual"
            label="使用说明书"
            icon={BookOpen}
            active={location.pathname === '/manual'}
          />
        </div>
      </div>
    </aside>
  )
}

export function MacOSTitlebar({ showBack = false, onBack }: { showBack?: boolean, onBack?: () => void }) {
  return (
    <div className="macOS-titlebar">
      <div className="macOS-titlebar-controls">
        <div className="macOS-control close" />
        <div className="macOS-control minimize" />
        <div className="macOS-control maximize" />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', flex: 1, justifyContent: 'center' }}>
        {showBack && (
          <button 
            onClick={onBack}
            style={{
              position: 'absolute',
              left: '16px',
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              padding: '4px',
              borderRadius: '6px',
              display: 'flex',
              alignItems: 'center'
            }}
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <path d="M12.5 15L7.5 10L12.5 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
        )}
        <div className="macOS-title">刑事案卷分析系统</div>
      </div>
      <div style={{ width: 52 }} />
    </div>
  )
}

export function MacOSToolbar({ title, children }: { title: string, children?: React.ReactNode }) {
  return (
    <div className="macOS-toolbar">
      <div className="macOS-toolbar-title">{title}</div>
      <div className="macOS-toolbar-actions">
        {children}
      </div>
    </div>
  )
}

export function MacOSButton({ 
  variant = 'secondary', 
  children, 
  onClick,
  icon: Icon,
  disabled = false
}: { 
  variant?: 'primary' | 'secondary' | 'icon'
  children?: React.ReactNode
  onClick?: () => void
  icon?: React.ComponentType<{ className?: string }>
  disabled?: boolean
}) {
  if (variant === 'icon' && Icon) {
    return (
      <button className="macOS-button macOS-button-icon" onClick={onClick}>
        <Icon className="w-4 h-4" />
      </button>
    )
  }
  
  return (
    <button 
      className={`macOS-button ${variant === 'primary' ? 'macOS-button-primary' : 'macOS-button-secondary'}`}
      onClick={onClick}
      disabled={disabled}
      style={disabled ? { opacity: 0.5, cursor: 'not-allowed' } : {}}
    >
      {Icon && <Icon className="w-4 h-4" />}
      {children}
    </button>
  )
}

export function MacOSCard({ children, className = '', style, onClick }: { children: React.ReactNode, className?: string, style?: React.CSSProperties, onClick?: (e: React.MouseEvent) => void }) {
  return (
    <div className={`macOS-card ${className}`} style={style} onClick={onClick}>
      {children}
    </div>
  )
}

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
    <div className="macOS-empty-state">
      <Icon className="macOS-empty-state-icon" />
      <div className="macOS-empty-state-title">{title}</div>
      <div className="macOS-empty-state-description">{description}</div>
      {action && <div style={{ marginTop: 20 }}>{action}</div>}
    </div>
  )
}
