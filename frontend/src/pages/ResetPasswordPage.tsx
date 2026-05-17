// 找回密码页面 - 支持邮箱验证码和原密码两种方式

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Lock, ArrowLeft, Check, Mail } from 'lucide-react'
import { MacOSTitlebar } from '../components/MacOSLayout'
import { sendResetCode, resetWithCode, resetPassword } from '../api'
import { showAlert } from '../components/MacOSDialog'

export function ResetPasswordPage() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<'email' | 'password'>('email')
  const [email, setEmail] = useState('')
  const [oldPassword, setOldPassword] = useState('')
  const [code, setCode] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [codeSent, setCodeSent] = useState(false)
  const [countdown, setCountdown] = useState(0)
  const [success, setSuccess] = useState(false)

  const handleSendCode = async () => {
    if (!email.trim()) {
      showAlert({ title: '发送失败', message: '请输入邮箱', variant: 'danger' })
      return
    }
    setLoading(true)
    try {
      const result = await sendResetCode(email.trim())
      setCodeSent(true)
      showAlert({ title: '发送成功', message: result.message || '验证码已发送', variant: 'success' })
      setCountdown(60)
      const timer = setInterval(() => {
        setCountdown(prev => {
          if (prev <= 1) { clearInterval(timer); return 0 }
          return prev - 1
        })
      }, 1000)
    } catch (err) {
      showAlert({ title: '发送失败', message: err instanceof Error ? err.message : '未知错误', variant: 'danger' })
    } finally {
      setLoading(false)
    }
  }

  const handleResetEmail = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!code.trim()) {
      showAlert({ title: '重置失败', message: '请输入验证码', variant: 'danger' })
      return
    }
    if (newPassword.length < 6) {
      showAlert({ title: '重置失败', message: '新密码至少 6 位', variant: 'danger' })
      return
    }
    if (newPassword !== confirmPassword) {
      showAlert({ title: '重置失败', message: '两次输入的密码不一致', variant: 'danger' })
      return
    }

    setLoading(true)
    try {
      await resetWithCode(email.trim(), code, newPassword)
      setSuccess(true)
    } catch (err) {
      showAlert({ title: '重置失败', message: err instanceof Error ? err.message : '未知错误', variant: 'danger' })
    } finally {
      setLoading(false)
    }
  }

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!oldPassword) {
      showAlert({ title: '重置失败', message: '请输入原密码', variant: 'danger' })
      return
    }
    if (newPassword.length < 6) {
      showAlert({ title: '重置失败', message: '新密码至少 6 位', variant: 'danger' })
      return
    }
    if (newPassword !== confirmPassword) {
      showAlert({ title: '重置失败', message: '两次输入的密码不一致', variant: 'danger' })
      return
    }

    setLoading(true)
    try {
      await resetPassword(email.trim(), oldPassword, newPassword)
      setSuccess(true)
    } catch (err) {
      showAlert({ title: '重置失败', message: err instanceof Error ? err.message : '未知错误', variant: 'danger' })
    } finally {
      setLoading(false)
    }
  }

  if (success) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#f5f5f7', overflow: 'hidden' }}>
        <MacOSTitlebar />
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ width: '100%', maxWidth: 380, padding: '40px 36px', background: '#fff', borderRadius: 16, textAlign: 'center', boxShadow: '0 4px 24px rgba(0,0,0,0.08)' }}>
            <div style={{ width: 56, height: 56, margin: '0 auto 16px', background: '#2d8f3d', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Check className="w-7 h-7" color="#fff" />
            </div>
            <h2 style={{ fontSize: 20, fontWeight: 700, color: '#1d1d1f', marginBottom: 8 }}>密码已重置</h2>
            <p style={{ fontSize: 13, color: '#86868b', marginBottom: 24 }}>请使用新密码登录</p>
            <button onClick={() => navigate('/login')} style={{ width: '100%', padding: '12px', background: '#1e3a5f', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer', fontSize: 15, fontWeight: 600 }}>返回登录</button>
          </div>
        </div>
      </div>
    )
  }

  const inputStyle = { width: '100%', padding: '10px 12px', border: '1.5px solid #d2d2d7', borderRadius: 8, fontSize: 14, boxSizing: 'border-box' as const, outline: 'none' }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#f5f5f7', overflow: 'hidden' }}>
      <MacOSTitlebar />
      <div style={{ display: 'flex', alignItems: 'center', padding: '12px 20px', borderBottom: '1px solid var(--macos-border)', background: '#fafafa', gap: '12px' }}>
        <button onClick={() => navigate('/login')} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 12px', background: 'transparent', border: 'none', cursor: 'pointer', fontSize: '13px', color: '#1e3a5f', borderRadius: '6px' }}
          onMouseEnter={e => e.currentTarget.style.background = 'rgba(0,122,255,0.08)'}
          onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
          <ArrowLeft className="w-4 h-4" />
          返回登录
        </button>
      </div>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ width: '100%', maxWidth: 380, padding: '40px 36px', background: '#fff', borderRadius: 16, boxShadow: '0 4px 24px rgba(0,0,0,0.08)' }}>
          <div style={{ textAlign: 'center', marginBottom: 28 }}>
            <div style={{ width: 56, height: 56, margin: '0 auto 12px', background: '#1e3a5f', borderRadius: 16, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Lock className="w-7 h-7" color="#fff" />
            </div>
            <h1 style={{ fontSize: 20, fontWeight: 700, color: '#1d1d1f', marginBottom: 6 }}>找回密码</h1>
          </div>

          {/* 方式切换 */}
          <div style={{ display: 'flex', marginBottom: 24, background: '#f5f5f7', borderRadius: 8, padding: 3 }}>
            {[
              { key: 'email' as const, icon: Mail, label: '邮箱验证' },
              { key: 'password' as const, icon: Lock, label: '原密码' },
            ].map(tab => (
              <button key={tab.key} onClick={() => setMode(tab.key)} style={{
                flex: 1, padding: '8px 0', background: mode === tab.key ? '#fff' : 'transparent',
                border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: mode === tab.key ? 600 : 400,
                color: mode === tab.key ? '#1e3a5f' : '#86868b', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                boxShadow: mode === tab.key ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
              }}>
                <tab.icon className="w-3 h-3" />{tab.label}
              </button>
            ))}
          </div>

          {/* 邮箱验证方式 */}
          {mode === 'email' && (
            <form onSubmit={handleResetEmail}>
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: '#1d1d1f', marginBottom: 6 }}>邮箱</label>
                <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="your@email.com" required style={inputStyle} />
              </div>
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: '#1d1d1f', marginBottom: 6 }}>验证码</label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <input type="text" value={code} onChange={e => setCode(e.target.value)} placeholder="4 位验证码" maxLength={4} style={{ ...inputStyle, flex: 1 }} />
                  <button type="button" onClick={handleSendCode} disabled={loading || countdown > 0} style={{
                    padding: '0 16px', background: countdown > 0 ? '#f5f5f7' : '#1e3a5f', color: countdown > 0 ? '#86868b' : '#fff',
                    border: 'none', borderRadius: 8, cursor: countdown > 0 ? 'not-allowed' : 'pointer', fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap',
                  }}>
                    {countdown > 0 ? `${countdown}s` : codeSent ? '重新发送' : '发送验证码'}
                  </button>
                </div>
              </div>
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: '#1d1d1f', marginBottom: 6 }}>新密码</label>
                <input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder="至少 6 位" required style={inputStyle} />
              </div>
              <div style={{ marginBottom: 24 }}>
                <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: '#1d1d1f', marginBottom: 6 }}>确认新密码</label>
                <input type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} placeholder="再次输入新密码" required style={inputStyle} />
              </div>
              <button type="submit" disabled={loading} style={{ width: '100%', padding: '12px', background: loading ? '#86868b' : '#1e3a5f', color: '#fff', border: 'none', borderRadius: 8, cursor: loading ? 'not-allowed' : 'pointer', fontSize: 15, fontWeight: 600 }}>
                {loading ? '重置中...' : '重置密码'}
              </button>
            </form>
          )}

          {/* 原密码方式 */}
          {mode === 'password' && (
            <form onSubmit={handleResetPassword}>
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: '#1d1d1f', marginBottom: 6 }}>邮箱</label>
                <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="your@email.com" required style={inputStyle} />
              </div>
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: '#1d1d1f', marginBottom: 6 }}>原密码</label>
                <input type="password" value={oldPassword} onChange={e => setOldPassword(e.target.value)} placeholder="请输入原密码" required style={inputStyle} />
              </div>
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: '#1d1d1f', marginBottom: 6 }}>新密码</label>
                <input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder="至少 6 位" required style={inputStyle} />
              </div>
              <div style={{ marginBottom: 24 }}>
                <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: '#1d1d1f', marginBottom: 6 }}>确认新密码</label>
                <input type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} placeholder="再次输入新密码" required style={inputStyle} />
              </div>
              <button type="submit" disabled={loading} style={{ width: '100%', padding: '12px', background: loading ? '#86868b' : '#1e3a5f', color: '#fff', border: 'none', borderRadius: 8, cursor: loading ? 'not-allowed' : 'pointer', fontSize: 15, fontWeight: 600 }}>
                {loading ? '重置中...' : '重置密码'}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
