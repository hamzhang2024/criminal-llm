import { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { HomePage } from './pages/HomePage'
import { CaseDetailPage } from './pages/CaseDetailPage'
import { AnalyzePage } from './pages/AnalyzePage'
import { ProcessPage } from './pages/ProcessPage'
import { ConvertPage } from './pages/ConvertPage'
import { ReportPage } from './pages/ReportPage'
import { SettingsPage } from './pages/SettingsPage'
import { ManualPage } from './pages/ManualPage'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { ResetPasswordPage } from './pages/ResetPasswordPage'
import { useDialogProvider } from './components/MacOSDialog'
import { verifyToken, getToken, clearToken, clearAuthEmail, claimCases } from './api'

function DialogWrapper() {
  const DialogComponent = useDialogProvider()
  return DialogComponent
}

/** 认证门禁：检查 token 有效性，无效则显示登录页 */
function AuthGate({ children }: { children: React.ReactNode }) {
  const [authed, setAuthed] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false

    const checkAuth = async () => {
      const token = getToken()
      if (!token) {
        if (!cancelled) setAuthed(false)
        return
      }
      // 有 token，验证有效性，5 秒超时
      try {
        const timeoutId = setTimeout(() => { if (!cancelled) setAuthed(false) }, 5000)
        const result = await verifyToken(token)
        clearTimeout(timeoutId)
        if (!cancelled) {
          setAuthed(true)
          // 首次登录，自动关联无主案件
          if (result.email) {
            claimCases(result.email).catch(() => {})
          }
        }
      } catch (err) {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : ''
          // 网络错误（服务器不可达）→ 信任本地 token，允许使用
          if (msg.includes('网络错误')) {
            setAuthed(true)
          } else {
            // token 明确无效/过期 → 清除并跳转登录
            clearToken()
            clearAuthEmail()
            setAuthed(false)
          }
        }
      }
    }
    checkAuth()

    return () => { cancelled = true }
  }, [])

  // 验证中，最多显示 500ms，超时后直接显示登录页
  if (authed === null) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: '#f5f5f7' }}>
        <div style={{ fontSize: 14, color: '#86868b' }}>验证中...</div>
      </div>
    )
  }

  // 未认证，显示登录页
  if (!authed) {
    return <LoginPage />
  }

  // 已认证，显示主应用
  return <>{children}</>
}

function App() {
  return (
    <BrowserRouter>
      {/* 登录/注册页面不需要认证 */}
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
      </Routes>

      <AuthGate>
        <Routes>
          {/* 首页 - 案件管理 */}
          <Route path="/" element={<HomePage />} />

          {/* 设置页面 */}
          <Route path="/settings" element={<SettingsPage />} />

          {/* 案件详情 - 完整工作流 */}
          <Route path="/case/:caseId" element={<CaseDetailPage />} />

          {/* 独立页面（保留用于向后兼容） */}
          <Route path="/process" element={<ProcessPage />} />
          <Route path="/convert" element={<ConvertPage />} />
          <Route path="/analyze" element={<AnalyzePage />} />

          {/* 案卷分析报告页面 */}
          <Route path="/case/:caseId/report" element={<ReportPage />} />

          {/* 使用说明书 */}
          <Route path="/manual" element={<ManualPage />} />
        </Routes>
      </AuthGate>
      <DialogWrapper />
    </BrowserRouter>
  )
}

export default App
