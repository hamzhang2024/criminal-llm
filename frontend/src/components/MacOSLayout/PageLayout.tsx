import { ReactNode } from 'react'
import '../../styles/macOS.css'

/**
 * 统一页面外层布局组件
 * 消除每个页面重复的 `display: flex; flex-direction: column; height: 100vh; background: var(--macos-bg-primary); overflow: hidden`
 */
export function PageLayout({ children }: { children: ReactNode }) {
  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      background: 'var(--macos-bg-primary)',
      overflow: 'hidden'
    }}>
      {children}
    </div>
  )
}

/**
 * 页面内水平分栏容器（如 CaseDetailPage 的 左侧导航 + 主区域）
 */
export function PageColumns({ children, sidebarWidth = 200 }: { children: ReactNode; sidebarWidth?: number }) {
  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
      {children}
    </div>
  )
}

/**
 * 页面内容主区域（右侧滚动区域）
 */
export function PageContent({ children }: { children: ReactNode }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {children}
    </div>
  )
}
