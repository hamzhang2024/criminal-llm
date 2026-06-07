// 文件预览覆盖层 + MDPreview

import React, { useState, useEffect } from 'react'
import { API_BASE } from '../../../api'
import { marked } from 'marked'

marked.setOptions({ async: false, gfm: true, breaks: true })

interface PreviewFile {
  id: string | number
  name: string
  path: string
}

interface PreviewProps {
  file: PreviewFile
  onClose: () => void
}

export function Preview({ file, onClose }: PreviewProps) {
  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      background: 'rgba(0,0,0,0.85)',
      display: 'flex',
      flexDirection: 'column',
      zIndex: 9999
    }}>
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
        <span style={{ fontSize: '13px', color: 'var(--macos-text-secondary)', flex: 1 }}>
          {file.name}
        </span>
      </div>

      {file.name.endsWith('.md') ? (
        <div style={{ flex: 1, overflow: 'auto', background: '#fff', padding: '24px' }}>
          <MDPreview url={file.path} />
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
        setHtml(marked.parse(rewritten) as string)
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
      <style>{`
        .md-preview table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 12px; }
        .md-preview th { background: #f5f5f7; font-weight: 600; padding: 8px 10px; border: 1px solid #e5e5e7; text-align: left; }
        .md-preview td { padding: 6px 10px; border: 1px solid #e5e5e7; }
        .md-preview tr:nth-child(even) { background: #fafafa; }
        .md-preview h1, .md-preview h2, .md-preview h3 { margin: 16px 0 8px; color: #1d1d1f; }
        .md-preview img { max-width: 100%; border-radius: 4px; margin: 8px 0; }
        .md-preview p { margin: 8px 0; }
      `}</style>
      <div className="md-preview" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  )
}