// 登录页面 - 认证门禁

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Lock } from 'lucide-react'
import { MacOSTitlebar } from '../components/MacOSLayout'
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
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#f5f5f7', overflow: 'hidden' }}>
      <MacOSTitlebar />

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{
          width: '100%', maxWidth: 380, padding: '40px 36px',
          background: '#fff', borderRadius: 16,
          boxShadow: '0 4px 24px rgba(0,0,0,0.08)',
        }}>
          {/* Logo */}
          <div style={{ textAlign: 'center', marginBottom: 28 }}>
            <div style={{
              width: 56, height: 56, margin: '0 auto 12px',
              background: '#007aff', borderRadius: 16,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Lock className="w-7 h-7" color="#fff" />
            </div>
            <h1 style={{ fontSize: 20, fontWeight: 700, color: '#1d1d1f', marginBottom: 6 }}>
              刑事案卷分析系统
            </h1>
            <p style={{ fontSize: 13, color: '#86868b' }}>
              登录您的账号
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleLogin}>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: '#1d1d1f', marginBottom: 6 }}>
                邮箱
              </label>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="your@email.com"
                required
                style={{
                  width: '100%', padding: '10px 12px',
                  border: '1.5px solid #d2d2d7', borderRadius: 8,
                  fontSize: 14, boxSizing: 'border-box', outline: 'none',
                  transition: 'border-color 0.15s',
                }}
                onFocus={e => e.currentTarget.style.borderColor = '#007aff'}
                onBlur={e => e.currentTarget.style.borderColor = '#d2d2d7'}
              />
            </div>

            <div style={{ marginBottom: 24 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: '#1d1d1f', marginBottom: 6 }}>
                密码
              </label>
              <div style={{ position: 'relative' }}>
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="请输入密码"
                  required
                  style={{
                    width: '100%', padding: '10px 40px 10px 12px',
                    border: '1.5px solid #d2d2d7', borderRadius: 8,
                    fontSize: 14, boxSizing: 'border-box', outline: 'none',
                    transition: 'border-color 0.15s',
                  }}
                  onFocus={e => e.currentTarget.style.borderColor = '#007aff'}
                  onBlur={e => e.currentTarget.style.borderColor = '#d2d2d7'}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  style={{
                    position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)',
                    background: 'transparent', border: 'none', cursor: 'pointer', padding: 4,
                    fontSize: 12, color: '#86868b',
                  }}
                >
                  {showPassword ? '隐藏' : '显示'}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              style={{
                width: '100%', padding: '12px',
                background: loading ? '#86868b' : '#007aff', color: '#fff',
                border: 'none', borderRadius: 8, cursor: loading ? 'not-allowed' : 'pointer',
                fontSize: 15, fontWeight: 600,
                transition: 'background 0.15s',
              }}
            >
              {loading ? '登录中...' : '登录'}
            </button>
          </form>

          {/* Links */}
          <div style={{ textAlign: 'center', marginTop: 20, fontSize: 13, color: '#86868b' }}>
            <span
              onClick={() => navigate('/reset-password')}
              style={{ color: '#007aff', textDecoration: 'none', cursor: 'pointer' }}
              onMouseEnter={e => e.currentTarget.style.textDecoration = 'underline'}
              onMouseLeave={e => e.currentTarget.style.textDecoration = 'none'}
            >
              忘记密码？
            </span>
            <span style={{ margin: '0 8px', color: '#d2d2d7' }}>|</span>
            还没有账号？{' '}
            <span
              onClick={() => navigate('/register')}
              style={{ color: '#007aff', textDecoration: 'none', cursor: 'pointer' }}
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
