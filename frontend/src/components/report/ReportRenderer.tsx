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

// 按 mermaid 代码块拆分 markdown，返回交替的 text/mermaid 片段
function splitByMermaid(markdown: string): Array<{ type: 'text' | 'mermaid'; content: string }> {
  const segments: Array<{ type: 'text' | 'mermaid'; content: string }> = []
  const regex = /```mermaid\s*\n([\s\S]*?)```/g
  let lastIndex = 0
  let match

  while ((match = regex.exec(markdown)) !== null) {
    // 文字片段（可能为空）
    if (match.index > lastIndex) {
      segments.push({ type: 'text', content: markdown.slice(lastIndex, match.index) })
    }
    // mermaid 代码块
    segments.push({ type: 'mermaid', content: match[1].trim() })
    lastIndex = regex.lastIndex
  }

  // 剩余文字
  if (lastIndex < markdown.length) {
    segments.push({ type: 'text', content: markdown.slice(lastIndex) })
  }

  return segments
}

// 检测三阶层分析段落
function extractThreeTierSections(markdown: string): Array<{ tier: 'constitutive' | 'illegality' | 'responsibility'; title: string; content: string }> {
  const sections: Array<{ tier: 'constitutive' | 'illegality' | 'responsibility'; title: string; content: string }> = []

  const tierPatterns = [
    { key: 'constitutive' as const, regex: /(?:构成要件|构成要件符合性)/ },
    { key: 'illegality' as const, regex: /(?:违法性)/ },
    { key: 'responsibility' as const, regex: /(?:有责性)/ },
  ]

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

// 检测对比表格
function extractContrastTable(markdown: string): string {
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
}

export function ReportRenderer({ markdown, evidenceItems, onEvidenceClick }: ReportRendererProps) {
  const contentRef = useRef<HTMLDivElement>(null)
  const onClickRef = useRef(onEvidenceClick)
  onClickRef.current = onEvidenceClick

  // 构建证据编号 → 文件映射
  const evidenceNumMap = useMemo(() => {
    if (!evidenceItems || evidenceItems.length === 0) return {}
    const map: Record<number, { id: string; mdFile: string }> = {}
    for (const item of evidenceItems) {
      const m = item.mdFile.match(/^(\d+)_/)
      if (m) map[parseInt(m[1])] = item
    }
    return map
  }, [evidenceItems])

  // 点击事件委托
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

  // 拆分 markdown 为 text/mermaid 片段（内联渲染）
  const segments = useMemo(() => splitByMermaid(markdown), [markdown])

  // 不含 mermaid 的纯文字部分（用于三阶层和对比表格提取）
  const textOnly = useMemo(() => {
    return segments
      .filter(s => s.type === 'text')
      .map(s => s.content)
      .join('\n\n')
  }, [segments])

  // 三阶层段落
  const threeTierSections = useMemo(() => extractThreeTierSections(textOnly), [textOnly])

  // 对比表格
  const contrastMarkdown = useMemo(() => extractContrastTable(textOnly), [textOnly])

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
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
          table-layout: fixed;
        }
        /* 各列宽度分配 */
        .report-content th:nth-child(1),
        .report-content td:nth-child(1) { width: 70px; }
        .report-content th:nth-child(2),
        .report-content td:nth-child(2) { width: 100px; }
        .report-content th:nth-child(3),
        .report-content td:nth-child(3) { width: 120px; }
        .report-content th:nth-child(4),
        .report-content td:nth-child(4) { width: 56px; }
        .report-content th:nth-child(5),
        .report-content td:nth-child(5) { width: 80px; }
        .report-content th:nth-child(6),
        .report-content td:nth-child(6) { width: auto; }
        .report-content thead th {
          background: #1e3a5f;
          color: #fff;
          font-weight: 600;
          font-size: 12px;
          padding: 10px 12px;
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

      <div ref={contentRef} className="report-content" style={{ display: 'flex', flexDirection: 'column', fontSize: '13px', lineHeight: '1.85' }}>
      {segments.map((segment, i) => {
        if (segment.type === 'mermaid') {
          return (
            <div key={`mermaid-${i}`} style={{ margin: '16px 0' }}>
              <MermaidRenderer code={segment.content} />
            </div>
          )
        }

        if (!segment.content.trim()) return null
        const html = marked.parse(segment.content, { async: false }) as string
        const processed = processEvidenceLinks(html, evidenceNumMap)

        return (
          <div
            key={`text-${i}`}
            dangerouslySetInnerHTML={{ __html: processed }}
          />
        )
      })}

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
      </div>
    </div>
  )
}

// 处理证据链接（独立函数，避免嵌套 useMemo）
function processEvidenceLinks(html: string, evidenceNumMap: Record<number, { id: string; mdFile: string }>): string {
  if (!html || Object.keys(evidenceNumMap).length === 0) return html
  return html.replace(/证据(\d{1,4})/g, (match, num) => {
    const n = parseInt(num)
    const ev = evidenceNumMap[n]
    if (!ev) return match
    return `<a class="evidence-link" data-mdfile="${ev.mdFile}">${match}</a>`
  })
}
