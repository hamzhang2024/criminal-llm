// 文件预览覆盖层 + MDPreview

import React, { useState, useEffect } from 'react'
import { API_BASE } from '../../../api'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { PdfViewer } from '../../../components/pdf-viewer/PdfViewer'

marked.setOptions({ async: false, gfm: true, breaks: true })

interface PreviewFile {
  id: string | number
  name: string
  path: string
  caseId?: string
  dir?: string
}

interface PreviewProps {
  file: PreviewFile
  onClose: () => void
  digest?: string
  digestWarning?: boolean
}

export function Preview({ file, onClose, digest, digestWarning }: PreviewProps) {
  const [viewMode, setViewMode] = useState<'digest' | 'full'>(digest ? 'digest' : 'full')
  const isPdf = !file.name.endsWith('.md')
  // 所有 PDF 预览都用 PdfViewer（页码跳转/缩放/缩略图侧栏/页面管理一体），
  // 原生 iframe 在 WKWebView 中没有页面导航控件。
  // PdfViewer 固定从 processed/ 加载，serve-file 找不到会全局兜底（原件仅存在于 original/ 时也能看）；
  // 页面管理的旋转/重转仍是 processed/ 专属（后端保护原件设计）。
  const usePdfViewer = isPdf && !!file.caseId
  // 证据编辑状态（仅 evidence/ 目录可编辑）
  const [editing, setEditing] = useState(false)
  const [editContent, setEditContent] = useState('')
  const [saving, setSaving] = useState(false)
  const canEdit = file.dir === 'evidence'

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      background: 'rgba(0,0,0,0.85)',
      display: 'flex',
      flexDirection: 'column',
      zIndex: 9999
    }}>
      {/* .md-preview 样式已提升到全局 styles/macOS.css，与 StageResultModal 共用 */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        padding: '12px 16px',
        background: 'var(--macos-bg-secondary)',
        borderBottom: '1px solid var(--macos-border)',
        gap: '12px'
      }}>
        <button
          onClick={onClose}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '6px 14px',
            background: 'rgba(0, 122, 255, 0.15)',
            border: 'none',
            borderRadius: '6px',
            cursor: 'pointer',
            fontSize: '13px',
            color: 'var(--macos-accent)',
            fontWeight: '500'
          }}
        >
          ← 返回
        </button>
        {canEdit && !editing && (
          <button
            onClick={() => {
              // 进入编辑模式：加载完整内容到 textarea
              fetch(file.path).then(res => res.text()).then(text => {
                setEditContent(text)
                setEditing(true)
              })
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 14px',
              background: 'rgba(0, 122, 255, 0.15)',
              border: 'none',
              borderRadius: '6px',
              cursor: 'pointer',
              fontSize: '13px',
              color: 'var(--macos-accent)',
              fontWeight: '500'
            }}
          >
            编辑
          </button>
        )}
        {editing && (
          <>
            <button
              onClick={async () => {
                setSaving(true)
                try {
                  const res = await fetch(`${API_BASE}/cases/${file.caseId}/evidence/${encodeURIComponent(file.name)}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ md_file: file.name, content: editContent }),
                  })
                  if (!res.ok) throw new Error(`HTTP ${res.status}`)
                  // 保存成功，退出编辑模式并刷新
                  setEditing(false)
                  window.location.reload()
                } catch (e) {
                  alert(`保存失败：${e instanceof Error ? e.message : String(e)}`)
                } finally {
                  setSaving(false)
                }
              }}
              disabled={saving}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '6px 14px',
                background: saving ? 'rgba(0,0,0,0.1)' : 'rgba(52, 199, 89, 0.15)',
                border: 'none',
                borderRadius: '6px',
                cursor: saving ? 'not-allowed' : 'pointer',
                fontSize: '13px',
                color: saving ? '#999' : 'var(--macos-success)',
                fontWeight: '500'
              }}
            >
              {saving ? '保存中…' : '保存'}
            </button>
            <button
              onClick={() => setEditing(false)}
              disabled={saving}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '6px 14px',
                background: 'rgba(0, 122, 255, 0.15)',
                border: 'none',
                borderRadius: '6px',
                cursor: saving ? 'not-allowed' : 'pointer',
                fontSize: '13px',
                color: 'var(--macos-accent)',
                fontWeight: '500'
              }}
            >
              取消
            </button>
          </>
        )}
        <span style={{ fontSize: '13px', color: 'var(--macos-text-secondary)', flex: 1 }}>
          {file.name}
        </span>
      </div>

      {file.name.endsWith('.md') ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: '#fff' }}>
          {digest && !editing && (
            <div style={{ display: 'flex', gap: '4px', padding: '8px 16px', background: 'var(--macos-bg-secondary)', borderBottom: '1px solid var(--macos-border)' }}>
              {(['digest', 'full'] as const).map(mode => (
                <button key={mode}
                  onClick={() => setViewMode(mode)}
                  aria-pressed={viewMode === mode}
                  style={{
                    padding: '4px 12px', fontSize: '12px', border: 'none', borderRadius: '4px', cursor: 'pointer',
                    background: viewMode === mode ? 'var(--macos-accent)' : 'transparent',
                    color: viewMode === mode ? '#fff' : 'var(--macos-text-secondary)',
                  }}>
                  {mode === 'digest' ? '摘要' : '全文'}
                </button>
              ))}
              {digestWarning && (
                <span role="img" aria-label="摘要保真校验未完全通过，建议核对全文" title="保真校验未完全通过，建议核对全文" style={{ fontSize: '12px', color: '#b7791f', alignSelf: 'center' }}>⚠️</span>
              )}
            </div>
          )}
          {editing ? (
            <textarea
              value={editContent}
              onChange={e => setEditContent(e.target.value)}
              style={{
                flex: 1,
                padding: '24px',
                fontSize: '13px',
                lineHeight: '1.6',
                fontFamily: 'Monaco, Menlo, monospace',
                border: 'none',
                outline: 'none',
                resize: 'none',
                background: '#fff',
                color: '#1d1d1f',
              }}
              placeholder="编辑证据内容..."
            />
          ) : viewMode === 'digest' && digest ? (
            <div
              className="md-preview"
              style={{ flex: 1, overflow: 'auto', padding: '24px', fontSize: '13px', lineHeight: '1.6', color: '#1d1d1f' }}
              dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(digest) as string) }}
            />
          ) : (
            <div style={{ flex: 1, overflow: 'auto' }}>
              <MDPreview url={file.path} />
            </div>
          )}
        </div>
      ) : usePdfViewer ? (
        <div style={{ flex: 1, overflow: 'hidden' }}>
          <PdfViewer
            caseId={file.caseId!}
            pdfFilename={file.name}
            annotations={[]}
            onAddAnnotation={() => {}}
            onUpdateAnnotation={() => {}}
            onDragAnnotation={() => {}}
            onDeleteAnnotation={() => {}}
            annotationMode={false}
          />
        </div>
      ) : (
        <div style={{ flex: 1, overflow: 'hidden', background: '#1a1a1e' }}>
          <iframe
            src={file.path}
            style={{ width: '100%', height: '100%', border: 'none' }}
            title={file.name}
          />
        </div>
      )}
    </div>
  )
}

// MD 预览组件（渲染 Markdown + HTML）
function MDPreview({ url }: { url: string }) {
  const [html, setHtml] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetch(url)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.text()
      })
      .then(text => {
        const m = url.match(/\/cases\/([^/]+)\/serve-file/)
        const caseId = m ? m[1] : null
        const rewritten = caseId
          ? text.replace(
              /(!\[[^\]]*\]\(|<img[^>]+src=["'])\.?\/?([^/"')\s]+_images)\/([^)"'\s]+)/g,
              (_full, prefix, imagesDir, fileName) => {
                const u = `${API_BASE}/cases/${caseId}/serve-file?file_path=${encodeURIComponent(fileName)}&dir=${encodeURIComponent(`md/${imagesDir}`)}`
                return `${prefix}${u}`
              }
            )
          : text
        setHtml(DOMPurify.sanitize(marked.parse(rewritten) as string))
        setLoading(false)
      })
      .catch(err => {
        setError(err.message)
        setLoading(false)
      })
  }, [url])

  if (loading) return <div style={{ color: '#86868b', fontSize: '14px' }}>加载中...</div>
  if (error) return <div style={{ color: '#666666', fontSize: '14px' }}>加载失败：{error}</div>

  return (
    <div style={{
      width: '100%',
      height: '100%',
      overflow: 'auto',
      padding: '24px',
      background: '#fff',
      borderRadius: '8px',
      fontSize: '13px',
      lineHeight: '1.6',
      fontFamily: 'system-ui, -apple-system, sans-serif',
      color: '#1d1d1f',
      margin: 0
    }}>
      <div className="md-preview" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  )
}