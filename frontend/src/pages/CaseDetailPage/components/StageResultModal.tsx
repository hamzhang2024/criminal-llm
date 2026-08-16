// 阶段分析结果弹窗：Markdown 渲染（表格/标题/列表样式齐全，替代原纯文本 showAlert）

import { useEffect, useState } from 'react'
import { X, Loader2 } from 'lucide-react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { getStageMarkdown } from '../../../api'

marked.setOptions({ async: false, gfm: true, breaks: true })

interface StageResultModalProps {
  caseId: string
  stageNum: number
  stageName: string
  onClose: () => void
}

export function StageResultModal({ caseId, stageNum, stageName, onClose }: StageResultModalProps) {
  const [html, setHtml] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 拉取阶段 Markdown 并渲染为 HTML（DOMPurify 消毒防 XSS）
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getStageMarkdown(caseId, stageNum)
      .then(r => {
        if (cancelled) return
        const content: string = r?.content || '（无内容）'
        setHtml(DOMPurify.sanitize(marked.parse(content) as string))
        setLoading(false)
      })
      .catch(e => { if (!cancelled) { setError(e instanceof Error ? e.message : '加载失败'); setLoading(false) } })
    return () => { cancelled = true }
  }, [caseId, stageNum])

  // ESC 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9998 }}
      onClick={onClose}>
      <div style={{ width: 'min(860px, 92vw)', height: 'min(78vh, 900px)', background: '#fff', borderRadius: 12, display: 'flex', flexDirection: 'column', overflow: 'hidden', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}
        onClick={e => e.stopPropagation()}>
        {/* 标题栏 */}
        <div style={{ display: 'flex', alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid var(--macos-border)', background: 'var(--macos-bg-secondary)' }}>
          <span style={{ flex: 1, fontSize: 14, fontWeight: 600 }}>{stageName} · 分析结果</span>
          <button onClick={onClose} aria-label="关闭"
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--macos-text-tertiary)', padding: 4 }}>
            <X className="w-4 h-4" />
          </button>
        </div>
        {/* 内容区（.md-preview 全局样式定义在 styles/macOS.css） */}
        <div className="md-preview" style={{ flex: 1, overflow: 'auto', padding: '20px 24px', fontSize: 13, lineHeight: 1.7, color: '#1d1d1f' }}>
          {loading && <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#86868b' }}><Loader2 className="w-4 h-4 animate-spin" /> 加载中...</div>}
          {error && <div style={{ color: '#c62828' }}>{error}</div>}
          {!loading && !error && <div dangerouslySetInnerHTML={{ __html: html }} />}
        </div>
      </div>
    </div>
  )
}
