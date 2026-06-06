import { Link, useLocation } from 'react-router-dom'
import { FileText, Scale, Wand2, ChevronRight, FileDown, BookOpen } from 'lucide-react'
import '../../styles/macOS.css'

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
        <div style={{ padding: '0 12px 12px' }}>
          <div style={{ height: '4px', background: 'rgba(0,0,0,0.08)', borderRadius: '2px', overflow: 'hidden' }}>
            <div style={{
              height: '100%',
              width: `${currentIndex >= 0 ? ((currentIndex + 1) / mainItems.length) * 100 : 0}%`,
              background: currentIndex >= 0 ? 'var(--macos-accent)' : 'transparent',
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
