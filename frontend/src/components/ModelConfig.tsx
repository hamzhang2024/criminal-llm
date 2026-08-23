// 多模型配置管理组件

import React, { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
import { API_BASE } from '../api'

interface LlmProfile {
  id: string
  name: string
  base_url: string
  model: string
  api_key: string
  context_limit: number
  max_concurrent: number
  read_timeout: number
  is_local: boolean
  provider?: string  // 供应商标识（deepseek/qwen/minimax/glm/doubao/custom）
}

interface ModelConfigProps {
  onProfilesChange?: (profiles: LlmProfile[]) => void
}

// 格式化上下文大小：1000000 → 1M，250000 → 250K
function formatContextLimit(limit: number): string {
  if (limit >= 1000000) return `${(limit / 1000000).toFixed(1)}M`
  if (limit >= 1000) return `${(limit / 1000).toFixed(0)}K`
  return String(limit)
}

// 供应商预设：一个 API Key 可配置多个模型
const PROVIDER_PRESETS = [
  {
    key: 'deepseek',
    label: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com/v1',
    models: [
      { name: 'deepseek-v4-flash', context: 1000000, label: 'V4 Flash（证据提取）' },
      { name: 'deepseek-v4-pro', context: 1000000, label: 'V4 Pro（案卷分析）' },
    ],
    keyHint: 'Key 为 sk- 开头，platform.deepseek.com 创建',
  },
  {
    key: 'qwen',
    label: '通义千问（阿里云百炼）',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    models: [
      { name: 'qwen3.5-plus', context: 1000000, label: 'Qwen3.5 Plus（证据提取）' },
      { name: 'qwen3.5-max', context: 1000000, label: 'Qwen3.5 Max（案卷分析）' },
    ],
    keyHint: 'Key 为 sk- 开头，bailian.console.aliyun.com 创建',
  },
  {
    key: 'minimax',
    label: 'MiniMax',
    baseUrl: 'https://api.minimax.chat/v1',
    models: [
      { name: 'abab6.5-chat', context: 245000, label: 'ABAB 6.5（证据提取）' },
      { name: 'abab6.5s-chat', context: 245000, label: 'ABAB 6.5s（案卷分析）' },
    ],
    keyHint: 'Key 为 Bearer 开头，api.minimax.chat 创建',
  },
  {
    key: 'glm',
    label: '智谱 GLM',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    models: [
      { name: 'glm-4-flash', context: 128000, label: 'GLM-4 Flash（证据提取）' },
      { name: 'glm-4-plus', context: 128000, label: 'GLM-4 Plus（案卷分析）' },
    ],
    keyHint: 'Key 为 . 分隔，open.bigmodel.cn 创建',
  },
  {
    key: 'doubao',
    label: '豆包（火山引擎）',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    models: [
      { name: 'doubao-seed-1.6-flash', context: 256000, label: 'Seed 1.6 Flash（证据提取）' },
      { name: 'doubao-seed-1.6', context: 256000, label: 'Seed 1.6（案卷分析）' },
    ],
    keyHint: 'Key 在火山引擎控制台（console.volcengine.com/ark）创建',
  },
  {
    key: 'ollama',
    label: 'Ollama（本地）',
    baseUrl: 'http://localhost:11434/v1',
    models: [
      { name: 'qwen3.6-27b-uncensored', context: 250000, label: 'Qwen3.6 27B（证据提取）' },
      { name: 'qwen3.8-27b', context: 256000, label: 'Qwen3.8 27B（案卷分析）' },
    ],
    keyHint: '本地部署，无需 API Key；baseUrl 默认为 http://localhost:11434/v1',
  },
  {
    key: 'kimi',
    label: 'Kimi（月之暗面）',
    baseUrl: 'https://api.moonshot.cn/v1',
    models: [
      { name: 'kimi-k2.6', context: 256000, label: 'Kimi K2.6（证据提取）' },
      { name: 'kimi-k3', context: 1000000, label: 'Kimi K3（案卷分析，1M 上下文）' },
    ],
    keyHint: 'Key 为 sk- 开头，platform.moonshot.cn 创建；Kimi K3 支持 1M 上下文',
  },
  {
    key: 'custom',
    label: '自定义',
    baseUrl: '',
    models: [],
    keyHint: '',
  },
]

export function ModelConfig({ onProfilesChange }: ModelConfigProps) {
  const [profiles, setProfiles] = useState<LlmProfile[]>([])
  const [evidenceProfile, setEvidenceProfile] = useState('default')
  const [analysisProfile, setAnalysisProfile] = useState('default')
  const [editing, setEditing] = useState<LlmProfile | null>(null)
  const [showModal, setShowModal] = useState(false)

  // 加载模型列表（只执行一次，避免闪动）
  useEffect(() => {
    loadProfiles()
    loadAssignments()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadProfiles = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/config/llm-profiles`)
      const data = await res.json()
      setProfiles(data.profiles || [])
      onProfilesChange?.(data.profiles || [])
    } catch (e) {
      console.error('加载模型列表失败：', e)
    }
  }, [onProfilesChange])

  const loadAssignments = useCallback(async () => {
    try {
      const [evRes, anRes] = await Promise.all([
        fetch(`${API_BASE}/config/llm-profile/evidence`),
        fetch(`${API_BASE}/config/llm-profile/analysis`),
      ])
      const evData = await evRes.json()
      const anData = await anRes.json()
      setEvidenceProfile(evData.profile?.id || 'default')
      setAnalysisProfile(anData.profile?.id || 'default')
    } catch (e) {
      console.error('加载用途分配失败：', e)
    }
  }, [])

  const saveProfile = async (profile: LlmProfile) => {
    try {
      await fetch(`${API_BASE}/config/llm-profiles`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile }),
      })
      await loadProfiles()
      setShowModal(false)
      setEditing(null)
    } catch (e) {
      alert('保存失败：' + e)
    }
  }

  const deleteProfile = async (id: string) => {
    if (id === 'default') {
      alert('不能删除默认模型')
      return
    }
    if (!confirm('确定删除这个模型配置吗？')) return
    try {
      await fetch(`${API_BASE}/config/llm-profiles/${id}`, { method: 'DELETE' })
      await loadProfiles()
    } catch (e) {
      alert('删除失败：' + e)
    }
  }

  const saveAssignment = async (purpose: 'evidence' | 'analysis', profileId: string) => {
    try {
      await fetch(`${API_BASE}/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [`llm_profile_${purpose}`]: profileId }),
      })
    } catch (e) {
      alert('保存失败：' + e)
    }
  }

  return (
    <div>
      {/* 模型列表 */}
      <div style={{ marginBottom: '24px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h3 style={{ fontSize: '14px', fontWeight: '600', color: '#1d1d1f' }}>模型列表</h3>
          <button
            onClick={() => {
              setEditing({
                id: `profile_${Date.now()}`,
                name: '',
                base_url: '',
                model: '',
                api_key: '',
                context_limit: 1000000,
                max_concurrent: 3,
                read_timeout: 600,
                is_local: false,
              })
              setShowModal(true)
            }}
            style={{
              padding: '6px 12px',
              fontSize: '12px',
              borderRadius: '6px',
              border: '1px solid var(--macos-accent)',
              background: 'var(--macos-accent)',
              color: '#fff',
              cursor: 'pointer',
            }}
          >
            + 添加模型
          </button>
        </div>

        {profiles.map(p => (
          <div
            key={p.id}
            style={{
              padding: '12px 16px',
              marginBottom: '8px',
              background: '#f8fafc',
              borderRadius: '8px',
              border: '1px solid var(--macos-border)',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            {/* 左侧：名称 + 徽标 + 摘要。minWidth:0 让长名称在 flex 内可收缩换行，
                不挤压右侧按钮；徽标 flexShrink:0 禁止被压成竖条 */}
            <div style={{ flex: 1, minWidth: 0, marginRight: '12px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px', flexWrap: 'wrap' }}>
                <span style={{ fontSize: '13px', fontWeight: '600', color: '#1d1d1f', wordBreak: 'break-all' }}>
                  {p.name || p.id}
                </span>
                {p.is_local && (
                  <span style={{
                    fontSize: '10px',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    background: '#fef3c7',
                    color: '#92400e',
                    flexShrink: 0,
                    whiteSpace: 'nowrap',
                  }}>
                    本地
                  </span>
                )}
                {p.provider && p.provider !== 'custom' && (
                  <span style={{
                    fontSize: '10px',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    background: '#dbeafe',
                    color: '#1e40af',
                    flexShrink: 0,
                    whiteSpace: 'nowrap',
                  }}>
                    {/* 括号注释（如「Ollama（本地）」）在徽标里冗余且占宽，只显示主名 */}
                    {(PROVIDER_PRESETS.find(pr => pr.key === p.provider)?.label || p.provider).split('（')[0]}
                  </span>
                )}
              </div>
              <div style={{ fontSize: '12px', color: '#86868b', wordBreak: 'break-all' }}>
                {p.model} · {formatContextLimit(p.context_limit)} 上下文 · {p.max_concurrent} 并发
              </div>
            </div>
            <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
              <button
                onClick={() => {
                  setEditing(p)
                  setShowModal(true)
                }}
                style={{
                  padding: '4px 10px',
                  fontSize: '11px',
                  borderRadius: '4px',
                  border: '1px solid var(--macos-border)',
                  background: 'transparent',
                  cursor: 'pointer',
                  color: 'var(--macos-accent)',
                  whiteSpace: 'nowrap',
                }}
              >
                编辑
              </button>
              {p.id !== 'default' && (
                <button
                  onClick={() => deleteProfile(p.id)}
                  style={{
                    padding: '4px 10px',
                    fontSize: '11px',
                    borderRadius: '4px',
                    border: '1px solid #ef4444',
                    background: 'transparent',
                    cursor: 'pointer',
                    color: '#ef4444',
                    whiteSpace: 'nowrap',
                  }}
                >
                  删除
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* 用途分配 */}
      <div style={{ marginTop: '24px', paddingTop: '24px', borderTop: '1px solid var(--macos-border)' }}>
        <h3 style={{ fontSize: '14px', fontWeight: '600', color: '#1d1d1f', marginBottom: '16px' }}>用途分配</h3>

        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
            证据提取
          </label>
          <select
            value={evidenceProfile}
            onChange={e => {
              setEvidenceProfile(e.target.value)
              saveAssignment('evidence', e.target.value)
            }}
            style={{
              width: '100%',
              padding: '10px 12px',
              border: '1px solid var(--macos-border)',
              borderRadius: '8px',
              fontSize: '14px',
              background: 'white',
              cursor: 'pointer',
            }}
          >
            {profiles.map(p => (
              <option key={p.id} value={p.id}>
                {p.name || p.id} ({p.model})
              </option>
            ))}
          </select>
        </div>

        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
            案卷分析
          </label>
          <select
            value={analysisProfile}
            onChange={e => {
              setAnalysisProfile(e.target.value)
              saveAssignment('analysis', e.target.value)
            }}
            style={{
              width: '100%',
              padding: '10px 12px',
              border: '1px solid var(--macos-border)',
              borderRadius: '8px',
              fontSize: '14px',
              background: 'white',
              cursor: 'pointer',
            }}
          >
            {profiles.map(p => (
              <option key={p.id} value={p.id}>
                {p.name || p.id} ({p.model})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* 编辑弹窗：Portal 到 body（脱离卡片/页面动画的 transform 层叠上下文，
          否则 fixed 遮罩被收容到卡片区域，导致闪烁/跳动，同 OCR lightbox f79c9c6） */}
      {showModal && editing && createPortal(
        <ModelEditModal
          profile={editing}
          onSave={saveProfile}
          onCancel={() => {
            setShowModal(false)
            setEditing(null)
          }}
        />,
        document.body
      )}
    </div>
  )
}

// 模型编辑弹窗（支持同一供应商多模型）
function ModelEditModal({ profile, onSave, onCancel }: {
  profile: LlmProfile
  onSave: (p: LlmProfile) => void
  onCancel: () => void
}) {
  const [form, setForm] = useState(profile)
  const [models, setModels] = useState<Array<{ name: string; context: number; verified?: boolean }>>([
    { name: profile.model, context: profile.context_limit, verified: false }
  ])
  const [testingModel, setTestingModel] = useState<string | null>(null)
  const modalRef = useRef<HTMLDivElement>(null)
  const dragStateRef = useRef({ dragging: false, startX: 0, startY: 0, currentX: 0, currentY: 0 })

  // 本地模型勾选后预填建议值（可修改）
  const handleLocalChange = (isLocal: boolean) => {
    setForm(prev => ({
      ...prev,
      is_local: isLocal,
      context_limit: isLocal ? 250000 : 1000000,
      max_concurrent: isLocal ? 1 : 3,
      read_timeout: isLocal ? 3600 : 600,
    }))
  }

  // 供应商切换后更新模型列表
  const handleProviderChange = (providerKey: string) => {
    const provider = PROVIDER_PRESETS.find(p => p.key === providerKey)
    if (!provider || provider.key === 'custom') {
      setForm(prev => ({ ...prev, provider: 'custom' }))
      return
    }
    setForm(prev => ({
      ...prev,
      provider: provider.key,
      base_url: provider.baseUrl,
    }))
    // 自动填充供应商的模型列表
    setModels(provider.models.map(m => ({ name: m.name, context: m.context, verified: false })))
  }

  // 添加模型
  const addModel = () => {
    setModels(prev => [...prev, { name: '', context: form.context_limit, verified: false }])
  }

  // 删除模型
  const removeModel = (index: number) => {
    setModels(prev => prev.filter((_, i) => i !== index))
  }

  // 验证模型
  const testModel = async (index: number) => {
    const model = models[index]
    if (!model.name) {
      alert('请先填写模型名')
      return
    }
    setTestingModel(model.name)
    try {
      const res = await fetch(`${API_BASE}/config/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'llm',
          api_key: form.api_key,
          base_url: form.base_url,
          model: model.name,
        }),
      })
      const data = await res.json()
      if (data.success) {
        setModels(prev => prev.map((m, i) => i === index ? { ...m, verified: true } : m))
        alert('验证成功')
      } else {
        alert('验证失败：' + (data.error || '未知错误'))
      }
    } catch (e) {
      alert('验证失败：' + e)
    } finally {
      setTestingModel(null)
    }
  }

  // 拖动功能（用 ref 避免重渲染）
  const handleMouseDown = (e: React.MouseEvent) => {
    dragStateRef.current = {
      dragging: true,
      startX: e.clientX - dragStateRef.current.currentX,
      startY: e.clientY - dragStateRef.current.currentY,
      currentX: dragStateRef.current.currentX,
      currentY: dragStateRef.current.currentY,
    }

    const handleMouseMove = (e: MouseEvent) => {
      if (dragStateRef.current.dragging && modalRef.current) {
        const newX = e.clientX - dragStateRef.current.startX
        const newY = e.clientY - dragStateRef.current.startY
        dragStateRef.current.currentX = newX
        dragStateRef.current.currentY = newY
        modalRef.current.style.transform = `translate(${newX}px, ${newY}px)`
      }
    }

    const handleMouseUp = () => {
      dragStateRef.current.dragging = false
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
  }

  // 保存：生成多个 profile（一个模型一个）
  const handleSave = async () => {
    // 过滤掉空模型名
    const validModels = models.filter(m => m.name.trim())
    if (validModels.length === 0) {
      alert('请至少添加一个模型')
      return
    }

    // 为每个模型生成一个 profile
    for (const model of validModels) {
      const profileId = models.length > 1
        ? `${form.provider || 'custom'}_${model.name.replace(/[^a-z0-9]/g, '_')}`
        : form.id

      const profileToSave: LlmProfile = {
        ...form,
        id: profileId,
        name: models.length > 1 ? `${form.name || form.provider} - ${model.name}` : form.name,
        model: model.name,
        context_limit: model.context,
      }
      await onSave(profileToSave)
    }
  }

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      background: 'rgba(0,0,0,0.5)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 10000,
    }}>
      <div
        ref={modalRef}
        style={{
          background: 'white',
          borderRadius: '12px',
          padding: '24px',
          width: '560px',
          maxWidth: '90vw',
          maxHeight: '90vh',
          overflow: 'auto',
        }}
      >
        {/* 可拖动的标题栏 */}
        <div
          onMouseDown={handleMouseDown}
          style={{
            cursor: 'grab',
            marginBottom: '20px',
            paddingBottom: '12px',
            borderBottom: '1px solid var(--macos-border)',
            userSelect: 'none',
          }}
        >
          <h3 style={{ fontSize: '16px', fontWeight: '600', margin: 0 }}>编辑模型</h3>
        </div>

        {/* 供应商预设 */}
        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '6px' }}>供应商</label>
          <select
            value={form.provider || 'custom'}
            onChange={e => handleProviderChange(e.target.value)}
            style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--macos-border)', borderRadius: '6px', fontSize: '14px', background: 'white', cursor: 'pointer' }}
          >
            {PROVIDER_PRESETS.map(p => (
              <option key={p.key} value={p.key}>{p.label}</option>
            ))}
          </select>
          {(() => {
            const provider = PROVIDER_PRESETS.find(p => p.key === form.provider)
            return provider?.keyHint ? (
              <div style={{ marginTop: '6px', fontSize: '11px', color: '#8b6914' }}>{provider.keyHint}</div>
            ) : null
          })()}
        </div>

        {/* API 地址 */}
        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '6px' }}>API 地址</label>
          <input
            value={form.base_url}
            onChange={e => setForm(prev => ({ ...prev, base_url: e.target.value }))}
            placeholder="https://..."
            style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--macos-border)', borderRadius: '6px', fontSize: '14px' }}
          />
        </div>

        {/* API Key */}
        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '6px' }}>API Key</label>
          <input
            type="password"
            value={form.api_key}
            onChange={e => setForm(prev => ({ ...prev, api_key: e.target.value }))}
            placeholder="sk-..."
            style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--macos-border)', borderRadius: '6px', fontSize: '14px' }}
          />
        </div>

        {/* 模型列表（同一供应商可添加多个模型） */}
        <div style={{ marginBottom: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <label style={{ fontSize: '13px', fontWeight: '500' }}>模型列表</label>
            <button
              onClick={addModel}
              style={{
                padding: '4px 10px',
                fontSize: '11px',
                borderRadius: '4px',
                border: '1px solid var(--macos-accent)',
                background: 'transparent',
                cursor: 'pointer',
                color: 'var(--macos-accent)',
              }}
            >
              + 添加模型
            </button>
          </div>

          {models.map((model, index) => (
            <div
              key={index}
              style={{
                display: 'flex',
                gap: '8px',
                marginBottom: '8px',
                padding: '8px 12px',
                background: '#f8fafc',
                borderRadius: '6px',
                border: '1px solid var(--macos-border)',
                alignItems: 'center',
              }}
            >
              <input
                value={model.name}
                onChange={e => setModels(prev => prev.map((m, i) => i === index ? { ...m, name: e.target.value } : m))}
                placeholder="模型名（如：deepseek-v4-flash）"
                style={{ flex: 1, padding: '6px 10px', border: '1px solid var(--macos-border)', borderRadius: '4px', fontSize: '13px' }}
              />
              <input
                type="number"
                value={model.context}
                onChange={e => setModels(prev => prev.map((m, i) => i === index ? { ...m, context: parseInt(e.target.value) || 0 } : m))}
                placeholder="上下文"
                style={{ width: '100px', padding: '6px 10px', border: '1px solid var(--macos-border)', borderRadius: '4px', fontSize: '13px' }}
              />
              <button
                onClick={() => testModel(index)}
                disabled={testingModel === model.name}
                style={{
                  padding: '6px 12px',
                  fontSize: '11px',
                  borderRadius: '4px',
                  border: '1px solid var(--macos-border)',
                  background: model.verified ? '#d1fae5' : 'transparent',
                  cursor: testingModel === model.name ? 'not-allowed' : 'pointer',
                  color: model.verified ? '#065f46' : 'var(--macos-accent)',
                  opacity: testingModel === model.name ? 0.6 : 1,
                }}
              >
                {testingModel === model.name ? '验证中…' : model.verified ? '✓ 已验证' : '验证'}
              </button>
              {models.length > 1 && (
                <button
                  onClick={() => removeModel(index)}
                  style={{
                    padding: '6px 10px',
                    fontSize: '11px',
                    borderRadius: '4px',
                    border: '1px solid #ef4444',
                    background: 'transparent',
                    cursor: 'pointer',
                    color: '#ef4444',
                    whiteSpace: 'nowrap',
                  }}
                >
                  删除
                </button>
              )}
            </div>
          ))}
        </div>

        {/* 本地模型 */}
        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={form.is_local}
              onChange={e => handleLocalChange(e.target.checked)}
              style={{ width: '16px', height: '16px', cursor: 'pointer' }}
            />
            <span style={{ fontSize: '13px', fontWeight: '500' }}>本地模型</span>
          </label>
          <div style={{ fontSize: '11px', color: '#86868b', marginTop: '4px' }}>
            勾选后预填建议值（可修改）
          </div>
        </div>

        {/* 并发数和超时 */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '20px' }}>
          <div>
            <label style={{ display: 'block', fontSize: '12px', fontWeight: '500', marginBottom: '4px' }}>并发数</label>
            <input
              type="number"
              value={form.max_concurrent}
              onChange={e => setForm(prev => ({ ...prev, max_concurrent: parseInt(e.target.value) || 1 }))}
              style={{ width: '100%', padding: '6px 10px', border: '1px solid var(--macos-border)', borderRadius: '6px', fontSize: '13px' }}
            />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '12px', fontWeight: '500', marginBottom: '4px' }}>超时</label>
            <input
              type="number"
              value={form.read_timeout}
              onChange={e => setForm(prev => ({ ...prev, read_timeout: parseInt(e.target.value) || 600 }))}
              style={{ width: '100%', padding: '6px 10px', border: '1px solid var(--macos-border)', borderRadius: '6px', fontSize: '13px' }}
            />
            <div style={{ fontSize: '10px', color: '#86868b', marginTop: '2px' }}>秒</div>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px' }}>
          <button
            onClick={onCancel}
            style={{
              padding: '8px 16px',
              fontSize: '13px',
              borderRadius: '6px',
              border: '1px solid var(--macos-border)',
              background: 'transparent',
              cursor: 'pointer',
            }}
          >
            取消
          </button>
          <button
            onClick={handleSave}
            style={{
              padding: '8px 16px',
              fontSize: '13px',
              borderRadius: '6px',
              border: 'none',
              background: 'var(--macos-accent)',
              color: '#fff',
              cursor: 'pointer',
            }}
          >
            保存
          </button>
        </div>
      </div>
    </div>
  )
}
