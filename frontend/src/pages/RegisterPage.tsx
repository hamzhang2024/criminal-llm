// 注册页面

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UserPlus, ArrowLeft } from 'lucide-react'
import { MacOSTitlebar, MacOSInput } from '../components/MacOSLayout'
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
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--macos-bg-secondary)', overflow: 'hidden' }}>
      <MacOSTitlebar />

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="macOS-animate-page-in" style={{
          width: '100%', maxWidth: 380, padding: '40px 36px',
          background: 'var(--macos-bg-primary)', borderRadius: 16,
          boxShadow: 'var(--macos-shadow-lg)',
        }}>
          {/* 返回按钮 */}
          <button
            onClick={() => navigate('/login')}
            className="macOS-back-button"
            style={{ position: 'static', marginBottom: 16, gap: 4, fontSize: 13 }}
          >
            <ArrowLeft className="w-4 h-4" />
            返回登录
          </button>

          {/* Logo */}
          <div style={{ textAlign: 'center', marginBottom: 28 }}>
            <div style={{
              width: 56, height: 56, margin: '0 auto 12px',
              background: 'var(--macos-success)', borderRadius: 16,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <UserPlus className="w-7 h-7" color="#fff" />
            </div>
            <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--macos-text-primary)', marginBottom: 6 }}>
              注册账号
            </h1>
            <p style={{ fontSize: 13, color: 'var(--macos-text-secondary)' }}>
              创建新账号以使用系统
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleRegister}>
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

            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--macos-text-primary)', marginBottom: 6 }}>
                密码
              </label>
              <MacOSInput
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="至少 6 位"
                showToggle
                onToggleShow={() => setShowPassword(!showPassword)}
              />
            </div>

            <div style={{ marginBottom: 24 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--macos-text-primary)', marginBottom: 6 }}>
                确认密码
              </label>
              <MacOSInput
                type={showPassword ? 'text' : 'password'}
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                placeholder="再次输入密码"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="macOS-button macOS-button-primary"
              style={{
                width: '100%', padding: '12px',
                fontSize: 15, fontWeight: 600,
                background: loading ? 'var(--macos-text-tertiary)' : 'var(--macos-success)',
              }}
            >
              {loading ? '注册中...' : '注册'}
            </button>
          </form>

          {/* Links */}
          <div style={{ textAlign: 'center', marginTop: 20, fontSize: 13, color: 'var(--macos-text-secondary)' }}>
            已有账号？{' '}
            <span
              onClick={() => navigate('/login')}
              style={{ color: 'var(--macos-accent)', textDecoration: 'none', cursor: 'pointer' }}
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
