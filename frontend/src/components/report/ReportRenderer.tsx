import { useMemo, useRef, useEffect } from 'react'
import { marked } from 'marked'
import { EvidenceContrastTable } from './EvidenceContrastTable'
import { ThreeTierCard } from './ThreeTierCard'
import { MermaidRenderer } from '../MermaidRenderer'

marked.setOptions({ async: false })

interface ReportRendererProps {
  markdown: string
  evidenceItems?: Array<{ id: string; mdFile: string; displayName: string }>
  onEvidenceClick?: (mdFile: string) => void
}

// 解析 Markdown 表格数据，返回 header + rows
function parseMarkdownTable(text: string): { headers: string[]; rows: string[][] } | null {
  const lines = text.split('\n').filter(l => l.trim() && !l.includes('---'))
  if (lines.length < 2) return null

  const headers = lines[0].split('|').map(c => c.trim()).filter(c => c)
  const rows: string[][] = []
  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split('|').map(c => c.trim()).filter(c => c)
    if (cells.length === headers.length) {
      rows.push(cells)
    }
  }
  return headers.length > 0 ? { headers, rows } : null
}

// 判断是否是人物关系表格
function isRelationshipTable(html: string): boolean {
  return html.includes('人物') && html.includes('角色') && html.includes('关联人物')
}

// 判断是否是对比表格（包含"是否矛盾"列）
function isContrastTable(html: string): boolean {
  return html.includes('矛盾') && html.includes('证据')
}

