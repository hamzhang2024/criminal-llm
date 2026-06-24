import { useEffect, useState } from 'react'
import DOMPurify from 'dompurify'

let nextId = 0

// SVG 净化配置：仅保留 SVG 渲染所需的标签与属性，剥离脚本与事件处理器
const SVG_PURIFY_CONFIG = {
  USE_PROFILES: { svg: true, svgFilters: true },
  ADD_TAGS: ['foreignObject'],
  FORBID_TAGS: ['script'],
  FORBID_ATTR: ['onload', 'onclick', 'onerror', 'onmouseover'],
}

interface MermaidRendererProps {
  code: string
}

export function MermaidRenderer({ code }: MermaidRendererProps) {
  const [svgHtml, setSvgHtml] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [zoom, setZoom] = useState(1.2)

  useEffect(() => {
    if (!code || !code.trim()) {
      setError('Mermaid 代码为空')
      setLoading(false)
      return
    }

    let cancelled = false
    const id = `m-${Date.now()}-${nextId++}`

    // 预处理
    const sanitized = code
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      .split('\n')
      .map(line => {
        const cleaned = line.replace(/[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\u{FE00}-\u{FE0F}]|[\u{1F1E0}-\u{1F1FF}]|[\u{200D}]/gu, '')
        return cleaned.replace(/subgraph\s+([^\n[]+)\[([^\]]+)\]/g, 'subgraph $1-$2')
      })
      .join('\n')
      .trim()

    if (!sanitized) {
      setError('Mermaid 代码预处理后为空')
      setLoading(false)
      return
    }

    // 动态导入 mermaid
    import('mermaid').then(async (mermaidModule) => {
      const mermaid = mermaidModule.default

      // 初始化
      mermaid.initialize({
        startOnLoad: false,
        theme: 'default',
        securityLevel: 'strict',
        fontSize: 18,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        flowchart: {
          diagramPadding: 10,
          nodeSpacing: 40,
          rankSpacing: 80,
          curve: 'basis',
        },
        layout: 'dagre',
      })

      // 先解析检查语法
      await mermaid.parse(sanitized)

      if (cancelled) return

      // 渲染
      const result = await mermaid.render(id, sanitized)
      const svg = (result as { svg: string }).svg

      if (cancelled) return

      // 防御性净化：mermaid 输出经 securityLevel:'strict' 已初步消毒，
      // 再用 DOMPurify (SVG profile) 兜底，确保无 <script> 与内联事件处理器
      const safeSvg = DOMPurify.sanitize(svg, SVG_PURIFY_CONFIG) as string
      setSvgHtml(safeSvg)
      setLoading(false)
      setError(null)
    }).catch((err) => {
      if (cancelled) return
      const msg = err instanceof Error ? err.message : '语法错误'
      setError(`Mermaid 渲染失败：${msg}`)
      setLoading(false)
    })

    return () => { cancelled = true }
  }, [code])

  if (loading && !error && !svgHtml) {
    return (
      <div style={{ padding: '12px', textAlign: 'center', fontSize: '12px', color: '#6e6e73' }}>
        正在渲染图表...
      </div>
    )
  }

  if (error && !svgHtml) {
    return (
      <div style={{ padding: '12px', background: '#fff3f3', borderRadius: '8px', border: '1px solid #ffc9c9', fontSize: '12px', color: '#c0392b' }}>
        &#9888;&#65039; {error}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', padding: '12px 0' }}>
      {svgHtml && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '4px 10px', background: '#f5f5f7', borderRadius: '6px', fontSize: '12px' }}>
            <button onClick={() => setZoom(z => Math.max(z - 0.2, 0.4))}
              style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: 'var(--macos-accent)', padding: '0 4px' }}>−</button>
            <span style={{ color: 'var(--macos-text-secondary)', minWidth: '40px', textAlign: 'center' }}>{Math.round(zoom * 100)}%</span>
            <button onClick={() => setZoom(z => Math.min(z + 0.2, 3))}
              style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: 'var(--macos-accent)', padding: '0 4px' }}>+</button>
            <button onClick={() => setZoom(1.2)}
              style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '11px', color: 'var(--macos-accent)', marginLeft: '4px' }}>重置</button>
          </div>
          <div style={{ overflow: 'auto', width: '100%', padding: '8px 0' }}>
            <div
              style={{
                transform: `scale(${zoom})`,
                transformOrigin: 'top left',
                transition: 'transform 0.15s ease',
                minWidth: 'fit-content',
              }}
              dangerouslySetInnerHTML={{ __html: svgHtml }}
            />
          </div>
        </>
      )}
    </div>
  )
}
