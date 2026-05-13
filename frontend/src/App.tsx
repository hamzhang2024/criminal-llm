import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { HomePage } from './pages/HomePage'
import { CaseDetailPage } from './pages/CaseDetailPage'
import { AnalyzePage } from './pages/AnalyzePage'
import { ProcessPage } from './pages/ProcessPage'
import { ConvertPage } from './pages/ConvertPage'
import { ReportPage } from './pages/ReportPage'
import { SettingsPage } from './pages/SettingsPage'
import { ManualPage } from './pages/ManualPage'
import { useDialogProvider } from './components/MacOSDialog'

function DialogWrapper() {
  const DialogComponent = useDialogProvider()
  return DialogComponent
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* 首页 - 案件管理 */}
        <Route path="/" element={<HomePage />} />

        {/* 设置页面 */}
        <Route path="/settings" element={<SettingsPage />} />

        {/* 案件详情 - 完整工作流 */}
        <Route path="/case/:caseId" element={<CaseDetailPage />} />

        {/* 独立页面（保留用于向后兼容） */}
        <Route path="/process" element={<ProcessPage />} />
        <Route path="/convert" element={<ConvertPage />} />
        <Route path="/analyze" element={<AnalyzePage />} />

        {/* 案卷分析报告页面 */}
        <Route path="/case/:caseId/report" element={<ReportPage />} />

        {/* 使用说明书 */}
        <Route path="/manual" element={<ManualPage />} />
      </Routes>
      <DialogWrapper />
    </BrowserRouter>
  )
}

export default App
