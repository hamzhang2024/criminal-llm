// 设置页面 - 配置 MinerU Token 和 LLM API Key

import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Check, Eye, EyeOff, Settings as SettingsIcon, RotateCw, Download } from 'lucide-react'
import { MacOSTitlebar, MacOSCard } from '../components/MacOSLayout'
import { API_BASE, getAuthEmail, clearToken, clearAuthEmail, getAppVersion, checkUpdate, openUrl } from '../api'
import { showAlert, showConfirm } from '../components/MacOSDialog'

interface ConfigStatus {
  mineru_token: boolean
  llm_api_key: boolean
  llm_base_url: boolean
  llm_model: boolean
  evidence_concurrency: number
}

interface ConfigForm {
  mineru_token: string
  llm_api_key: string
  llm_base_url: string
  llm_model: string
  evidence_concurrency: number
}

// 默认 LLM 配置（阿里云百炼 Token Plan）
const DEFAULT_LLM_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
const DEFAULT_LLM_MODEL = 'qwen3.5-plus'

export function SettingsPage() {
  const navigate = useNavigate()
  const [initialConfig, setInitialConfig] = useState<ConfigForm | null>(null)
  const [config, setConfig] = useState<ConfigForm>({
    mineru_token: '',
    llm_api_key: '',
    llm_base_url: DEFAULT_LLM_BASE_URL,
    llm_model: DEFAULT_LLM_MODEL,
    evidence_concurrency: 3,
  })
  const [status, setStatus] = useState<ConfigStatus | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<Record<string, 'ok' | 'fail' | null>>({
    mineru_token: null,
    llm_api_key: null,
  })
  const [showToken, setShowToken] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [appVersion, setAppVersion] = useState('')
  const [checkingUpdate, setCheckingUpdate] = useState(false)

  // 检测是否有未保存的变更
  const hasUnsavedChanges = useCallback((): boolean => {
    if (!initialConfig) return false
    return (
      config.mineru_token !== initialConfig.mineru_token ||
      config.llm_api_key !== initialConfig.llm_api_key ||
      config.llm_base_url !== initialConfig.llm_base_url ||
      config.llm_model !== initialConfig.llm_model ||
      config.evidence_concurrency !== initialConfig.evidence_concurrency
    )
  }, [initialConfig, config])

  useEffect(() => {
    // 加载版本号
    getAppVersion().then(v => setAppVersion(v)).catch(() => {})
  }, [])

  useEffect(() => {
    loadConfigStatus()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 浏览器关闭/刷新时提示
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (hasUnsavedChanges()) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [hasUnsavedChanges])

  const loadConfigStatus = async () => {
    try {
      const res = await fetch(`${API_BASE}/config`)
      const data = await res.json()
      setStatus(data)
      const updates: Partial<ConfigForm> = {}
      if (data.mineru_token_value) updates.mineru_token = data.mineru_token_value
      if (data.llm_api_key_value) updates.llm_api_key = data.llm_api_key_value
      if (data.llm_base_url) updates.llm_base_url = data.llm_base_url
      if (data.llm_model) updates.llm_model = data.llm_model
      if (data.evidence_concurrency) updates.evidence_concurrency = data.evidence_concurrency
      const loaded = { ...config, ...updates }
      setConfig(loaded)
      setInitialConfig(loaded)
    } catch (err) {
      console.error('加载配置状态失败:', err)
    }
  }

  const handleSave = async () => {
    if (!config.mineru_token.trim()) {
      showAlert({ title: '保存失败', message: 'MinerU Token 不能为空', variant: 'danger' })
      return
    }
    if (!config.llm_api_key.trim()) {
      showAlert({ title: '保存失败', message: 'API Key 不能为空', variant: 'danger' })
      return
    }

    setSaving(true)
    try {
      // 先保存主配置
      const res = await fetch(`${API_BASE}/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mineru_token: config.mineru_token.trim(),
          llm_api_key: config.llm_api_key.trim(),
          llm_base_url: config.llm_base_url.trim(),
          llm_model: config.llm_model.trim(),
          evidence_concurrency: config.evidence_concurrency,
        }),
      })
      if (res.ok) {
        await loadConfigStatus()
        showAlert({ title: '保存成功', message: '配置已保存', variant: 'success' })
      } else {
        showAlert({ title: '保存失败', message: '保存配置时出错', variant: 'danger' })
      }
    } catch (err) {
      showAlert({ title: '保存失败', message: err instanceof Error ? err.message : '网络错误', variant: 'danger' })
    } finally {
      setSaving(false)
    }
  }

  /** 导航前检查未保存的变更 */
  const navigateWithSaveCheck = async (path: string) => {
    if (hasUnsavedChanges()) {
      const confirmed = await showConfirm({
        title: '未保存的更改',
        message: '您有未保存的配置更改，是否先保存？',
        confirmText: '保存并离开',
        cancelText: '不保存',
        variant: 'warning',
      })
      if (confirmed) {
        await handleSave()
      }
    }
    navigate(path)
  }

  const testMineruToken = async () => {
    if (!config.mineru_token.trim()) {
      showAlert({ title: '验证失败', message: '请先输入 MinerU Token', variant: 'danger' })
      return
    }
    setTesting('mineru')
    setTestResult(prev => ({ ...prev, mineru_token: null }))
    try {
      // 先检查后端是否可达
      try {
        const healthRes = await fetch(`${API_BASE}/health`)
        if (!healthRes.ok) {
          setTestResult(prev => ({ ...prev, mineru_token: 'fail' }))
          showAlert({ title: '验证失败', message: '后端服务未就绪（健康检查失败），请完全退出应用（Cmd+Q）后重新打开', variant: 'danger' })
          return
        }
      } catch {
        setTestResult(prev => ({ ...prev, mineru_token: 'fail' }))
        showAlert({ title: '验证失败', message: '无法连接后端服务。请：1) 完全退出应用（Cmd+Q） 2) 重新打开应用 3) 如果仍失败，请重新安装', variant: 'danger' })
        return
      }

      const res = await fetch(`${API_BASE}/config/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'mineru', token: config.mineru_token.trim() }),
      })

      const responseText = await res.text()
      console.log('[Mineru Test] status:', res.status, 'body:', responseText)

      if (!res.ok) {
        let errorMsg = `服务器错误 (${res.status})`
        try {
          const errData = JSON.parse(responseText)
          errorMsg = errData.detail || errData.error || errorMsg
        } catch {
          errorMsg = responseText || errorMsg
        }
        setTestResult(prev => ({ ...prev, mineru_token: 'fail' }))
        showAlert({ title: '验证失败', message: `后端返回错误: ${errorMsg}`, variant: 'danger' })
        return
      }

      let data: { success?: boolean; message?: string; error?: string }
      try {
        data = JSON.parse(responseText)
      } catch {
        setTestResult(prev => ({ ...prev, mineru_token: 'fail' }))
        showAlert({ title: '验证失败', message: `后端返回了无效的响应格式: ${responseText.substring(0, 200)}`, variant: 'danger' })
        return
      }

      if (data.success) {
        setTestResult(prev => ({ ...prev, mineru_token: 'ok' }))
        showAlert({ title: '验证成功', message: data.message || 'Token 有效', variant: 'success' })
      } else {
        setTestResult(prev => ({ ...prev, mineru_token: 'fail' }))
        showAlert({ title: '验证失败', message: data.error || 'MinerU Token 无效，请检查是否正确复制', variant: 'danger' })
      }
    } catch (err) {
      console.error('[Mineru Test] exception:', err)
      setTestResult(prev => ({ ...prev, mineru_token: 'fail' }))
      const msg = err instanceof Error ? err.message : String(err)
      let friendlyMsg: string
      if (msg.includes('Failed to fetch') || msg.includes('NetworkError') || msg.includes('fetch')) {
        friendlyMsg = '无法连接后端服务。请：1) 完全退出应用（Cmd+Q） 2) 重新打开应用 3) 如果仍失败，请重新安装'
      } else if (msg.includes('timeout') || msg.includes('Timeout')) {
        friendlyMsg = '请求超时，后端响应太慢。请检查网络连接后重试'
      } else {
        friendlyMsg = `未知错误: ${msg}`
      }
      showAlert({ title: '验证失败', message: friendlyMsg, variant: 'danger' })
    } finally {
      setTesting(null)
    }
  }

  const testLlmKey = async () => {
    if (!config.llm_api_key.trim()) {
      showAlert({ title: '验证失败', message: '请先输入 API Key', variant: 'danger' })
      return
    }
    setTesting('llm')
    setTestResult(prev => ({ ...prev, llm_api_key: null }))
    try {
      // 先检查后端是否可达
      try {
        const healthRes = await fetch(`${API_BASE}/health`)
        if (!healthRes.ok) {
          setTestResult(prev => ({ ...prev, llm_api_key: 'fail' }))
          showAlert({ title: '验证失败', message: '后端服务未就绪（健康检查失败），请完全退出应用（Cmd+Q）后重新打开', variant: 'danger' })
          return
        }
      } catch {
        setTestResult(prev => ({ ...prev, llm_api_key: 'fail' }))
        showAlert({ title: '验证失败', message: '无法连接后端服务。请：1) 完全退出应用（Cmd+Q） 2) 重新打开应用 3) 如果仍失败，请重新安装', variant: 'danger' })
        return
      }

      const res = await fetch(`${API_BASE}/config/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'llm',
          api_key: config.llm_api_key.trim(),
          base_url: config.llm_base_url.trim(),
          model: config.llm_model.trim(),
        }),
      })

      const responseText = await res.text()
      console.log('[LLM Test] status:', res.status, 'body:', responseText)

      if (!res.ok) {
        let errorMsg = `服务器错误 (${res.status})`
        try {
          const errData = JSON.parse(responseText)
          errorMsg = errData.detail || errData.error || errorMsg
        } catch {
          errorMsg = responseText || errorMsg
        }
        setTestResult(prev => ({ ...prev, llm_api_key: 'fail' }))
        showAlert({ title: '验证失败', message: `后端返回错误: ${errorMsg}`, variant: 'danger' })
        return
      }

      let data: { success?: boolean; message?: string; error?: string }
      try {
        data = JSON.parse(responseText)
      } catch {
        setTestResult(prev => ({ ...prev, llm_api_key: 'fail' }))
        showAlert({ title: '验证失败', message: `后端返回了无效的响应格式: ${responseText.substring(0, 200)}`, variant: 'danger' })
        return
      }

      if (data.success) {
        setTestResult(prev => ({ ...prev, llm_api_key: 'ok' }))
        showAlert({ title: '验证成功', message: data.message || 'API Key 有效', variant: 'success' })
      } else {
        setTestResult(prev => ({ ...prev, llm_api_key: 'fail' }))
        showAlert({ title: '验证失败', message: data.error || 'API Key 无效，请检查是否正确复制', variant: 'danger' })
      }
    } catch (err) {
      console.error('[LLM Test] exception:', err)
      setTestResult(prev => ({ ...prev, llm_api_key: 'fail' }))
      const msg = err instanceof Error ? err.message : String(err)
      let friendlyMsg: string
      if (msg.includes('Failed to fetch') || msg.includes('NetworkError') || msg.includes('fetch')) {
        friendlyMsg = '无法连接后端服务。请：1) 完全退出应用（Cmd+Q） 2) 重新打开应用 3) 如果仍失败，请重新安装'
      } else if (msg.includes('timeout') || msg.includes('Timeout')) {
        friendlyMsg = '请求超时，后端响应太慢。请检查网络连接后重试'
      } else {
        friendlyMsg = `未知错误: ${msg}`
      }
      showAlert({ title: '验证失败', message: friendlyMsg, variant: 'danger' })
    } finally {
      setTesting(null)
    }
  }

  const statusIcon = (configured: boolean, testState: 'ok' | 'fail' | null) => {
    if (testState === 'ok') return <Check className="w-4 h-4" color="#2d8f3d" />
    if (testState === 'fail') return <span style={{ fontSize: '11px', color: '#ff3b30' }}>失败</span>
    if (configured) return <Check className="w-4 h-4" color="#86868b" />
    return null
  }

  const handleCheckUpdate = async () => {
    setCheckingUpdate(true)
    try {
      const info = await checkUpdate()
      if (info.has_update) {
        const confirmed = await showConfirm({
          title: '发现新版本',
          message: `当前版本：${info.current_version}\n最新版本：${info.latest_version}\n\n${info.release_notes || '前往下载页面获取更新'}`,
          confirmText: '前往下载',
          variant: 'info',
        })
        if (confirmed) {
          window.open(info.download_url || 'http://118.196.83.43:8000/', '_blank')
        }
      } else {
        showAlert({
          title: '已是最新版本',
          message: `当前版本：${info.current_version}`,
          variant: 'success',
        })
      }
    } catch (err) {
      showAlert({ title: '检查更新失败', message: err instanceof Error ? err.message : '无法连接更新服务器', variant: 'danger' })
    } finally {
      setCheckingUpdate(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#ffffff', overflow: 'hidden' }}>
      <MacOSTitlebar />

      <div style={{
        display: 'flex', alignItems: 'center', padding: '12px 20px',
        borderBottom: '1px solid var(--macos-border)', background: '#fafafa',
        gap: '12px',
      }}>
        <button
          onClick={() => navigateWithSaveCheck('/')}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px',
            padding: '6px 12px', background: 'transparent', border: 'none',
            cursor: 'pointer', fontSize: '13px', color: 'var(--macos-accent)', borderRadius: '6px',
          }}
          onMouseEnter={e => e.currentTarget.style.background = 'rgba(0,122,255,0.08)'}
          onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
        >
          <ArrowLeft className="w-4 h-4" />
          返回首页
        </button>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <SettingsIcon className="w-5 h-5" color="#1d1d1f" />
          <h1 style={{ fontSize: '16px', fontWeight: '600', margin: 0 }}>系统设置</h1>
        </div>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: '30px' }}>
        <div style={{ maxWidth: '560px', margin: '0 auto' }}>
          {/* 账号管理 */}
          <MacOSCard>
            <h2 style={{ fontSize: '15px', fontWeight: '600', marginBottom: '16px', color: '#1d1d1f' }}>
              账号
            </h2>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 500, color: '#1d1d1f' }}>
                  {getAuthEmail() || '未登录'}
                </div>
                <div style={{ fontSize: 12, color: '#86868b', marginTop: 2 }}>
                  远程认证服务器
                </div>
              </div>
              <button
                onClick={async () => {
                  const confirmed = await showConfirm({
                    title: '退出登录',
                    message: '退出后需要重新登录才能使用系统',
                    confirmText: '退出',
                    variant: 'danger',
                  })
                  if (confirmed) {
                    clearToken()
                    clearAuthEmail()
                    navigate('/login')
                  }
                }}
                style={{
                  padding: '8px 16px', fontSize: 13, fontWeight: 500,
                  background: 'transparent', border: '1.5px solid #ff3b30',
                  borderRadius: '8px', cursor: 'pointer', color: '#ff3b30',
                }}
              >
                退出登录
              </button>
            </div>
          </MacOSCard>

          <MacOSCard style={{ marginTop: '16px' }}>
            <h2 style={{ fontSize: '15px', fontWeight: '600', marginBottom: '20px', color: '#1d1d1f' }}>
              MinerU 配置
            </h2>

            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                MinerU Token
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <a
                    href="https://mineru.net"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ fontSize: '11px', color: 'var(--macos-accent)', textDecoration: 'none', cursor: 'pointer' }}
                    onMouseEnter={e => e.currentTarget.style.textDecoration = 'underline'}
                    onMouseLeave={e => e.currentTarget.style.textDecoration = 'none'}
                  >
                    前往申请 →
                  </a>
                  {statusIcon(status?.mineru_token ?? false, testResult.mineru_token)}
                  <button
                    onClick={testMineruToken}
                    disabled={testing === 'mineru'}
                    style={{
                      padding: '4px 10px', fontSize: '11px', borderRadius: '4px',
                      border: '1px solid var(--macos-border)', background: 'transparent',
                      cursor: testing === 'mineru' ? 'not-allowed' : 'pointer',
                      color: testing === 'mineru' ? '#86868b' : 'var(--macos-accent)',
                      opacity: testing === 'mineru' ? 0.6 : 1,
                    }}
                  >
                    {testing === 'mineru' ? '验证中...' : '验证'}
                  </button>
                </div>
              </label>
              <div style={{ position: 'relative' }}>
                <input
                  type={showToken ? 'text' : 'password'}
                  value={config.mineru_token}
                  onChange={e => setConfig(prev => ({ ...prev, mineru_token: e.target.value }))}
                  placeholder="输入 MinerU API Token"
                  style={{
                    width: '100%', padding: '10px 40px 10px 12px',
                    border: '1px solid var(--macos-border)', borderRadius: '8px',
                    fontSize: '14px', boxSizing: 'border-box',
                  }}
                />
                <button
                  onClick={() => setShowToken(!showToken)}
                  style={{
                    position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)',
                    background: 'transparent', border: 'none', cursor: 'pointer', padding: '4px',
                    display: 'flex', alignItems: 'center',
                  }}
                >
                  {showToken ? <EyeOff className="w-4 h-4" color="#86868b" /> : <Eye className="w-4 h-4" color="#86868b" />}
                </button>
              </div>
            </div>
          </MacOSCard>

          <MacOSCard style={{ marginTop: '16px' }}>
            <h2 style={{ fontSize: '15px', fontWeight: '600', color: '#1d1d1f', display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '20px' }}>
              大模型配置
              <span style={{ fontSize: '11px', color: '#2d8f3d', fontWeight: 500, background: 'rgba(52,199,89,0.1)', padding: '2px 8px', borderRadius: '4px' }}>推荐 ollama qwen3.6 35b-a3b</span>
            </h2>

            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                API Key
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  {statusIcon(status?.llm_api_key ?? false, testResult.llm_api_key)}
                  <button
                    onClick={testLlmKey}
                    disabled={testing === 'llm'}
                    style={{
                      padding: '4px 10px', fontSize: '11px', borderRadius: '4px',
                      border: '1px solid var(--macos-border)', background: 'transparent',
                      cursor: testing === 'llm' ? 'not-allowed' : 'pointer',
                      color: testing === 'llm' ? '#86868b' : 'var(--macos-accent)',
                      opacity: testing === 'llm' ? 0.6 : 1,
                    }}
                  >
                    {testing === 'llm' ? '验证中...' : '验证'}
                  </button>
                </div>
              </label>
              <div style={{ position: 'relative' }}>
                <input
                  type={showApiKey ? 'text' : 'password'}
                  value={config.llm_api_key}
                  onChange={e => setConfig(prev => ({ ...prev, llm_api_key: e.target.value }))}
                  placeholder="输入阿里云百炼 API Key"
                  style={{
                    width: '100%', padding: '10px 40px 10px 12px',
                    border: '1px solid var(--macos-border)', borderRadius: '8px',
                    fontSize: '14px', boxSizing: 'border-box',
                  }}
                />
                <button
                  onClick={() => setShowApiKey(!showApiKey)}
                  style={{
                    position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)',
                    background: 'transparent', border: 'none', cursor: 'pointer', padding: '4px',
                    display: 'flex', alignItems: 'center',
                  }}
                >
                  {showApiKey ? <EyeOff className="w-4 h-4" color="#86868b" /> : <Eye className="w-4 h-4" color="#86868b" />}
                </button>
              </div>
            </div>

            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                Base URL
              </label>
              <input
                type="text"
                value={config.llm_base_url}
                onChange={e => setConfig(prev => ({ ...prev, llm_base_url: e.target.value }))}
                placeholder="如 https://dashscope.aliyuncs.com/compatible-mode/v1"
                style={{
                  width: '100%', padding: '10px 12px',
                  border: '1px solid var(--macos-border)', borderRadius: '8px',
                  fontSize: '14px', boxSizing: 'border-box',
                }}
              />
            </div>

            <div style={{ marginBottom: '20px' }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                模型名称
              </label>
              <input
                type="text"
                value={config.llm_model}
                onChange={e => setConfig(prev => ({ ...prev, llm_model: e.target.value }))}
                placeholder="如 qwen-plus, qwen-max, qwen-long"
                style={{
                  width: '100%', padding: '10px 12px',
                  border: '1px solid var(--macos-border)', borderRadius: '8px',
                  fontSize: '14px', boxSizing: 'border-box',
                }}
              />
            </div>

            <div style={{ marginBottom: '20px' }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                证据提取并发数
              </label>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={config.evidence_concurrency}
                  onChange={e => {
                    const v = parseInt(e.target.value, 10)
                    if (!isNaN(v) && v >= 1 && v <= 50) {
                      setConfig(prev => ({ ...prev, evidence_concurrency: v }))
                    }
                  }}
                  style={{
                    width: '80px', padding: '10px 12px',
                    border: '1px solid var(--macos-border)', borderRadius: '8px',
                    fontSize: '14px', boxSizing: 'border-box',
                  }}
                />
                <span style={{ fontSize: '12px', color: '#86868b' }}>
                  范围 1-50，默认 3。过高可能导致 API 限流，建议 1-5
                </span>
              </div>
            </div>
          </MacOSCard>

          <div style={{ display: 'flex', gap: '12px', marginTop: '24px' }}>
            <button
              onClick={handleSave}
              disabled={saving}
              style={{
                flex: 1, padding: '12px', background: 'var(--macos-accent)', color: 'white',
                border: 'none', borderRadius: '8px', cursor: saving ? 'not-allowed' : 'pointer',
                fontSize: '14px', fontWeight: '500', opacity: saving ? 0.6 : 1,
              }}
            >
              {saving ? '保存中...' : '保存配置'}
            </button>
            <button
              onClick={() => navigateWithSaveCheck('/')}
              style={{
                padding: '12px 24px', background: 'var(--macos-bg-secondary)',
                border: 'none', borderRadius: '8px', cursor: 'pointer',
                fontSize: '14px',
              }}
            >
              取消
            </button>
          </div>

          <div style={{
            marginTop: '24px', padding: '12px 16px',
            background: 'rgba(0, 122, 255, 0.05)', borderRadius: '8px',
            fontSize: '12px', color: '#6e6e73', lineHeight: '1.6',
          }}>
            <strong>MinerU Token：</strong>在 <a href="https://mineru.net" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--macos-accent)', textDecoration: 'none' }} onMouseEnter={e => e.currentTarget.style.textDecoration = 'underline'} onMouseLeave={e => e.currentTarget.style.textDecoration = 'none'}>mineru.net</a> 注册后获取
            <br />
            <strong>API Key：</strong>在阿里云百炼平台开通，推荐 <code style={{ fontSize: '11px' }}>qwen-plus</code>。Base URL 和模型名称可在上方输入框中自定义
          </div>

          {/* 版本与更新 */}
          <MacOSCard style={{ marginTop: '16px' }}>
            <h2 style={{ fontSize: '15px', fontWeight: '600', marginBottom: '16px', color: '#1d1d1f' }}>
              关于
            </h2>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 500, color: '#1d1d1f' }}>
                  刑事案卷分析系统
                </div>
                <div style={{ fontSize: 12, color: '#86868b', marginTop: 2 }}>
                  版本 {appVersion || '加载中...'}
                </div>
              </div>
              <button
                onClick={handleCheckUpdate}
                disabled={checkingUpdate}
                style={{
                  display: 'flex', alignItems: 'center', gap: '6px',
                  padding: '8px 16px', fontSize: 13, fontWeight: 500,
                  background: 'transparent', border: '1.5px solid var(--macos-accent)',
                  borderRadius: '8px', cursor: checkingUpdate ? 'not-allowed' : 'pointer',
                  color: 'var(--macos-accent)', opacity: checkingUpdate ? 0.6 : 1,
                }}
              >
                {checkingUpdate ? <RotateCw className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                {checkingUpdate ? '检查中...' : '检查更新'}
              </button>
            </div>
          </MacOSCard>
        </div>
      </div>
    </div>
  )
}
