// 文书类型徽标 + 提取完整性状态点（证据列表共用）

interface DocTypeBadgeProps {
  docType?: string  // "evidence" | "non_evidence:封面" | ...
}

export default function DocTypeBadge({ docType }: DocTypeBadgeProps) {
  if (!docType || docType === 'evidence') return null
  const subtype = docType.includes(':') ? docType.split(':')[1] : docType
  return (
    <span style={{
      fontSize: '10px', padding: '1px 6px', borderRadius: '4px',
      background: 'rgba(142,142,147,0.12)', color: '#8e8e93',
      border: '1px solid rgba(142,142,147,0.2)', marginLeft: '6px',
      whiteSpace: 'nowrap',
    }}>
      非证据·{subtype}
    </span>
  )
}

interface CompletenessDotProps {
  status?: 'ok' | 'suspect' | 'failed'
  missingCount?: number
  needsReview?: boolean
}

// 完整性状态点：绿=完整，黄=疑似遗漏，红=校验失败；无报告/无记录时不渲染
export function CompletenessDot({ status, missingCount = 0, needsReview }: CompletenessDotProps) {
  if (!status) return null
  const color = status === 'ok' ? '#34c759' : status === 'suspect' ? '#ff9500' : '#ff3b30'
  const title = status === 'ok'
    ? '提取完整'
    : status === 'suspect'
      ? `疑似遗漏 ${missingCount} 项${needsReview ? '，建议人工复核' : ''}`
      : '完整性校验失败'
  return (
    <span
      title={title}
      style={{
        width: '8px', height: '8px', borderRadius: '50%',
        background: color, flexShrink: 0, display: 'inline-block',
      }}
    />
  )
}
