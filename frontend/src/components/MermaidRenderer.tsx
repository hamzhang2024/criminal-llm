import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'

// 初始化 mermaid
mermaid.initialize({
  startOnLoad: false,
  theme: 'default',
  securityLevel: 'loose',
  fontSize: 18,
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  flowchart: {
    padding: 20,
    nodeSpacing: 40,
    rankSpacing: 80,
    diagramPadding: 10,
  },
})

let nextId = 0

interface MermaidRendererProps {
  code: string
}

export function MermaidRenderer({ code }: MermaidRendererProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [rendered, setRendered] = useState(false)
  const [zoom, setZoom] = useState(1.2)

  useEffect(() => {
    let cancelled = false
    const id = `mermaid-${nextId++}`

    // 预处理：清理 Mermaid 语法错误
    const sanitized = code
      .split('\n')
      .map(line => {
        // 移除 emoji
        const cleaned = line.replace(/[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\u{FE00}-\u{FE0F}]|[\u{1F1E0}-\u{1F1FF}]|[\u{200D}]/gu, '')
        // 修复 subgraph 中的方括号
        return cleaned.replace(/subgraph\s+([^\n\[]+)\[([^\]]+)\]/g, 'subgraph $1-$2')
      })
      .join('\n')

    mermaid
      .render(id, sanitized)
      .then(({ svg }) => {
        if (!cancelled && containerRef.current) {
          // 设置 SVG 尺寸
          const scaled = svg.replace(/<svg /, '<svg style="width: auto; height: auto; display: block;" ')
          containerRef.current.innerHTML = scaled
          setRendered(true)
          setError(null)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          // 尝试二次修复：移除 %% 注释行
          const retry = sanitized
            .split('\n')
            .filter(l => !/^\s*(%%|\/\/)/.test(l))
            .join('\n')
          if (retry !== sanitized) {
            mermaid.render(`${id}-retry`, retry)
              .then(({ svg }) => {
                if (!cancelled && containerRef.current) {
                  const scaled = svg.replace(/<svg /, '<svg style="width: auto; height: auto; display: block;" ')
                  containerRef.current.innerHTML = scaled
                  setRendered(true)
                  setError(null)
                }
              })
              .catch((err2) => {
                if (!cancelled) setError(`Mermaid 渲染失败：${err2.message || '语法错误'}`)
              })
            return
          }
          setError(`Mermaid 渲染失败：${err.message || '语法错误'}`)
          setRendered(false)
        }
      })

    return () => { cancelled = true }
  }, [code])

  if (error) {
    return (
      <div style={{ padding: '12px', background: '#fff3f3', borderRadius: '8px', border: '1px solid #ffc9c9', fontSize: '12px', color: '#c0392b' }}>
        ⚠️ {error}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', padding: '12px 0' }}>
      {/* 缩放控制 */}
      {rendered && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '4px 10px', background: '#f5f5f7', borderRadius: '6px', fontSize: '12px' }}>
          <button onClick={() => setZoom(z => Math.max(z - 0.2, 0.4))}
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: 'var(--macos-accent)', padding: '0 4px' }}>−</button>
          <span style={{ color: 'var(--macos-text-secondary)', minWidth: '40px', textAlign: 'center' }}>{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom(z => Math.min(z + 0.2, 3))}
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: 'var(--macos-accent)', padding: '0 4px' }}>+</button>
          <button onClick={() => setZoom(1.2)}
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '11px', color: 'var(--macos-accent)', marginLeft: '4px' }}>重置</button>
        </div>
      )}
      {/* 可滚动的图容器 */}
      <div style={{ overflow: 'auto', maxWidth: '100%', maxHeight: '70vh', width: '100%' }}>
        <div
          ref={containerRef}
          style={{
            transform: `scale(${zoom})`,
            transformOrigin: 'top center',
            transition: 'transform 0.15s ease',
            minWidth: rendered ? 'fit-content' : undefined,
          }}
        />
      </div>
    </div>
  )
}
