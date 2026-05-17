import { useMemo } from 'react'

interface EvidenceContrastTableProps {
  markdown: string
}

/**
 * 解析 Markdown 中的证据对比表格，自动标红矛盾行、标绿印证行。
 * 触发条件：包含"是否矛盾"列的表格。
 */
export function EvidenceContrastTable({ markdown }: EvidenceContrastTableProps) {
  const tables = useMemo(() => {
    const results: string[] = []
    // Find all markdown tables
    const tableRegex = /\|[^|]+\|[^|]+\|[^|]+\|[^|]+\|[^|]*\|/g
    const matches = markdown.match(tableRegex)
    if (!matches) return []

    // Group table rows by looking for consecutive table lines
    const lines = markdown.split('\n')
    let currentTable: string[] = []
    for (const line of lines) {
      const trimmed = line.trim()
      if (trimmed.startsWith('|') && trimmed.includes('|') && !trimmed.startsWith('---')) {
        if (trimmed.includes('人物') && trimmed.includes('角色') && trimmed.includes('关联人物')) {
          // Skip relationship tables (handled by RelationshipGraphCard)
          currentTable = []
          continue
        }
        currentTable.push(trimmed)
      } else if (currentTable.length > 0) {
        if (currentTable.length >= 2) {
          results.push(currentTable.join('\n'))
        }
        currentTable = []
      }
    }
    if (currentTable.length >= 2) {
      results.push(currentTable.join('\n'))
    }
    return results
  }, [markdown])

  if (tables.length === 0) {
    return null
  }

  return (
    <div style={{ marginBottom: '16px' }}>
      {tables.map((table, i) => (
        <div key={i} style={{ overflowX: 'auto', marginBottom: '12px' }}>
          <table style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: '13px',
          }}>
            {(() => {
              const rows = table.split('\n').filter(r => r.trim() && !r.includes('---'))
              if (rows.length === 0) return null
              const headers = rows[0].split('|').map(c => c.trim()).filter(c => c)
              const dataRows = rows.slice(1)

              return (
                <>
                  <thead>
                    <tr>
                      {headers.map((h, j) => (
                        <th key={j} style={{
                          padding: '8px 12px',
                          background: 'var(--macos-bg-secondary)',
                          borderBottom: '2px solid var(--macos-border)',
                          textAlign: 'left',
                          fontWeight: 600,
                          fontSize: '12px',
                          color: 'var(--macos-text-secondary)',
                        }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {dataRows.map((row, ri) => {
                      const cells = row.split('|').map(c => c.trim()).filter(c => c)
                      // 检查是否包含"矛盾"关键词
                      const hasContradiction = cells.some(c =>
                        c.includes('矛盾') || c.includes('不一致') || c.includes('冲突')
                      )
                      // 检查是否包含"印证"关键词
                      const hasCorroboration = cells.some(c =>
                        c.includes('印证') || c.includes('一致') || c.includes('相符')
                      )
                      const rowBg = hasContradiction ? 'rgba(255,59,48,0.06)'
                        : hasCorroboration ? 'rgba(52,199,89,0.06)'
                        : 'transparent'
                      const borderColor = hasContradiction ? 'rgba(255,59,48,0.2)'
                        : hasCorroboration ? 'rgba(52,199,89,0.2)'
                        : 'transparent'

                      return (
                        <tr key={ri} style={{
                          background: rowBg,
                          borderBottom: `1px solid ${borderColor}`,
                        }}>
                          {cells.map((cell, ci) => (
                            <td key={ci} style={{
                              padding: '8px 12px',
                              color: hasContradiction ? '#ff3b30' : hasCorroboration ? '#34c759' : 'var(--macos-text-primary)',
                            }}>
                              {cell}
                            </td>
                          ))}
                        </tr>
                      )
                    })}
                  </tbody>
                </>
              )
            })()}
          </table>
        </div>
      ))}
    </div>
  )
}
