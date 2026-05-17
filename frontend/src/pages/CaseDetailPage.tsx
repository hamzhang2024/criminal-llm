import { useState, useCallback, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Upload, Wand2, FileDown, Scale, CheckCircle, AlertCircle, FileText, ArrowRight, Loader2, Trash2, CheckSquare, Square, XCircle, Play, RefreshCw } from 'lucide-react'
import { MacOSTitlebar, MacOSSidebar, MacOSToolbar, MacOSButton, MacOSCard, MacOSEmptyState } from '../components/MacOSLayout'
import { api, thumbnailUrl, serveFileUrl, API_BASE } from '../api'
import { showConfirm, showAlert } from '../components/MacOSDialog'
import { marked } from 'marked'

// 配置 marked 使用同步解析
marked.setOptions({ async: false })

/** 工作中动画 — 三个跳动的小圆点 */
function WorkingDots() {
  return (
    <span style={{ display: 'inline-flex', gap: '3px', alignItems: 'flex-end', height: '14px' }}>
      {[0, 1, 2].map(i => (
        <span
          key={i}
          style={{
            width: '5px',
            height: '5px',
            borderRadius: '50%',
            background: '#1e3a5f',
            animation: `bounceDot 1.2s ease-in-out ${i * 0.15}s infinite`,
          }}
        />
      ))}
      <style>{`
        @keyframes bounceDot {
          0%, 80%, 100% { transform: translateY(0) scale(1); }
          40% { transform: translateY(-6px) scale(1.2); }
        }
      `}</style>
    </span>
  )
}

// 文件状态
interface CaseFile {
  id: string
  name: string
  size: number
  status: 'pending' | 'processing' | 'done' | 'error'
  selected?: boolean        // 是否选中
  error?: string
  path?: string           // 文件完整路径
  processedPath?: string    // 去水印后路径
  mdPath?: string           // MD 路径
  splitResults?: any[]      // 拆分结果
  source?: string           // 来源文件夹名
}

