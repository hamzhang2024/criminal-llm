import { MacOSTitlebar } from '../components/MacOSLayout'

export function ManualPage() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <MacOSTitlebar showBack onBack={() => history.back()} />
      <iframe
        src="/user-manual.html"
        style={{ flex: 1, border: 'none' }}
        title="使用说明书"
      />
    </div>
  )
}
