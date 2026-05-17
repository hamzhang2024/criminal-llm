import { useMemo } from 'react'
import { marked } from 'marked'
import { EvidenceContrastTable } from './EvidenceContrastTable'
import { ThreeTierCard } from './ThreeTierCard'
import { MermaidRenderer } from '../MermaidRenderer'

marked.setOptions({ async: false })

interface ReportRendererProps {
  markdown: string
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

export function ReportRenderer({ markdown }: ReportRendererProps) {
  // 提取 mermaid 代码块
  const mermaidRegex = /```mermaid\n([\s\S]*?)```/g
  const mermaidCodes: string[] = []
  let m
  while ((m = mermaidRegex.exec(markdown)) !== null) {
    mermaidCodes.push(m[1].trim())
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
      const withoutMermaid = markdown.replace(/```mermaid\n[\s\S]*?```/g, '')
      return marked.parse(withoutMermaid, { async: false }) as string
    } catch {
      return markdown
    }
  }, [markdown])

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {/* 基础 Markdown 内容 */}
      <div
        className="report-content"
        style={{ fontSize: '13px', lineHeight: '1.85' }}
        dangerouslySetInnerHTML={{ __html: baseHtml }}
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
      {mermaidCodes.map((code, i) => (
        <MermaidRenderer key={i} code={code} />
      ))}
    </div>
  )
}
