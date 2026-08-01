// 设置页面 - 配置 MinerU Token 和 LLM API Key

import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Check, Eye, EyeOff, Settings as SettingsIcon, RotateCw, Download, FolderOpen, FileText } from 'lucide-react'
import { MacOSTitlebar, MacOSCard, MacOSInput, MacOSButton } from '../components/MacOSLayout'
import { API_BASE, getAuthEmail, clearToken, clearAuthEmail, getAppVersion, checkUpdate, openUrl } from '../api'
import { showAlert, showConfirm } from '../components/MacOSDialog'

// ═══════════════════════════════════════════════════════════
// 数据目录管理组件
// ═══════════════════════════════════════════════════════════

interface DataDirInfo {
  current_dir: string
  config_file: string
  exists: boolean
  is_default: boolean
}

function DataDirCard() {
  const [dataDir, setDataDir] = useState<DataDirInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [migrating, setMigrating] = useState(false)

  useEffect(() => {
    fetch(`${API_BASE}/data-dir`)
      .then(res => res.json())
      .then(setDataDir)
      .catch(err => showAlert({ title: '获取数据目录失败', message: err.message, variant: 'danger' }))
      .finally(() => setLoading(false))
  }, [])

  const handleMigrate = async () => {
    const confirmed = await showConfirm({
      title: '迁移数据',
      message: '将旧版本数据迁移到新目录？\n\n此操作会复制旧数据到新位置，不会删除原文件。',
      confirmText: '迁移',
      variant: 'info',
    })
    if (!confirmed) return

    setMigrating(true)
    try {
      const res = await fetch(`${API_BASE}/data-dir/migrate`, { method: 'POST' })
      const data = await res.json()
      if (data.success) {
        showAlert({ title: '迁移成功', message: data.message, variant: 'success' })
        // 刷新数据目录信息
        const newInfo = await fetch(`${API_BASE}/data-dir`).then(r => r.json())
        setDataDir(newInfo)
      } else {
        showAlert({ title: '迁移失败', message: data.error, variant: 'danger' })
      }
    } catch (err) {
      showAlert({ title: '迁移失败', message: err instanceof Error ? err.message : '未知错误', variant: 'danger' })
    } finally {
      setMigrating(false)
    }
  }

  const handleBrowse = async () => {
    // 调用后端打开目录选择对话框
    const res = await fetch(`${API_BASE}/process/browse-directory`)
    const data = await res.json()
    if (data.path) {
      const confirmed = await showConfirm({
        title: '更改数据目录',
        message: `新目录：${data.path}\n\n更改后需要重启应用生效。\n现有数据需要手动迁移或重新导入。`,
        confirmText: '确认更改',
        variant: 'warning',
      })
      if (!confirmed) return

      try {
        const setRes = await fetch(`${API_BASE}/data-dir`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ new_dir: data.path }),
        })
        const setData = await setRes.json()
        if (setData.success) {
          showAlert({ title: '已更改', message: setData.message, variant: 'success' })
        } else {
          showAlert({ title: '更改失败', message: setData.error, variant: 'danger' })
        }
      } catch (err) {
        showAlert({ title: '更改失败', message: err instanceof Error ? err.message : '未知错误', variant: 'danger' })
      }
    }
  }

  if (loading) {
    return (
      <MacOSCard style={{ marginTop: '16px' }}>
        <div style={{ color: '#86868b', fontSize: '14px' }}>加载中...</div>
      </MacOSCard>
    )
  }

  return (
    <MacOSCard style={{ marginTop: '16px' }}>
      <h2 style={{ fontSize: '15px', fontWeight: '600', marginBottom: '16px', color: '#1d1d1f' }}>
        数据存储位置
      </h2>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
        <div>
          <div style={{ fontSize: 13, color: '#1d1d1f', fontWeight: 500 }}>
            {dataDir?.current_dir || '未设置'}
          </div>
          <div style={{ fontSize: 12, color: '#86868b', marginTop: 4 }}>
            {dataDir?.is_default ? '默认位置（用户文档目录）' : '自定义位置'}
          </div>
        </div>
        <button
          onClick={handleBrowse}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px',
            padding: '8px 12px', fontSize: 13, fontWeight: 500,
            background: 'var(--macos-accent-light)', border: 'none',
            borderRadius: '8px', cursor: 'pointer', color: 'var(--macos-accent)',
          }}
        >
          <FolderOpen className="w-4 h-4" />
          更改位置
        </button>
      </div>

      {/* Windows 打包版显示迁移按钮 */}
      {dataDir && !dataDir.is_default && (
        <button
          onClick={handleMigrate}
          disabled={migrating}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px',
            padding: '8px 12px', fontSize: 13,
            background: 'transparent', border: '1.5px solid var(--macos-border)',
            borderRadius: '8px', cursor: migrating ? 'wait' : 'pointer', color: '#86868b',
          }}
        >
          {migrating ? '迁移中...' : '从旧版本迁移数据'}
        </button>
      )}
    </MacOSCard>
  )
}

