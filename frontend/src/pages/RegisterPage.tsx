// 注册页面

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UserPlus, ArrowLeft } from 'lucide-react'
import { MacOSTitlebar } from '../components/MacOSLayout'
import { register } from '../api'
import { showAlert } from '../components/MacOSDialog'

export function RegisterPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim() || !password.trim()) {
      showAlert({ title: '注册失败', message: '邮箱和密码不能为空', variant: 'danger' })
      return
    }
    if (password.length < 6) {
      showAlert({ title: '注册失败', message: '密码至少 6 位', variant: 'danger' })
      return
    }
    if (password !== confirmPassword) {
      showAlert({ title: '注册失败', message: '两次输入的密码不一致', variant: 'danger' })
      return
    }

    setLoading(true)
    try {
      const result = await register(email.trim(), password)
      if (result.success) {
        showAlert({
          title: '注册成功',
          message: '请使用新账号登录',
          variant: 'success',
        })
        navigate('/login')
      }
    } catch (err) {
      showAlert({
        title: '注册失败',
        message: err instanceof Error ? err.message : '未知错误',
        variant: 'danger',
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
          {/* 返回按钮 */}
          <button
            onClick={() => navigate('/login')}
            style={{
              display: 'flex', alignItems: 'center', gap: '4px',
              padding: '4px 8px', background: 'transparent', border: 'none',
              cursor: 'pointer', fontSize: '13px', color: '#007aff', marginBottom: 16,
            }}
          >
            <ArrowLeft className="w-4 h-4" />
            返回登录
          </button>

          {/* Logo */}
          <div style={{ textAlign: 'center', marginBottom: 28 }}>
            <div style={{
              width: 56, height: 56, margin: '0 auto 12px',
              background: '#34c759', borderRadius: 16,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <UserPlus className="w-7 h-7" color="#fff" />
            </div>
            <h1 style={{ fontSize: 20, fontWeight: 700, color: '#1d1d1f', marginBottom: 6 }}>
              注册账号
            </h1>
            <p style={{ fontSize: 13, color: '#86868b' }}>
              创建新账号以使用系统
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleRegister}>
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

            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: '#1d1d1f', marginBottom: 6 }}>
                密码
              </label>
              <div style={{ position: 'relative' }}>
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="至少 6 位"
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

            <div style={{ marginBottom: 24 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: '#1d1d1f', marginBottom: 6 }}>
                确认密码
              </label>
              <input
                type={showPassword ? 'text' : 'password'}
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                placeholder="再次输入密码"
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

            <button
              type="submit"
              disabled={loading}
              style={{
                width: '100%', padding: '12px',
                background: loading ? '#86868b' : '#34c759', color: '#fff',
                border: 'none', borderRadius: 8, cursor: loading ? 'not-allowed' : 'pointer',
                fontSize: 15, fontWeight: 600,
                transition: 'background 0.15s',
              }}
            >
              {loading ? '注册中...' : '注册'}
            </button>
          </form>

          {/* Links */}
          <div style={{ textAlign: 'center', marginTop: 20, fontSize: 13, color: '#86868b' }}>
            已有账号？{' '}
            <span
              onClick={() => navigate('/login')}
              style={{ color: '#007aff', textDecoration: 'none', cursor: 'pointer' }}
              onMouseEnter={e => e.currentTarget.style.textDecoration = 'underline'}
              onMouseLeave={e => e.currentTarget.style.textDecoration = 'none'}
            >
              去登录
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
