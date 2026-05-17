interface ThreeTierCardProps {
  tier: 'constitutive' | 'illegality' | 'responsibility'
  title: string
  content: string
}

const TIER_COLORS = {
  constitutive: { bg: 'rgba(30,58,95,0.06)', border: 'rgba(30,58,95,0.2)', accent: '#1e3a5f', label: '构成要件符合性' },
  illegality: { bg: 'rgba(52,199,89,0.06)', border: 'rgba(52,199,89,0.2)', accent: '#34c759', label: '违法性' },
  responsibility: { bg: 'rgba(255,149,0,0.06)', border: 'rgba(255,149,0,0.2)', accent: '#ff9500', label: '有责性' },
}

export function ThreeTierCard({ tier, title, content }: ThreeTierCardProps) {
  const colors = TIER_COLORS[tier]

  return (
    <div style={{
      padding: '16px',
      background: colors.bg,
      borderRadius: '10px',
      border: `1px solid ${colors.border}`,
      marginBottom: '16px',
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        marginBottom: '12px',
      }}>
        <div style={{
          width: '8px',
          height: '8px',
          borderRadius: '50%',
          background: colors.accent,
        }} />
        <h4 style={{
          fontSize: '14px',
          fontWeight: '600',
          color: colors.accent,
          margin: 0,
        }}>
          {title || colors.label}
        </h4>
      </div>
      <div style={{
        fontSize: '13px',
        lineHeight: '1.7',
        color: 'var(--macos-text-primary)',
        whiteSpace: 'pre-wrap',
      }}>
        {content}
      </div>
    </div>
  )
}