export function CaseDetailPage() {
  const { caseId } = useParams()
  const navigate = useNavigate()
  const [caseName, setCaseName] = useState('加载中...')
  const [defendant, setDefendant] = useState('')
  const [currentStep, setCurrentStep] = useState(() => {
    const saved = localStorage.getItem(`case_${caseId}_step`)
    return saved !== null ? parseInt(saved, 10) : 0
  })
  const [files, setFiles] = useState<CaseFile[]>([])
  const [password, setPassword] = useState('')
  const [processing, setProcessing] = useState(false)
  const [progress, setProgress] = useState('')
  const [error, setError] = useState<string | null>(null)

  // 保存当前步骤到 localStorage（刷新后恢复）
  useEffect(() => {
    if (caseId) localStorage.setItem(`case_${caseId}_step`, String(currentStep))
  }, [caseId, currentStep])

  // 加载案件信息
  useEffect(() => {
    if (!caseId) return
    
    api.getCaseInfo(caseId!)
      .then(data => {
        if (data.id) {
          setCaseName(data.name)
          setDefendant(data.defendant)
        }
      })
      .catch(err => console.error('加载案件失败:', err))

    // 页面加载时检查分析是否已完成（刷新后恢复状态）
    api.getStageStatus(caseId!)
      .then(status => {
        const stages = status?.status || {}
        const stageMap: Record<string, number> = { stage_1: 1, stage_2: 2, stage_3: 3, stage_4: 4, stage_5: 5, stage_6: 6, stage_51: 51, stage_52: 52, stage_53: 53 }
        const newStatus: Record<number, 'idle' | 'completed'> = {}
        for (const [key, num] of Object.entries(stageMap)) {
          if (stages[key]?.completed) newStatus[num] = 'completed'
        }
        if (Object.keys(newStatus).length > 0) setStageStatus(prev => ({ ...prev, ...newStatus }))
        if (stages.stage_5?.completed && stages.stage_51?.completed && stages.stage_52?.completed && stages.stage_53?.completed && stages.stage_6?.completed) {
          setAnalysisCompleted(true)
        }
        // 恢复运行中的阶段状态
        const task = status?.task
        if (task?.status === 'running') {
          const runningStage = task.current_stage
          if (runningStage) {
            setStageStatus(prev => ({ ...prev, [runningStage]: 'running' }))
            setRunningStage(runningStage)
          }
        }
      })
      .catch(() => { /* 无分析状态 */ })

    // 加载断点续传状态
    api.getAnalysisState(caseId!)
      .then(state => {
        if (state.state) {
          setAnalysisState(state.state)
          setNextStep(state.next_step)
        }
      })
      .catch(() => { /* 无断点状态 */ })
  }, [caseId])

  // 切换步骤时加载对应文件
  useEffect(() => {
    if (!caseId) return

    if (currentStep === 0) {
      // 步骤 0：加载原始文件
      api.getCaseFiles(caseId!)
        .then(data => {
          if (Array.isArray(data)) {
            setFiles(data.map((f: any) => ({
              id: f.id,
              name: f.name,
              size: f.size,
              status: 'pending',
              selected: true
            })))
          }
        })
        .catch(err => console.error('加载文件失败:', err))
    } else if (currentStep >= 1 && currentStep <= 3) {
      // 步骤 1-3：加载上一步的输出
      api.getStepFiles(caseId!, currentStep)
        .then(data => {
          if (Array.isArray(data)) {
            setFiles(data.map((f: any) => ({
              id: f.id,
              name: f.name,
              size: f.size,
              status: f.status || 'pending',
              source: f.source,
            })))
          }
        })
        .catch(err => console.error('加载步骤文件失败:', err))

      // 步骤 2-3：检查是否已有证据
      if (currentStep >= 2 && currentStep <= 3) {
        // 检查证据提取是否正在运行
        const checkExtractStatus = async () => {
          try {
            const st = await fetch(`${API_BASE}/cases/${caseId}/extract-status`).then(r => r.json())
            return st.status === 'running'
          } catch {
            return false
          }
        }

        api.getEvidenceIndex(caseId!)
          .then(async data => {
            if (data.total_evidence > 0) {
              setEvidenceList(data.evidence || [])
            }

            const isRunning = await checkExtractStatus()
            if (isRunning) {
              setProcessing(true)
              setProgress('正在提取证据...')
              // 启动轮询：等待提取完成
              if (extractPollRef.current) clearInterval(extractPollRef.current)
              extractPollRef.current = setInterval(async () => {
                // 用户主动停止后，不再跟随后端状态
                if (extractUserStoppedRef.current) {
                  clearInterval(extractPollRef.current!)
                  extractPollRef.current = null
                  return
                }
                try {
                  const st2 = await fetch(`${API_BASE}/cases/${caseId}/extract-status`).then(r => r.json())
                  if (st2.status !== 'running') {
                    clearInterval(extractPollRef.current!)
                    extractPollRef.current = null
                    const data2 = await api.getEvidenceIndex(caseId!)
                    setEvidenceList(data2.evidence || [])
                    setEvidenceExtracted(true)
                    setProcessing(false)
                    setProgress(`✅ 已提取 ${data2.total_evidence} 份证据`)
                    setTimeout(() => setProgress(''), 3000)
                  } else {
                    // 刷新进度：使用后端返回的进度数据
                    const totalFiles = st2.total_files || 0
                    const processedFiles = st2.processed_files || 0
                    const currentFile = st2.current_file || ''
                    const pct = totalFiles > 0 ? Math.round((processedFiles / totalFiles) * 100) : 0
                    if (processedFiles > 0 || totalFiles > 0) {
                      setProgress(`正在提取证据... ${processedFiles}/${totalFiles} (${pct}%) ${currentFile ? '— ' + currentFile : ''}`)
                    }
                    // 同时刷新已提取的证据数
                    const data3 = await api.getEvidenceIndex(caseId!)
                    if (data3.total_evidence > 0) {
                      setEvidenceList(data3.evidence || [])
                    }
                  }
                } catch {
                  clearInterval(extractPollRef.current!)
                  extractPollRef.current = null
                  setProcessing(false)
                }
              }, 3000)
            } else {
              // 没有在运行，检查是否已完成
              if (data.total_evidence > 0) {
                setEvidenceExtracted(true)
              }
            }
          })
          .catch(() => { /* 无证据 */ })

        // 步骤 2：检查是否有运行中的转换任务（刷新后恢复进度）
        fetch(`${API_BASE}/tasks/${caseId}/convert-status`)
          .then(r => r.json())
          .then(data => {
            const st = data.status
            if (st === 'running' || st === 'pending') {
              setProcessing(true)
              const cur0 = data.current || 0
              const tot0 = data.total || 0
              const pct0 = tot0 > 0 ? Math.round((cur0 / tot0) * 100) : 0
              setProgress(`转换中：${cur0}/${tot0} (${pct0}%) — ${data.message || ''}`)
              // 启动轮询
              const pollInterval = setInterval(async () => {
                try {
                  const statusResp = await fetch(`${API_BASE}/tasks/${caseId}/convert-status`)
                  const statusData = await statusResp.json()
                  const st2 = statusData.status

                  if (st2 === 'running') {
                    const cur = statusData.current || 0
                    const tot = statusData.total || 0
                    const pct = tot > 0 ? Math.round((cur / tot) * 100) : 0
                    setProgress(`转换中：${cur}/${tot} (${pct}%) — ${statusData.message || ''}`)
                  } else if (st2 === 'completed') {
                    clearInterval(pollInterval)
                    const results = statusData.results || []
                    const successCount = results.filter((r: any) => r.success).length
                    setProgress(`✅ 已转换 ${successCount}/${statusData.total || 0} 个文件`)
                    setProcessing(false)
                    // 重新加载文件
                    const filesData = await api.getStepFiles(caseId!, 2)
                    if (Array.isArray(filesData)) {
                      setFiles(filesData.map((f: any) => ({
                        id: f.id, name: f.name, size: f.size, status: f.status || 'done', source: f.source,
                      })))
                    }
                    // 转换完成后自动提取证据
                    if (successCount > 0) {
                      setProgress('正在提取证据清单...')
                      try {
                        const evResult = await api.extractEvidence(caseId!)
                        if (evResult.success && evResult.evidence) {
                          setEvidenceList(evResult.evidence)
                          setEvidenceExtracted(true)
                          setProgress(`✅ 已提取 ${evResult.total_evidence} 份证据，正在自动开始分析...`)
                          if (defendant.trim()) {
                            setTimeout(async () => {
                              try {
                                const analysisResult = await api.runAllStages(caseId!, defendant, crimeType || undefined)
                                if (!analysisResult.success) throw new Error(analysisResult.detail || analysisResult.error || '分析执行失败')
                                setProgress('✅ 分析任务已启动，正在跟踪进度...')
                                setProcessing(true)
                                pollAnalysisProgress()
                              } catch (err) {
                                setError(err instanceof Error ? err.message : '分析触发失败')
                                setProcessing(false)
                              }
                            }, 500)
                          } else {
                            setProgress(`✅ 已提取 ${evResult.total_evidence} 份证据（请补充被告人信息后手动开始分析）`)
                          }
                        } else {
                          throw new Error(evResult.detail || evResult.error || '提取失败')
                        }
                      } catch (extractErr) {
                        const isStop = extractErr instanceof Error && extractErr.message === '用户已停止提取'
                        if (isStop) {
                          setProgress('⏹ 已停止提取，当前已提取的证据已保存')
                        } else {
                          setError(extractErr instanceof Error ? extractErr.message : '证据提取失败')
                          setProgress('')
                        }
                      }
                    }
                  } else if (st2 === 'failed' || st2 === 'cancelled') {
                    clearInterval(pollInterval)
                    setProgress('')
                    setError(statusData.message || '转换任务失败')
                    setProcessing(false)
                  }
                } catch {
                  clearInterval(pollInterval)
                }
              }, 2000)
            } else if (st === 'completed') {
              // 已完成：显示完成信息
              const results = data.results || []
              const successCount = results.filter((r: any) => r.success).length
              setProgress(`✅ 已转换 ${successCount}/${data.total || 0} 个文件`)
            }
          })
          .catch(() => { /* 无转换任务 */ })
      }
    } else if (currentStep === 3) {
      // 步骤 3：加载 MD 文件用于分析
      api.getStepFiles(caseId!, 3)
        .then(data => {
          if (Array.isArray(data)) {
            setFiles(data.map((f: any) => ({
              id: f.id,
              name: f.name,
              size: f.size,
              status: 'pending',
              source: f.source
            })))
          }
        })
        .catch(err => console.error('加载分析数据失败:', err))

      // 检查是否有任务在运行
      api.getStageStatus(caseId!)
        .then(status => {
          const task = status?.task
          if (task?.status === 'running') {
            setProcessing(true)
            setProgress(task.message || '分析任务在运行中...')
            pollAnalysisProgress()
          }
        })
        .catch(() => { /* 忽略 */ })
    }
  }, [caseId, currentStep])
  const [showPassword, setShowPassword] = useState(false)
  const [enhanceDpi, setEnhanceDpi] = useState(300)  // PDF 精度提升的 DPI
  // PDF 处理选项
  const [optWatermark, setOptWatermark] = useState(true)   // 默认去水印
  const [optEnhance, setOptEnhance] = useState(false)       // 默认不提升精度
  const [optDeleteOriginal, setOptDeleteOriginal] = useState(false)  // 默认保留原始文件
  const [crimeType, setCrimeType] = useState('')

  // 流水线状态
  const [pipelineStatus, setPipelineStatus] = useState<Record<number | string, boolean>>({})
  const [pipelineRunning, setPipelineRunning] = useState(false)
  const [currentPipelineStep, setCurrentPipelineStep] = useState<number>(0)
  const [stepResults, setStepResults] = useState<Record<number, any>>({})

  // 断点续传状态
  const [analysisState, setAnalysisState] = useState<any>(null)
  const [nextStep, setNextStep] = useState<number | null>(null)

  // 流水线实时进度（LLM 调用进度）
  const [liveProgress, setLiveProgress] = useState<{ message: string; current: number; total: number; elapsed: number } | null>(null)

  // 步骤配置 — 简化版：上传 → PDF处理 → 转MD → 分析
  const steps = [
    { id: 'upload', name: '上传文件', icon: Upload, description: '选择原始案卷文件' },
    { id: 'process', name: '文件处理', icon: Wand2, description: '去水印、密码解除、精度提升（可多选）' },
    { id: 'convert', name: '证据提取', icon: FileDown, description: '转换为结构化格式 → LLM 提取证据，准备分析' },
    { id: 'analyze', name: '案卷分析', icon: Scale, description: '6 阶段智能分析（指控要素 → 人物关系 → 事件拆解 → 法律法规 → 控辩对抗 → 三阶层辩护）' }
  ]

  // 上传文件
  const [uploading, setUploading] = useState(false)

  const handleFileSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files
    if (!selected || selected.length === 0) {
      showAlert({ title: '提示', message: '未选择任何文件', variant: 'info' })
      return
    }
    if (!caseId) {
      showAlert({ title: '错误', message: '案件 ID 不存在，请刷新页面后重试', variant: 'danger' })
      return
    }

    // 过滤重复文件（按文件名匹配）
    const existingNames = new Set(files.map(f => f.name))
    const newFiles = Array.from(selected).filter(f => !existingNames.has(f.name))
    const dupCount = selected.length - newFiles.length

    if (newFiles.length === 0) {
      showAlert({ title: '提示', message: `所选 ${selected.length} 个文件均已已存在，无需重复上传`, variant: 'info' })
      e.target.value = ''
      return
    }

    setUploading(true)
    try {
      console.log('[upload] caseId:', caseId, 'new:', newFiles.length, 'dup:', dupCount)
      const result = await api.uploadFiles(caseId, newFiles)
      console.log('[upload] result:', result)

      if (!result.success) {
        const msg = result.error || result.detail || '上传失败'
        throw new Error(msg)
      }
      // 重新从后端加载文件列表
      const filesData = await api.getCaseFiles(caseId)
      setFiles(filesData.map((f: any) => ({
        id: f.id,
        name: f.name,
        size: f.size,
        status: f.status || 'pending',
        selected: true
      })))
      const msg = dupCount > 0
        ? `已上传 ${newFiles.length} 个新文件，跳过 ${dupCount} 个重复文件`
        : `已上传 ${newFiles.length} 个文件`
      showAlert({ title: '上传成功', message: msg, variant: 'success' })
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : '未知错误'
      showAlert({ title: '上传失败', message: errMsg, variant: 'danger' })
    } finally {
      setUploading(false)
    }
    // 重置 input 以便重复选择同一文件
    e.target.value = ''
  }, [caseId, files])

  // 预览文件（在应用内打开）
  const [previewFile, setPreviewFile] = useState<CaseFile | null>(null)
  const [previewThumbs, setPreviewThumbs] = useState<string[]>([])
  const [previewThumbError, setPreviewThumbError] = useState<string | null>(null)
  const [previewThumbLoading, setPreviewThumbLoading] = useState(false)
  const [enlargedPage, setEnlargedPage] = useState<number>(-1)  // -1=不放大

  const handleOpenFile = async (file: CaseFile) => {
    // 根据当前步骤确定要预览的目录优先级
    let dirsToTry: string[]
    if (currentStep === 0) {
      dirsToTry = ['original']
    } else if (currentStep === 1) {
      dirsToTry = ['processed', 'original']
    } else if (currentStep === 2) {
      dirsToTry = ['processed', 'original']
    } else {
      dirsToTry = ['md', 'original']
    }

    const isMarkdown = file.name.endsWith('.md')

    // 步骤1：优先使用 processed/ 中对应的 _去水印 文件
    let previewName = file.name
    if (currentStep === 1 && !file.name.includes('_去水印')) {
      const stem = file.name.replace(/\.pdf$/i, '')
      const candidate = `${stem}_去水印.pdf`
      try {
        const testData = await api.getThumbnails(caseId!, candidate, 'processed', 500)
        if (testData.success && testData.thumbnails) {
          // processed/ 中有对应的去水印文件
          previewName = candidate
          setPreviewThumbs(testData.thumbnails)
          setPreviewThumbError(null)
          setPreviewFile({ ...file, path: serveFileUrl(caseId!, candidate, 'processed'), name: candidate })
          setEnlargedPage(-1)
          setPreviewThumbLoading(false)
          return
        }
      } catch { /* fallback to original logic */ }
    }

    // 异步加载缩略图（PDF）或跳过（MD）
    if (!isMarkdown) {
      setPreviewThumbs([])
      setPreviewThumbError(null)
      setPreviewThumbLoading(true)
      for (const d of dirsToTry) {
        try {
          const data = await api.getThumbnails(caseId!, previewName, d, 500)
          if (data.success && data.thumbnails) {
            setPreviewFile({ ...file, path: serveFileUrl(caseId!, previewName, d), name: previewName })
            setPreviewThumbs(data.thumbnails)
            setEnlargedPage(-1)
            setPreviewThumbLoading(false)
            return
          }
        } catch (err: unknown) {
          const msg = err instanceof Error ? err.message : ''
          // 检测加密错误
          if (msg.includes('加密') || msg.includes('encrypt') || msg.includes('password')) {
            setPreviewFile({ ...file, path: serveFileUrl(caseId!, previewName, d), name: previewName })
            setPreviewThumbError('该 PDF 文件已加密，无法预览缩略图。文件仍可正常使用，不影响后续处理步骤。')
            setPreviewThumbLoading(false)
            return
          }
          // 其他错误，尝试下一个目录
        }
      }
      // 所有目录都尝试完毕
      setPreviewFile({ ...file, path: serveFileUrl(caseId!, previewName, dirsToTry[0]), name: previewName })
      setPreviewThumbError('所有目录中均未找到该 PDF 文件')
      setPreviewThumbLoading(false)
    } else {
      // Markdown 文件：直接打开文本预览
      const serveUrl = serveFileUrl(caseId!, previewName, dirsToTry[0])
      setPreviewFile({ ...file, path: serveUrl })
      setEnlargedPage(-1)
    }
  }

  const closePreview = () => {
    setPreviewFile(null)
    setPreviewThumbs([])
    setPreviewThumbError(null)
    setEnlargedPage(-1)
  }

  // 切换选中状态
  const toggleSelect = useCallback((id: string) => {
    setFiles(prev => prev.map(f => f.id === id ? { ...f, selected: !f.selected } : f))
  }, [])
  
  // 全选/取消全选
  const toggleSelectAll = useCallback(() => {
    const allSelected = files.every(f => f.selected)
    setFiles(prev => prev.map(f => ({ ...f, selected: !allSelected })))
  }, [files])
  
  // 选中处理的文件（未选中的文件默认选中）
  const getSelectedFiles = useCallback(() => {
    const selected = files.filter(f => f.selected && f.status === 'pending')
    // 如果没有选中的，处理所有 pending 文件
    if (selected.length === 0) return files.filter(f => f.status === 'pending')
    return selected
  }, [files])

  // 删除文件（调用后端真实删除接口）
  const handleRemoveFile = useCallback(async (file: CaseFile) => {
    try {
      await api.deleteFile(caseId!, file.name)
      // 重新加载文件列表
      const filesData = await api.getCaseFiles(caseId!)
      setFiles(filesData.map((f: any) => ({
        id: f.id,
        name: f.name,
        size: f.size,
        status: f.status || 'pending',
        selected: true
      })))
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : '删除失败'
      showAlert({ title: '删除失败', message: errMsg, variant: 'danger' })
    }
  }, [caseId])

  // 删除 MD 文件
  const handleDeleteMd = useCallback(async (mdFileName: string) => {
    const confirmed = await showConfirm({
      title: '确认删除',
      message: `确定要删除「${mdFileName}」吗？删除后可从 PDF 重新转换。`,
      variant: 'danger',
    })
    if (!confirmed) return

    try {
      const result = await api.deleteMdFile(caseId!, mdFileName)
      if (result.success) {
        setProgress(`✅ 已删除 ${mdFileName}`)
        setTimeout(() => setProgress(''), 2000)
        // 重新加载文件列表
        if (currentStep >= 3) {
          const filesData = await api.getStepFiles(caseId!, 3)
          if (Array.isArray(filesData)) {
            setFiles(filesData.map((f: any) => ({
              id: f.id, name: f.name, size: f.size, status: 'pending', source: f.source,
            })))
          }
        }
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : '删除失败'
      showAlert({ title: '删除失败', message: errMsg, variant: 'danger' })
    }
  }, [caseId, currentStep])

  // 删除 PDF 文件（步骤 2）
  const handleDeletePdf = useCallback(async (pdfFileName: string) => {
    const confirmed = await showConfirm({
      title: '确认删除',
      message: `确定要删除「${pdfFileName}」吗？删除后可重新上传。`,
      variant: 'danger',
    })
    if (!confirmed) return

    try {
      const result = await api.deletePdfFile(caseId!, pdfFileName)
      if (result.success) {
        setProgress(`✅ 已删除 ${pdfFileName}`)
        setTimeout(() => setProgress(''), 2000)
        // 重新加载文件列表
        const filesData = await api.getStepFiles(caseId!, 2)
        if (Array.isArray(filesData)) {
          setFiles(filesData.map((f: any) => ({
            id: f.id, name: f.name, size: f.size, status: f.status || 'pending', source: f.source,
          })))
        }
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : '删除失败'
      showAlert({ title: '删除失败', message: errMsg, variant: 'danger' })
    }
  }, [caseId])

  // 删除原始 PDF 文件（步骤 1）
  const handleDeleteOriginal = useCallback(async (pdfFileName: string) => {
    const confirmed = await showConfirm({
      title: '确认删除',
      message: `确定要删除「${pdfFileName}」吗？删除后可重新上传。`,
      variant: 'danger',
    })
    if (!confirmed) return

    try {
      const result = await api.deleteFile(caseId!, pdfFileName)
      if (result.success) {
        setProgress(`✅ 已删除 ${pdfFileName}`)
        setTimeout(() => setProgress(''), 2000)
        // 重新加载步骤 1 文件列表
        const stepFiles = await api.getStepFiles(caseId!, 1)
        if (Array.isArray(stepFiles)) {
          setFiles(stepFiles.map((f: any) => ({
            id: f.id, name: f.name, size: f.size, status: f.status || 'pending', source: f.source,
          })))
        }
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : '删除失败'
      showAlert({ title: '删除失败', message: errMsg, variant: 'danger' })
    }
  }, [caseId])

  // 重新转换 MD（从 PDF 转换）
  const handleReconvertMd = useCallback(async (mdFileName: string) => {
    const pdfName = mdFileName.replace(/\.md$/, '') + '.pdf'
    try {
      setProgress(`正在转换 ${mdFileName}...`)
      const result = await api.convertToMd(caseId!, pdfName)
      if (result.success) {
        setProgress(`✅ 已重新转换 ${mdFileName}`)
        setTimeout(() => setProgress(''), 2000)
        // 刷新文件列表
        if (currentStep >= 3) {
          const filesData = await api.getStepFiles(caseId!, 3)
          if (Array.isArray(filesData)) {
            setFiles(filesData.map((f: any) => ({
              id: f.id, name: f.name, size: f.size, status: 'pending', source: f.source,
            })))
          }
        }
      } else {
        throw new Error(result.error || '转换失败')
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : '转换失败'
      showAlert({ title: '转换失败', message: errMsg, variant: 'danger' })
      setProgress('')
    }
  }, [caseId, currentStep])

  // 批量处理（去水印/转MD）
  const handleBatchProcess = useCallback(async (stepIndex: number) => {
    let pendingFiles = getSelectedFiles()
    // 兜底：如果没有选中任何 pending 文件，从后端重新加载
    if (pendingFiles.length === 0) {
      try {
        const data = await api.getStepFiles(caseId!, stepIndex)
        if (Array.isArray(data) && data.length > 0) {
          setFiles(data.map((f: any) => ({
            id: f.id,
            name: f.name,
            size: f.size,
            status: f.status || 'pending',
            selected: true,
            source: f.source,
          })))
          pendingFiles = data.map((f: any) => ({
            id: f.id,
            name: f.name,
            size: f.size,
            status: f.status || 'pending',
            selected: true,
            source: f.source,
          }))
        }
      } catch (err) {
        setError('加载文件列表失败')
        return
      }
      // 再次过滤（只处理 pending 状态的文件）
      pendingFiles = pendingFiles.filter(f => f.status === 'pending')
      if (pendingFiles.length === 0) {
        setProgress('✅ 所有文件已处理完成，无需重复处理')
        return
      }
    }

    const abortController = new AbortController()
    if (stepIndex === 1) {
      processAbortRef.current = abortController
    }

    setProcessing(true)
    setError(null)
    setProgress(`正在处理 ${pendingFiles.length} 个文件，请稍候...`)

    try {
      // 调用真实后端 API
      const result = await api.batchProcess(caseId!, stepIndex, pendingFiles.map(f => f.name), {
        password: password || undefined,
        dpi: stepIndex === 1 && optEnhance ? enhanceDpi : undefined,
        remove_watermark: stepIndex === 1 ? optWatermark : undefined,
        enhance_resolution: stepIndex === 1 ? optEnhance : undefined,
        delete_original: stepIndex === 1 ? optDeleteOriginal : undefined,
      })

      if (result.results) {
        for (const r of result.results) {
          if (r.success) {
            setFiles(prev => prev.map(f =>
              f.name === r.file ? {
                ...f,
                status: 'done',
                processedPath: stepIndex === 1 ? r.output : undefined,
                mdPath: stepIndex === 2 ? r.output : undefined,
                mdName: stepIndex === 2 ? r.md_name : undefined
              } : f
            ))
          } else {
            setFiles(prev => prev.map(f =>
              f.name === r.file ? { ...f, status: 'error', error: r.error?.substring(0, 100) } : f
            ))
          }
        }
      }

      setProgress(`✅ ${pendingFiles.length} 个文件处理完成！`)
      setCurrentStep(stepIndex + 1)
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        setProgress('已取消处理，请选择是否删除已处理的文件')
        // 显示删除确认对话框
        setError('cancelled_process')
        return
      }
      const errorMsg = err instanceof Error ? err.message : '处理失败'
      setError(errorMsg)
      setProgress('')
    } finally {
      setProcessing(false)
      if (stepIndex === 1) {
        processAbortRef.current = null
      }
    }
  }, [caseId, files, password, optWatermark, optEnhance, optDeleteOriginal, enhanceDpi])

  // 取消正在进行的 PDF 处理
  const handleCancelProcess = useCallback(() => {
    if (processAbortRef.current) {
      processAbortRef.current.abort()
    }
  }, [])

  // 删除步骤1已处理的文件（取消处理后调用）
  const cleanupPartialProcess = useCallback(async () => {
    try {
      const data = await api.cleanupProcessed(caseId!)
      if (data.success) {
        setError(null)
        setProgress('已清理已处理文件，恢复到处理前状态')
        // 重新加载文件列表
        const filesData = await api.getCaseFiles(caseId!)
        if (filesData) {
          setFiles(filesData)
        }
      }
    } catch {
      setError(null)
    } finally {
      setProcessing(false)
    }
  }, [caseId])

  // 步骤 2：一键全部转 MD（跳过拆分）
  // 证据清单状态
  const [evidenceList, setEvidenceList] = useState<any[]>([])
  const [evidenceExtracted, setEvidenceExtracted] = useState(false)
  const [analysisCompleted, setAnalysisCompleted] = useState(false)

  // 6 阶段独立控制（控辩对抗放在最后）
  const STAGES = [
    { num: 1, name: '指控要素', desc: '读取起诉书，提取指控要素' },
    { num: 2, name: '人物关系', desc: '构建人物关系图谱' },
    { num: 3, name: '事件拆解', desc: '梳理事件时间线，拆解事件' },
    { num: 4, name: '法律法规', desc: '梳理涉案法律法规' },
    { num: 5, name: '综合辩护', desc: '证据分析 + 矛盾分析 + 三阶层辩护' },
    { num: 6, name: '控辩对抗', desc: '红蓝对抗，生成攻防对照表' },
  ]
  const [stageStatus, setStageStatus] = useState<Record<number, 'idle' | 'running' | 'completed' | 'error'>>({})
  const [stageMessages, setStageMessages] = useState<Record<number, string>>({})
  const [stageErrors, setStageErrors] = useState<Record<number, string>>({})
  const [runningStage, setRunningStage] = useState<number | null>(null)
  const stageAbortRef = useRef<Record<number, AbortController | null>>({})

  // 证据提取轮询和停止状态
  const extractPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const extractUserStoppedRef = useRef(false)

  // 运行单个阶段
  const handleRunStage = useCallback(async (stageNum: number) => {
    if (!defendant.trim()) {
      showAlert({ title: '提示', message: '案件缺少被告人信息，无法开始分析', variant: 'warning' })
      return
    }

    // 清理该阶段的旧状态
    setStageStatus(prev => ({ ...prev, [stageNum]: 'running' }))
    setStageMessages(prev => ({ ...prev, [stageNum]: `正在执行阶段 ${stageNum}：${STAGES.find(s => s.num === stageNum)?.name}...` }))
    setStageErrors(prev => ({ ...prev, [stageNum]: '' }))
    setRunningStage(stageNum)

    const controller = new AbortController()
    stageAbortRef.current[stageNum] = controller

    try {
      const result = await api.runSingleStage(caseId!, stageNum, defendant, crimeType || undefined)
      if (!result.success) {
        throw new Error(result.detail || result.error || '阶段执行失败')
      }
      setStageStatus(prev => ({ ...prev, [stageNum]: 'completed' }))
      setStageMessages(prev => ({ ...prev, [stageNum]: '' }))
      setRunningStage(null)

      // 检查所有阶段是否完成
      const status = await api.getStageStatus(caseId!)
      const stages = status?.status || {}
      const allDone = [1, 2, 3, 4, 5, 6].every(s => stages[`stage_${s}`]?.completed)
      if (allDone) {
        setAnalysisCompleted(true)
        setProgress('✅ 6 阶段分析全部完成！')
        setTimeout(() => navigate(`/case/${caseId}/report`), 2000)
      }
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        // 用户主动停止，清理部分输出
        await handleClearStage(stageNum)
        setStageStatus(prev => ({ ...prev, [stageNum]: 'idle' }))
        setStageMessages(prev => ({ ...prev, [stageNum]: '' }))
      } else {
        setStageStatus(prev => ({ ...prev, [stageNum]: 'error' }))
        const errorMsg = err instanceof Error ? err.message : '阶段执行失败'
        setStageErrors(prev => ({ ...prev, [stageNum]: errorMsg }))
      }
      setStageMessages(prev => ({ ...prev, [stageNum]: '' }))
      setRunningStage(null)
    }
  }, [caseId, defendant, crimeType, navigate])

  // 停止运行中的阶段
  const handleStopStage = useCallback((stageNum: number) => {
    const controller = stageAbortRef.current[stageNum]
    if (controller) {
      controller.abort()
    }
  }, [])

  // 清理某个阶段的输出
  const handleClearStage = useCallback(async (stageNum: number) => {
    try {
      const res = await fetch(`${API_BASE}/cases/${caseId}/clear-stage/${stageNum}`, { method: 'POST' })
      const data = await res.json()
      if (data.success) {
        setStageStatus(prev => ({ ...prev, [stageNum]: 'idle' }))
        setStageMessages(prev => ({ ...prev, [stageNum]: '' }))
        setStageErrors(prev => ({ ...prev, [stageNum]: '' }))
      }
    } catch { /* 忽略 */ }
  }, [caseId])

  // 查看阶段 Markdown
  const handleViewStage = useCallback(async (stageNum: number) => {
    try {
      const result = await api.getStageMarkdown(caseId!, stageNum)
      const mdContent = result?.content || '无内容'
      showAlert({ title: `${STAGES.find(s => s.num === stageNum)?.name} - 分析结果`, message: mdContent.substring(0, 3000) + (mdContent.length > 3000 ? '\n\n...内容过长，请在报告中查看完整版本' : ''), variant: 'info' })
    } catch { /* 忽略 */ }
  }, [caseId])

  // 步骤 2：仅将 PDF 转 MD（后台异步任务 + 轮询进度）
  const handleConvertAllToMd = useCallback(async () => {
    setProcessing(true)
    setError(null)
    setProgress('正在转换并提取证据...')
    try {
      const result = await fetch(`${API_BASE}/tasks/${caseId}/convert-all-to-md`, { method: 'POST' })
      const data = await result.json()
      if (!result.ok) {
        throw new Error(data.detail || data.error || '转换失败')
      }
      if (!data.success) {
        // 已有运行中的任务
        if (data.status === 'running') {
          setProgress(`转换任务正在运行中：${data.message || ''}`)
        } else {
          throw new Error(data.error || data.detail || '转换失败')
        }
      }

      // 轮询后台任务进度
      const pollInterval = setInterval(async () => {
        try {
          const statusResp = await fetch(`${API_BASE}/tasks/${caseId}/convert-status`)
          const statusData = await statusResp.json()
          const st = statusData.status

          if (st === 'running') {
            const cur = statusData.current || 0
            const tot = statusData.total || 0
            const pct = tot > 0 ? Math.round((cur / tot) * 100) : 0
            setProgress(`转换中：${cur}/${tot} (${pct}%) — ${statusData.message || ''}`)
          } else if (st === 'completed') {
            clearInterval(pollInterval)
            const results = statusData.results || []
            const blankFiles = results.filter((r: any) => !r.success && r.error)
            const successCount = results.filter((r: any) => r.success).length
            const totalCount = statusData.total || 0

            if (blankFiles.length > 0) {
              const names = blankFiles.map((r: any) => r.file).join('、')
              setError(`${blankFiles.length} 个文件转换失败（内容为空）：${names}。PDF 可能已加密，请先进行去水印处理并输入密码。`)
            }

            setProgress(`✅ 已转换 ${successCount}/${totalCount} 个文件${blankFiles.length > 0 ? `，${blankFiles.length} 个失败` : ''}`)
            setCurrentStep(2)

            // 重新加载文件列表
            const filesData = await api.getStepFiles(caseId!, 2)
            if (Array.isArray(filesData)) {
              setFiles(filesData.map((f: any) => ({
                id: f.id, name: f.name, size: f.size, status: f.status || 'done', source: f.source,
              })))
            }

            // 转换完成后自动开始证据提取
            if (successCount > 0) {
              setProgress('正在提取证据清单...')
              try {
                const evResult = await api.extractEvidence(caseId!)
                if (evResult.success && evResult.evidence) {
                  setEvidenceList(evResult.evidence)
                  setEvidenceExtracted(true)
                  setProgress(`✅ 已提取 ${evResult.total_evidence} 份证据，正在自动开始分析...`)

                  // 证据提取完成后自动触发分析
                  if (defendant.trim()) {
                    setTimeout(async () => {
                      try {
                        const analysisResult = await api.runAllStages(caseId!, defendant, crimeType || undefined)
                        if (!analysisResult.success) {
                          throw new Error(analysisResult.detail || analysisResult.error || '分析执行失败')
                        }
                        setProgress('✅ 分析任务已启动，正在跟踪进度...')
                        setProcessing(true)
                        pollAnalysisProgress()
                      } catch (err) {
                        const errorMsg = err instanceof Error ? err.message : '分析触发失败'
                        setError(errorMsg)
                        setProcessing(false)
                      }
                    }, 500)
                  } else {
                    setProgress(`✅ 已提取 ${evResult.total_evidence} 份证据（请补充被告人信息后手动开始分析）`)
                    setProcessing(false)
                  }
                } else {
                  throw new Error(evResult.detail || evResult.error || '提取失败')
                }
              } catch (extractErr) {
                const isStop = extractErr instanceof Error && extractErr.message === '用户已停止提取'
                if (isStop) {
                  setProgress('⏹ 已停止提取，当前已提取的证据已保存')
                  setProcessing(false)
                } else {
                  const errorMsg = extractErr instanceof Error ? extractErr.message : '证据提取失败'
                  setError(errorMsg)
                  setProgress('')
                  setProcessing(false)
                }
              }
            } else {
              setProcessing(false)
            }
          } else if (st === 'failed' || st === 'cancelled') {
            clearInterval(pollInterval)
            throw new Error(statusData.message || '转换任务失败')
          }
          // 'pending' / 'interrupted' 继续轮询
        } catch (pollErr) {
          clearInterval(pollInterval)
          throw pollErr
        }
      }, 2000)

      // 15 分钟超时自动停止轮询（后端 MinerU 转换超时为 1 小时）
      setTimeout(() => {
        clearInterval(pollInterval)
        if (processing) {
          setProgress('⚠️ 转换超时，任务可能仍在后台运行，请稍后刷新页面查看结果')
          setProcessing(false)
        }
      }, 900000)

    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '转换失败'
      setError(errorMsg)
      setProgress('')
      setProcessing(false)
    }
  }, [caseId, processing, defendant, crimeType])

  // 提取证据（独立按钮，转 MD 后手动触发）
  const handleExtractEvidence = useCallback(async () => {
    extractUserStoppedRef.current = false
    if (extractPollRef.current) {
      clearInterval(extractPollRef.current)
      extractPollRef.current = null
    }
    setProcessing(true)
    setError(null)
    setProgress('正在提取证据清单...')
    try {
      // 启动提取任务（不等待返回，用 polling 跟踪进度）
      fetch(`${API_BASE}/cases/${caseId}/extract-evidence`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      }).catch(() => {}) // 忽略错误，由 polling 处理

      // 轮询进度
      extractPollRef.current = setInterval(async () => {
        if (extractUserStoppedRef.current) {
          clearInterval(extractPollRef.current!)
          extractPollRef.current = null
          return
        }
        try {
          const st = await fetch(`${API_BASE}/cases/${caseId}/extract-status`).then(r => r.json())
          if (st.status !== 'running') {
            clearInterval(extractPollRef.current!)
            extractPollRef.current = null
            const data = await api.getEvidenceIndex(caseId!)
            setEvidenceList(data.evidence || [])
            setEvidenceExtracted(true)
            setProcessing(false)
            setProgress(`✅ 已提取 ${data.total_evidence} 份证据，正在自动开始分析...`)
            // 自动触发分析
            if (defendant.trim()) {
              setTimeout(async () => {
                try {
                  const analysisResult = await api.runAllStages(caseId!, defendant, crimeType || undefined)
                  if (!analysisResult.success) throw new Error(analysisResult.detail || analysisResult.error || '分析执行失败')
                  setProgress('✅ 分析任务已启动，正在跟踪进度...')
                  setProcessing(true)
                  pollAnalysisProgress()
                } catch (err) {
                  setError(err instanceof Error ? err.message : '分析触发失败')
                  setProcessing(false)
                }
              }, 500)
            } else {
              setProgress(`✅ 已提取 ${data.total_evidence} 份证据（请补充被告人信息后手动开始分析）`)
            }
          } else {
            const totalFiles = st.total_files || 0
            const processedFiles = st.processed_files || 0
            const currentFile = st.current_file || ''
            const pct = totalFiles > 0 ? Math.round((processedFiles / totalFiles) * 100) : 0
            if (processedFiles > 0 || totalFiles > 0) {
              setProgress(`正在提取证据... ${processedFiles}/${totalFiles} (${pct}%) ${currentFile ? '— ' + currentFile : ''}`)
            }
            const data = await api.getEvidenceIndex(caseId!)
            if (data.total_evidence > 0) setEvidenceList(data.evidence || [])
          }
        } catch {
          clearInterval(extractPollRef.current!)
          extractPollRef.current = null
          setProcessing(false)
        }
      }, 3000)

      // 15 分钟超时
      setTimeout(() => {
        if (extractPollRef.current) {
          clearInterval(extractPollRef.current)
          extractPollRef.current = null
          setProcessing(false)
          setProgress('⚠️ 提取超时，任务可能仍在后台运行，请稍后刷新页面查看结果')
        }
      }, 900000)
    } catch (err) {
      // 后端返回 400：无 MD 文件，询问用户是否转换
      if (err instanceof Error && (err.message.includes('无 MD 文件') || err.message.includes('请先完成 PDF 转 MD'))) {
        setProcessing(false)
        setProgress('')
        const confirmed = await showConfirm({
          title: '需要先进行文件转换',
          message: '当前案件中还没有转换后的文件，需要先进行 PDF 转 MD 才能提取证据。\n是否立即开始转换？',
          confirmText: '开始转换',
          cancelText: '取消',
          variant: 'info',
        })
        if (confirmed) {
          handleConvertAllToMd()
        }
        return
      }
      const isStop = err instanceof Error && err.message === '用户已停止提取'
      if (isStop) {
        setProgress('⏹ 已停止提取，当前已提取的证据已保存')
      } else {
        const errorMsg = err instanceof Error ? err.message : '证据提取失败'
        setError(errorMsg)
        setProgress('')
      }
      setProcessing(false)
    }
  }, [caseId, defendant, crimeType, handleConvertAllToMd])
  const handleStopExtract = useCallback(() => {
    extractUserStoppedRef.current = true
    if (extractPollRef.current) {
      clearInterval(extractPollRef.current)
      extractPollRef.current = null
    }
    api.stopExtractEvidence()
    setProgress('⏹ 已停止提取，当前已提取的证据已保存')
    setProcessing(false)
  }, [])

  // 清除证据
  const handleClearEvidence = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/cases/${caseId}/clear-evidence`, { method: 'POST' })
      const data = await res.json()
      if (data.success) {
        setEvidenceList([])
        setEvidenceExtracted(false)
        setProgress('✅ 证据已清除')
        setTimeout(() => setProgress(''), 2000)
      } else {
        throw new Error(data.detail || data.error || '清除失败')
      }
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '清除证据失败'
      setError(errorMsg)
    }
  }, [caseId])

  // 步骤 3：执行 5 阶段分析
  const handleRunAnalysis = useCallback(async () => {
    if (!defendant.trim()) {
      showAlert({ title: '提示', message: '案件缺少被告人信息，无法开始分析', variant: 'warning' })
      return
    }

    await startAnalysis()
  }, [caseId, defendant])

  // 触发分析（异步，立即返回）
  const startAnalysis = useCallback(async () => {
    setError(null)
    setProgress('正在触发 5 阶段分析...')
    setProcessing(true)
    try {
      const result = await api.runAllStages(caseId!, defendant, crimeType || undefined)
      if (!result.success) {
        throw new Error(result.detail || result.error || '触发失败')
      }
      setProgress('✅ 分析任务已启动，请稍候...')
      // 开始轮询进度
      pollAnalysisProgress()
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '触发失败'
      setError(errorMsg)
      setProgress('')
      setProcessing(false)
    }
  }, [caseId, defendant, crimeType])

  // 轮询分析进度
  const pollAnalysisProgress = useCallback(() => {
    let pollCount = 0
    const maxPolls = 600 // 最多轮询 600 次 * 3s = 30 分钟
    const pollInterval = setInterval(async () => {
      pollCount++
      if (pollCount > maxPolls) {
        clearInterval(pollInterval)
        setProcessing(false)
        setProgress('')
        setError('分析耗时过长，请检查后端是否正常运行')
        return
      }

      try {
        // 先查实时进度消息
        const progress = await api.getStageProgress(caseId!)
        if (progress.running) {
          const msg = progress.message || ''
          setProgress(`${msg}`)
          return // 仍在运行，继续轮询
        }

        // 不在运行了，查最终状态
        const status = await api.getStageStatus(caseId!)
        const taskState = status?.task?.status
        if (taskState === 'completed') {
          clearInterval(pollInterval)
          setAnalysisCompleted(true)
          setProgress('✅ 6 阶段分析全部完成！')
          setProcessing(false)
          setTimeout(() => navigate(`/case/${caseId}/report`), 1500)
        } else if (taskState === 'error') {
          clearInterval(pollInterval)
          setError(status?.task?.error || '分析出错')
          setProgress('')
          setProcessing(false)
        } else if (taskState === 'idle' || !taskState) {
          // 没有任务状态，但 stage_53 可能已存在（手动单步跑的情况）
          const stageStatus = status?.status || {}
          if (stageStatus.stage_53?.completed) {
            clearInterval(pollInterval)
            setAnalysisCompleted(true)
            setProgress('✅ 6 阶段分析全部完成！')
            setProcessing(false)
            setTimeout(() => navigate(`/case/${caseId}/report`), 1500)
          } else if (pollCount < 10) {
            // 刚触发，等一会儿
            return
          } else {
            // 没找到任务也没找到完成状态
            clearInterval(pollInterval)
            setProcessing(false)
            setProgress('')
            setError('未检测到分析任务，请手动点击"开始 5 阶段分析"')
          }
        }
      } catch {
        // 轮询出错，继续尝试
      }
    }, 3000) // 每 3 秒轮询一次

    // 保存 interval ID，组件卸载时清理
    return () => clearInterval(pollInterval)
  }, [caseId, navigate])

  const processAbortRef = useRef<AbortController | null>(null)  // PDF 处理中止控制器

  // 步骤 4：案卷分析 - 5 步流水线
  const PIPELINE_STEPS = [
    { num: 1, name: '合并笔录', desc: '按人名+类型合并笔录' },
    { num: 2, name: '逐次总结', desc: '每次笔录单独 LLM 总结' },
    { num: 3, name: '矛盾分析', desc: '多次笔录者对比差异' },
    { num: 4, name: '案件 Wiki', desc: 'LLM Wiki 模式构建证据知识库' },
    { num: 4.5, name: '控辩对抗', desc: '红蓝对抗，生成攻防对照表' },
    { num: 5, name: '辩护意见', desc: '综合前 4 步形成辩护意见' },
  ]

  // Wiki 浏览状态
  const [wikiPages, setWikiPages] = useState<Array<{path: string; filename: string}>>([])
  const [selectedWikiPage, setSelectedWikiPage] = useState<string>('')
  const [wikiContent, setWikiContent] = useState('')
  const [wikiLoading, setWikiLoading] = useState(false)

  // 证据浏览器状态
  const [evidenceSummaries, setEvidenceSummaries] = useState<Array<{name: string; files: Array<{name: string; displayName: string}>}>>([])
  const [evidenceOther, setEvidenceOther] = useState<Array<{name: string; dir: string}>>([])
  const [evidenceContent, setEvidenceContent] = useState('')
  const [evidenceLoading, setEvidenceLoading] = useState(false)
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set(['讯问笔录']))
  const [expandedEvidenceGroups, setExpandedEvidenceGroups] = useState<Set<string>>(new Set())

  // 分析卡片状态
  const [selectedAnalysisCard, setSelectedAnalysisCard] = useState<number>(0)
  const [evidenceAnalysisFiles, setEvidenceAnalysisFiles] = useState<string[]>([])
  const [selectedEvidenceAnalysis, setSelectedEvidenceAnalysis] = useState<string>('')
  const [contradictionFilesList, setContradictionFilesList] = useState<Array<{filename: string; displayName: string}>>([])
  const [selectedContradictionFile, setSelectedContradictionFile] = useState<string>('')
  const [analysisContent, setAnalysisContent] = useState('')

  const executePipelineStep = useCallback(async (step: number) => {
    if (!defendant.trim()) {
      showAlert({ title: '提示', message: '案件缺少被告人信息，无法开始分析', variant: 'warning' })
      return
    }
    setPipelineRunning(true)
    setCurrentPipelineStep(step)
    setError(null)
    setProgress(`${PIPELINE_STEPS[step - 1].name} 执行中...`)
    setLiveProgress(null)

    try {
      const result = await api.runPipelineStep(caseId!, step, defendant, crimeType || undefined)
      if (!result.success) {
        throw new Error(result.detail || result.error || `步骤 ${step} 执行失败`)
      }
      setStepResults(prev => ({ ...prev, [step]: result.data }))
      setPipelineStatus(prev => ({ ...prev, [step]: true }))
      setProgress('')
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : `步骤 ${step} 执行失败`
      setError(errorMsg)
      setProgress('')
      throw err  // Re-throw so caller (executeAllSteps) knows it failed
    } finally {
      setPipelineRunning(false)
      setCurrentPipelineStep(0)
      setLiveProgress(null)
    }
  }, [caseId, defendant, crimeType])

  const executeAllSteps = useCallback(async () => {
    if (!defendant.trim()) {
      showAlert({ title: '提示', message: '案件缺少被告人信息，无法开始分析', variant: 'warning' })
      return
    }
    for (const step of [1, 2, 3, 4, 5]) {
      if (!pipelineStatus[step]) {
        setPipelineRunning(true)
        setCurrentPipelineStep(step)
        setError(null)
        setProgress(`${PIPELINE_STEPS[step - 1].name} 执行中...`)
        setLiveProgress(null)
        try {
          const result = await api.runPipelineStep(caseId!, step, defendant, crimeType || undefined)
          if (!result.success) {
            throw new Error(result.detail || result.error || `步骤 ${step} 执行失败`)
          }
          setStepResults(prev => ({ ...prev, [step]: result.data }))
          setPipelineStatus(prev => ({ ...prev, [step]: true }))
          setProgress('')
          setLiveProgress(null)
          // 步骤 5 完成后自动跳转到报告页面
          if (step === 5) {
            navigate(`/case/${caseId}/report`)
          }
        } catch (err) {
          const errorMsg = err instanceof Error ? err.message : `步骤 ${step} 执行失败`
          setError(errorMsg)
          setProgress('')
          setPipelineRunning(false)
          setCurrentPipelineStep(0)
          setLiveProgress(null)
          return  // Stop execution on error
        }
      }
    }
    setPipelineRunning(false)
    setCurrentPipelineStep(0)
    setLiveProgress(null)
  }, [caseId, defendant, crimeType, pipelineStatus, navigate])

  // 执行下一个未完成的步骤
  const executeNextStep = useCallback(async () => {
    for (const step of [1, 2, 3, 4, 5]) {
      if (!pipelineStatus[step]) {
        await executePipelineStep(step)
        return
      }
    }
  }, [pipelineStatus, executePipelineStep])

  // 执行指定步骤（单步）
  const executeSingleStep = useCallback(async (step: number) => {
    if (pipelineStatus[step] || pipelineRunning) return
    try {
      await executePipelineStep(step)
    } catch (err) {
      console.error('步骤执行失败:', err)
    }
  }, [pipelineStatus, pipelineRunning, executePipelineStep])

  // 从断点恢复
  const handleResumeAnalysis = useCallback(async () => {
    if (!defendant.trim()) {
      showAlert({ title: '提示', message: '案件缺少被告人信息，无法继续分析', variant: 'warning' })
      return
    }
    setPipelineRunning(true)
    setError(null)
    setProgress('从断点恢复分析...')
    setLiveProgress(null)

    try {
      const result = await api.resumePipeline(caseId!, defendant, crimeType || undefined)
      if (result.success) {
        if (result.all_done) {
          setProgress('所有步骤已完成')
          navigate(`/case/${caseId}/report`)
        } else {
          setProgress(`步骤 ${result.step} 已完成`)
          // 刷新状态
          api.getPipelineStatus(caseId!).then(st => {
            const newStatus: Record<number | string, boolean> = {}
            for (const [key, val] of Object.entries(st.status || {})) {
              const num = parseFloat(key.replace('step_', ''))
              if ((val as any)?.completed) newStatus[num] = true
            }
            setPipelineStatus(prev => ({ ...prev, ...newStatus }))
          })
          api.getAnalysisState(caseId!).then(st => {
            if (st.state) {
              setAnalysisState(st.state)
              setNextStep(st.next_step)
            }
          })
          if (!result.next_step) {
            navigate(`/case/${caseId}/report`)
          }
        }
      } else {
        throw new Error(result.detail || result.error || '断点恢复失败')
      }
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '断点恢复失败'
      setError(errorMsg)
    } finally {
      setPipelineRunning(false)
      setCurrentPipelineStep(0)
      setLiveProgress(null)
    }
  }, [caseId, defendant, crimeType, navigate])

  // 加载已有流水线状态
  const loadPipelineState = useCallback(async () => {
    if (!caseId) return
    try {
      const statusData = await api.getPipelineStatus(caseId)
      const status: Record<number, boolean> = {}
      for (let i = 1; i <= 5; i++) {
        if (statusData.status?.[`step_${i}`]?.completed) {
          status[i] = true
        }
      }
      setPipelineStatus(status)

      // 加载已有步骤结果
      const results: Record<number, any> = {}
      for (let i = 1; i <= 5; i++) {
        if (status[i]) {
          try {
            const r = await api.getStepResult(caseId, i)
            results[i] = r
          } catch { /* ignore */ }
        }
      }
      setStepResults(results)
    } catch { /* ignore */ }
  }, [caseId])

  // 切换到步骤 4 时加载流水线状态
  useEffect(() => {
    if (currentStep === 3 && caseId) {
      loadPipelineState()
    }
  }, [currentStep, caseId, loadPipelineState])

  // 流水线运行时轮询 LLM 实时进度（步骤 2+）
  useEffect(() => {
    if (!pipelineRunning || !caseId || currentPipelineStep < 2) return
    const timer = setInterval(async () => {
      try {
        const p = await api.getPipelineProgress(caseId)
        if (p.running) {
          setLiveProgress({
            message: p.message,
            current: p.current,
            total: p.total,
            elapsed: p.elapsed_seconds || 0,
          })
        }
      } catch { /* ignore polling errors */ }
    }, 2000)
    return () => clearInterval(timer)
  }, [pipelineRunning, caseId, currentPipelineStep])

  // 步骤 4 完成时加载 Wiki 目录
  const loadWikiPages = useCallback(async () => {
    if (!caseId) return
    try {
      const data = await api.getWikiIndex(caseId)
      if (data.pages && data.pages.length > 0) {
        setWikiPages(data.pages)
        // 默认选中第一个页面
        setSelectedWikiPage(data.pages[0].path)
      }
    } catch { /* ignore */ }
  }, [caseId])

  // 加载 Wiki 页面内容
  const loadWikiPage = useCallback(async (path: string) => {
    if (!caseId || !path) return
    setWikiLoading(true)
    try {
      const data = await api.getWikiPage(caseId, path)
      setWikiContent(data.content || '')
    } catch {
      setWikiContent('加载失败')
    } finally {
      setWikiLoading(false)
    }
  }, [caseId])

  useEffect(() => {
    if (currentStep === 3 && pipelineStatus[4]) {
      loadWikiPages()
    }
  }, [currentStep, pipelineStatus, loadWikiPages])

  useEffect(() => {
    if (selectedWikiPage) {
      loadWikiPage(selectedWikiPage)
    }
  }, [selectedWikiPage, loadWikiPage])

  // 步骤 4 完成时加载证据列表
  const loadEvidenceData = useCallback(async () => {
    if (!caseId) return
    try {
      const [summariesData, otherData] = await Promise.all([
        api.getEvidenceSummaries(caseId),
        api.getEvidenceOther(caseId),
      ])
      if (summariesData.categories) setEvidenceSummaries(summariesData.categories)
      if (otherData.files) setEvidenceOther(otherData.files)
    } catch { /* ignore */ }
  }, [caseId])

  // 加载证据分析文件列表和矛盾分析文件列表
  const loadEvidenceAnalysisFiles = useCallback(async () => {
    if (!caseId) return
    try {
      const indexData = await api.getWikiIndex(caseId)
      const analysisFiles = indexData.pages
        ?.filter((p: any) => p.path.startsWith('03-证据分析/'))
        .map((p: any) => p.path) || []
      setEvidenceAnalysisFiles(analysisFiles)
      if (analysisFiles.length > 0) setSelectedEvidenceAnalysis(analysisFiles[0])
    } catch { /* ignore */ }
    try {
      const contrData = await api.getContradictionFiles(caseId)
      if (contrData.files && contrData.files.length > 0) {
        setContradictionFilesList(contrData.files)
        setSelectedContradictionFile(contrData.files[0].filename)
      }
    } catch { /* ignore */ }
  }, [caseId])

  // 加载分析内容
  const loadAnalysisContent = useCallback(async (type: number, subPath?: string) => {
    if (!caseId) return
    setAnalysisContent('')
    try {
      let path = ''
      switch (type) {
        case 0: path = '01-指控要素.md'; break
        case 1: path = subPath || ''; break
        case 2:
          // 矛盾分析：从 contradiction 目录读取
          if (selectedContradictionFile && contradictionFilesList.length > 0) {
            const data = await api.getContradictionContent(caseId, selectedContradictionFile)
            setAnalysisContent(data.content || '')
            return
          }
          path = '05-矛盾记录.md'; break
        case 3: path = '04-法律依据/适用法条.md'; break
        case 4: path = ''; break
      }
      if (!path && type !== 4) return
      if (type === 4) {
        // 辩护意见从步骤 5 结果读取
        const step5 = await api.getStepResult(caseId, 5)
        setAnalysisContent(step5.full_report || step5.defense_opinion || '无内容')
        return
      }
      const data = await api.getWikiPage(caseId, path)
      setAnalysisContent(data.content || '')
    } catch {
      setAnalysisContent('加载失败')
    }
  }, [caseId])

  // 加载证据内容
  const loadEvidenceContent = useCallback(async (category: string, filename: string, dir?: string) => {
    if (!caseId) return
    setEvidenceContent('')
    setEvidenceLoading(true)
    try {
      if (dir) {
        // PDF 文件，设置 URL 用于预览
        setEvidenceContent(`__pdf__:${api.serveFileUrl(caseId, filename, dir)}`)
      } else {
        // MD 总结文件
        const data = await api.getSummaryContent(caseId, category, filename)
        setEvidenceContent(data.content || '')
      }
    } catch {
      setEvidenceContent('加载失败')
    } finally {
      setEvidenceLoading(false)
    }
  }, [caseId])

  // 步骤 4 完成后加载证据数据和分析文件列表
  useEffect(() => {
    if (pipelineStatus[4]) {
      loadEvidenceData()
      loadEvidenceAnalysisFiles()
    }
  }, [pipelineStatus[4], loadEvidenceData, loadEvidenceAnalysisFiles])

  // 分析卡片切换时加载内容
  useEffect(() => {
    if (selectedAnalysisCard >= 0 && selectedAnalysisCard <= 4 && pipelineStatus[4]) {
      loadAnalysisContent(selectedAnalysisCard, selectedEvidenceAnalysis)
    }
  }, [selectedAnalysisCard, selectedEvidenceAnalysis, selectedContradictionFile, contradictionFilesList, pipelineStatus[4], loadAnalysisContent])

  // 步骤结果渲染辅助函数
  const renderStepResultText = (analysis: string) => {
    return (
      <div style={{
        maxHeight: '300px', overflow: 'auto', padding: '12px',
        background: 'var(--macos-bg-tertiary)', borderRadius: '8px', fontSize: '12px', lineHeight: '1.6',
        whiteSpace: 'pre-wrap'
      }}>
        {analysis}
      </div>
    )
  }

  // 开始处理
  const handleStart = useCallback(async () => {
    console.log('[handleStart] currentStep:', currentStep, 'files:', files.length, 'optWatermark:', optWatermark, 'optEnhance:', optEnhance, 'password:', password, 'processing:', processing)
    if (currentStep === 0) {
      if (files.length === 0) return
      setCurrentStep(1)
    } else if (currentStep === 1) {
      // PDF 处理：检查是否至少选了一个选项
      if (!optWatermark && !optEnhance) {
        showAlert({ title: '提示', message: '请至少选择一个处理选项', variant: 'warning' })
        return
      }
      // 检查去水印选项是否开启但未输入密码
      if (optWatermark && !password) {
        const confirmed = await new Promise<boolean>((resolve) => {
          showConfirm({
            title: '未输入密码',
            message: '已开启去水印选项但未输入密码。如果 PDF 文件已加密，处理将会失败。\n\n是否继续尝试处理？',
            confirmText: '继续处理',
            cancelText: '取消',
            variant: 'warning',
            onConfirm: () => resolve(true),
            onCancel: () => resolve(false),
          })
        })
        if (!confirmed) return
      }
      // 重新从后端加载文件列表，确保状态是最新的
      try {
        const stepFiles = await api.getStepFiles(caseId!, 1)
        console.log('[PDF处理] step-files from backend:', JSON.stringify(stepFiles))
        if (Array.isArray(stepFiles) && stepFiles.length > 0) {
          const processedFiles: CaseFile[] = stepFiles.map((f: any) => ({
            id: f.id,
            name: f.name,
            size: f.size,
            status: f.status === 'done' ? 'done' as const : 'pending' as const,
            selected: f.status !== 'done',
            source: f.source,
          }))
          setFiles(processedFiles)

          // 只处理 pending 的文件
          const filesToProcess = processedFiles.filter((f: any) => f.status === 'pending')
          console.log('[PDF处理] filesToProcess:', filesToProcess.map((f: any) => f.name))
          if (filesToProcess.length === 0) {
            setProgress('✅ 所有文件已处理完成')
            return
          }

          // 直接调用 API，不依赖 handleBatchProcess 的闭包
          setProcessing(true)
          setProgress(`正在处理 ${filesToProcess.length} 个文件，请稍候...`)

          const result = await api.batchProcess(caseId!, 1, filesToProcess.map((f: any) => f.name), {
            password: password || undefined,
            remove_watermark: true,
          })

          if (result.results) {
            setFiles(prev => prev.map(f => {
              const r = result.results?.find((r: any) => r.file === f.name)
              if (r?.success) {
                return { ...f, status: 'done' as const, processedPath: r.output }
              } else if (r?.error) {
                return { ...f, status: 'error' as const, error: r.error.substring(0, 100) }
              }
              return f
            }))
          }
          setProgress(`✅ ${filesToProcess.length} 个文件处理完成！`)
          setCurrentStep(2)
        } else {
          setError('没有找到可处理的文件')
        }
      } catch (err) {
        console.error('[PDF处理] error:', err)
        const errorMsg = err instanceof Error ? err.message : '处理失败'
        setError(errorMsg)
        setProgress('')
      } finally {
        setProcessing(false)
      }
    } else if (currentStep === 2) {
      // 转MD：一键全部转换
      handleConvertAllToMd()
    } else if (currentStep === 3) {
      // 案卷分析：检查是否已提取证据，未提取则引导用户先提取
      if (!evidenceExtracted) {
        const confirmed = await showConfirm({
          title: '尚未提取证据',
          message: '查看报告前需要先提取证据清单，是否立即提取？',
          confirmText: '提取证据',
          cancelText: '取消',
          variant: 'info',
        })
        if (confirmed) {
          handleExtractEvidence()
        }
        return
      }
      navigate(`/case/${caseId}/report`)
    }
  }, [currentStep, files, handleBatchProcess, handleConvertAllToMd, handleRunAnalysis, optWatermark, optEnhance, optDeleteOriginal, password])

  const StepIcon = steps[currentStep]?.icon || FileText
  const pendingCount = files.filter(f => f.status === 'pending').length
  const doneCount = files.filter(f => f.status === 'done').length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--macos-bg-primary)', overflow: 'hidden' }}>
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* 左侧：案件步骤导航 */}
        <div style={{ width: '200px', background: 'var(--macos-bg-secondary)', borderRight: '1px solid var(--macos-border)', padding: '16px' }}>
          {/* 返回案件管理 */}
          <button
            onClick={() => navigate('/')}
            style={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
              padding: '8px 12px',
              marginBottom: '12px',
              background: 'rgba(0, 122, 255, 0.1)',
              border: 'none',
              borderRadius: '6px',
              cursor: 'pointer',
              fontSize: '12px',
              color: '#1e3a5f',
              fontWeight: '500'
            }}
          >
            ← 案件管理
          </button>
          <div style={{ fontSize: '11px', fontWeight: '600', color: 'var(--macos-text-tertiary)', textTransform: 'uppercase', marginBottom: '12px', padding: '0 8px' }}>
            案件：{caseName}
          </div>
          <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)', marginBottom: '16px', padding: '0 8px' }}>
            被告人：{defendant}
          </div>
          
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {steps.map((step, index) => {
              const Icon = step.icon
              const isActive = index === currentStep
              const isDone = index < currentStep
              
              return (
                <button
                  key={step.id}
                  onClick={() => setCurrentStep(index)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '8px 12px',
                    background: isActive ? 'rgba(0,122,255,0.1)' : 'transparent',
                    border: 'none',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    textAlign: 'left',
                    fontSize: '13px',
                    color: isActive ? '#1e3a5f' : 'var(--macos-text-primary)'
                  }}
                >
                  {isDone ? (
                    <CheckCircle className="w-4 h-4" color="#2d8f3d" />
                  ) : (
                    <Icon className="w-4 h-4" color={isActive ? '#1e3a5f' : '#86868b'} />
                  )}
                  <span>{step.name}</span>
                </button>
              )
            })}
          </div>
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <MacOSToolbar title={caseName}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginRight: '16px' }}>
              <span style={{ fontSize: '13px', color: 'var(--macos-text-secondary)' }}>被告人：{defendant}</span>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              {currentStep === 0 && (
                <MacOSButton variant="primary" icon={uploading ? Loader2 : Upload} disabled={uploading} onClick={() => document.getElementById('case-upload')?.click()}>
                  {uploading ? '上传中...' : '添加文件'}
                </MacOSButton>
              )}
              <MacOSButton
                variant="primary"
                icon={processing ? Loader2 : StepIcon}
                disabled={processing || (currentStep === 0 && files.length === 0)}
                onClick={async () => {
                  if (currentStep === 0) {
                    if (files.length === 0) return
                    setCurrentStep(1)
                  } else if (currentStep === 1) {
                    if (!optWatermark && !optEnhance) {
                      showAlert({ title: '提示', message: '请至少选择一个处理选项', variant: 'warning' })
                      return
                    }
                    setProcessing(true)
                    setProgress('正在处理文件，请稍候...')
                    try {
                      const filesToProcess = files.filter(f => f.status === 'pending')
                      if (filesToProcess.length === 0) {
                        setProgress('✅ 所有文件已处理完成')
                        setProcessing(false)
                        return
                      }
                      const result = await api.batchProcess(caseId!, 1, filesToProcess.map(f => f.name), {
                        password: password || undefined,
                        remove_watermark: optWatermark,
                        enhance_resolution: optEnhance,
                        delete_original: optDeleteOriginal,
                      })
                      if (result.results) {
                        const allDone = result.results.every((r: any) => r.success)
                        if (allDone) {
                          setProgress(`✅ ${result.results.length} 个文件处理完成！`)
                          setCurrentStep(2)
                        } else {
                          const failed = result.results.find((r: any) => !r.success)
                          if (failed) {
                            setError(`处理失败：${failed.error}`)
                          }
                          setProgress('')
                        }
                      }
                    } catch (err) {
                      setError(err instanceof Error ? err.message : '处理失败')
                      setProgress('')
                    } finally {
                      setProcessing(false)
                    }
                  } else if (currentStep === 2) {
                    handleConvertAllToMd()
                  } else if (currentStep === 3) {
                    // 检查是否已提取证据
                    if (!evidenceExtracted) {
                      handleExtractEvidence()
                    } else {
                      navigate(`/case/${caseId}/report`)
                    }
                  }
                }}
              >
                {processing ? '处理中...' :
                 currentStep === 0 ? '开始处理' :
                 currentStep === 2 ? '全部转换' :
                 currentStep === 3 ? (!evidenceExtracted ? '提取证据' : '查看报告') :
                 `开始${steps[currentStep]?.name} (${getSelectedFiles().length} 个)`}
              </MacOSButton>
            </div>
          </MacOSToolbar>

          {error && (
            <div style={{ padding: '12px 20px', background: 'rgba(255, 59, 48, 0.1)', borderBottom: '1px solid var(--macos-border)', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <AlertCircle className="w-4 h-4" style={{ color: 'var(--macos-danger)' }} />
              <span style={{ color: 'var(--macos-danger)', fontSize: '13px' }}>{error}</span>
              <button onClick={() => setError(null)} style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', fontSize: '18px' }}>×</button>
            </div>
          )}

          {/* 取消处理后的确认对话框：选择是否删除已处理文件 */}
          {error === 'cancelled_process' && (
            <div style={{
              position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
              backdropFilter: 'blur(12px) saturate(180%)',
              WebkitBackdropFilter: 'blur(12px) saturate(180%)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10001
            }}>
              <div style={{
                background: 'rgba(255, 255, 255, 0.72)',
                backdropFilter: 'blur(20px) saturate(180%)',
                WebkitBackdropFilter: 'blur(20px) saturate(180%)',
                borderRadius: '12px',
                padding: '24px', maxWidth: '400px', width: '90vw',
                boxShadow: '0 20px 60px rgba(0,0,0,0.3)'
              }}>
                <h3 style={{ fontSize: '16px', fontWeight: '600', marginBottom: '8px' }}>
                  已取消处理
                </h3>
                <p style={{ fontSize: '13px', color: 'var(--macos-text-secondary)', marginBottom: '20px', lineHeight: '1.5' }}>
                  是否删除已经处理的文件？<br/>
                  <span style={{ fontSize: '12px', color: '#86868b' }}>
                    选择"是"将清除 processed/ 中已生成的文件，回到处理前状态；<br/>
                    选择"否"则保留已处理的文件。
                  </span>
                </p>
                <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
                  <button onClick={() => { setError(null); setProcessing(false) }} style={{
                    padding: '8px 16px', borderRadius: '8px', border: '1px solid #d1d1d6',
                    background: 'transparent', color: '#86868b', fontSize: '13px',
                    cursor: 'pointer'
                  }}>保留文件</button>
                  <button onClick={cleanupPartialProcess} style={{
                    padding: '8px 16px', borderRadius: '8px', border: 'none',
                    background: '#ff3b30', color: '#fff', fontSize: '13px',
                    cursor: 'pointer'
                  }}>删除已处理文件</button>
                </div>
              </div>
            </div>
          )}

          <div style={{ flex: 1, overflow: 'auto', padding: '24px' }}>
            {/* 步骤进度 */}
            <MacOSCard style={{ marginBottom: '20px' }}>
              <div style={{ display: 'flex', alignItems: 'center' }}>
                {steps.map((step, index) => {
                  const Icon = step.icon
                  const isActive = index === currentStep
                  const isDone = index < currentStep
                  
                  return (
                    <div key={step.id} style={{ display: 'flex', alignItems: 'center', flex: 1 }}>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1 }}>
                        <div style={{
                          width: '40px',
                          height: '40px',
                          borderRadius: '50%',
                          background: isDone ? 'rgba(52, 199, 89, 0.1)' : isActive ? 'rgba(0, 122, 255, 0.1)' : 'var(--macos-bg-secondary)',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          marginBottom: '6px'
                        }}>
                          {isDone ? (
                            <CheckCircle className="w-5 h-5" color="#2d8f3d" />
                          ) : (
                            <Icon className="w-5 h-5" color={isActive ? '#1e3a5f' : '#86868b'} />
                          )}
                        </div>
                        <div style={{ fontSize: '11px', fontWeight: isActive ? '600' : '400', color: isActive ? '#1e3a5f' : '#6e6e73', textAlign: 'center' }}>
                          {step.name}
                        </div>
                      </div>
                      
                      {index < steps.length - 1 && (
                        <div style={{
                          flex: 1,
                          height: '2px',
                          background: index < currentStep ? '#2d8f3d' : 'var(--macos-border)',
                          marginBottom: '20px'
                        }} />
                      )}
                    </div>
                  )
                })}
              </div>
            </MacOSCard>

            {/* 当前步骤说明 */}
            <MacOSCard style={{ marginBottom: '16px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
                <div>
                  <h3 style={{ fontSize: '16px', fontWeight: '600', marginBottom: '4px' }}>{steps[currentStep]?.name}</h3>
                  <p style={{ fontSize: '13px', color: 'var(--macos-text-secondary)' }}>{steps[currentStep]?.description}</p>
                  {currentStep >= 1 && (
                    <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--macos-text-tertiary)' }}>
                      📂 数据来源：{
                        currentStep === 1 ? '原始文件 (original/) → 处理后 (processed/)' :
                        currentStep === 3 ? '证据目录 (evidence/) + MD 文件' :
                        'MD 文件 (md/)'
                      }
                    </div>
                  )}
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: '12px', color: 'var(--macos-text-tertiary)' }}>案件</div>
                  <div style={{ fontSize: '14px', fontWeight: '500' }}>{caseName}</div>
                  <div style={{ fontSize: '12px', color: 'var(--macos-text-tertiary)' }}>被告人：{defendant}</div>
                </div>
              </div>

              {currentStep === 1 && files.length > 0 && (
                <div style={{ marginTop: '12px' }}>
                  <div style={{ fontSize: '13px', fontWeight: '500', marginBottom: '12px' }}>处理选项</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                    {/* 去水印 */}
                    <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', cursor: 'pointer', padding: '12px', background: optWatermark ? 'rgba(0,122,255,0.05)' : 'var(--macos-bg-secondary)', borderRadius: '8px', border: optWatermark ? '1px solid rgba(0,122,255,0.3)' : '1px solid transparent' }}>
                      <input type="checkbox" checked={optWatermark} onChange={(e) => setOptWatermark(e.target.checked)} style={{ marginTop: '2px', accentColor: '#1e3a5f' }} />
                      <div>
                        <div style={{ fontSize: '13px', fontWeight: '500' }}>去水印 / 密码</div>
                        <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)' }}>移除 PDF 水印和加密保护，输出干净文件</div>
                      </div>
                    </label>
                    {/* 密码输入框 */}
                    {optWatermark && (
                      <div style={{ marginLeft: '32px', marginTop: '-4px' }}>
                        <input
                          type="password"
                          value={password}
                          onChange={(e) => setPassword(e.target.value)}
                          placeholder="PDF 密码（如有）"
                          style={{
                            width: '100%',
                            padding: '8px 12px',
                            border: '1px solid var(--macos-border)',
                            borderRadius: '6px',
                            fontSize: '13px'
                          }}
                        />
                      </div>
                    )}
                    {/* 精度提升 */}
                    <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', cursor: 'pointer', padding: '12px', background: optEnhance ? 'rgba(0,122,255,0.05)' : 'var(--macos-bg-secondary)', borderRadius: '8px', border: optEnhance ? '1px solid rgba(0,122,255,0.3)' : '1px solid transparent' }}>
                      <input type="checkbox" checked={optEnhance} onChange={(e) => setOptEnhance(e.target.checked)} style={{ marginTop: '2px', accentColor: '#1e3a5f' }} />
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: '13px', fontWeight: '500' }}>精度提升</div>
                        <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)', marginBottom: optEnhance ? '8px' : '0' }}>提高 PDF 图片分辨率，适用于低分辨率扫描件</div>
                        {optEnhance && (
                          <div style={{ display: 'flex', gap: '8px' }}>
                            {[200, 300, 400, 600].map(dpi => (
                              <button
                                key={dpi}
                                onClick={() => setEnhanceDpi(dpi)}
                                style={{
                                  padding: '4px 12px',
                                  background: enhanceDpi === dpi ? '#1e3a5f' : 'rgba(142,142,147,0.12)',
                                  color: enhanceDpi === dpi ? '#fff' : 'var(--macos-text-primary)',
                                  border: 'none',
                                  borderRadius: '6px',
                                  cursor: 'pointer',
                                  fontSize: '12px',
                                  fontWeight: '500'
                                }}
                              >{dpi} dpi</button>
                            ))}
                          </div>
                        )}
                      </div>
                    </label>
                    {/* 删除原始文件 */}
                    <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', cursor: 'pointer', padding: '12px', background: optDeleteOriginal ? 'rgba(255,59,48,0.05)' : 'var(--macos-bg-secondary)', borderRadius: '8px', border: optDeleteOriginal ? '1px solid rgba(255,59,48,0.3)' : '1px solid transparent' }}>
                      <input type="checkbox" checked={optDeleteOriginal} onChange={(e) => setOptDeleteOriginal(e.target.checked)} style={{ marginTop: '2px', accentColor: '#ff3b30' }} />
                      <div>
                        <div style={{ fontSize: '13px', fontWeight: '500', color: optDeleteOriginal ? '#ff3b30' : 'var(--macos-text-primary)' }}>删除原始文件</div>
                        <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)' }}>处理完成后删除 original/ 中的原始 PDF（不可恢复）</div>
                      </div>
                    </label>
                  </div>
                </div>
              )}
            </MacOSCard>

            {/* 进度条 */}
            {progress && (
              <MacOSCard style={{ marginBottom: '16px', background: 'var(--macos-bg-secondary)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  {processing ? (
                    <Loader2 className="w-5 h-5 animate-spin" color="#1e3a5f" />
                  ) : (
                    <CheckCircle className="w-5 h-5" color="#2d8f3d" />
                  )}
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: '14px', fontWeight: '500', marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {processing ? (
                        <>处理中 <WorkingDots /></>
                      ) : (
                        '处理完成'
                      )}
                      {/* 步骤1处理中时显示停止按钮 */}
                      {processing && currentStep === 1 && (
                        <button
                          onClick={handleCancelProcess}
                          style={{
                            padding: '2px 10px', borderRadius: '4px', border: 'none',
                            background: '#ff3b30', color: '#fff',
                            fontSize: '11px', fontWeight: '500', cursor: 'pointer',
                            display: 'flex', alignItems: 'center', gap: '3px'
                          }}
                          title="停止处理"
                        >
                          <XCircle className="w-3 h-3" /> 停止
                        </button>
                      )}
                    </div>
                    <div style={{ fontSize: '13px', color: progress.startsWith('✅') ? '#2d8f3d' : 'var(--macos-text-primary)' }}>
                      {progress}
                    </div>
                    {processing && (() => {
                      // 尝试从进度文本中提取百分比（如 "已转换 3/10 个证据 (30%)"）
                      const pctMatch = progress.match(/(\d+)%/)
                      const pct = pctMatch ? parseInt(pctMatch[1]) : (doneCount / Math.max(files.length, 1)) * 100
                      return (
                        <div style={{ marginTop: '8px', height: '4px', background: 'var(--macos-border)', borderRadius: '2px', overflow: 'hidden' }}>
                          <div style={{ height: '100%', background: '#1e3a5f', width: `${Math.min(pct, 100)}%`, borderRadius: '2px', transition: 'width 0.3s ease' }} />
                        </div>
                      )
                    })()}
                  </div>
                </div>
              </MacOSCard>
            )}

            {/* 证据清单（步骤 2 转换完成后显示操作区） */}
            {currentStep === 2 && (
              <MacOSCard style={{ marginBottom: '16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                  <h4 style={{ fontSize: '14px', fontWeight: '600' }}>证据提取</h4>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    {processing ? (
                      <button
                        onClick={handleStopExtract}
                        style={{
                          padding: '6px 12px', borderRadius: '6px',
                          border: 'none', background: '#ff9500',
                          color: '#fff', fontSize: '12px', fontWeight: '500',
                          cursor: 'pointer'
                        }}
                      >停止提取</button>
                    ) : evidenceExtracted ? (
                      <>
                        <button
                          onClick={handleExtractEvidence}
                          style={{
                            padding: '6px 12px', borderRadius: '6px',
                            border: 'none', background: '#1e3a5f',
                            color: '#fff', fontSize: '12px', fontWeight: '500',
                            cursor: 'pointer'
                          }}
                        >继续提取</button>
                        <button
                          onClick={handleClearEvidence}
                          style={{
                            padding: '6px 12px', borderRadius: '6px',
                            border: '1px solid #ff3b30', background: 'transparent',
                            color: '#ff3b30', fontSize: '12px', cursor: 'pointer', fontWeight: '500'
                          }}
                        >清除</button>
                      </>
                    ) : (
                      <button
                        onClick={handleExtractEvidence}
                        style={{
                          padding: '6px 12px', borderRadius: '6px',
                          border: 'none', background: '#1e3a5f',
                          color: '#fff', fontSize: '12px', fontWeight: '500',
                          cursor: 'pointer'
                        }}
                      >提取证据</button>
                    )}
                  </div>
                </div>
                {evidenceList.length > 0 && (
                  <>
                    <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)', marginBottom: '12px' }}>共 {evidenceList.length} 份证据</div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '300px', overflowY: 'auto' }}>
                      {evidenceList.map(ev => (
                        <div key={ev.id} style={{
                          display: 'flex', alignItems: 'center', gap: '12px',
                          padding: '10px 12px',
                          background: 'var(--macos-bg-secondary)',
                          borderRadius: '8px',
                          border: '1px solid var(--macos-border)'
                        }}>
                          <div style={{
                            width: '28px', height: '28px', borderRadius: '6px',
                            background: 'rgba(0,122,255,0.1)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            fontSize: '12px', fontWeight: '600', color: '#1e3a5f'
                          }}>{ev.id}</div>
                          <div style={{ flex: 1, overflow: 'hidden' }}>
                            <div style={{ fontSize: '13px', fontWeight: '500', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {ev.name}
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--macos-text-secondary)' }}>
                              {ev.type} · {ev.source}{ev.page_range ? ` · ${ev.page_range}` : ''}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </>
                )}
                {!evidenceExtracted && (
                  <div style={{ fontSize: '12px', color: 'var(--macos-text-tertiary)', padding: '12px 0' }}>
                    请先完成转换后再提取证据
                  </div>
                )}
              </MacOSCard>
            )}

            {/* 文件列表 */}
            {files.length > 0 && (
              <MacOSCard>
                {/* 步骤 2：转MD - 显示待转换的 PDF 列表 */}
                {currentStep === 2 ? (
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                    <h4 style={{ fontSize: '14px', fontWeight: '600' }}>待转换 PDF</h4>
                    <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)' }}>
                      {(() => {
                        const doneCount = files.filter(f => f.status === 'done').length
                        return doneCount > 0 ? `已转换 ${doneCount}/${files.length}` : `共 ${files.length} 个文件`
                      })()}
                    </div>
                  </div>
                ) : (
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {/* 全选/取消全选 */}
                      <button
                        onClick={toggleSelectAll}
                        style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px', display: 'flex', alignItems: 'center' }}
                      >
                        {files.every(f => f.selected) ? (
                          <CheckSquare className="w-4 h-4" color="#1e3a5f" />
                        ) : (
                          <Square className="w-4 h-4" color="#86868b" />
                        )}
                      </button>
                      <h4 style={{ fontSize: '14px', fontWeight: '600' }}>
                        {currentStep === 0 ? '原始文件' :
                         currentStep === 1 ? '待处理 PDF' :
                         'MD 文件'}
                      </h4>
                    </div>
                    <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)' }}>
                      已选 {getSelectedFiles().length}/{files.length}
                    </div>
                  </div>
                )}
                
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {files.map(file => {
                    // 显示当前步骤的输入文件
                    const inputFile = currentStep === 1 ? file :
                                      currentStep === 2 ? { ...file, name: file.processedPath || file.name } :
                                      file;
                    
                    return (
                      <div key={file.id} style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '12px',
                        padding: '12px',
                        background: file.splitResults ? 'rgba(52, 199, 89, 0.08)' :
                                   file.status === 'done' ? 'rgba(52, 199, 89, 0.05)' :
                                   file.status === 'processing' ? 'rgba(0, 122, 255, 0.05)' :
                                   file.status === 'error' ? 'rgba(255, 59, 48, 0.05)' : 'var(--macos-bg-secondary)',
                        borderRadius: '8px',
                        border: file.splitResults ? '1px solid rgba(52, 199, 89, 0.3)' :
                                  file.selected ? '1px solid rgba(0, 122, 255, 0.3)' : '1px solid transparent'
                      }}>
                        {/* 步骤 2：状态指示（无复选框，拆分是单文件操作） */}
                        {currentStep === 2 ? (
                          file.splitResults ? (
                            <div style={{
                              width: '32px', height: '32px', borderRadius: '8px',
                              background: 'rgba(52, 199, 89, 0.1)',
                              display: 'flex', alignItems: 'center', justifyContent: 'center'
                            }}>
                              <CheckCircle className="w-4 h-4" color="#2d8f3d" />
                            </div>
                          ) : file.status === 'processing' ? (
                            <div style={{
                              width: '32px', height: '32px', borderRadius: '8px',
                              background: 'rgba(0, 122, 255, 0.1)',
                              display: 'flex', alignItems: 'center', justifyContent: 'center'
                            }}>
                              <Loader2 className="w-4 h-4 animate-spin" color="#1e3a5f" />
                            </div>
                          ) : (
                            <div style={{
                              width: '32px', height: '32px', borderRadius: '8px',
                              background: 'rgba(0, 122, 255, 0.1)',
                              display: 'flex', alignItems: 'center', justifyContent: 'center'
                            }}>
                              <FileText className="w-4 h-4" color="#86868b" />
                            </div>
                          )
                        ) : (
                          /* 其他步骤：复选框 */
                          <button
                            onClick={() => toggleSelect(file.id)}
                            disabled={file.status !== 'pending'}
                            style={{ background: 'none', border: 'none', cursor: file.status === 'pending' ? 'pointer' : 'default', padding: '2px', display: 'flex', alignItems: 'center', opacity: file.status !== 'pending' ? 0.3 : 1 }}
                          >
                            {file.selected ? (
                              <CheckSquare className="w-4 h-4" color="#1e3a5f" />
                            ) : (
                              <Square className="w-4 h-4" color="#86868b" />
                            )}
                          </button>
                        )}
                        {/* 其他步骤：通用状态图标 */}
                        {currentStep !== 2 && (
                          <div style={{
                            width: '32px',
                            height: '32px',
                            borderRadius: '8px',
                            background: file.status === 'done' ? 'rgba(52, 199, 89, 0.1)' : 'rgba(0, 122, 255, 0.1)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center'
                          }}>
                            {file.status === 'done' ? (
                              <CheckCircle className="w-4 h-4" color="#2d8f3d" />
                            ) : file.status === 'processing' ? (
                              <Loader2 className="w-4 h-4 animate-spin" color="#1e3a5f" />
                            ) : file.status === 'error' ? (
                              <AlertCircle className="w-4 h-4" color="#ff3b30" />
                            ) : (
                              <FileText className="w-4 h-4" color="#86868b" />
                            )}
                          </div>
                        )}
                        
                        <div style={{ flex: 1, overflow: 'hidden' }}>
                          <div style={{ fontSize: '13px', fontWeight: '500', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {inputFile.name}
                          </div>
                          <div style={{ fontSize: '11px', color: 'var(--macos-text-secondary)' }}>
                            {currentStep === 0 ? `${(file.size / 1024).toFixed(1)} KB` :
                             currentStep === 1 ? `去水印${optWatermark ? ' ✓' : ''}${optEnhance ? ` · ${enhanceDpi}dpi` : ''}` :
                             currentStep === 2 ? (
                               file.splitResults ? `✓ 已拆分 · 生成 ${file.splitResults.length} 个文书段` :
                               file.status === 'processing' ? 'LLM 分析中...' :
                               '待拆分'
                             ) :
                             currentStep === 3 ? '已拆分 MD' :
                             'MD 格式'}
                            {file.error && ` - ${file.error}`}
                          </div>
                        </div>

                        {/* 步骤 2：已拆分文件显示标记，未拆分显示操作按钮 */}
                        {currentStep === 2 && file.splitResults && (
                          <span style={{
                            padding: '4px 10px', borderRadius: '12px',
                            background: 'rgba(52, 199, 89, 0.1)', color: '#2d8f3d',
                            fontSize: '12px', fontWeight: '500', whiteSpace: 'nowrap'
                          }}>
                            ✓ 已拆分
                          </span>
                        )}
                        {currentStep === 2 && !file.splitResults && processing && (
                          <span style={{
                            padding: '8px 12px',
                            background: 'rgba(255, 149, 0, 0.1)',
                            color: '#ff9500',
                            fontSize: '12px',
                            fontWeight: '500',
                            whiteSpace: 'nowrap'
                          }}>
                            ⏳ 拆分中...
                          </span>
                        )}

                        {/* 步骤 2：PDF 删除和重新转换按钮 */}
                        {currentStep === 2 && file.status !== 'processing' && (
                          <>
                            <button
                              onClick={() => handleDeletePdf(file.name)}
                              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
                              title="删除此 PDF，删除后可重新上传转换"
                            >
                              <Trash2 className="w-4 h-4" color="#86868b" />
                            </button>
                          </>
                        )}

                        {file.status !== 'processing' && currentStep === 0 && (
                          <button
                            onClick={() => handleRemoveFile(file)}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
                          >
                            <Trash2 className="w-4 h-4" color="#86868b" />
                          </button>
                        )}

                        {/* 步骤 1：PDF 删除按钮 */}
                        {file.status !== 'processing' && currentStep === 1 && (
                          <button
                            onClick={() => handleDeleteOriginal(file.name)}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
                            title="删除此 PDF，删除后可重新上传"
                          >
                            <Trash2 className="w-4 h-4" color="#86868b" />
                          </button>
                        )}

                        {/* MD 文件删除和重新转换按钮（步骤 3+） */}
                        {file.status !== 'processing' && currentStep >= 3 && file.name.endsWith('.md') && (
                          <>
                            <button
                              onClick={() => handleReconvertMd(file.name)}
                              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
                              title="从 PDF 重新转换此文件"
                            >
                              <RefreshCw className="w-4 h-4" color="#86868b" />
                            </button>
                            <button
                              onClick={() => handleDeleteMd(file.name)}
                              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
                              title="删除此 MD 文件，删除后可从 PDF 重新转换"
                            >
                              <Trash2 className="w-4 h-4" color="#86868b" />
                            </button>
                          </>
                        )}
                        
                        {/* 打开文件按钮 */}
                        {file.status !== 'processing' && (
                          <button
                            onClick={() => handleOpenFile(file)}
                            style={{
                              padding: '6px 12px',
                              background: 'rgba(0, 122, 255, 0.1)',
                              color: '#1e3a5f',
                              border: 'none',
                              borderRadius: '6px',
                              cursor: 'pointer',
                              fontSize: '12px',
                              fontWeight: '500'
                            }}
                          >
                            预览
                          </button>
                        )}
                      </div>
                    )
                  })}
                </div>
              </MacOSCard>
            )}

            {/* 步骤 3：案卷分析 — 5 阶段独立控制 */}
            {currentStep === 3 && (
              <>
                {/* 被告人信息 + 罪名输入 */}
                <MacOSCard style={{ marginBottom: '16px' }}>
                  <div style={{ display: 'flex', gap: '12px', alignItems: 'center', marginBottom: '12px' }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: '11px', color: 'var(--macos-text-tertiary)', marginBottom: '4px' }}>被告人</div>
                      <div style={{ fontSize: '14px', color: 'var(--macos-text-primary)', fontWeight: 500 }}>{defendant || '未指定'}</div>
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: '11px', color: 'var(--macos-text-tertiary)', marginBottom: '4px' }}>罪名（可选）</div>
                      <input
                        type="text"
                        value={crimeType}
                        onChange={(e) => setCrimeType(e.target.value)}
                        placeholder="如：诈骗罪、职务侵占罪"
                        style={{
                          width: '100%',
                          padding: '6px 10px',
                          border: '1px solid var(--macos-border)',
                          borderRadius: '8px',
                          fontSize: '13px',
                          background: 'var(--macos-bg-secondary)',
                          boxSizing: 'border-box'
                        }}
                      />
                    </div>
                  </div>
                </MacOSCard>

                {/* 5 阶段独立按钮 */}
                <MacOSCard style={{ marginBottom: '16px' }}>
                  <h4 style={{ fontSize: '14px', fontWeight: '600', marginBottom: '12px' }}>分析阶段（不建议并行处理）</h4>
                  {!evidenceExtracted && (
                    <div style={{
                      padding: '8px 12px', borderRadius: '8px', marginBottom: '12px',
                      background: 'rgba(255,149,0,0.08)', border: '1px solid rgba(255,149,0,0.2)',
                      fontSize: '12px', color: '#ff9500',
                      display: 'flex', alignItems: 'center', gap: '8px'
                    }}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                      请先完成证据提取，再进行案卷分析
                    </div>
                  )}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {STAGES.map(stage => {
                      const status = stageStatus[stage.num] || 'idle'
                      const isRunning = runningStage === stage.num
                      const msg = stageMessages[stage.num]
                      const errMsg = stageErrors[stage.num]
                      const analysisDisabled = !evidenceExtracted

                      return (
                        <div key={stage.num} style={{
                          display: 'flex', alignItems: 'center', gap: '12px',
                          padding: '12px',
                          borderRadius: '8px',
                          border: `1px solid ${status === 'completed' ? 'rgba(52,199,89,0.3)' : status === 'error' ? 'rgba(255,59,48,0.2)' : 'var(--macos-border)'}`,
                          background: status === 'completed' ? 'rgba(52,199,89,0.04)' : status === 'error' ? 'rgba(255,59,48,0.03)' : 'var(--macos-bg-secondary)',
                          opacity: analysisDisabled ? 0.5 : 1,
                          transition: 'opacity 0.2s'
                        }}>
                          {/* 状态图标 */}
                          <div style={{
                            width: '28px', height: '28px', borderRadius: '50%',
                            background: status === 'completed' ? 'rgba(52,199,89,0.15)' : isRunning ? 'rgba(0,122,255,0.1)' : status === 'error' ? 'rgba(255,59,48,0.1)' : 'var(--macos-bg-tertiary)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            fontSize: '14px', fontWeight: '600', flexShrink: 0,
                            color: status === 'completed' ? '#2d8f3d' : isRunning ? '#1e3a5f' : status === 'error' ? '#ff3b30' : '#86868b'
                          }}>
                            {status === 'completed' ? '✓' : isRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : stage.num}
                          </div>

                          {/* 阶段信息 */}
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontSize: '13px', fontWeight: '500' }}>{stage.name}</div>
                            {msg && <div style={{ fontSize: '11px', color: '#1e3a5f' }}>{msg}</div>}
                            {errMsg && <div style={{ fontSize: '11px', color: '#ff3b30' }}>{errMsg}</div>}
                            {!msg && !errMsg && <div style={{ fontSize: '11px', color: 'var(--macos-text-tertiary)' }}>{stage.desc}</div>}
                          </div>

                          {/* 操作按钮 */}
                          <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
                            {status === 'completed' && (
                              <>
                                <button onClick={() => handleViewStage(stage.num)} disabled={analysisDisabled} style={{
                                  padding: '4px 10px', borderRadius: '6px',
                                  border: '1px solid var(--macos-border)', background: 'transparent',
                                  color: analysisDisabled ? '#d1d1d6' : '#1e3a5f',
                                  fontSize: '12px', cursor: analysisDisabled ? 'not-allowed' : 'pointer'
                                }}>查看</button>
                                <button onClick={() => handleClearStage(stage.num)} disabled={analysisDisabled} style={{
                                  padding: '4px 10px', borderRadius: '6px',
                                  border: analysisDisabled ? '1px solid #d1d1d6' : '1px solid #ff3b30', background: 'transparent',
                                  color: analysisDisabled ? '#d1d1d6' : '#ff3b30',
                                  fontSize: '12px', cursor: analysisDisabled ? 'not-allowed' : 'pointer'
                                }}>清除</button>
                              </>
                            )}
                            {status === 'error' && (
                              <button onClick={() => handleRunStage(stage.num)} disabled={analysisDisabled} style={{
                                padding: '4px 10px', borderRadius: '6px',
                                border: 'none', background: analysisDisabled ? '#d1d1d6' : '#ff3b30',
                                color: '#fff', fontSize: '12px',
                                cursor: analysisDisabled ? 'not-allowed' : 'pointer'
                              }}>重试</button>
                            )}
                            {isRunning && (
                              <button onClick={() => handleStopStage(stage.num)} style={{
                                padding: '4px 10px', borderRadius: '6px',
                                border: 'none', background: '#ff9500',
                                color: '#fff', fontSize: '12px', cursor: 'pointer'
                              }}>停止</button>
                            )}
                            {(status === 'idle' || status === 'error') && !isRunning && (
                              <button onClick={() => handleRunStage(stage.num)} disabled={!defendant.trim() || analysisDisabled} style={{
                                padding: '4px 10px', borderRadius: '6px',
                                border: 'none',
                                background: (!defendant.trim() || analysisDisabled) ? '#d1d1d6' : '#1e3a5f',
                                color: '#fff', fontSize: '12px',
                                cursor: (!defendant.trim() || analysisDisabled) ? 'not-allowed' : 'pointer'
                              }}>开始</button>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </MacOSCard>
              </>
            )}

            {/* 步骤 4：案卷分析 - 旧版流水线（保留兼容） */}
            {false && currentStep === 4 && (
              <>
                {/* 被告人信息 + 罪名输入 + 执行按钮 */}
                <div style={{ display: 'flex', gap: '12px', alignItems: 'center', marginBottom: '16px' }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: '11px', color: 'var(--macos-text-tertiary)', marginBottom: '4px' }}>被告人</div>
                    <div style={{ fontSize: '14px', color: 'var(--macos-text-primary)', fontWeight: 500 }}>{defendant || '未指定'}</div>
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: '11px', color: 'var(--macos-text-tertiary)', marginBottom: '4px' }}>罪名（可选）</div>
                    <input
                      type="text"
                      value={crimeType}
                      onChange={(e) => setCrimeType(e.target.value)}
                      placeholder="如：诈骗罪、职务侵占罪"
                      style={{
                        width: '100%',
                        padding: '6px 10px',
                        border: '1px solid var(--macos-border)',
                        borderRadius: '8px',
                        fontSize: '13px',
                        background: 'var(--macos-bg-secondary)',
                        boxSizing: 'border-box'
                      }}
                    />
                  </div>
                  <MacOSButton
                    variant="primary"
                    icon={pipelineRunning ? Loader2 : Scale}
                    onClick={executeNextStep}
                    disabled={pipelineRunning || (pipelineStatus[5] && nextStep !== 4.5) || !defendant.trim()}
                  >
                    {pipelineRunning ? '分析中...' : pipelineStatus[5] && nextStep === 4.5 ? '补充控辩对抗' : pipelineStatus[5] ? '分析完成' : '执行下一步'}
                  </MacOSButton>
                  {nextStep && !pipelineRunning && (
                    <MacOSButton
                      variant="secondary"
                      icon={RefreshCw}
                      onClick={handleResumeAnalysis}
                      disabled={!defendant.trim()}
                    >
                      继续分析（步骤 {nextStep}）
                    </MacOSButton>
                  )}
                </div>

                {/* 流水线步骤进度条 */}
                <div style={{ display: 'flex', gap: '4px', marginBottom: '16px' }}>
                  {PIPELINE_STEPS.map(step => {
                    const isDone = pipelineStatus[step.num]
                    const isRunning = pipelineRunning && currentPipelineStep === step.num
                    const canRun = !isDone && !pipelineRunning
                    return (
                      <div
                        key={step.num}
                        style={{
                          flex: 1,
                          padding: '8px 8px',
                          borderRadius: '6px',
                          background: isDone ? 'rgba(52,199,89,0.1)' : isRunning ? 'rgba(0,122,255,0.1)' : 'var(--macos-bg-secondary)',
                          fontSize: '11px',
                          textAlign: 'center',
                          color: isDone ? '#2d8f3d' : isRunning ? '#1e3a5f' : 'var(--macos-text-tertiary)',
                          fontWeight: isDone || isRunning ? '600' : '400',
                          border: isRunning ? '1px solid rgba(0,122,255,0.3)' : '1px solid transparent',
                          cursor: canRun ? 'pointer' : 'default',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          gap: '4px',
                        }}
                        onClick={() => canRun && executeSingleStep(step.num)}
                        title={canRun ? '点击执行此步骤' : ''}
                      >
                        {isDone ? (
                          <>✓ {step.name}</>
                        ) : isRunning ? (
                          <>⟳ {step.name}</>
                        ) : (
                          <>
                            <Play className="w-3 h-3" style={{ flexShrink: 0, opacity: 0.6 }} />
                            {step.name}
                          </>
                        )}
                      </div>
                    )
                  })}
                </div>

                {/* 已有分析结果摘要 */}
                {(Object.keys(pipelineStatus).length > 0 || analysisCompleted || nextStep) && (
                  <MacOSCard style={{ marginBottom: '12px', padding: '12px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                      <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--macos-text-primary)' }}>
                        {analysisCompleted ? '✓ 6 阶段分析已完成' : pipelineStatus[5] ? (nextStep === 4.5 ? '分析已完成，可补充控辩对抗' : '分析记录') : nextStep ? `部分分析完成（${Object.values(pipelineStatus).filter(Boolean).length}/6 步，继续从步骤 ${nextStep} 开始）` : `已有 ${Object.values(pipelineStatus).filter(Boolean).length}/6 步完成`}
                      </div>
                      <div style={{ display: 'flex', gap: '8px' }}>
                        {nextStep && !pipelineRunning && (
                          <MacOSButton
                            variant="secondary"
                            icon={RefreshCw}
                            onClick={handleResumeAnalysis}
                            disabled={!defendant.trim()}
                          >
                            {nextStep === 4.5 && pipelineStatus[5] ? '补充控辩对抗' : '继续分析'}
                          </MacOSButton>
                        )}
                        <MacOSButton
                            variant="primary"
                            icon={FileText}
                            onClick={() => {
                              if (!evidenceExtracted) {
                                handleExtractEvidence()
                              } else {
                                navigate(`/case/${caseId}/report`)
                              }
                            }}
                          >
                            {!evidenceExtracted ? '提取证据' : '查看报告'}
                          </MacOSButton>
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                      {PIPELINE_STEPS.map(step => {
                        const done = pipelineStatus[step.num]
                        const result = stepResults[step.num]
                        let summary = ''
                        if (step.num === 1 && result) summary = `${result.total_persons || 0} 人，${result.total_sessions || 0} 次笔录`
                        else if (step.num === 2 && result) summary = `${result.total_persons || 0} 人总结`
                        else if (step.num === 3 && result) summary = `${result.total_analyzed || 0} 人矛盾分析`
                        else if (step.num === 4 && result) {
                          const subs = result.sub_steps || []
                          const done = subs.filter((s: any) => s.status === 'done').length
                          summary = `Wiki ${done}/${subs.length} 子步骤`
                        }
                        else if (step.num === 4.5 && result) summary = '控辩对抗已生成'
                        else if (step.num === 5 && result) summary = '辩护意见已生成'
                        return (
                          <div key={step.num} style={{
                            flex: '1 1 calc(20% - 8px)', minWidth: '120px',
                            padding: '8px', borderRadius: '6px',
                            background: done ? 'rgba(52,199,89,0.06)' : 'var(--macos-bg-secondary)',
                            border: `1px solid ${done ? 'rgba(52,199,89,0.2)' : 'transparent'}`,
                          }}>
                            <div style={{ fontSize: '11px', fontWeight: '600', color: done ? '#2d8f3d' : 'var(--macos-text-tertiary)', marginBottom: '2px' }}>
                              {done ? '✓' : '○'} {step.name}
                            </div>
                            {done && summary && (
                              <div style={{ fontSize: '10px', color: 'var(--macos-text-tertiary)', lineHeight: '1.4' }}>
                                {summary}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </MacOSCard>
                )}

                {/* 进度文字 */}
                {progress && (
                  <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)', marginBottom: '12px' }}>
                    {progress}
                  </div>
                )}

                {/* LLM 实时进度 */}
                {liveProgress && (
                  <MacOSCard style={{ marginBottom: '12px', background: 'rgba(0,122,255,0.04)', border: '1px solid rgba(0,122,255,0.15)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                      <Loader2 className="w-5 h-5 animate-spin" color="#1e3a5f" />
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: '13px', fontWeight: '500', color: '#1e3a5f', marginBottom: '4px' }}>
                          {liveProgress!.message}
                        </div>
                        <div style={{ fontSize: '11px', color: 'var(--macos-text-tertiary)' }}>
                          {liveProgress!.total > 0 ? `${liveProgress!.current}/${liveProgress!.total} 份文件 · ` : ''}
                          已运行 {Math.floor(liveProgress!.elapsed / 60)}分{liveProgress!.elapsed % 60}秒，请耐心等待
                        </div>
                        {liveProgress!.total > 0 && (
                          <div style={{ marginTop: '8px', height: '4px', background: 'rgba(0,122,255,0.1)', borderRadius: '2px', overflow: 'hidden' }}>
                            <div style={{
                              width: `${(liveProgress!.current / liveProgress!.total) * 100}%`,
                              height: '100%',
                              background: '#1e3a5f',
                              borderRadius: '2px',
                              transition: 'width 0.3s ease',
                            }} />
                          </div>
                        )}
                      </div>
                    </div>
                  </MacOSCard>
                )}

                {/* 错误提示 */}
                {error && (
                  <div style={{
                    padding: '10px 14px',
                    background: 'rgba(255,59,48,0.08)',
                    border: '1px solid rgba(255,59,48,0.2)',
                    borderRadius: '8px',
                    fontSize: '13px',
                    color: '#ff3b30',
                    marginBottom: '12px'
                  }}>
                    {error}
                  </div>
                )}

                {/* 本次新完成的完成卡片 */}
                {progress === '' && pipelineStatus[5] && (
                  <MacOSCard style={{ padding: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                      <div style={{
                        width: '40px', height: '40px', borderRadius: '50%',
                        background: 'rgba(52,199,89,0.1)', display: 'flex',
                        alignItems: 'center', justifyContent: 'center'
                      }}>
                        <span style={{ fontSize: '20px' }}>✓</span>
                      </div>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: '14px', fontWeight: '600', color: '#2d8f3d' }}>分析完成</div>
                        <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)', marginTop: '2px' }}>
                          5 步流水线分析已完成，查看完整报告
                        </div>
                      </div>
                      <MacOSButton
                        variant="primary"
                        icon={FileText}
                        onClick={() => {
                          if (!evidenceExtracted) {
                            handleExtractEvidence()
                          } else {
                            navigate(`/case/${caseId}/report`)
                          }
                        }}
                      >
                        {!evidenceExtracted ? '提取证据' : '查看分析报告'}
                      </MacOSButton>
                    </div>
                  </MacOSCard>
                )}

                {/* 分析报告浏览（步骤 4 完成后显示） */}
                {pipelineStatus[4] && (
                  <MacOSCard style={{ padding: '0', overflow: 'hidden' }}>
                    <div style={{ display: 'flex', height: '600px' }}>
                      {/* 左侧：证据文件浏览器 */}
                      <div style={{
                        width: '240px', background: 'var(--macos-bg-secondary)',
                        borderRight: '1px solid var(--macos-border)', overflow: 'auto', padding: '12px', flexShrink: 0
                      }}>
                        <div style={{ fontSize: '12px', fontWeight: '600', marginBottom: '10px', color: 'var(--macos-text-primary)' }}>
                          📁 证据文件
                        </div>

                        {/* 言辞证据（汇总） */}
                        <div style={{ marginBottom: '4px' }}>
                          <button
                            onClick={() => {
                              const next = new Set(expandedCategories)
                              if (next.has('言辞证据')) next.delete('言辞证据'); else next.add('言辞证据')
                              setExpandedCategories(next)
                            }}
                            style={{
                              width: '100%', textAlign: 'left', padding: '4px 6px', borderRadius: '4px',
                              fontSize: '11px', fontWeight: '600', background: 'transparent',
                              border: 'none', cursor: 'pointer', color: 'var(--macos-text-primary)',
                              display: 'flex', alignItems: 'center', gap: '4px'
                            }}
                          >
                            {expandedCategories.has('言辞证据') ? '▼' : '▶'} 言辞证据（汇总）
                          </button>
                          {expandedCategories.has('言辞证据') && evidenceSummaries.map(cat => (
                            <div key={cat.name} style={{ paddingLeft: '12px' }}>
                              <button
                                onClick={() => {
                                  const next = new Set(expandedCategories)
                                  if (next.has(cat.name)) next.delete(cat.name); else next.add(cat.name)
                                  setExpandedCategories(next)
                                }}
                                style={{
                                  width: '100%', textAlign: 'left', padding: '3px 6px', borderRadius: '4px',
                                  fontSize: '10px', fontWeight: '600', background: 'transparent',
                                  border: 'none', cursor: 'pointer', color: 'var(--macos-text-secondary)',
                                  display: 'flex', alignItems: 'center', gap: '4px'
                                }}
                              >
                                {expandedCategories.has(cat.name) ? '▼' : '▶'} {cat.name} ({cat.files.length})
                              </button>
                              {expandedCategories.has(cat.name) && cat.files.map(file => (
                                <button
                                  key={file.name}
                                  onClick={() => { setSelectedAnalysisCard(-1); loadEvidenceContent(cat.name, file.name) }}
                                  style={{
                                    display: 'block', width: '100%', textAlign: 'left',
                                    padding: '3px 8px', marginBottom: '1px', borderRadius: '4px',
                                    fontSize: '10px', background: 'transparent',
                                    border: 'none', cursor: 'pointer', color: 'var(--macos-text-secondary)',
                                  }}
                                  onMouseEnter={e => (e.currentTarget.style.background = 'rgba(0,122,255,0.08)')}
                                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                                >
                                  {file.displayName}
                                </button>
                              ))}
                            </div>
                          ))}
                        </div>

                        {/* 其他证据 */}
                        <div style={{ marginBottom: '4px' }}>
                          <button
                            onClick={() => {
                              const next = new Set(expandedCategories)
                              if (next.has('其他证据')) next.delete('其他证据'); else next.add('其他证据')
                              setExpandedCategories(next)
                            }}
                            style={{
                              width: '100%', textAlign: 'left', padding: '4px 6px', borderRadius: '4px',
                              fontSize: '11px', fontWeight: '600', background: 'transparent',
                              border: 'none', cursor: 'pointer', color: 'var(--macos-text-primary)',
                              display: 'flex', alignItems: 'center', gap: '4px'
                            }}
                          >
                            {expandedCategories.has('其他证据') ? '▼' : '▶'} 其他证据 ({evidenceOther.length})
                          </button>
                          {expandedCategories.has('其他证据') && evidenceOther.map(file => (
                            <button
                              key={file.name}
                              onClick={() => { setSelectedAnalysisCard(-1); loadEvidenceContent('', file.name, file.dir) }}
                              style={{
                                display: 'block', width: '100%', textAlign: 'left',
                                padding: '3px 8px 3px 18px', marginBottom: '1px', borderRadius: '4px',
                                fontSize: '10px', background: 'transparent',
                                border: 'none', cursor: 'pointer', color: 'var(--macos-text-secondary)',
                              }}
                              onMouseEnter={e => (e.currentTarget.style.background = 'rgba(0,122,255,0.08)')}
                              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                            >
                              {file.name.replace('.pdf', '')}
                            </button>
                          ))}
                        </div>
                      </div>

                      {/* 中间：分析卡片导航 */}
                      <div style={{
                        width: '180px', background: 'var(--macos-bg-secondary)',
                        borderRight: '1px solid var(--macos-border)', overflow: 'auto', padding: '12px', flexShrink: 0
                      }}>
                        <div style={{ fontSize: '12px', fontWeight: '600', marginBottom: '10px', color: 'var(--macos-text-primary)' }}>
                          📊 分析结果
                        </div>
                        {[
                          { num: 0, icon: '📋', label: '指控要素' },
                          { num: 1, icon: '🔍', label: '证据分析' },
                          { num: 2, icon: '⚡', label: '矛盾分析' },
                          { num: 3, icon: '⚖️', label: '法律依据' },
                          { num: 4, icon: '📝', label: '辩护意见' },
                        ].map(item => {
                          const isActive = selectedAnalysisCard === item.num
                          return (
                            <div key={item.num}>
                              <button
                                onClick={() => setSelectedAnalysisCard(item.num)}
                                style={{
                                  display: 'block', width: '100%', textAlign: 'left',
                                  padding: '6px 8px', marginBottom: '2px', borderRadius: '4px',
                                  fontSize: '11px',
                                  background: isActive ? 'rgba(0,122,255,0.1)' : 'transparent',
                                  color: isActive ? '#1e3a5f' : 'var(--macos-text-secondary)',
                                  border: 'none', cursor: 'pointer',
                                  fontWeight: isActive ? '600' : '400',
                                }}
                              >
                                {item.icon} {item.label}
                              </button>
                              {/* 证据分析子项 */}
                              {item.num === 1 && isActive && evidenceAnalysisFiles.length > 0 && (
                                <div style={{ paddingLeft: '8px', marginTop: '2px', marginBottom: '4px' }}>
                                  <select
                                    value={selectedEvidenceAnalysis}
                                    onChange={e => {
                                      setSelectedEvidenceAnalysis(e.target.value)
                                      loadAnalysisContent(1, e.target.value)
                                    }}
                                    style={{
                                      width: '100%', fontSize: '10px', padding: '2px 4px',
                                      background: 'var(--macos-bg-secondary)', color: 'var(--macos-text-primary)',
                                      border: '1px solid var(--macos-border)', borderRadius: '3px'
                                    }}
                                  >
                                    {evidenceAnalysisFiles.map(p => {
                                      const name = p.replace('03-证据分析/', '').replace('.md', '')
                                      return <option key={p} value={p}>{name}</option>
                                    })}
                                  </select>
                                </div>
                              )}
                              {/* 矛盾分析子项 */}
                              {item.num === 2 && isActive && contradictionFilesList.length > 1 && (
                                <div style={{ paddingLeft: '8px', marginTop: '2px', marginBottom: '4px' }}>
                                  <select
                                    value={selectedContradictionFile}
                                    onChange={e => {
                                      setSelectedContradictionFile(e.target.value)
                                      loadAnalysisContent(2)
                                    }}
                                    style={{
                                      width: '100%', fontSize: '10px', padding: '2px 4px',
                                      background: 'var(--macos-bg-secondary)', color: 'var(--macos-text-primary)',
                                      border: '1px solid var(--macos-border)', borderRadius: '3px'
                                    }}
                                  >
                                    {contradictionFilesList.map(f => (
                                      <option key={f.filename} value={f.filename}>{f.displayName}</option>
                                    ))}
                                  </select>
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>

                      {/* 右侧：内容浏览区 */}
                      <div style={{ flex: 1, overflow: 'auto', padding: '16px' }}>
                        {evidenceLoading || (selectedAnalysisCard >= 0 && !analysisContent) ? (
                          <div style={{ textAlign: 'center', padding: '40px', color: 'var(--macos-text-tertiary)' }}>
                            <Loader2 className="w-5 h-5" style={{ animation: 'spin 1s linear infinite' }} />
                            <div style={{ marginTop: '8px', fontSize: '12px' }}>加载中...</div>
                          </div>
                        ) : evidenceContent.startsWith('__pdf__:') ? (
                          /* PDF 预览 */
                          <iframe
                            src={evidenceContent.replace('__pdf__:', '')}
                            style={{ width: '100%', height: '100%', border: 'none' }}
                            title="证据预览"
                          />
                        ) : (analysisContent || evidenceContent) ? (
                          <div
                            style={{ fontSize: '13px', lineHeight: '1.7' }}
                            dangerouslySetInnerHTML={{ __html: marked.parse(analysisContent || evidenceContent, { async: false }) as string }}
                          />
                        ) : (
                          <div style={{ textAlign: 'center', padding: '40px', color: 'var(--macos-text-tertiary)' }}>
                            选择左侧证据文件或中间分析结果进行浏览
                          </div>
                        )}
                      </div>
                    </div>
                  </MacOSCard>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {/* 预览覆盖层（PDF/MD） */}
      {previewFile && (
        <div style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.85)',
          display: 'flex',
          flexDirection: 'column',
          zIndex: 9999
        }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            padding: '12px 16px',
            background: 'var(--macos-bg-secondary)',
            borderBottom: '1px solid var(--macos-border)',
            gap: '12px'
          }}>
            <button
              onClick={closePreview}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '6px 14px',
                background: 'rgba(0, 122, 255, 0.15)',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                fontSize: '13px',
                color: '#1e3a5f',
                fontWeight: '500'
              }}
            >
              ← 返回
            </button>
            <span style={{ fontSize: '13px', color: 'var(--macos-text-secondary)', flex: 1 }}>
              {previewFile.name}
            </span>
            {enlargedPage >= 0 && (
              <button
                onClick={() => setEnlargedPage(-1)}
                style={{
                  padding: '4px 12px',
                  background: 'rgba(142,142,147,0.12)',
                  border: 'none',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '12px',
                  color: 'var(--macos-text-primary)'
                }}
              >退出放大</button>
            )}
          </div>

          {previewFile.name.endsWith('.md') ? (
            <div style={{ flex: 1, overflow: 'auto', background: '#fff', padding: '24px' }}>
              <MDPreview url={previewFile.path!} />
            </div>
          ) : enlargedPage >= 0 ? (
            /* 放大单页 */
            <div style={{
              flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
              background: '#1a1a1e', padding: '20px', overflow: 'auto'
            }}>
              <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.5)', marginBottom: '12px' }}>
                第 {enlargedPage + 1} / {previewThumbs.length} 页
              </div>
              <div style={{ maxWidth: '90vw', maxHeight: '80vh' }}>
                <img
                  key={previewThumbs[enlargedPage]}
                  src={previewThumbs[enlargedPage]}
                  alt={`第 ${enlargedPage + 1} 页`}
                  style={{ width: 'auto', maxHeight: '80vh', borderRadius: '8px', background: '#fff' }}
                />
              </div>
              {/* 翻页按钮 */}
              <div style={{ display: 'flex', gap: '12px', marginTop: '16px' }}>
                <button
                  onClick={() => setEnlargedPage(Math.max(0, enlargedPage - 1))}
                  disabled={enlargedPage === 0}
                  style={{
                    padding: '8px 16px', background: 'rgba(255,255,255,0.1)', color: '#fff',
                    border: '1px solid rgba(255,255,255,0.2)', borderRadius: '6px',
                    cursor: enlargedPage === 0 ? 'not-allowed' : 'pointer', fontSize: '13px',
                    opacity: enlargedPage === 0 ? 0.3 : 1
                  }}
                >← 上一页</button>
                <button
                  onClick={() => setEnlargedPage(Math.min(previewThumbs.length - 1, enlargedPage + 1))}
                  disabled={enlargedPage === previewThumbs.length - 1}
                  style={{
                    padding: '8px 16px', background: 'rgba(255,255,255,0.1)', color: '#fff',
                    border: '1px solid rgba(255,255,255,0.2)', borderRadius: '6px',
                    cursor: enlargedPage === previewThumbs.length - 1 ? 'not-allowed' : 'pointer', fontSize: '13px',
                    opacity: enlargedPage === previewThumbs.length - 1 ? 0.3 : 1
                  }}
                >下一页 →</button>
              </div>
            </div>
          ) : (
            /* 缩略图网格 */
            <div style={{
              flex: 1, overflow: 'auto', background: '#1a1a1e', padding: '20px'
            }}>
              {previewThumbLoading ? (
                <div style={{ color: '#86868b', textAlign: 'center', paddingTop: '60px' }}>生成缩略图中...</div>
              ) : previewThumbs.length > 0 ? (
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))',
                  gap: '20px',
                  maxWidth: '1400px',
                  margin: '0 auto'
                }}>
                  {previewThumbs.map((thumbUrl, i) => (
                    <div
                      key={i}
                      onClick={() => setEnlargedPage(i)}
                      style={{
                        cursor: 'pointer',
                        background: '#fff',
                        borderRadius: '8px',
                        overflow: 'hidden',
                        boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
                        transition: 'transform 0.15s ease, box-shadow 0.15s ease'
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.transform = 'scale(1.03)'
                        e.currentTarget.style.boxShadow = '0 4px 16px rgba(0,0,0,0.5)'
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.transform = 'scale(1)'
                        e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.3)'
                      }}
                    >
                      <img
                        src={thumbUrl}
                        alt={`第 ${i + 1} 页`}
                        style={{ width: '100%', display: 'block' }}
                      />
                      <div style={{
                        padding: '6px 10px',
                        background: '#f5f5f7',
                        fontSize: '12px',
                        color: '#6e6e73',
                        textAlign: 'center',
                        fontWeight: '500'
                      }}>
                        第 {i + 1} 页 · 点击放大
                      </div>
                    </div>
                  ))}
                </div>
              ) : previewThumbError ? (
                <div style={{ color: '#86868b', textAlign: 'center', paddingTop: '60px' }}>
                  <div style={{ fontSize: '14px', marginBottom: '8px' }}>{previewThumbError}</div>
                </div>
              ) : (
                <div style={{ color: '#86868b', textAlign: 'center', paddingTop: '60px' }}>
                  当前文件不是 PDF，无缩略图可预览
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* 起诉书选择对话框 */}
      <input
        id="case-upload"
        type="file"
        accept=".pdf"
        multiple
        style={{ display: 'none' }}
        onChange={handleFileSelect}
      />
    </div>
  )
}

// MD 预览组件（渲染 Markdown + HTML）
function MDPreview({ url }: { url: string }) {
  const [html, setHtml] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    marked.setOptions({ gfm: true, breaks: true })
    fetch(url)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.text()
      })
      .then(text => {
        setHtml(marked.parse(text) as string)
        setLoading(false)
      })
      .catch(err => {
        setError(err.message)
        setLoading(false)
      })
  }, [url])

  if (loading) return <div style={{ color: '#86868b', fontSize: '14px' }}>加载中...</div>
  if (error) return <div style={{ color: '#ff3b30', fontSize: '14px' }}>加载失败：{error}</div>

  return (
    <div style={{
      width: '100%',
      height: '100%',
      overflow: 'auto',
      padding: '24px',
      background: '#fff',
      borderRadius: '8px',
      fontSize: '13px',
      lineHeight: '1.6',
      fontFamily: 'system-ui, -apple-system, sans-serif',
      color: '#1d1d1f',
      margin: 0
    }}>
      <style>{`
        .md-preview table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 12px; }
        .md-preview th { background: #f5f5f7; font-weight: 600; padding: 8px 10px; border: 1px solid #e5e5e7; text-align: left; }
        .md-preview td { padding: 6px 10px; border: 1px solid #e5e5e7; }
        .md-preview tr:nth-child(even) { background: #fafafa; }
        .md-preview h1, .md-preview h2, .md-preview h3 { margin: 16px 0 8px; color: #1d1d1f; }
        .md-preview img { max-width: 100%; border-radius: 4px; margin: 8px 0; }
        .md-preview p { margin: 8px 0; }
      `}</style>
      <div className="md-preview" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  )
}
