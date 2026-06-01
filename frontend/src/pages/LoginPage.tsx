// 登录页面 - 认证门禁

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Lock } from 'lucide-react'
import { MacOSTitlebar, MacOSInput } from '../components/MacOSLayout'
import { login, setToken, setAuthEmail } from '../api'
import { showAlert } from '../components/MacOSDialog'

export function LoginPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim() || !password.trim()) {
      showAlert({ title: '登录失败', message: '邮箱和密码不能为空', variant: 'danger' })
      return
    }

    setLoading(true)
    try {
      const result = await login(email.trim(), password)
      setToken(result.token)
      setAuthEmail(result.email)
      // 用 window.location 强制刷新，让 AuthGate 重新检查认证状态
      window.location.href = '/'
    } catch (err) {
      showAlert({
        title: '登录失败',
        message: err instanceof Error ? err.message : '未知错误',
        variant: 'danger'
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--macos-bg-secondary)', overflow: 'hidden' }}>
      <MacOSTitlebar />

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="macOS-animate-page-in" style={{
          width: '100%', maxWidth: 380, padding: '40px 36px',
          background: 'var(--macos-bg-primary)', borderRadius: 16,
          boxShadow: 'var(--macos-shadow-lg)',
        }}>
          {/* Logo */}
          <div style={{ textAlign: 'center', marginBottom: 28 }}>
            <div style={{
              width: 56, height: 56, margin: '0 auto 12px',
              background: 'var(--macos-accent)', borderRadius: 16,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Lock className="w-7 h-7" color="#fff" />
            </div>
            <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--macos-text-primary)', marginBottom: 6 }}>
              刑事案卷分析系统
            </h1>
            <p style={{ fontSize: 13, color: 'var(--macos-text-secondary)' }}>
              登录您的账号
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleLogin}>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--macos-text-primary)', marginBottom: 6 }}>
                邮箱
              </label>
              <MacOSInput
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="your@email.com"
              />
            </div>

            <div style={{ marginBottom: 24 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--macos-text-primary)', marginBottom: 6 }}>
                密码
              </label>
              <MacOSInput
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="请输入密码"
                showToggle
                onToggleShow={() => setShowPassword(!showPassword)}
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="macOS-button macOS-button-primary"
              style={{
                width: '100%', padding: '12px',
                fontSize: 15, fontWeight: 600,
              }}
            >
              {loading ? '登录中...' : '登录'}
            </button>
          </form>

          {/* Links */}
          <div style={{ textAlign: 'center', marginTop: 20, fontSize: 13, color: 'var(--macos-text-secondary)' }}>
            <span
              onClick={() => navigate('/reset-password')}
              style={{ color: 'var(--macos-accent)', textDecoration: 'none', cursor: 'pointer' }}
              onMouseEnter={e => e.currentTarget.style.textDecoration = 'underline'}
              onMouseLeave={e => e.currentTarget.style.textDecoration = 'none'}
            >
              忘记密码？
            </span>
            <span style={{ margin: '0 8px', color: 'var(--macos-border)' }}>|</span>
            还没有账号？{' '}
            <span
              onClick={() => navigate('/register')}
              style={{ color: 'var(--macos-accent)', textDecoration: 'none', cursor: 'pointer' }}
              onMouseEnter={e => e.currentTarget.style.textDecoration = 'underline'}
              onMouseLeave={e => e.currentTarget.style.textDecoration = 'none'}
            >
              去注册
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