// ═══════════════════════════════════════════════════════════

interface ConfigStatus {
  mineru_token: boolean
  llm_api_key: boolean
  llm_base_url: boolean
  llm_model: boolean
  evidence_concurrency: number
  pdf_engine: 'mineru' | 'paddleocr'
  paddleocr_token: boolean
  paddleocr_quota: {
    date: string
    used_pages: number
    total_limit: number
    remaining_pages: number
    exceeded: boolean
  } | null
  model_context_limit: number
  model_window_detected: number | null
}

interface ConfigForm {
  mineru_token: string
  llm_api_key: string
  llm_base_url: string
  llm_model: string
  evidence_concurrency: number
  pdf_engine: 'mineru' | 'paddleocr'
  paddleocr_token: string
  pdf_convert_concurrency: number
  mineru_model_version: string
  model_context_limit: number
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
    pdf_engine: 'mineru',
    paddleocr_token: '',
    pdf_convert_concurrency: 10,
    mineru_model_version: 'vlm',
    model_context_limit: 250,
  })
  const [status, setStatus] = useState<ConfigStatus | null>(null)
  const [modelWindowDetected, setModelWindowDetected] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<Record<string, 'ok' | 'fail' | null>>({
    mineru_token: null,
    llm_api_key: null,
    paddleocr_token: null,
  })
  const [showToken, setShowToken] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [showPaddleocrToken, setShowPaddleocrToken] = useState(false)
  // 案例检索 API 配置
  const [caseApiKey, setCaseApiKey] = useState('')
  const [caseServiceUrl, setCaseServiceUrl] = useState('')
  const [initialCaseApiKey, setInitialCaseApiKey] = useState('')
  const [initialCaseServiceUrl, setInitialCaseServiceUrl] = useState('')
  const [caseKeyStatus, setCaseKeyStatus] = useState<'idle' | 'checking' | 'ok' | 'fail'>('idle')
  const [caseKeyInfo, setCaseKeyInfo] = useState('')
  const [showCaseKey, setShowCaseKey] = useState(false)
  const [appVersion, setAppVersion] = useState('')
  const [checkingUpdate, setCheckingUpdate] = useState(false)

  // 检测是否有未保存的变更
  const hasUnsavedChanges = useCallback((): boolean => {
    if (!initialConfig) return false
    return (
      config.pdf_engine !== initialConfig.pdf_engine ||
      config.mineru_token !== initialConfig.mineru_token ||
      config.paddleocr_token !== initialConfig.paddleocr_token ||
      config.llm_api_key !== initialConfig.llm_api_key ||
      config.llm_base_url !== initialConfig.llm_base_url ||
      config.llm_model !== initialConfig.llm_model ||
      config.pdf_convert_concurrency !== initialConfig.pdf_convert_concurrency ||
      config.mineru_model_version !== initialConfig.mineru_model_version ||
      config.evidence_concurrency !== initialConfig.evidence_concurrency ||
      config.model_context_limit !== initialConfig.model_context_limit ||
      caseApiKey !== initialCaseApiKey ||
      caseServiceUrl !== initialCaseServiceUrl
    )
  }, [initialConfig, config, caseApiKey, initialCaseApiKey, caseServiceUrl, initialCaseServiceUrl])

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
      if (data.pdf_engine) updates.pdf_engine = data.pdf_engine
      if (data.mineru_token_value) updates.mineru_token = data.mineru_token_value
      if (data.paddleocr_token_value) updates.paddleocr_token = data.paddleocr_token_value
      if (data.llm_api_key_value) updates.llm_api_key = data.llm_api_key_value
      if (data.llm_base_url) updates.llm_base_url = data.llm_base_url
      if (data.llm_model) updates.llm_model = data.llm_model
      if (data.evidence_concurrency) updates.evidence_concurrency = data.evidence_concurrency
      if (data.pdf_convert_concurrency) updates.pdf_convert_concurrency = data.pdf_convert_concurrency
      if (data.mineru_model_version) updates.mineru_model_version = data.mineru_model_version
      if (data.model_context_limit) updates.model_context_limit = Math.round(data.model_context_limit / 1000)
      setModelWindowDetected(data.model_window_detected ?? null)
      const loaded = { ...config, ...updates }
      setConfig(loaded)
      setInitialConfig(loaded)
      // 案例检索 API 配置（后端 GET /api/config 返回 case_api_key_value / case_service_url）
      const loadedCaseApiKey = data.case_api_key_value || ''
      const loadedCaseServiceUrl = data.case_service_url || ''
      setCaseApiKey(loadedCaseApiKey)
      setCaseServiceUrl(loadedCaseServiceUrl)
      setInitialCaseApiKey(loadedCaseApiKey)
      setInitialCaseServiceUrl(loadedCaseServiceUrl)
    } catch (err) {
      console.error('加载配置状态失败:', err)
    }
  }

  const handleSave = async () => {
    if (!config.llm_api_key.trim()) {
      showAlert({ title: '保存失败', message: 'API Key 不能为空', variant: 'danger' })
      return
    }

    // 根据引擎选择验证对应 token
    if (config.pdf_engine === 'mineru' && !config.mineru_token.trim()) {
      showAlert({ title: '保存失败', message: 'MinerU Token 不能为空（当前选择 MinerU 引擎）', variant: 'danger' })
      return
    }
    if (config.pdf_engine === 'paddleocr' && !config.paddleocr_token.trim()) {
      showAlert({ title: '保存失败', message: 'PaddleOCR Token 不能为空（当前选择 PaddleOCR 引擎）', variant: 'danger' })
      return
    }

    setSaving(true)
    try {
      // 先保存主配置
      const res = await fetch(`${API_BASE}/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pdf_engine: config.pdf_engine,
          mineru_token: config.mineru_token.trim(),
          paddleocr_token: config.paddleocr_token.trim(),
          llm_api_key: config.llm_api_key.trim(),
          llm_base_url: config.llm_base_url.trim(),
          llm_model: config.llm_model.trim(),
          evidence_concurrency: config.evidence_concurrency,
          model_context_limit: config.model_context_limit * 1000,
          case_api_key: caseApiKey.trim(),
          case_service_url: caseServiceUrl.trim(),
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

  const testPaddleocrToken = async () => {
    if (!config.paddleocr_token.trim()) {
      showAlert({ title: '验证失败', message: '请先输入 Token', variant: 'danger' })
      return
    }
    setTesting('paddleocr')
    setTestResult(prev => ({ ...prev, paddleocr_token: null }))
    try {
      try {
        const healthRes = await fetch(`${API_BASE}/health`)
        if (!healthRes.ok) {
          setTestResult(prev => ({ ...prev, paddleocr_token: 'fail' }))
          showAlert({ title: '验证失败', message: '后端服务未就绪，请完全退出应用后重新打开', variant: 'danger' })
          return
        }
      } catch {
        setTestResult(prev => ({ ...prev, paddleocr_token: 'fail' }))
        showAlert({ title: '验证失败', message: '无法连接后端服务，请完全退出应用后重新打开', variant: 'danger' })
        return
      }

      const res = await fetch(`${API_BASE}/config/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'paddleocr',
          token: config.paddleocr_token.trim(),
        }),
      })

      const responseText = await res.text()
      if (!res.ok) {
        let errorMsg = `服务器错误 (${res.status})`
        try { errorMsg = JSON.parse(responseText).detail || responseText } catch { /* ignore */ }
        setTestResult(prev => ({ ...prev, paddleocr_token: 'fail' }))
        showAlert({ title: '验证失败', message: errorMsg, variant: 'danger' })
        return
      }

      const data: { success?: boolean; message?: string; error?: string } = JSON.parse(responseText)
      if (data.success) {
        setTestResult(prev => ({ ...prev, paddleocr_token: 'ok' }))
        showAlert({ title: '验证成功', message: data.message || '连接成功', variant: 'success' })
      } else {
        setTestResult(prev => ({ ...prev, paddleocr_token: 'fail' }))
        showAlert({ title: '验证失败', message: data.error || '连接失败', variant: 'danger' })
      }
    } catch (err) {
      setTestResult(prev => ({ ...prev, paddleocr_token: 'fail' }))
      const msg = err instanceof Error ? err.message : String(err)
      showAlert({ title: '验证失败', message: msg, variant: 'danger' })
    } finally {
      setTesting(null)
    }
  }

  /** 验证案例检索 API Key（调本地后端 /api/case-search/validate → 云端校验） */
  const handleValidateCaseKey = async () => {
    if (!caseApiKey.trim()) {
      showAlert({ title: '验证失败', message: '请先输入 API Key', variant: 'danger' })
      return
    }
    setCaseKeyStatus('checking')
    setCaseKeyInfo('')
    try {
      const { validateCaseKey } = await import('../api/caseSearch')
      const result = await validateCaseKey(caseApiKey.trim(), caseServiceUrl.trim())
      if (result.valid) {
        setCaseKeyStatus('ok')
        setCaseKeyInfo(`有效 · 今日已用 ${result.used_today ?? 0}/${result.quota_per_day ?? '-'}，请记得保存`)
      } else {
        setCaseKeyStatus('fail')
        setCaseKeyInfo('Key 无效或已吊销')
      }
    } catch (e: unknown) {
      setCaseKeyStatus('fail')
      setCaseKeyInfo(e instanceof Error ? e.message : '验证失败')
    }
  }

  const statusIcon = (configured: boolean, testState: 'ok' | 'fail' | null) => {
    if (testState === 'ok') return <Check className="w-4 h-4" color="#8b6914" />
    if (testState === 'fail') return <span style={{ fontSize: '11px', color: '#666666' }}>失败</span>
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
          onMouseEnter={e => e.currentTarget.style.background = 'var(--macos-accent-light)'}
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
                  background: 'transparent', border: '1.5px solid #666666',
                  borderRadius: '8px', cursor: 'pointer', color: '#666666',
                }}
              >
                退出登录
              </button>
            </div>
          </MacOSCard>

          {/* 数据目录 */}
          <DataDirCard />

          <MacOSCard style={{ marginTop: '16px' }}>
            <h2 style={{ fontSize: '15px', fontWeight: '600', marginBottom: '20px', color: '#1d1d1f' }}>
              PDF 转 MD 引擎
            </h2>

            {/* 引擎选择器 */}
            <div style={{ marginBottom: '20px' }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                选择引擎
              </label>
              <div style={{ display: 'flex', gap: '12px' }}>
                {(['mineru', 'paddleocr'] as const).map(engine => (
                  <label
                    key={engine}
                    style={{
                      display: 'flex', alignItems: 'center', gap: '8px',
                      padding: '10px 16px', borderRadius: '8px',
                      border: `1.5px solid ${config.pdf_engine === engine ? 'var(--macos-accent)' : 'var(--macos-border)'}`,
                      background: config.pdf_engine === engine ? 'var(--macos-accent-surface)' : 'transparent',
                      cursor: 'pointer', flex: 1,
                    }}
                  >
                    <input
                      type="radio"
                      name="pdf_engine"
                      checked={config.pdf_engine === engine}
                      onChange={() => setConfig(prev => ({ ...prev, pdf_engine: engine }))}
                      style={{ accentColor: 'var(--macos-accent)' }}
                    />
                    <div>
                      <div style={{ fontSize: '14px', fontWeight: 500, color: '#1d1d1f' }}>
                        {engine === 'mineru' ? 'MinerU' : 'PaddleOCR-VL'}
                      </div>
                      <div style={{ fontSize: '11px', color: '#86868b' }}>
                        {engine === 'mineru' ? '高质量异步，200MB 限制' : '整文件提交，每日限额 20000 页'}
                      </div>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {/* MinerU 配置（仅在选择 MinerU 时显示） */}
            {config.pdf_engine === 'mineru' && (
              <div>
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

                {/* 上传并发数 */}
                <div style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                    上传并发数
                    <span style={{ fontSize: '11px', color: '#86868b', fontWeight: '400', marginLeft: '8px' }}>
                      同时上传文件数（1-50，过大易触发限频）
                    </span>
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={config.pdf_convert_concurrency ?? 10}
                    onChange={e => setConfig(prev => ({ ...prev, pdf_convert_concurrency: Math.max(1, Math.min(50, Number(e.target.value) || 10)) }))}
                    style={{ width: '120px', padding: '10px 12px', border: '1px solid var(--macos-border)', borderRadius: '8px', fontSize: '14px', boxSizing: 'border-box' }}
                  />
                </div>

                {/* 模型版本 */}
                <div style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                    模型版本
                    <span style={{ fontSize: '11px', color: '#86868b', fontWeight: '400', marginLeft: '8px' }}>
                      vlm 高精度 / pipeline 快速 / MinerU-HTML 仅 HTML
                    </span>
                  </label>
                  <select
                    value={config.mineru_model_version ?? 'vlm'}
                    onChange={e => setConfig(prev => ({ ...prev, mineru_model_version: e.target.value }))}
                    style={{ padding: '10px 12px', border: '1px solid var(--macos-border)', borderRadius: '8px', fontSize: '14px', background: 'white', cursor: 'pointer' }}
                  >
                    <option value="vlm">vlm（推荐，扫描件/手写）</option>
                    <option value="pipeline">pipeline（快速，电子版）</option>
                    <option value="MinerU-HTML">MinerU-HTML（仅 HTML）</option>
                  </select>
                </div>
              </div>
            )}

            {/* PaddleOCR 配置（仅在选择 PaddleOCR 时显示） */}
            {config.pdf_engine === 'paddleocr' && (
              <div>
                <div style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                    Token
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <a
                        href="https://aistudio.baidu.com/paddleocr"
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ fontSize: '11px', color: 'var(--macos-accent)', textDecoration: 'none', cursor: 'pointer' }}
                        onMouseEnter={e => e.currentTarget.style.textDecoration = 'underline'}
                        onMouseLeave={e => e.currentTarget.style.textDecoration = 'none'}
                      >
                        获取 Token →
                      </a>
                      {statusIcon(status?.paddleocr_token ?? false, testResult.paddleocr_token)}
                      <button
                        onClick={testPaddleocrToken}
                        disabled={testing === 'paddleocr'}
                        style={{
                          padding: '4px 10px', fontSize: '11px', borderRadius: '4px',
                          border: '1px solid var(--macos-border)', background: 'transparent',
                          cursor: testing === 'paddleocr' ? 'not-allowed' : 'pointer',
                          color: testing === 'paddleocr' ? '#86868b' : 'var(--macos-accent)',
                          opacity: testing === 'paddleocr' ? 0.6 : 1,
                        }}
                      >
                        {testing === 'paddleocr' ? '验证中...' : '验证'}
                      </button>
                    </div>
                  </label>
                  <div style={{ position: 'relative' }}>
                    <input
                      type={showPaddleocrToken ? 'text' : 'password'}
                      value={config.paddleocr_token}
                      onChange={e => setConfig(prev => ({ ...prev, paddleocr_token: e.target.value }))}
                      placeholder="输入 PaddleOCR API Token"
                      style={{
                        width: '100%', padding: '10px 40px 10px 12px',
                        border: '1px solid var(--macos-border)', borderRadius: '8px',
                        fontSize: '14px', boxSizing: 'border-box',
                      }}
                    />
                    <button
                      onClick={() => setShowPaddleocrToken(!showPaddleocrToken)}
                      style={{
                        position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)',
                        background: 'transparent', border: 'none', cursor: 'pointer', padding: '4px',
                        display: 'flex', alignItems: 'center',
                      }}
                    >
                      {showPaddleocrToken ? <EyeOff className="w-4 h-4" color="#86868b" /> : <Eye className="w-4 h-4" color="#86868b" />}
                    </button>
                  </div>
                </div>

                {/* 每日配额状态条 */}
                {status?.paddleocr_quota && (
                  <div style={{
                    padding: '10px 12px', borderRadius: '8px',
                    background: status.paddleocr_quota.exceeded ? 'rgba(255,59,48,0.08)' : 'var(--macos-accent-surface)',
                    border: `1px solid ${status.paddleocr_quota.exceeded ? 'rgba(255,59,48,0.2)' : 'var(--macos-accent-border)'}`,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                      <span style={{ fontSize: '12px', fontWeight: 500, color: '#1d1d1f' }}>
                        今日配额
                      </span>
                      <span style={{
                        fontSize: '12px', fontWeight: 600,
                        color: status.paddleocr_quota.exceeded ? '#666666' : '#1d1d1f',
                      }}>
                        {status.paddleocr_quota.exceeded
                          ? '已用完'
                          : `剩余 ${status.paddleocr_quota.remaining_pages} 页`}
                      </span>
                    </div>
                    <div style={{
                      height: '4px', borderRadius: '2px', overflow: 'hidden',
                      background: 'rgba(0,0,0,0.06)',
                    }}>
                      <div style={{
                        height: '100%',
                        width: `${(status.paddleocr_quota.used_pages / status.paddleocr_quota.total_limit) * 100}%`,
                        background: status.paddleocr_quota.exceeded ? '#666666' : 'var(--macos-accent)',
                        borderRadius: '2px',
                        transition: 'width 0.3s ease',
                      }} />
                    </div>
                    <div style={{ fontSize: '11px', color: '#86868b', marginTop: '4px' }}>
                      今日已转换 {status.paddleocr_quota.used_pages} / {status.paddleocr_quota.total_limit} 页
                      {status.paddleocr_quota.exceeded && ' — 超额部分将自动回退到 MinerU'}
                    </div>
                  </div>
                )}
              </div>
            )}
          </MacOSCard>

          <MacOSCard style={{ marginTop: '16px' }}>
            <h2 style={{ fontSize: '15px', fontWeight: '600', color: '#1d1d1f', display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '20px' }}>
              大模型配置
              <span style={{ fontSize: '11px', color: '#8b6914', fontWeight: 500, background: 'rgba(139,105,20,0.1)', padding: '2px 8px', borderRadius: '4px' }}>推荐 ollama qwen3.6 35b-a3b</span>
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
              {modelWindowDetected && (
                <div style={{ marginTop: '6px', fontSize: '11px', color: '#86868b' }}>
                  检测到 {config.llm_model} 窗口为 {modelWindowDetected.toLocaleString()} tokens（可在下方覆盖）
                </div>
              )}
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

            <div style={{ marginBottom: '20px' }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                模型上下文大小
              </label>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                {[
                  { k: 250, label: '250K', desc: '本地/通用模型' },
                  { k: 512, label: '512K', desc: '中等上下文' },
                  { k: 1000, label: '100万', desc: 'DeepSeek V4 等' },
                ].map(opt => (
                  <label
                    key={opt.k}
                    style={{
                      display: 'flex', alignItems: 'center', gap: '6px',
                      padding: '8px 12px', borderRadius: '8px',
                      border: `1.5px solid ${config.model_context_limit === opt.k ? 'var(--macos-accent)' : 'var(--macos-border)'}`,
                      background: config.model_context_limit === opt.k ? 'var(--macos-accent-surface)' : 'transparent',
                      cursor: 'pointer', flex: '1 1 0', minWidth: '120px',
                    }}
                  >
                    <input
                      type="radio"
                      name="model_context"
                      checked={config.model_context_limit === opt.k}
                      onChange={() => setConfig(prev => ({ ...prev, model_context_limit: opt.k }))}
                      style={{ accentColor: 'var(--macos-accent)' }}
                    />
                    <div>
                      <div style={{ fontSize: '13px', fontWeight: 500, color: '#1d1d1f' }}>{opt.label} tokens</div>
                      <div style={{ fontSize: '11px', color: '#86868b' }}>{opt.desc}</div>
                    </div>
                  </label>
                ))}
              </div>
              <div style={{ marginTop: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '12px', color: '#86868b' }}>自定义（K tokens）：</span>
                <input
                  type="number"
                  min={50}
                  max={10000}
                  value={config.model_context_limit}
                  onChange={e => setConfig(prev => ({ ...prev, model_context_limit: Math.max(50, parseInt(e.target.value) || 250) }))}
                  style={{ width: '100px', padding: '6px 10px', border: '1px solid var(--macos-border)', borderRadius: '8px', fontSize: '13px' }}
                />
              </div>
              <div style={{ marginTop: '6px', fontSize: '11px', color: '#86868b' }}>
                影响证据提取的分块策略。值越大，单次处理的文本越多，提取越完整。
              </div>
            </div>
          </MacOSCard>

          {/* 案例检索 API */}
          <MacOSCard style={{ marginTop: '16px' }}>
            <h2 style={{ fontSize: '15px', fontWeight: '600', marginBottom: '8px', color: '#1d1d1f' }}>
              案例检索 API
            </h2>
            <div style={{ fontSize: '12px', color: '#86868b', lineHeight: '1.6', marginBottom: '16px' }}>
              用于报告页检索《刑事审判参考》案例。
              <a
                href="https://casefix.cn/api-access"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: 'var(--macos-accent)', textDecoration: 'none', cursor: 'pointer' }}
                onMouseEnter={e => e.currentTarget.style.textDecoration = 'underline'}
                onMouseLeave={e => e.currentTarget.style.textDecoration = 'none'}
              >
                前往申请 →
              </a>
            </div>

            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                API Key
                <button
                  onClick={handleValidateCaseKey}
                  disabled={caseKeyStatus === 'checking'}
                  style={{
                    padding: '4px 10px', fontSize: '11px', borderRadius: '4px',
                    border: '1px solid var(--macos-border)', background: 'transparent',
                    cursor: caseKeyStatus === 'checking' ? 'not-allowed' : 'pointer',
                    color: caseKeyStatus === 'checking' ? '#86868b' : 'var(--macos-accent)',
                    opacity: caseKeyStatus === 'checking' ? 0.6 : 1,
                  }}
                >
                  {caseKeyStatus === 'checking' ? '验证中...' : '验证'}
                </button>
              </label>
              <div style={{ position: 'relative' }}>
                <input
                  type={showCaseKey ? 'text' : 'password'}
                  value={caseApiKey}
                  onChange={e => { setCaseApiKey(e.target.value); setCaseKeyStatus('idle'); setCaseKeyInfo('') }}
                  placeholder="cca_xxxxxxxx"
                  style={{
                    width: '100%', padding: '10px 40px 10px 12px',
                    border: '1px solid var(--macos-border)', borderRadius: '8px',
                    fontSize: '14px', boxSizing: 'border-box',
                  }}
                />
                <button
                  onClick={() => setShowCaseKey(!showCaseKey)}
                  style={{
                    position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)',
                    background: 'transparent', border: 'none', cursor: 'pointer', padding: '4px',
                    display: 'flex', alignItems: 'center',
                  }}
                >
                  {showCaseKey ? <EyeOff className="w-4 h-4" color="#86868b" /> : <Eye className="w-4 h-4" color="#86868b" />}
                </button>
              </div>
              {caseKeyStatus !== 'idle' && caseKeyInfo && (
                <div style={{
                  marginTop: '6px', fontSize: '12px',
                  color: caseKeyStatus === 'ok' ? '#34c759' : '#ff3b30',
                }}>
                  {caseKeyInfo}
                </div>
              )}
            </div>

            <div>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                服务地址
              </label>
              <input
                type="text"
                value={caseServiceUrl}
                onChange={e => { setCaseServiceUrl(e.target.value); setCaseKeyStatus('idle'); setCaseKeyInfo('') }}
                placeholder="服务地址（留空用默认）"
                style={{
                  width: '100%', padding: '10px 12px',
                  border: '1px solid var(--macos-border)', borderRadius: '8px',
                  fontSize: '14px', boxSizing: 'border-box',
                }}
              />
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
            background: 'var(--macos-accent-surface)', borderRadius: '8px',
            fontSize: '12px', color: '#6e6e73', lineHeight: '1.6',
          }}>
            <strong>PDF 转 MD 引擎：</strong>
            {config.pdf_engine === 'mineru'
              ? 'MinerU — 在 ' + 'mineru.net' + ' 注册后获取 Token，高质量异步转换'
              : 'PaddleOCR-VL — 在 ' + 'aistudio.baidu.com/paddleocr' + ' 获取 Token，同步逐页转换'}
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
