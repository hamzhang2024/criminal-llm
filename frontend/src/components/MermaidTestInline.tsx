import { useEffect, useState } from 'react'

// 独立测试组件：直接在页面中显示 mermaid 状态
export function MermaidTestInline() {
  const [status, setStatus] = useState('加载中...')
  const [svgHtml, setSvgHtml] = useState('')

  useEffect(() => {
    const TEST_CODE = `graph LR
    A[测试] --> B[图表]`

    import('mermaid').then(async (m) => {
      const mermaid = m.default
      setStatus('模块加载成功')

      try {
        mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' })
        setStatus('初始化完成，开始渲染')

        const { svg } = await mermaid.render('test-inline', TEST_CODE)
        setSvgHtml(svg)
        setStatus('渲染成功!')
      } catch (e: unknown) {
        setStatus(`渲染失败: ${e instanceof Error ? e.message : '未知错误'}`)
      }
    }).catch((e) => {
      setStatus(`模块加载失败: ${e.message}`)
    })
  }, [])

  return (
    <div style={{ padding: '16px', border: '2px solid #007aff', borderRadius: '8px', margin: '8px', background: '#f8f9fa' }}>
      <h3 style={{ fontSize: '14px', fontWeight: 600 }}>🔍 Mermaid 渲染测试</h3>
      <div style={{ fontSize: '12px', fontFamily: 'monospace', color: '#555', marginBottom: '8px' }}>
        状态: {status}
      </div>
      {svgHtml && (
        <div style={{ border: '1px solid #ddd', borderRadius: '4px', padding: '8px', background: '#fff' }}
             dangerouslySetInnerHTML={{ __html: svgHtml }} />
      )}
    </div>
  )
}
