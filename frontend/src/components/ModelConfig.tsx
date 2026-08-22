// 多模型配置管理组件

import React, { useState, useEffect, useCallback } from 'react'
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
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                <span style={{ fontSize: '13px', fontWeight: '600', color: '#1d1d1f' }}>
                  {p.name || p.id}
                </span>
                {p.is_local && (
                  <span style={{
                    fontSize: '10px',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    background: '#fef3c7',
                    color: '#92400e',
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
                  }}>
                    {PROVIDER_PRESETS.find(pr => pr.key === p.provider)?.label || p.provider}
                  </span>
                )}
              </div>
              <div style={{ fontSize: '12px', color: '#86868b' }}>
                {p.model} · {formatContextLimit(p.context_limit)} 上下文 · {p.max_concurrent} 并发
              </div>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
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

      {/* 编辑弹窗 */}
      {showModal && editing && (
        <ModelEditModal
          profile={editing}
          onSave={saveProfile}
          onCancel={() => {
            setShowModal(false)
            setEditing(null)
          }}
        />
      )}
    </div>
  )
}

// 模型编辑弹窗
function ModelEditModal({ profile, onSave, onCancel }: {
  profile: LlmProfile
  onSave: (p: LlmProfile) => void
  onCancel: () => void
}) {
  const [form, setForm] = useState(profile)
  const [dragging, setDragging] = useState(false)
  const [position, setPosition] = useState({ x: 0, y: 0 })
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 })

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

  // 拖动功能
  const handleMouseDown = (e: React.MouseEvent) => {
    setDragging(true)
    setDragStart({ x: e.clientX - position.x, y: e.clientY - position.y })
  }

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (dragging) {
        setPosition({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y })
      }
    }
    const handleMouseUp = () => setDragging(false)

    if (dragging) {
      document.addEventListener('mousemove', handleMouseMove)
      document.addEventListener('mouseup', handleMouseUp)
      return () => {
        document.removeEventListener('mousemove', handleMouseMove)
        document.removeEventListener('mouseup', handleMouseUp)
      }
    }
  }, [dragging, dragStart])

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
        style={{
          background: 'white',
          borderRadius: '12px',
          padding: '24px',
          width: '480px',
          maxWidth: '90vw',
          maxHeight: '90vh',
          overflow: 'auto',
          transform: `translate(${position.x}px, ${position.y}px)`,
          cursor: dragging ? 'grabbing' : 'default',
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
            onChange={e => {
              const provider = PROVIDER_PRESETS.find(p => p.key === e.target.value)
              if (!provider || provider.key === 'custom') {
                setForm(prev => ({ ...prev, provider: 'custom' }))
                return
              }
              // 选择供应商后，自动填充 base_url 和第一个模型
              const firstModel = provider.models[0]
              setForm(prev => ({
                ...prev,
                provider: provider.key,
                base_url: provider.baseUrl,
                model: firstModel?.name || '',
                context_limit: firstModel?.context || 1000000,
              }))
            }}
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

        {/* 模型选择（同一供应商下可选多个模型） */}
        {form.provider && form.provider !== 'custom' && (() => {
          const provider = PROVIDER_PRESETS.find(p => p.key === form.provider)
          return provider && provider.models.length > 0 ? (
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '6px' }}>模型</label>
              <select
                value={form.model}
                onChange={e => {
                  const model = provider.models.find(m => m.name === e.target.value)
                  if (model) {
                    setForm(prev => ({
                      ...prev,
                      model: model.name,
                      context_limit: model.context,
                    }))
                  }
                }}
                style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--macos-border)', borderRadius: '6px', fontSize: '14px', background: 'white', cursor: 'pointer' }}
              >
                {provider.models.map(m => (
                  <option key={m.name} value={m.name}>{m.label}</option>
                ))}
              </select>
              <div style={{ marginTop: '6px', fontSize: '11px', color: '#86868b' }}>
                同一供应商可添加多个模型（如：Flash 用于证据提取，Pro 用于案卷分析）
              </div>
            </div>
          ) : null
        })()}

        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '6px' }}>名称</label>
          <input
            value={form.name}
            onChange={e => setForm(prev => ({ ...prev, name: e.target.value }))}
            placeholder="如：DeepSeek V4 Flash（证据提取）"
            style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--macos-border)', borderRadius: '6px', fontSize: '14px' }}
          />
        </div>

        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '6px' }}>API 地址</label>
          <input
            value={form.base_url}
            onChange={e => setForm(prev => ({ ...prev, base_url: e.target.value }))}
            placeholder="https://..."
            style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--macos-border)', borderRadius: '6px', fontSize: '14px' }}
          />
        </div>

        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '6px' }}>模型名</label>
          <input
            value={form.model}
            onChange={e => setForm(prev => ({ ...prev, model: e.target.value }))}
            placeholder="如：qwen3.6-27b-uncensored"
            style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--macos-border)', borderRadius: '6px', fontSize: '14px' }}
          />
        </div>

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

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px', marginBottom: '20px' }}>
          <div>
            <label style={{ display: 'block', fontSize: '12px', fontWeight: '500', marginBottom: '4px' }}>上下文</label>
            <input
              type="number"
              value={form.context_limit}
              onChange={e => setForm(prev => ({ ...prev, context_limit: parseInt(e.target.value) || 0 }))}
              style={{ width: '100%', padding: '6px 10px', border: '1px solid var(--macos-border)', borderRadius: '6px', fontSize: '13px' }}
            />
            <div style={{ fontSize: '10px', color: '#86868b', marginTop: '2px' }}>tokens</div>
          </div>
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
            onClick={() => onSave(form)}
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
