import { useState, useCallback, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { PlusCircle, FolderOpen, Trash2, Calendar, FileText, ArrowRight, Settings, AlertCircle, ChevronRight } from 'lucide-react'
import { MacOSTitlebar, MacOSToolbar, MacOSButton, MacOSCard, MacOSEmptyState } from '../components/MacOSLayout'
import { api, getAuthEmail } from '../api'
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

  // 检查配置状态
  useEffect(() => {
    const checkConfig = async () => {
      try {
        const res = await fetch('http://localhost:8080/api/config')
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
  }, [])

  // 加载案件列表和待导入文件夹
  const loadData = useCallback(async () => {
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
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handleCreateCase = useCallback(async () => {
    if (!newCaseName.trim() || !defendant.trim()) return

    // 创建前检查 API 配置
    if (configMissing.length > 0) {
      showAlert({
        title: '需要先完成配置',
        message: `请先前往设置页完成以下配置：\n${configMissing.join('、')}`,
        variant: 'danger',
      })
      navigate('/settings')
      return
    }

    try {
      const newCase = await api.createCase(newCaseName, defendant, getAuthEmail() || undefined)
      setShowNewCase(false)
      setNewCaseName('')
      setDefendant('')
      loadData()
      navigate(`/case/${newCase.id}`)
    } catch (err) {
      console.error('创建案件失败:', err)
      const msg = err instanceof Error ? err.message : '未知错误'
      showAlert({ title: '创建失败', message: `创建案件失败：${msg}`, variant: 'danger' })
    }
  }, [newCaseName, defendant, navigate, loadData, configMissing])

  const handleImportFolder = useCallback(async () => {
    if (!selectedFolder || !newCaseName.trim() || !defendant.trim()) return

    // 导入前检查 API 配置
    if (configMissing.length > 0) {
      showAlert({
        title: '需要先完成配置',
        message: `请先前往设置页完成以下配置：\n${configMissing.join('、')}`,
        variant: 'danger',
      })
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
      navigate(`/case/${newCase.id}`)
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
        showAlert({ title: '删除失败', message: `删除失败：${result.error}`, variant: 'danger' })
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
        showAlert({ title: '恢复失败', message: `恢复失败：${result.error}`, variant: 'warning' })
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
      showAlert({ title: '删除失败', message: `彻底删除失败：${msg}`, variant: 'danger' })
    }
  }, [loadData])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--macos-bg-primary)', overflow: 'hidden' }}>
      <MacOSTitlebar />
      <MacOSToolbar title="刑事案卷分析系统">
        <MacOSButton variant="primary" icon={PlusCircle} onClick={() => setShowNewCase(true)}>
          新建案件
        </MacOSButton>
        <button
          onClick={() => navigate('/settings')}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px',
            padding: '8px 14px', background: configMissing.length > 0 ? 'rgba(255, 149, 0, 0.1)' : 'transparent',
            border: '1px solid var(--macos-border)', borderRadius: '8px',
            cursor: 'pointer', fontSize: '13px', color: configMissing.length > 0 ? '#ff9500' : '#86868b',
          }}
        >
          <Settings className="w-4 h-4" />
          设置
          {configMissing.length > 0 && (
            <span style={{
              width: '8px', height: '8px', borderRadius: '50%', background: '#ff9500',
              display: 'inline-block',
            }} />
          )}
        </button>
        {/* 用户头像 + 邮箱 */}
        <div
          onClick={() => navigate('/settings')}
          style={{
            display: 'flex', alignItems: 'center', gap: '10px',
            padding: '4px 12px 4px 4px',
            background: 'transparent',
            borderRadius: '20px',
            cursor: 'pointer',
            transition: 'background 0.15s',
          }}
          onMouseEnter={e => e.currentTarget.style.background = 'rgba(0,0,0,0.04)'}
          onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
        >
          <div style={{
            width: 28, height: 28, borderRadius: '50%',
            background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: '#fff', fontSize: '12px', fontWeight: 600,
          }}>
            {(getAuthEmail() || '?')[0].toUpperCase()}
          </div>
          <span style={{ fontSize: '13px', color: '#1d1d1f', fontWeight: 500 }}>
            {getAuthEmail()}
          </span>
        </div>
      </MacOSToolbar>

      <div style={{ flex: 1, overflow: 'auto', padding: '30px' }}>
        {/* 配置引导横幅 */}
        {configMissing.length > 0 && (
          <div style={{
            marginBottom: '24px', padding: '16px 20px',
            background: 'rgba(255, 149, 0, 0.06)',
            border: '1px solid rgba(255, 149, 0, 0.2)',
            borderRadius: '12px',
            display: 'flex', alignItems: 'center', gap: '14px',
          }}>
            <AlertCircle className="w-5 h-5" color="#ff9500" style={{ flexShrink: 0 }} />
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: '14px', fontWeight: '600', color: '#1d1d1f', marginBottom: '2px' }}>
                需要完成配置
              </div>
              <div style={{ fontSize: '13px', color: '#6e6e73' }}>
                以下配置尚未设置：{configMissing.join('、')}。前往设置页完成配置后即可正常使用。
              </div>
            </div>
            <button
              onClick={() => navigate('/settings')}
              style={{
                display: 'flex', alignItems: 'center', gap: '4px',
                padding: '8px 16px', background: '#ff9500', color: 'white',
                border: 'none', borderRadius: '8px', cursor: 'pointer',
                fontSize: '13px', fontWeight: '500', whiteSpace: 'nowrap',
              }}
            >
              前往设置
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        )}
        {cases.length === 0 ? (
          <MacOSEmptyState
            icon={FolderOpen}
            title="还没有案件"
            description="点击新建案件开始分析刑事案卷"
            action={
              <MacOSButton variant="primary" icon={PlusCircle} onClick={() => setShowNewCase(true)}>
                新建案件
              </MacOSButton>
            }
          />
        ) : (
          <div>
            <h2 style={{ fontSize: '20px', fontWeight: '600', marginBottom: '20px', color: '#1d1d1f' }}>
              我的案件
            </h2>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '20px' }}>
              {cases.map(caseItem => (
                <MacOSCard key={caseItem.id}>
                  <div style={{ cursor: 'pointer' }} onClick={() => navigate(`/case/${caseItem.id}`)}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                        <div style={{
                          width: '48px',
                          height: '48px',
                          borderRadius: '12px',
                          background: 'rgba(0, 122, 255, 0.1)',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center'
                        }}>
                          <FileText className="w-6 h-6" color="var(--macos-accent)" />
                        </div>
                        <div>
                          <h3 style={{ fontSize: '16px', fontWeight: '600', marginBottom: '4px' }}>{caseItem.name}</h3>
                          <p style={{ fontSize: '13px', color: '#6e6e73' }}>被告人：{caseItem.defendant}</p>
                        </div>
                      </div>
                    </div>

                    <div style={{ display: 'flex', gap: '12px', fontSize: '12px', color: '#86868b', marginBottom: '12px' }}>
                      <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <Calendar className="w-3 h-3" />
                        {caseItem.created_at}
                      </span>
                      <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <FileText className="w-3 h-3" />
                        {caseItem.file_count} 个文件
                      </span>
                    </div>

                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{
                        fontSize: '11px',
                        padding: '3px 10px',
                        borderRadius: '10px',
                        background: caseItem.status === 'done' ? 'rgba(52, 199, 89, 0.1)' :
                                   caseItem.status === 'processing' ? 'rgba(255, 149, 0, 0.1)' : 'rgba(30, 58, 95, 0.1)',
                        color: caseItem.status === 'done' ? '#2d8f3d' :
                               caseItem.status === 'processing' ? '#ff9500' : 'var(--macos-accent)'
                      }}>
                        {caseItem.status === 'done' ? '已完成' :
                         caseItem.status === 'processing' ? '处理中' : '新建'}
                      </span>
                      <ArrowRight className="w-4 h-4" color="#86868b" />
                    </div>
                  </div>

                  <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--macos-border)', display: 'flex', gap: '8px' }}>
                    <button
                      onClick={() => navigate(`/case/${caseItem.id}`)}
                      style={{
                        flex: 1,
                        padding: '6px',
                        background: 'var(--macos-accent)',
                        color: 'white',
                        border: 'none',
                        borderRadius: '6px',
                        cursor: 'pointer',
                        fontSize: '12px',
                        transition: 'background 0.15s ease',
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = 'var(--macos-accent-hover)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'var(--macos-accent)'}
                    >
                      打开
                    </button>
                    <button
                      onClick={() => handleDeleteCase(caseItem.id)}
                      style={{
                        padding: '6px 12px',
                        background: 'var(--macos-bg-secondary)',
                        color: '#ff3b30',
                        border: 'none',
                        borderRadius: '6px',
                        cursor: 'pointer',
                        fontSize: '12px'
                      }}
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                </MacOSCard>
              ))}
            </div>
          </div>
        )}

        {/* 回收站 */}
        {trashItems.length > 0 && (
          <div style={{ marginTop: '30px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '20px', cursor: 'pointer' }} onClick={() => setShowTrash(!showTrash)}>
              <h2 style={{ fontSize: '20px', fontWeight: '600', color: '#1d1d1f', margin: 0 }}>回收站</h2>
              <span style={{ fontSize: '12px', background: 'rgba(255, 59, 48, 0.1)', color: '#ff3b30', padding: '2px 10px', borderRadius: '10px' }}>{trashItems.length}</span>
              <span style={{ fontSize: '12px', color: '#86868b' }}>{showTrash ? '▼' : '▶'}</span>
            </div>

            {showTrash && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '20px' }}>
                {trashItems.map(item => (
                  <MacOSCard key={item.id}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
                      <div style={{
                        width: '48px',
                        height: '48px',
                        borderRadius: '12px',
                        background: 'rgba(255, 59, 48, 0.1)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center'
                      }}>
                        <Trash2 className="w-6 h-6" color="#ff3b30" />
                      </div>
                      <div style={{ flex: 1 }}>
                        <h3 style={{ fontSize: '14px', fontWeight: '600', marginBottom: '4px' }}>{item.name}</h3>
                        <p style={{ fontSize: '12px', color: '#6e6e73' }}>被告人：{item.defendant}</p>
                      </div>
                    </div>

                    <div style={{ display: 'flex', gap: '12px', fontSize: '11px', color: '#86868b', marginBottom: '12px' }}>
                      <span>删除于：{item.deleted_at}</span>
                      <span style={{ color: item.days_left < 2 ? '#ff3b30' : '#ff9500' }}>剩余 {item.days_left} 天</span>
                    </div>

                    <div style={{ display: 'flex', gap: '8px' }}>
                      <button
                        onClick={() => handleRestoreCase(item.id)}
                        style={{
                          flex: 1,
                          padding: '6px',
                          background: 'var(--macos-accent)',
                          color: 'white',
                          border: 'none',
                          borderRadius: '6px',
                          cursor: 'pointer',
                          fontSize: '12px'
                        }}
                      >
                        恢复
                      </button>
                      <button
                        onClick={() => handlePermanentDelete(item.id)}
                        style={{
                          padding: '6px 12px',
                          background: 'rgba(255, 59, 48, 0.1)',
                          color: '#ff3b30',
                          border: 'none',
                          borderRadius: '6px',
                          cursor: 'pointer',
                          fontSize: '12px'
                        }}
                      >
                        彻底删除
                      </button>
                    </div>
                  </MacOSCard>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 待导入文件夹 */}
        {pendingFolders.length > 0 && (
          <div style={{ marginTop: '30px' }}>
            <h2 style={{ fontSize: '20px', fontWeight: '600', marginBottom: '20px', color: '#1d1d1f' }}>
              待导入文件夹
            </h2>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '20px' }}>
              {pendingFolders.map((folder, index) => (
                <MacOSCard key={index}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
                    <div style={{
                      width: '48px',
                      height: '48px',
                      borderRadius: '12px',
                      background: 'rgba(255, 149, 0, 0.1)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center'
                    }}>
                      <FolderOpen className="w-6 h-6" color="#ff9500" />
                    </div>
                    <div style={{ flex: 1 }}>
                      <h3 style={{ fontSize: '14px', fontWeight: '600', marginBottom: '4px' }}>{folder.name}</h3>
                      <p style={{ fontSize: '12px', color: '#6e6e73' }}>{folder.pdf_count} 个 PDF · {folder.size_mb} MB</p>
                    </div>
                  </div>

                  <button
                    onClick={() => {
                      setSelectedFolder(folder)
                      setNewCaseName(folder.name.replace(/^案件_/, '').replace(/_\d{8}$/, ''))
                      setShowImportDialog(true)
                    }}
                    style={{
                      width: '100%',
                      padding: '8px',
                      background: '#ff9500',
                      color: 'white',
                      border: 'none',
                      borderRadius: '6px',
                      cursor: 'pointer',
                      fontSize: '13px',
                      fontWeight: '500'
                    }}
                  >
                    导入为案件
                  </button>
                </MacOSCard>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 新建案件对话框 */}
      {showNewCase && (
        <div style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0, 0, 0, 0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 100
        }} onClick={() => setShowNewCase(false)}>
          <MacOSCard style={{ maxWidth: '400px', width: '90%', margin: '0 20px' }} onClick={(e: React.MouseEvent) => e.stopPropagation()}>
            <h2 style={{ fontSize: '18px', fontWeight: '600', marginBottom: '20px' }}>新建案件</h2>

            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                案件名称
              </label>
              <input
                type="text"
                value={newCaseName}
                onChange={(e) => setNewCaseName(e.target.value)}
                placeholder="如：彭帮生案"
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  border: '1px solid var(--macos-border)',
                  borderRadius: '8px',
                  fontSize: '14px'
                }}
              />
            </div>

            <div style={{ marginBottom: '20px' }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                被告人姓名
              </label>
              <input
                type="text"
                value={defendant}
                onChange={(e) => setDefendant(e.target.value)}
                placeholder="被告人姓名"
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  border: '1px solid var(--macos-border)',
                  borderRadius: '8px',
                  fontSize: '14px'
                }}
              />
            </div>

            <div style={{ display: 'flex', gap: '12px' }}>
              <button
                onClick={handleCreateCase}
                disabled={!newCaseName.trim() || !defendant.trim()}
                style={{
                  flex: 1,
                  padding: '10px',
                  background: 'var(--macos-accent)',
                  color: 'white',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: newCaseName.trim() && defendant.trim() ? 'pointer' : 'not-allowed',
                  opacity: newCaseName.trim() && defendant.trim() ? 1 : 0.5,
                  fontSize: '14px',
                  fontWeight: '500'
                }}
              >
                创建案件
              </button>
              <button
                onClick={() => setShowNewCase(false)}
                style={{
                  padding: '10px 20px',
                  background: 'var(--macos-bg-secondary)',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: 'pointer',
                  fontSize: '14px'
                }}
              >
                取消
              </button>
            </div>
          </MacOSCard>
        </div>
      )}

      {/* 导入案件对话框 */}
      {showImportDialog && selectedFolder && (
        <div style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0, 0, 0, 0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 100
        }} onClick={() => setShowImportDialog(false)}>
          <MacOSCard style={{ maxWidth: '400px', width: '90%', margin: '0 20px' }} onClick={(e: React.MouseEvent) => e.stopPropagation()}>
            <h2 style={{ fontSize: '18px', fontWeight: '600', marginBottom: '20px' }}>导入文件夹为案件</h2>

            <div style={{ padding: '12px', background: 'var(--macos-bg-secondary)', borderRadius: '8px', marginBottom: '16px' }}>
              <div style={{ fontSize: '13px', fontWeight: '500', marginBottom: '4px' }}>{selectedFolder.name}</div>
              <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)' }}>将导入为合法案件</div>
            </div>

            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                案件名称
              </label>
              <input
                type="text"
                value={newCaseName}
                onChange={(e) => setNewCaseName(e.target.value)}
                placeholder="如：彭帮生案"
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  border: '1px solid var(--macos-border)',
                  borderRadius: '8px',
                  fontSize: '14px'
                }}
              />
            </div>

            <div style={{ marginBottom: '20px' }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>
                被告人姓名
              </label>
              <input
                type="text"
                value={defendant}
                onChange={(e) => setDefendant(e.target.value)}
                placeholder="被告人姓名"
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  border: '1px solid var(--macos-border)',
                  borderRadius: '8px',
                  fontSize: '14px'
                }}
              />
            </div>

            <div style={{ display: 'flex', gap: '12px' }}>
              <button
                onClick={handleImportFolder}
                disabled={!newCaseName.trim() || !defendant.trim()}
                style={{
                  flex: 1,
                  padding: '10px',
                  background: '#ff9500',
                  color: 'white',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: newCaseName.trim() && defendant.trim() ? 'pointer' : 'not-allowed',
                  opacity: newCaseName.trim() && defendant.trim() ? 1 : 0.5,
                  fontSize: '14px',
                  fontWeight: '500'
                }}
              >
                导入案件
              </button>
              <button
                onClick={() => setShowImportDialog(false)}
                style={{
                  padding: '10px 20px',
                  background: 'var(--macos-bg-secondary)',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: 'pointer',
                  fontSize: '14px'
                }}
              >
                取消
              </button>
            </div>
          </MacOSCard>
        </div>
      )}
    </div>
  )
}
