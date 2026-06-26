import { useState, useCallback, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { PlusCircle, FolderOpen, Trash2, Calendar, FileText, ArrowRight, Settings, AlertCircle, ChevronRight, ChevronDown, Loader2 } from 'lucide-react'
import { MacOSTitlebar, MacOSToolbar, MacOSButton, MacOSCard, MacOSEmptyState, MacOSInput, PageLayout, InlineDialog } from '../components/MacOSLayout'
import { api, getAuthEmail, waitForBackend } from '../api'
import type { Case } from '../api/types'
import { showConfirm, showAlert } from '../components/MacOSDialog'

export function HomePage() {
  const navigate = useNavigate()
  const [cases, setCases] = useState<Case[]>([])
  const [pendingFolders, setPendingFolders] = useState<Array<{path: string, name: string, pdf_count: number, size_mb: number}>>([])
  const [trashItems, setTrashItems] = useState<Array<{id: string, name: string, defendant: string, deleted_at: string, days_left: number, size_mb: number}>>([])
  const [showTrash, setShowTrash] = useState(false)
  const [showNewCase, setShowNewCase] = useState(false)
  const [showImportDialog, setShowImportDialog] = useState(false)
  const [selectedFolder, setSelectedFolder] = useState<{path: string, name: string} | null>(null)
  const [newCaseName, setNewCaseName] = useState('')
  const [defendant, setDefendant] = useState('')
  const [configMissing, setConfigMissing] = useState<string[]>([])
  const [backendReady, setBackendReady] = useState(false)
  const [loading, setLoading] = useState(true)

  // 等待后端就绪
  useEffect(() => {
    const waitBackend = async () => {
      setLoading(true)
      const ready = await waitForBackend(90000, 1000)
      setBackendReady(ready)
      if (!ready) {
        showAlert({
          title: '后端启动超时',
          message: '后端服务启动超时（已等待90秒）。请尝试：\n1. 重新启动应用\n2. 检查 Windows 防火墙是否拦截\n3. 检查 8080 端口是否被占用\n\n如问题持续，请将安装目录下的 backend_stderr.log 文件发送给技术支持。',
          variant: 'danger',
        })
      }
      setLoading(false)
    }
    waitBackend()
  }, [])

  // 检查配置状态
  useEffect(() => {
    if (!backendReady) return
    const checkConfig = async () => {
      try {
        const res = await fetch('http://127.0.0.1:8080/api/config')
        const data = await res.json()
        const missing: string[] = []
        if (!data.mineru_token) missing.push('MinerU Token')
        if (!data.llm_api_key) missing.push('LLM API Key')
        setConfigMissing(missing)
      } catch {
        setConfigMissing([])
      }
    }
    checkConfig()
  }, [backendReady])

  // 加载数据
  const loadData = useCallback(async () => {
    if (!backendReady) return
    try {
      const owner = getAuthEmail() || undefined
      const [casesData, pendingData, trashData] = await Promise.all([
        api.listCases(owner),
        api.getPendingCases(),
        api.getTrash()
      ])
      setCases(casesData.map((c: Case) => ({
        id: c.id,
        name: c.name,
        defendant: c.defendant,
        created_at: c.created_at,
        file_count: c.file_count || 0,
        status: c.status || 'new',
        owner: c.owner || ''
      })))
      setPendingFolders(pendingData)
      setTrashItems(trashData)
    } catch (err) {
      console.error('加载案件失败:', err)
    }
  }, [backendReady])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handleCreateCase = useCallback(async () => {
    if (!newCaseName.trim() || !defendant.trim()) return
    if (configMissing.length > 0) {
      showAlert({ title: '需要先完成配置', message: `请先前往设置页完成以下配置：\n${configMissing.join('、')}`, variant: 'danger' })
      navigate('/settings')
      return
    }
    try {
      const newCase = await api.createCase(newCaseName, defendant, getAuthEmail() || undefined)
      setShowNewCase(false)
      setNewCaseName('')
      setDefendant('')
      loadData()
      navigate('/case/' + newCase.id)
    } catch (err) {
      console.error('创建案件失败:', err)
      const msg = err instanceof Error ? err.message : '未知错误'
      showAlert({ title: '创建失败', message: '创建案件失败：' + msg, variant: 'danger' })
    }
  }, [newCaseName, defendant, navigate, loadData, configMissing])

  const handleImportFolder = useCallback(async () => {
    if (!selectedFolder || !newCaseName.trim() || !defendant.trim()) return
    if (configMissing.length > 0) {
      showAlert({ title: '需要先完成配置', message: `请先前往设置页完成以下配置：\n${configMissing.join('、')}`, variant: 'danger' })
      navigate('/settings')
      return
    }
    try {
      const newCase = await api.importCase(selectedFolder.path, newCaseName, defendant)
      setShowImportDialog(false)
      setSelectedFolder(null)
      setNewCaseName('')
      setDefendant('')
      loadData()
      navigate('/case/' + newCase.id)
    } catch (err) {
      console.error('导入案件失败:', err)
      showAlert({ title: '导入失败', message: '导入案件失败', variant: 'warning' })
    }
  }, [selectedFolder, newCaseName, defendant, navigate, loadData, configMissing])

  const handleDeleteCase = useCallback(async (id: string) => {
    if (!await showConfirm({ title: '删除案件', message: '确定要删除此案件吗？\n案件将移入回收站，5 天后彻底删除。', confirmText: '删除', variant: 'danger' })) return
    try {
      const result = await api.deleteCase(id)
      if (result.success) {
        setCases(prev => prev.filter(c => c.id !== id))
        if (result.message) showAlert({ title: '提示', message: result.message, variant: 'success' })
        loadData()
      } else {
        showAlert({ title: '删除失败', message: '删除失败：' + result.error, variant: 'danger' })
      }
    } catch (err) {
      console.error('删除案件失败:', err)
      showAlert({ title: '删除失败', message: '删除案件失败', variant: 'danger' })
    }
  }, [loadData])

  const handleRestoreCase = useCallback(async (id: string) => {
    try {
      const result = await api.restoreCase(id)
      if (result.success) {
        loadData()
      } else {
        showAlert({ title: '恢复失败', message: '恢复失败：' + result.error, variant: 'warning' })
      }
    } catch (err) {
      console.error('恢复案件失败:', err)
    }
  }, [loadData])

  const handlePermanentDelete = useCallback(async (id: string) => {
    if (!await showConfirm({ title: '彻底删除', message: '确定要彻底删除此案件吗？\n此操作不可恢复！', confirmText: '彻底删除', variant: 'danger' })) return
    try {
      await api.permanentDeleteCase(id)
      loadData()
    } catch (err) {
      console.error('彻底删除案件失败:', err)
      const msg = err instanceof Error ? err.message : '未知错误'
      showAlert({ title: '删除失败', message: '彻底删除失败：' + msg, variant: 'danger' })
    }
  }, [loadData])

  // 用户头像
  const userEmail = getAuthEmail() || '?'
  const userInitial = userEmail[0].toUpperCase()

  return (
    <PageLayout>
      <MacOSTitlebar />
      <MacOSToolbar title="刑事案卷分析系统">
        <MacOSButton variant="primary" icon={PlusCircle} onClick={() => setShowNewCase(true)} disabled={loading}>
          新建案件
        </MacOSButton>
        <button
          onClick={() => navigate('/settings')}
          disabled={loading}
          className="flex-row gap-sm"
          style={{
            padding: '8px 14px',
            background: configMissing.length > 0 ? 'rgba(255, 149, 0, 0.1)' : 'transparent',
            border: '1px solid var(--macos-border)',
            borderRadius: 8,
            cursor: loading ? 'wait' : 'pointer',
            fontSize: 13,
            color: configMissing.length > 0 ? '#ff9500' : '#86868b',
            opacity: loading ? 0.5 : 1,
          }}
        >
          <Settings className="w-4 h-4" />
          设置
          {configMissing.length > 0 && (
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#ff9500' }} />
          )}
        </button>
        <div
          onClick={() => navigate('/settings')}
          className="macOS-avatar-button flex-row"
          style={{ padding: '4px 12px 4px 4px', background: 'transparent', borderRadius: 20, cursor: 'pointer' }}
        >
          <div style={{
            width: 28, height: 28, borderRadius: '50%',
            background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: '#fff', fontSize: 12, fontWeight: 600,
          }}>
            {userInitial}
          </div>
          <span className="text-base font-medium">{userEmail}</span>
        </div>
      </MacOSToolbar>

      <div style={{ flex: 1, overflow: 'auto', padding: 30 }}>
        {/* 加载中 */}
        {loading && (
          <div className="flex-center flex-col" style={{ height: 200, gap: 16 }}>
            <Loader2 className="w-8 h-8 animate-spin" color="var(--macos-accent)" />
            <div className="text-md text-secondary">正在连接后端服务...</div>
          </div>
        )}

        {/* 主内容 */}
        {!loading && (
          <>
            {/* 配置警告横幅 */}
            {configMissing.length > 0 && (
              <div className="flex-row gap-lg" style={{
                marginBottom: 24, padding: '16px 20px',
                background: 'rgba(255, 149, 0, 0.06)',
                border: '1px solid rgba(255, 149, 0, 0.2)',
                borderRadius: 12,
              }}>
                <AlertCircle className="w-5 h-5 flex-shrink-0" color="#ff9500" />
                <div className="flex-1">
                  <div className="text-md font-semibold mb-sm">需要完成配置</div>
                  <div className="text-sm text-secondary">以下配置尚未设置：{configMissing.join('、')}。前往设置页完成配置后即可正常使用。</div>
                </div>
                <button
                  onClick={() => navigate('/settings')}
                  className="flex-row"
                  style={{ padding: '8px 16px', background: '#ff9500', color: 'white', border: 'none', borderRadius: 8, cursor: 'pointer', fontSize: 13, fontWeight: 500 }}
                >
                  前往设置 <ChevronRight className="w-4 h-4" />
                </button>
              </div>
            )}

            {/* 案件列表 */}
            {cases.length === 0 ? (
              <MacOSEmptyState
                icon={FolderOpen}
                title="还没有案件"
                description="点击新建案件开始分析刑事案卷"
                action={<MacOSButton variant="primary" icon={PlusCircle} onClick={() => setShowNewCase(true)}>新建案件</MacOSButton>}
              />
            ) : (
              <div>
                <h2 className="text-2xl font-semibold mb-lg">我的案件</h2>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 20 }}>
                  {cases.map((caseItem, index) => (
                    <MacOSCard
                      key={caseItem.id}
                      clickable
                      onClick={() => navigate('/case/' + caseItem.id)}
                      className={`macOS-animate-slide-up macOS-stagger-${Math.min(index + 1, 8)}`}
                    >
                      <div className="flex-between mb-md">
                        <div className="flex-row gap-md">
                          <div style={{
                            width: 48, height: 48, borderRadius: 12,
                            background: 'linear-gradient(135deg, rgba(59,89,152,0.12) 0%, rgba(90,123,192,0.08) 100%)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            boxShadow: '0 2px 8px rgba(59, 89, 152, 0.1)'
                          }}>
                            <FileText className="w-6 h-6" color="#3b5998" />
                          </div>
                          <div>
                            <h3 className="text-lg font-semibold mb-xs">{caseItem.name}</h3>
                            <p className="text-sm text-secondary">被告人：{caseItem.defendant}</p>
                          </div>
                        </div>
                      </div>
                      <div className="flex-row gap-md text-xs text-tertiary mb-md">
                        <span className="flex-row gap-xs"><Calendar className="w-3 h-3" />{caseItem.created_at}</span>
                        <span className="flex-row gap-xs"><FileText className="w-3 h-3" />{caseItem.file_count} 个文件</span>
                      </div>
                      <div className="flex-between">
                        <span className={`macOS-badge ${caseItem.status === 'done' ? 'macOS-badge-success' : caseItem.status === 'processing' ? 'macOS-badge-warning' : 'macOS-badge-accent'}`}>
                          {caseItem.status === 'done' ? '已完成' : caseItem.status === 'processing' ? '处理中' : '新建'}
                        </span>
                        <ArrowRight className="w-4 h-4 text-tertiary" />
                      </div>
                      <div className="flex-row gap-sm" style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--macos-border)' }}>
                        <button
                          onClick={(e) => { e.stopPropagation(); navigate('/case/' + caseItem.id) }}
                          className="macOS-button macOS-button-primary"
                          style={{ flex: 1, fontSize: 12, padding: '6px 8px' }}
                        >打开</button>
                        <button
                          onClick={(e) => { e.stopPropagation(); handleDeleteCase(caseItem.id) }}
                          className="macOS-button macOS-button-secondary"
                          style={{ color: 'var(--macos-danger)', fontSize: 12, padding: '6px 12px' }}
                        ><Trash2 className="w-3 h-3" /></button>
                      </div>
                    </MacOSCard>
                  ))}
                </div>
              </div>
            )}

            {/* 回收站 */}
            {trashItems.length > 0 && (
              <div style={{ marginTop: 30 }}>
                <div className="flex-row gap-md mb-lg cursor-pointer" onClick={() => setShowTrash(!showTrash)}>
                  <h2 className="text-2xl font-semibold">回收站</h2>
                  <span style={{ fontSize: 12, background: 'rgba(102, 102, 102, 0.1)', color: '#666666', padding: '2px 10px', borderRadius: 10 }}>{trashItems.length}</span>
                  <span className="text-xs text-tertiary">{showTrash ? '▼' : '▶'}</span>
                </div>
                {showTrash && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 20 }}>
                    {trashItems.map(item => (
                      <MacOSCard key={item.id}>
                        <div className="flex-row gap-md mb-md">
                          <div style={{ width: 48, height: 48, borderRadius: 12, background: 'rgba(102, 102, 102, 0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                            <Trash2 className="w-6 h-6" color="#666666" />
                          </div>
                          <div className="flex-1">
                            <h3 className="text-md font-semibold mb-xs">{item.name}</h3>
                            <p className="text-xs text-secondary">被告人：{item.defendant}</p>
                          </div>
                        </div>
                        <div className="flex-row gap-md text-xs text-tertiary mb-md">
                          <span>删除于：{item.deleted_at}</span>
                          <span style={{ color: item.days_left < 2 ? '#666666' : '#f0a500' }}>剩余 {item.days_left} 天</span>
                        </div>
                        <div className="flex-row gap-sm">
                          <button onClick={() => handleRestoreCase(item.id)} style={{ flex: 1, padding: 6, background: 'var(--macos-accent)', color: 'white', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 12 }}>恢复</button>
                          <button onClick={() => handlePermanentDelete(item.id)} style={{ padding: '6px 12px', background: 'rgba(102, 102, 102, 0.1)', color: '#666666', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 12 }}>彻底删除</button>
                        </div>
                      </MacOSCard>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 待导入文件夹 */}
            {pendingFolders.length > 0 && (
              <div style={{ marginTop: 30 }}>
                <h2 className="text-2xl font-semibold mb-lg">待导入文件夹</h2>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 20 }}>
                  {pendingFolders.map((folder, index) => (
                    <MacOSCard key={index}>
                      <div className="flex-row gap-md mb-md">
                        <div style={{ width: 48, height: 48, borderRadius: 12, background: 'rgba(255, 149, 0, 0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                          <FolderOpen className="w-6 h-6" color="#ff9500" />
                        </div>
                        <div className="flex-1">
                          <h3 className="text-md font-semibold mb-xs">{folder.name}</h3>
                          <p className="text-xs text-secondary">{folder.pdf_count} 个 PDF · {folder.size_mb} MB</p>
                        </div>
                      </div>
                      <button
                        onClick={() => {
                          setSelectedFolder(folder)
                          setNewCaseName(folder.name.replace(/^案件_/, '').replace(/_\d{8}$/, ''))
                          setShowImportDialog(true)
                        }}
                        style={{ width: '100%', padding: 8, background: '#ff9500', color: 'white', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 500 }}
                      >导入为案件</button>
                    </MacOSCard>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* 新建案件对话框 */}
      <InlineDialog open={showNewCase} onClose={() => setShowNewCase(false)} title="新建案件" width={400}>
        <div className="mb-md">
          <label className="text-sm font-medium mb-sm block">案件名称</label>
          <MacOSInput type="text" value={newCaseName} onChange={(e) => setNewCaseName(e.target.value)} placeholder="xx涉嫌开设赌场罪" />
        </div>
        <div className="mb-lg">
          <label className="text-sm font-medium mb-sm block">被告人姓名</label>
          <MacOSInput type="text" value={defendant} onChange={(e) => setDefendant(e.target.value)} placeholder="请准确填写被告人（服务对象）姓名" />
        </div>
        <div className="flex-row gap-md">
          <MacOSButton variant="primary" onClick={handleCreateCase} disabled={!newCaseName.trim() || !defendant.trim()} style={{ flex: 1 }}>创建案件</MacOSButton>
          <MacOSButton variant="secondary" onClick={() => setShowNewCase(false)}>取消</MacOSButton>
        </div>
      </InlineDialog>

      {/* 导入案件对话框 */}
      <InlineDialog open={showImportDialog} onClose={() => setShowImportDialog(false)} title="导入文件夹为案件" width={400}>
        <div style={{ padding: 12, background: 'var(--macos-bg-secondary)', borderRadius: 8, marginBottom: 16 }}>
          <div className="text-sm font-medium mb-xs">{selectedFolder?.name}</div>
          <div className="text-xs text-secondary">将导入为合法案件</div>
        </div>
        <div className="mb-md">
          <label className="text-sm font-medium mb-sm block">案件名称</label>
          <MacOSInput type="text" value={newCaseName} onChange={(e) => setNewCaseName(e.target.value)} placeholder="xx涉嫌开设赌场罪" />
        </div>
        <div className="mb-lg">
          <label className="text-sm font-medium mb-sm block">被告人姓名</label>
          <MacOSInput type="text" value={defendant} onChange={(e) => setDefendant(e.target.value)} placeholder="请准确填写被告人（服务对象）姓名" />
        </div>
        <div className="flex-row gap-md">
          <MacOSButton variant="primary" onClick={handleImportFolder} disabled={!newCaseName.trim() || !defendant.trim()} style={{ flex: 1 }}>导入案件</MacOSButton>
          <MacOSButton variant="secondary" onClick={() => setShowImportDialog(false)}>取消</MacOSButton>
        </div>
      </InlineDialog>
    </PageLayout>
  )
}