// 检测三阶层分析段落
function extractThreeTierSections(markdown: string): Array<{ tier: 'constitutive' | 'illegality' | 'responsibility'; title: string; content: string }> {
  const sections: Array<{ tier: 'constitutive' | 'illegality' | 'responsibility'; title: string; content: string }> = []

  const tierPatterns = [
    { key: 'constitutive' as const, regex: /(?:构成要件|构成要件符合性)/ },
    { key: 'illegality' as const, regex: /(?:违法性)/ },
    { key: 'responsibility' as const, regex: /(?:有责性)/ },
  ]

  // 查找 h2/h3 级别的三阶层标题
  const headingRegex = /^(#{2,3})\s+(.+)$/gm
  const headings = [...markdown.matchAll(headingRegex)]

  for (const headingMatch of headings) {
    const headingText = headingMatch[2]
    const headingStart = headingMatch.index!
    const nextHeadingIdx = headings.findIndex(h => h.index! > headingStart)
    const nextHeadingStart = nextHeadingIdx >= 0 ? headings[nextHeadingIdx].index! : markdown.length

    const sectionContent = markdown.slice(headingStart, nextHeadingStart)

    for (const pattern of tierPatterns) {
      if (pattern.regex.test(headingText)) {
        sections.push({
          tier: pattern.key,
          title: headingText,
          content: sectionContent.replace(/^#{2,3}\s+.+/, '').trim().slice(0, 500),
        })
        break
      }
    }
  }

  return sections
}

export function ReportRenderer({ markdown, evidenceItems, onEvidenceClick }: ReportRendererProps) {
  const contentRef = useRef<HTMLDivElement>(null)
  const onClickRef = useRef(onEvidenceClick)
  // 始终持有最新的 onEvidenceClick
  onClickRef.current = onEvidenceClick

  // 构建证据编号 → 文件映射（从文件名提取编号，如 001_xxx.md → 1）
  const evidenceNumMap = useMemo(() => {
    if (!evidenceItems || evidenceItems.length === 0) return {}
    const map: Record<number, { id: string; mdFile: string }> = {}
    for (const item of evidenceItems) {
      const m = item.mdFile.match(/^(\d+)_/)
      if (m) map[parseInt(m[1])] = item
    }
    return map
  }, [evidenceItems])

  // 点击事件委托（只注册一次，通过 ref 避免闭包过期）
  useEffect(() => {
    const el = contentRef.current
    if (!el) return
    const handler = (e: MouseEvent) => {
      const target = (e.target as HTMLElement).closest('.evidence-link') as HTMLElement | null
      if (!target) return
      e.preventDefault()
      e.stopPropagation()
      const mdFile = target.getAttribute('data-mdfile')
      if (mdFile && onClickRef.current) onClickRef.current(mdFile)
    }
    el.addEventListener('click', handler)
    return () => el.removeEventListener('click', handler)
  }, [])

  // 提取 mermaid 代码块（支持 trailing space、\r\n 等）
  const mermaidRegex = /```mermaid\s*\n([\s\S]*?)```/g
  const mermaidCodes: string[] = []
  let m
  while ((m = mermaidRegex.exec(markdown)) !== null) {
    mermaidCodes.push(m[1].trim())
  }
  if (mermaidCodes.length > 0) {
    console.log('[ReportRenderer] 提取到', mermaidCodes.length, '个 mermaid 块, 首个长度:', mermaidCodes[0].length)
  } else if (markdown.length > 100) {
    console.log('[ReportRenderer] 未提取到 mermaid 块, markdown长度:', markdown.length, '包含```mermaid:', markdown.includes('```mermaid'))
  }

  // 提取三阶层段落
  const threeTierSections = useMemo(() => extractThreeTierSections(markdown), [markdown])

  // 检测对比表格
  const contrastMarkdown = useMemo(() => {
    const tableRegex = /\|[^|]+\|[^|]+\|[^|]+\|[^|]+\|[^|]*\|/g
    const matches = markdown.match(tableRegex)
    if (!matches) return ''
    const lines = markdown.split('\n')
    let currentTable: string[] = []
    const tables: string[] = []
    for (const line of lines) {
      const trimmed = line.trim()
      if (trimmed.startsWith('|') && !trimmed.startsWith('---')) {
        if (trimmed.includes('人物') && trimmed.includes('角色') && trimmed.includes('关联人物')) {
          currentTable = []
          continue
        }
        currentTable.push(trimmed)
      } else if (currentTable.length > 0) {
        if (currentTable.length >= 2) {
          const raw = currentTable.join('\n')
          if (raw.includes('矛盾') || raw.includes('不一致') || raw.includes('印证')) {
            tables.push(raw)
          }
        }
        currentTable = []
      }
    }
    if (currentTable.length >= 2) {
      const raw = currentTable.join('\n')
      if (raw.includes('矛盾') || raw.includes('不一致') || raw.includes('印证')) {
        tables.push(raw)
      }
    }
    return tables.join('\n\n---\n\n')
  }, [markdown])

  // 基础 HTML 渲染
  const baseHtml = useMemo(() => {
    if (!markdown) return ''
    try {
      // 移除 mermaid 代码块，避免重复渲染
      const withoutMermaid = markdown.replace(/```mermaid\s*\n[\s\S]*?```/g, '')
      return marked.parse(withoutMermaid, { async: false }) as string
    } catch {
      return markdown
    }
  }, [markdown])

  // 后处理：将"证据NNN"替换为可点击链接
  const processedHtml = useMemo(() => {
    if (!baseHtml || Object.keys(evidenceNumMap).length === 0) return baseHtml
    let html = baseHtml
    html = html.replace(/证据(\d{1,4})/g, (match, num) => {
      const n = parseInt(num)
      const ev = evidenceNumMap[n]
      if (!ev) return match
      return `<a class="evidence-link" data-mdfile="${ev.mdFile}">${match}</a>`
    })
    return html
  }, [baseHtml, evidenceNumMap])

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {/* 证据链接样式 + 基础 Markdown 内容 */}
      <style>{`
        .evidence-link {
          display: inline-block;
          padding: 1px 6px;
          border-radius: 4px;
          background: rgba(0,122,255,0.08);
          border: 1px solid rgba(0,122,255,0.2);
          color: #007aff;
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          text-decoration: none;
          transition: background 0.15s;
        }
        .evidence-link:hover {
          background: rgba(0,122,255,0.18);
        }
        .report-content table {
          width: 100%;
          border-collapse: separate;
          border-spacing: 0;
          margin: 12px 0 20px;
          border: 1px solid #e5e5ea;
          border-radius: 8px;
          overflow: hidden;
          font-size: 13px;
          box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }
        .report-content thead th {
          background: #1e3a5f;
          color: #fff;
          font-weight: 600;
          font-size: 12px;
          padding: 10px 14px;
          text-align: left;
          border: none;
        }
        .report-content tbody tr:nth-child(even) {
          background: #f8f9fa;
        }
        .report-content tbody tr:hover {
          background: #f0f4f8;
        }
        .report-content td {
          padding: 9px 14px;
          border-top: 1px solid #e5e5ea;
          vertical-align: top;
          line-height: 1.6;
        }
        .report-content td:first-child {
          font-weight: 500;
        }
        .report-content ul, .report-content ol {
          padding-left: 20px;
          margin: 8px 0;
        }
        .report-content li {
          margin: 4px 0;
          line-height: 1.7;
        }
        .report-content blockquote {
          border-left: 3px solid #1e3a5f;
          margin: 12px 0;
          padding: 8px 16px;
          background: rgba(30,58,95,0.03);
          border-radius: 0 6px 6px 0;
          color: #4a4a4a;
        }
        .report-content pre {
          background: #f8f9fa;
          border: 1px solid #e5e5ea;
          border-radius: 6px;
          padding: 12px 16px;
          overflow-x: auto;
          font-size: 12px;
          line-height: 1.6;
        }
        .report-content code {
          background: #f0f1f3;
          padding: 2px 6px;
          border-radius: 4px;
          font-size: 12px;
        }
        .report-content pre code {
          background: none;
          padding: 0;
        }
      `}</style>
      <div
        ref={contentRef}
        className="report-content"
        style={{ fontSize: '13px', lineHeight: '1.85' }}
        dangerouslySetInnerHTML={{ __html: processedHtml }}
      />

      {/* 三阶层分析卡片 */}
      {threeTierSections.length > 0 && (
        <div style={{ marginTop: '20px', marginBottom: '16px' }}>
          <h3 style={{
            fontSize: '15px', fontWeight: 600,
            marginBottom: '12px', color: 'var(--macos-text-primary)',
          }}>
            辩护要点
          </h3>
          {threeTierSections.map((section, i) => (
            <ThreeTierCard
              key={i}
              tier={section.tier}
              title={section.title}
              content={section.content}
            />
          ))}
        </div>
      )}

      {/* 证据对比表格高亮 */}
      {contrastMarkdown && (
        <div style={{ marginTop: '16px', marginBottom: '16px' }}>
          <h3 style={{
            fontSize: '15px', fontWeight: 600,
            marginBottom: '12px', color: 'var(--macos-text-primary)',
          }}>
            证据矛盾分析
          </h3>
          <EvidenceContrastTable markdown={contrastMarkdown} />
        </div>
      )}

      {/* Mermaid 图表 */}
      {mermaidCodes.length > 0 && (
        <div style={{ marginTop: '20px', borderTop: '1px solid #e5e5ea', paddingTop: '16px' }}>
          {mermaidCodes.map((code, i) => (
            <MermaidRenderer key={i} code={code} />
          ))}
        </div>
      )}
    </div>
  )
}
