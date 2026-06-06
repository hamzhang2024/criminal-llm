export function MacOSToolbar({ title, titleSlot, children }: { title: string, titleSlot?: React.ReactNode, children?: React.ReactNode }) {
  return (
    <div className="macOS-toolbar">
      <div className="macOS-toolbar-title">
        {title}
        {titleSlot}
      </div>
      <div className="macOS-toolbar-actions">
        {children}
      </div>
    </div>
  )
}
