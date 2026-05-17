import { Loader2 } from 'lucide-react'

interface SectionSkeletonProps {
  title: string
  progress?: number
}

export function SectionSkeleton({ title, progress }: SectionSkeletonProps) {
  return (
    <div style={{
      padding: '20px',
      background: 'var(--macos-bg-secondary)',
      borderRadius: '8px',
      border: '1px dashed var(--macos-border)',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: '120px',
    }}>
      <Loader2 className="w-5 h-5 animate-spin" style={{ color: 'var(--macos-accent)', marginBottom: '8px' }} />
      <div style={{ fontSize: '13px', color: 'var(--macos-text-secondary)', fontWeight: 500 }}>
        正在分析：{title}
      </div>
      {progress !== undefined && progress > 0 && (
        <div style={{ width: '100%', maxWidth: '200px', marginTop: '12px' }}>
          <div style={{ height: '3px', background: 'var(--macos-border)', borderRadius: '2px' }}>
            <div style={{
              height: '100%',
              width: `${progress}%`,
              background: 'var(--macos-accent)',
              borderRadius: '2px',
              transition: 'width 0.3s',
            }} />
          </div>
        </div>
      )}
    </div>
  )
}
