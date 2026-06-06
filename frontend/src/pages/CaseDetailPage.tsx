import { useState, useCallback, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Upload, Wand2, FileDown, Scale, CheckCircle, AlertCircle, FileText, ArrowRight, Loader2, Trash2, CheckSquare, Square, XCircle, Play, RefreshCw } from 'lucide-react'
import { MacOSTitlebar, MacOSToolbar, MacOSButton, MacOSCard, MacOSEmptyState, PageLayout, InlineDialog, StatusBar } from '../components/MacOSLayout'
import { api, serveFileUrl, API_BASE } from '../api'
import { showConfirm, showAlert } from '../components/MacOSDialog'
import { marked } from 'marked'

// 配置 marked 使用同步解析
marked.setOptions({ async: false })

/** 安全的 fetch：将网络错误转为友好的中文提示 */
async function safeFetchJson(url: string): Promise<any> {
  try {
    const res = await fetch(url)
    return res.json()
  } catch {
    throw new Error('后端未启动或连接失败，请重新启动应用')
  }
}

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
            background: 'var(--macos-accent)',
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
  const [currentStep, setCurrentStep] = useState(0) // 初始值 0，在 useEffect 中从 localStorage 恢复
  const [files, setFiles] = useState<CaseFile[]>([])
  const [password, setPassword] = useState('')
  const [processing, setProcessing] = useState(false)
  const [progress, setProgress] = useState('')
  const [error, setError] = useState<string | null>(null)

  // 从 localStorage 恢复步骤（确保 caseId 有效）
  useEffect(() => {
    if (caseId) {
      const saved = localStorage.getItem(`case_${caseId}_step`)
      if (saved !== null) {
        const step = parseInt(saved, 10)
        if (!isNaN(step) && step >= 0 && step <= 2) {
          setCurrentStep(step)
        }
      }
    }
  }, [caseId])

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
        // 恢复证据提取状态（stage_51 完成说明证据已提取）
        if (stages.stage_51?.completed) setEvidenceExtracted(true)
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

    // 页面加载时检查是否已有证据（直接读证据索引，不依赖 stage_51）
    api.getEvidenceIndex(caseId!)
      .then(data => {
        if (data?.total_evidence > 0) {
          setEvidenceExtracted(true)
          setEvidenceList(data.evidence || [])
        } else if (data?.error_hint) {
          // 之前提取失败，显示错误提示
          setError(data.error_hint)
        }
      })
      .catch(() => { /* 无证据 */ })

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

    // 清理函数：停止所有轮询
    const cleanup = () => {
      if (extractPollRef.current) {
        clearInterval(extractPollRef.current)
        extractPollRef.current = null
      }
      if (convertPollRef.current) {
        clearInterval(convertPollRef.current)
        convertPollRef.current = null
      }
    }

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
    } else if (currentStep >= 1 && currentStep <= 2) {
      // 步骤 1-2：加载上一步的输出
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

      // 步骤 1-2：检查是否已有证据
      if (currentStep >= 1 && currentStep <= 2) {
        // 检查证据提取是否正在运行
        const checkExtractStatus = async () => {
          try {
            const st = await api.getExtractStatus(caseId!)
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
                  const st2 = await api.getExtractStatus(caseId!)
                  if (st2.status !== 'running') {
                    clearInterval(extractPollRef.current!)
                    extractPollRef.current = null
                    const data2 = await api.getEvidenceIndex(caseId!)
                    setEvidenceList(data2.evidence || [])
                    setEvidenceExtracted(true)
                    setProcessing(false)
                    setProgress(`已提取 ${data2.total_evidence} 份证据`)
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
              // 启动轮询（使用 ref 存储）
              convertPollRef.current = setInterval(async () => {
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
                    if (convertPollRef.current) clearInterval(convertPollRef.current)
                    const results = statusData.results || []
                    const successCount = results.filter((r: any) => r.success).length
                    setProgress(`已转换 ${successCount}/${statusData.total || 0} 个文件`)
                    setProcessing(false)
                    // 重新加载文件
                    const filesData = await api.getStepFiles(caseId!, 2)
                    if (Array.isArray(filesData)) {
                      setFiles(filesData.map((f: any) => ({
                        id: f.id, name: f.name, size: f.size, status: f.status || 'done', source: f.source,
                      })))
                    }
                    // 转换完成，提示用户手动提取证据
                    if (successCount > 0) {
                      setProgress(`已转换 ${successCount}/${data.total || 0} 个文件，请点击「提取证据」按钮继续`)
                    }
                  } else if (st2 === 'failed' || st2 === 'cancelled') {
                    if (convertPollRef.current) clearInterval(convertPollRef.current)
                    setProgress('')
                    setError(statusData.message || '转换任务失败')
                    setProcessing(false)
                  }
                } catch {
                  if (convertPollRef.current) clearInterval(convertPollRef.current)
                }
              }, 2000)
            } else if (st === 'completed') {
              // 已完成：显示完成信息
              const results = data.results || []
              const successCount = results.filter((r: any) => r.success).length
              setProgress(`已转换 ${successCount}/${data.total || 0} 个文件`)
            }
          })
          .catch(() => { /* 无转换任务 */ })
      }
    } else if (currentStep === 2) {
      // 步骤 2：加载 MD 文件用于分析
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

    return cleanup
  }, [caseId, currentStep])
  const [showPassword, setShowPassword] = useState(false)
  // PDF 处理选项
  const [optDecrypt, setOptDecrypt] = useState(false)    // 解密选项（默认关闭）
  const [optWatermark, setOptWatermark] = useState(false) // 去水印选项（默认关闭）
  const [optDeleteOriginal, setOptDeleteOriginal] = useState(true) // 删除原始文件（默认开启）
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

  // 步骤配置 — 整合版：上传文件 → 证据提取 → 案卷分析
  const steps = [
    { id: 'upload', name: '上传文件', icon: Upload, description: '上传 PDF 文件，可选解密/去水印处理' },
    { id: 'convert', name: '证据提取', icon: FileDown, description: '转换为结构化格式 → LLM 提取证据' },
    { id: 'analyze', name: '案卷分析', icon: Scale, description: '6 阶段智能分析（指控要素 → 人物关系 → 事件拆解 → 法律法规 → 控辩对抗 → 三阶层辩护）' },
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
      const result = await api.uploadFiles(caseId, newFiles)

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

  const handleOpenFile = async (file: CaseFile) => {
    // MD 文件始终在 md/ 目录下
    if (file.name.endsWith('.md')) {
      const serveUrl = serveFileUrl(caseId!, file.name, 'md')
      setPreviewFile({ ...file, path: serveUrl, name: file.name })
      return
    }

    // PDF 文件：根据当前步骤确定目录优先级
    let dirsToTry: string[]
    if (currentStep === 0) {
      dirsToTry = ['original']
    } else if (currentStep === 1) {
      dirsToTry = ['processed', 'original']
    } else if (currentStep === 2) {
      dirsToTry = ['md', 'original']
    } else {
      dirsToTry = ['md', 'original']
    }

    // 步骤1：优先使用 processed/ 中对应的 _去水印 文件
    let previewName = file.name
    if (currentStep === 1 && !file.name.includes('_去水印')) {
      const stem = file.name.replace(/\.pdf$/i, '')
      const candidate = `${stem}_去水印.pdf`
      try {
        await fetch(serveFileUrl(caseId!, candidate, 'processed'), { method: 'HEAD' })
        previewName = candidate
      } catch { /* 不存在，用原始文件 */ }
    }

    const dir = dirsToTry[0]
    const serveUrl = serveFileUrl(caseId!, previewName, dir)
    setPreviewFile({ ...file, path: serveUrl, name: previewName })
  }

  const closePreview = () => {
    setPreviewFile(null)
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
    const confirmed = await showConfirm({
      title: '确认删除',
      message: `确定要删除「${file.name}」吗？删除后可重新上传。`,
      variant: 'danger',
    })
    if (!confirmed) return

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
        setProgress(`已删除 ${mdFileName}`)
        setTimeout(() => setProgress(''), 2000)
        // 始终刷新 MD 文件列表（步骤 3）
        const filesData = await api.getStepFiles(caseId!, 3)
        if (Array.isArray(filesData)) {
          setFiles(filesData.map((f: any) => ({
            id: f.id, name: f.name, size: f.size, status: 'pending', source: f.source,
          })))
        }
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : '删除失败'
      showAlert({ title: '删除失败', message: errMsg, variant: 'danger' })
    }
  }, [caseId])

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
        setProgress(`已删除 ${pdfFileName}`)
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
        setProgress(`已删除 ${pdfFileName}`)
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
        setProgress(`已重新转换 ${mdFileName}`)
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
        setProgress('所有文件已处理完成，无需重复处理')
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
        remove_watermark: stepIndex === 1 ? optWatermark : undefined,
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

      setProgress(`${pendingFiles.length} 个文件处理完成！`)
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
  }, [caseId, files, password, optWatermark])

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
  const convertPollRef = useRef<ReturnType<typeof setInterval> | null>(null)  // 转换轮询
  const extractUserStoppedRef = useRef(false)
  const extractPollFailuresRef = useRef(0)

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
        setProgress('6 阶段分析全部完成！')
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

  // 依次执行全部阶段（跳过已完成的）
  const handleRunAllAnalysis = useCallback(async () => {
    if (!defendant.trim()) {
      showAlert({ title: '提示', message: '案件缺少被告人信息，无法开始分析', variant: 'warning' })
      return
    }

    // 读取各阶段完成状态，跳过已完成的
    const stageStatus = await api.getStageStatus(caseId!)
    const stagesMap = stageStatus?.status || {}
    const stages = [1, 2, 3, 4, 5, 6]
    const completedStages = stages.filter(i => stagesMap[`stage_${i}`]?.completed)
    const startFrom = completedStages.length === 6 ? 99 : Math.min(...stages.filter(i => !stagesMap[`stage_${i}`]?.completed))

    if (startFrom > 6) {
      showAlert({ title: '提示', message: '全部分析已完成！', variant: 'info' })
      navigate(`/case/${caseId}/report`)
      return
    }

    // 恢复已有阶段的 UI 状态
    for (const i of completedStages) {
      setStageStatus(prev => ({ ...prev, [i]: 'completed' }))
    }

    setProcessing(true)
    setError(null)
    setProgress(startFrom > 1 ? `从步骤 ${startFrom} 继续分析...` : '正在执行全部分析...')

    for (let i = startFrom; i <= 6; i++) {
      const stage = STAGES.find(s => s.num === i)
      setStageStatus(prev => ({ ...prev, [i]: 'running' }))
      setStageMessages(prev => ({ ...prev, [i]: `正在执行：${stage?.name}...` }))
      setStageErrors(prev => ({ ...prev, [i]: '' }))
      setRunningStage(i)
      setProgress(`${i}/6 — ${stage?.name}`)

      try {
        const result = await api.runSingleStage(caseId!, i, defendant, crimeType || undefined)
        if (!result.success) {
          throw new Error(result.detail || result.error || '阶段执行失败')
        }
        setStageStatus(prev => ({ ...prev, [i]: 'completed' }))
        setStageMessages(prev => ({ ...prev, [i]: '' }))
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : '阶段执行失败'
        setStageStatus(prev => ({ ...prev, [i]: 'error' }))
        setStageErrors(prev => ({ ...prev, [i]: errorMsg }))
        setStageMessages(prev => ({ ...prev, [i]: '' }))
        setRunningStage(null)
        setProcessing(false)
        setProgress('')
        return
      }
      setRunningStage(null)
    }

    setProcessing(false)
    setProgress('全部分析已完成！')
    setTimeout(() => navigate(`/case/${caseId}/report`), 2000)
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
  // 固定 10 并发
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

            setProgress(`已转换 ${successCount}/${totalCount} 个文件${blankFiles.length > 0 ? `，${blankFiles.length} 个失败` : ''}`)
            setCurrentStep(2)

            // 重新加载文件列表
            const filesData = await api.getStepFiles(caseId!, 2)
            if (Array.isArray(filesData)) {
              setFiles(filesData.map((f: any) => ({
                id: f.id, name: f.name, size: f.size, status: f.status || 'done', source: f.source,
              })))
            }

            // 转换完成，提示用户手动点击提取证据
            setProgress(`已转换 ${successCount}/${totalCount} 个文件${blankFiles.length > 0 ? `，${blankFiles.length} 个失败` : ''}`)
            setProcessing(false)
          } else if (st === 'failed' || st === 'cancelled') {
            clearInterval(pollInterval)
            throw new Error(statusData.message || '转换任务失败')
          }
          // 'pending' / 'interrupted' 继续轮询
        } catch (pollErr) {
          clearInterval(pollInterval)
          setProcessing(false)
          let errMsg = '未知错误'
          if (pollErr instanceof Error) {
            errMsg = pollErr.message || '请求失败'
          } else if (typeof pollErr === 'string') {
            errMsg = pollErr
          }
          // 如果错误信息太短或无意义，尝试从状态获取更多信息
          if (errMsg.length < 5 || errMsg === '请求失败') {
            try {
              const statusResp = await fetch(`${API_BASE}/tasks/${caseId}/convert-status`)
              const statusData = await statusResp.json()
              if (statusData.message) {
                errMsg = statusData.message
              }
            } catch {
              // 忽略
            }
          }
          setError(`转换过程出错: ${errMsg}，请刷新页面后重试`)
          console.error('转换轮询错误:', pollErr)
          return
        }
      }, 2000)

      // 15 分钟超时自动停止轮询（后端 MinerU 转换超时为 1 小时）
      setTimeout(() => {
        clearInterval(pollInterval)
        setProcessing(false)
        setProgress('⚠️ 转换超时，任务可能仍在后台运行，请稍后刷新页面查看结果')
      }, 900000)

    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '转换失败'
      setError(errorMsg)
      setProgress('')
      setProcessing(false)
    }
  }, [caseId, processing, defendant, crimeType])

  // 串联转换 + 提取：先全部转 MD，完成后自动提取证据
  const handleConvertAndExtract = useCallback(async () => {
    setProcessing(true)
    setError(null)
    setProgress('正在转换并提取证据（第 1/2 步：PDF 转 MD）...')

    try {
      // ====================
      // Stage 1: 全部转 MD
      // ====================
      const convertResult = await fetch(`${API_BASE}/tasks/${caseId}/convert-all-to-md`, { method: 'POST' })
      const convertData = await convertResult.json()

      if (!convertResult.ok) {
        throw new Error(convertData.detail || convertData.error || '转换失败')
      }

      // 轮询等待转换完成
      await new Promise<void>((resolve, reject) => {
        const pollInterval = setInterval(async () => {
          try {
            const statusResp = await fetch(`${API_BASE}/tasks/${caseId}/convert-status`)
            const statusData = await statusResp.json()
            const st = statusData.status

            if (st === 'running') {
              const cur = statusData.current || 0
              const tot = statusData.total || 0
              const pct = tot > 0 ? Math.round((cur / tot) * 100) : 0
              setProgress(`正在转换并提取证据（第 1/2 步：PDF 转 MD）${cur}/${tot} (${pct}%) — ${statusData.message || ''}`)
            } else if (st === 'completed') {
              clearInterval(pollInterval)
              const results = statusData.results || []
              const successCount = results.filter((r: any) => r.success).length
              const totalCount = statusData.total || 0
              const blankFiles = results.filter((r: any) => !r.success && r.error)

              if (blankFiles.length > 0) {
                const names = blankFiles.map((r: any) => r.file).join('、')
                setError(`${blankFiles.length} 个文件转换失败（内容为空）：${names}。PDF 可能已加密，请先进行去水印处理并输入密码。`)
              }

              setProgress(`转换完成：${successCount}/${totalCount} 个文件。即将开始提取证据...`)

              // 重新加载文件列表
              api.getStepFiles(caseId!, 2).then(data => {
                if (Array.isArray(data)) {
                  setFiles(data.map((f: any) => ({
                    id: f.id, name: f.name, size: f.size, status: f.status || 'done', source: f.source,
                  })))
                }
              }).catch(() => {})

              resolve()
            } else if (st === 'failed' || st === 'cancelled') {
              clearInterval(pollInterval)
              reject(new Error(statusData.message || '转换任务失败'))
            }
          } catch (pollErr) {
            clearInterval(pollInterval)
            let errMsg = '未知错误'
            if (pollErr instanceof Error) errMsg = pollErr.message
            reject(new Error(`转换过程出错: ${errMsg}`))
          }
        }, 2000)

        setTimeout(() => {
          clearInterval(pollInterval)
          reject(new Error('转换超时，任务可能仍在后台运行，请稍后刷新页面查看结果'))
        }, 900000)
      })

      // ====================
      // Stage 2: 提取证据
      // ====================
      setProgress('正在转换并提取证据（第 2/2 步：LLM 提取证据）...')

      extractUserStoppedRef.current = false
      extractPollFailuresRef.current = 0

      // 启动提取
      try {
        await api.extractEvidence(caseId!)
      } catch (extractErr) {
        const msg = extractErr instanceof Error ? extractErr.message : '证据提取启动失败'
        throw new Error(msg)
      }

      // 轮询等待提取完成
      await new Promise<void>((resolve, reject) => {
        const pollInterval = setInterval(async () => {
          if (extractUserStoppedRef.current) {
            clearInterval(pollInterval)
            reject(new Error('用户已停止提取'))
            return
          }

          try {
            const st = await api.getExtractStatus(caseId!)
            extractPollFailuresRef.current = 0

            if (st.status !== 'running') {
              clearInterval(pollInterval)
              try {
                const data = await api.getEvidenceIndex(caseId!)
                const totalEvidence = data.total_evidence || 0
                if (totalEvidence > 0) {
                  setEvidenceList(data.evidence || [])
                  setEvidenceExtracted(true)
                  setProgress(`已完成全部操作！已提取 ${totalEvidence} 份证据`)
                } else {
                  const errorHint = data.error_hint || '证据提取未成功，请检查 LLM 配置或稍后重试'
                  setError(errorHint)
                  setProgress('')
                }
              } catch {
                setProgress('提取已完成，但未能加载证据列表，请刷新页面后查看')
              }
              resolve()
            } else {
              const totalFiles = st.total_files || 0
              const processedFiles = st.processed_files || 0
              const currentFile = st.current_file || ''
              const pct = totalFiles > 0 ? Math.round((processedFiles / totalFiles) * 100) : 0
              if (processedFiles > 0 || totalFiles > 0) {
                setProgress(`正在转换并提取证据（第 2/2 步：LLM 提取证据）${processedFiles}/${totalFiles} (${pct}%) ${currentFile ? '— ' + currentFile : ''}`)
              }
              try {
                const data = await api.getEvidenceIndex(caseId!)
                if (data.total_evidence > 0) setEvidenceList(data.evidence || [])
              } catch { /* ignore */ }
            }
          } catch {
            extractPollFailuresRef.current += 1
            if (extractPollFailuresRef.current >= 3) {
              clearInterval(pollInterval)
              reject(new Error('提取过程出错，请检查后端服务是否正常'))
            }
          }
        }, 3000)

        setTimeout(() => {
          clearInterval(pollInterval)
          reject(new Error('提取超时，任务可能仍在后台运行，请稍后刷新页面查看结果'))
        }, 900000)
      })

      // ====================
      // 全部完成，跳转到步骤 2
      // ====================
      setCurrentStep(2)
      setProcessing(false)
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '操作失败'
      setError(errorMsg)
      setProgress('')
      setProcessing(false)
    }
  }, [caseId])

  // 提取证据（独立按钮，转 MD 后手动触发）
  const handleExtractEvidence = useCallback(async () => {
    extractUserStoppedRef.current = false
    extractPollFailuresRef.current = 0
    if (extractPollRef.current) {
      clearInterval(extractPollRef.current)
      extractPollRef.current = null
    }

    // 先检查是否有 MD 文件
    try {
      const stepFiles = await api.getStepFiles(caseId!, 2)
      if (!Array.isArray(stepFiles) || stepFiles.length === 0) {
        const confirmed = await showConfirm({
          title: '需要先进行文件转换',
          message: '当前案件中还没有转换后的 MD 文件，需要先进行 PDF 转 MD 才能提取证据。\n是否立即开始转换？',
          confirmText: '开始转换',
          cancelText: '取消',
          variant: 'info',
        })
        if (confirmed) {
          handleConvertAllToMd()
        }
        return
      }
    } catch { /* 忽略检查错误 */ }

    setProcessing(true)
    setError(null)
    setProgress('正在提取证据清单...')
    try {
      // 启动提取任务（不等待返回，用 polling 跟踪进度）
      try {
        await api.extractEvidence(caseId!)
      } catch (extractErr) {
        // 后端返回 400（如无 MD 文件）或其他错误
        const msg = extractErr instanceof Error ? extractErr.message : '提取失败'
        throw new Error(msg)
      }

      // 轮询进度 — 加宽初始等待，给后端更多时间启动任务
      extractPollRef.current = setInterval(async () => {
        if (extractUserStoppedRef.current) {
          clearInterval(extractPollRef.current!)
          extractPollRef.current = null
          return
        }
        try {
          const st = await api.getExtractStatus(caseId!)
          // 单次成功，重置失败计数
          extractPollFailuresRef.current = 0

          if (st.status !== 'running') {
            // 任务已结束（idle/cancelled/error），直接读取证据
            clearInterval(extractPollRef.current!)
            extractPollRef.current = null
            try {
              const data = await api.getEvidenceIndex(caseId!)
              const totalEvidence = data.total_evidence || 0
              if (totalEvidence === 0) {
                setEvidenceList([])
                // 优先使用后端返回的错误提示，否则显示通用错误
                const errorHint = data.error_hint || '证据提取未成功，请检查 LLM 配置或稍后重试'
                setError(errorHint)
                setProgress('')
              } else {
                setEvidenceList(data.evidence || [])
                setEvidenceExtracted(true)
                setProgress(`已提取 ${totalEvidence} 份证据`)
              }
            } catch {
              // 读取证据失败，但任务已结束
              setProgress('提取已完成，但未能加载证据列表，请刷新页面后查看')
            }
            setProcessing(false)
          } else {
            // 运行中：更新进度和证据列表
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
          extractPollFailuresRef.current += 1
          // 连续 3 次失败才停止轮询
          if (extractPollFailuresRef.current >= 3) {
            clearInterval(extractPollRef.current!)
            extractPollRef.current = null
            setProcessing(false)
            setProgress('')
            setError('提取过程出错，请检查后端服务是否正常')
          }
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

  // 手动刷新证据列表（用于轮询失败但后台可能已完成提取的情况）
  const handleRefreshEvidence = useCallback(async () => {
    try {
      setProgress('正在检查提取状态...')
      setError(null)
      const st = await api.getExtractStatus(caseId!)
      if (st.status === 'running') {
        setProgress('后台仍在提取中，请继续等待...')
        setProcessing(true)
        return
      }
      setProgress('正在读取证据列表...')
      // 任务已结束，读取证据
      const data = await api.getEvidenceIndex(caseId!)
      const totalEvidence = data.total_evidence || 0
      if (totalEvidence > 0) {
        setEvidenceList(data.evidence || [])
        setEvidenceExtracted(true)
        setProgress(`已提取 ${totalEvidence} 份证据`)
      } else {
        setEvidenceList([])
        setProgress('尚未提取证据，请先点击「提取证据」')
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(`无法连接后端: ${msg}`)
      setProgress('')
    }
  }, [caseId])

  const handleStopExtract = useCallback(async () => {
    extractUserStoppedRef.current = true
    if (extractPollRef.current) {
      clearInterval(extractPollRef.current)
      extractPollRef.current = null
    }
    await api.stopExtractEvidence(caseId!)
    setProgress('⏹ 已停止提取，当前已提取的证据已保存')
    setProcessing(false)
    setEvidenceExtracted(false)
  }, [caseId])

  // 清除证据
  const handleClearEvidence = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/cases/${caseId}/clear-evidence`, { method: 'POST' })
      const data = await res.json()
      if (data.success) {
        setEvidenceList([])
        setEvidenceExtracted(false)
        setProgress('证据已清除')
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
      setProgress('分析任务已启动，请稍候...')
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
          setProgress('6 阶段分析全部完成！')
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
            setProgress('6 阶段分析全部完成！')
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

  // 切换到步骤 2 时加载流水线状态
  useEffect(() => {
    if (currentStep === 2 && caseId) {
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
    if (currentStep === 2 && pipelineStatus[4]) {
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
    if (currentStep === 0) {
      // 步骤 0：处理并继续 — 解密/去水印，完成后跳转到步骤 1
      if (files.length === 0) return

      const needProcess = optDecrypt || optWatermark

      if (!needProcess) {
        // 两个选项都未勾选，直接跳过步骤 1，进入转换步骤
        setCurrentStep(1)
        return
      }

      // 检查解密选项是否开启但未输入密码
      if (optDecrypt && !password) {
        const confirmed = await new Promise<boolean>((resolve) => {
          showConfirm({
            title: '未输入密码',
            message: '已勾选"PDF 有密码"但未输入密码。处理将会失败。\n\n是否继续尝试处理？',
            confirmText: '继续处理',
            cancelText: '取消',
            variant: 'warning',
            onConfirm: () => resolve(true),
            onCancel: () => resolve(false),
          })
        })
        if (!confirmed) return
      }

      setProcessing(true)
      setProgress('正在处理文件，请稍候...')
      try {
        const filesToProcess = files.filter(f => f.status === 'pending')
        if (filesToProcess.length === 0) {
          setProgress('所有文件已处理完成')
          setProcessing(false)
          return
        }
        const result = await api.batchProcess(caseId!, 1, filesToProcess.map(f => f.name), {
          password: password || undefined,
          remove_watermark: optWatermark,
          delete_original: optDeleteOriginal,
        })
        if (result.results) {
          const allDone = result.results.every((r: any) => r.success)
          if (allDone) {
            setProgress(optDeleteOriginal
              ? `${result.results.length} 个文件处理完成，已删除原始文件`
              : `${result.results.length} 个文件处理完成！`)
            setCurrentStep(1)
          } else {
            const failed = result.results.find((r: any) => !r.success)
            if (failed) {
              setError('处理失败：' + failed.error)
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
    } else if (currentStep === 1) {
      // 转换并提取
      await handleConvertAndExtract()
    } else if (currentStep === 2) {
      // 案卷分析：进入报告页面
      navigate('/case/' + caseId + '/report')
    }
  }, [currentStep, files, password, optDecrypt, optWatermark, optDeleteOriginal, handleConvertAndExtract, caseId])

  const StepIcon = steps[currentStep]?.icon || FileText
  const pendingCount = files.filter(f => f.status === 'pending').length
  const doneCount = files.filter(f => f.status === 'done').length

  return (
    <PageLayout>
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* 左侧：案件步骤导航 */}
        <div className="frosted-subtle" style={{ width: 220, borderRight: '1px solid var(--macos-border)', padding: 16, display: 'flex', flexDirection: 'column' }}>
          {/* 返回按钮 */}
          <button
            onClick={() => navigate('/')}
            className="flex-center cursor-pointer"
            style={{
              width: '100%',
              gap: 6,
              padding: '10px 12px',
              marginBottom: 16,
              background: 'var(--macos-accent-light)',
              border: 'none',
              borderRadius: 8,
              fontSize: 13,
              color: 'var(--macos-accent)',
              fontWeight: 500
            }}
          >
            ← 案件管理
          </button>

          {/* 案件信息卡片 */}
          <MacOSCard style={{ marginBottom: 16, padding: 14, background: 'linear-gradient(135deg, rgba(255,255,255,0.98) 0%, rgba(247,247,247,0.95) 100%)' }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--macos-text-tertiary)', textTransform: 'uppercase', marginBottom: 8, letterSpacing: '0.5px' }}>案件</div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--macos-text-primary)', marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{caseName || '未命名案件'}</div>
            <div style={{ fontSize: 12, color: 'var(--macos-text-secondary)', display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: defendant ? '#3b5998' : '#f0a500' }}></span>
              被告人：{defendant || '未指定'}
            </div>
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--macos-border)', display: 'flex', gap: 16, fontSize: 11, color: 'var(--macos-text-tertiary)' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <FileText className="w-3 h-3" />
                {files.length} 文件
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <CheckCircle className="w-3 h-3" color={doneCount > 0 ? '#3b5998' : '#86868b'} />
                {doneCount} 已处理
              </span>
            </div>
          </MacOSCard>

          {/* 步骤导航 */}
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--macos-text-tertiary)', textTransform: 'uppercase', marginBottom: 8, padding: '0 4px' }}>流程</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {steps.map((step, index) => {
                const Icon = step.icon
                const isActive = index === currentStep
                const isDone = index < currentStep

                return (
                  <button
                    key={step.id}
                    onClick={() => setCurrentStep(index)}
                    className="flex-row cursor-pointer"
                    style={{
                      gap: 10,
                      padding: '10px 12px',
                      background: isActive ? 'var(--macos-accent-light)' : 'transparent',
                      border: isActive ? '1px solid var(--macos-accent-border)' : '1px solid transparent',
                      borderRadius: 8,
                      textAlign: 'left',
                      fontSize: 13,
                      color: isActive ? 'var(--macos-accent)' : 'var(--macos-text-primary)',
                      transition: 'all 0.15s',
                    }}
                  >
                    <div style={{
                      width: 24, height: 24, borderRadius: 6,
                      background: isDone ? 'rgba(59, 89, 152, 0.12)' : isActive ? 'var(--macos-accent)' : 'var(--macos-bg-tertiary)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                      {isDone ? (
                        <CheckCircle className="w-4 h-4" color="#3b5998" />
                      ) : (
                        <Icon className="w-4 h-4" color={isActive ? '#fff' : '#86868b'} />
                      )}
                    </div>
                    <span className="font-medium">{step.name}</span>
                  </button>
                )
              })}
            </div>
          </div>
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <MacOSToolbar title={caseName}>
            <div className="flex-row gap-md" style={{ marginRight: 16 }}>
              <span className="text-sm text-secondary">被告人：{defendant}</span>
            </div>
            <div className="flex-row gap-sm">
              {currentStep === 0 && (
                <MacOSButton variant="primary" icon={uploading ? Loader2 : Upload} disabled={uploading} onClick={() => document.getElementById('case-upload')?.click()}>
                  {uploading ? '上传中...' : '添加文件'}
                </MacOSButton>
              )}
              <MacOSButton
                variant="primary"
                icon={processing ? Loader2 : StepIcon}
                disabled={processing || (currentStep === 0 && files.length === 0) || (currentStep === 2 && !pipelineStatus[4] && stageStatus[4] !== 'completed' && stageStatus[5] !== 'completed')}
                onClick={handleStart}
              >
                {processing ? '处理中...' :
                 currentStep === 0 ? (
                   files.length === 0 ? '开始处理' :
                   (optDecrypt || optWatermark) ? '处理并继续' : '跳过并继续'
                 ) :
                 currentStep === 1 ? '转换并提取' :
                 currentStep === 2 ? '查看报告' :
                 '开始' + (steps[currentStep]?.name || '')}
              </MacOSButton>
            </div>
          </MacOSToolbar>

          {/* 错误/进度状态栏 */}
          <StatusBar
            message={error || progress}
            variant={error ? 'error' : processing ? 'processing' : 'success'}
            onDismiss={error ? () => setError(null) : undefined}
            processing={processing}
          />

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
                    background: '#666666', color: '#fff', fontSize: '13px',
                    cursor: 'pointer'
                  }}>删除已处理文件</button>
                </div>
              </div>
            </div>
          )}

          <div style={{ flex: 1, overflow: 'auto', padding: '20px' }}>
            {/* 当前步骤说明 — 简化为一行 */}
            <div className="flex-between" style={{ marginBottom: 12 }}>
              <div>
                <h3 className="text-lg font-semibold">{steps[currentStep]?.name}</h3>
                <p className="text-sm text-secondary">{steps[currentStep]?.description}</p>
              </div>
            </div>

            {/* 步骤 0：处理选项 — 紧凑表单 */}
            {currentStep === 0 && files.length > 0 && (
              <MacOSCard style={{ marginBottom: 12, padding: 14 }}>
                <div className="text-sm font-medium mb-sm">处理选项</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <label className="flex-row gap-sm cursor-pointer">
                    <input type="checkbox" checked={optDecrypt} onChange={(e) => setOptDecrypt(e.target.checked)} style={{ accentColor: 'var(--macos-accent)' }} />
                    <span className="text-sm">PDF 有密码，需要解密</span>
                  </label>
                  {optDecrypt && (
                    <input
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="请输入 PDF 密码"
                      style={{ padding: '6px 10px', border: '1px solid var(--macos-border)', borderRadius: 6, fontSize: 13, width: 200, marginLeft: 20 }}
                    />
                  )}
                  <label className="flex-row gap-sm cursor-pointer">
                    <input type="checkbox" checked={optWatermark} onChange={(e) => setOptWatermark(e.target.checked)} style={{ accentColor: 'var(--macos-accent)' }} />
                    <span className="text-sm">PDF 有水印，需要去除</span>
                  </label>
                  {(optDecrypt || optWatermark) && (
                    <label className="flex-row gap-sm cursor-pointer" style={{ marginTop: 4, paddingTop: 8, borderTop: '1px solid var(--macos-border)' }}>
                      <input type="checkbox" checked={optDeleteOriginal} onChange={(e) => setOptDeleteOriginal(e.target.checked)} style={{ accentColor: 'var(--macos-accent)' }} />
                      <span className="text-sm">处理成功后删除原始文件（节省空间）</span>
                    </label>
                  )}
                </div>
              </MacOSCard>
            )}

            {/* 文件列表 - 步骤 1 时在证据提取之前显示 */}
            {currentStep < 2 && files.length > 0 && (
              <MacOSCard>
                {/* 步骤 1：转MD - 显示待转换的 PDF 列表或已转换的 MD 列表 */}
                {currentStep === 1 ? (
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                    <h4 style={{ fontSize: '14px', fontWeight: '600' }}>
                      {(() => {
                        const doneCount = files.filter(f => f.status === 'done').length
                        const allDone = doneCount === files.length && files.length > 0
                        if (allDone) return `已转换 MD`
                        return doneCount > 0 ? `已转换 ${doneCount}/${files.length}` : `待转换 PDF`
                      })()}
                    </h4>
                    <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)' }}>
                      {(() => {
                        const doneCount = files.filter(f => f.status === 'done').length
                        return doneCount > 0 ? `共 ${files.length} 个文件` : `共 ${files.length} 个文件`
                      })()}
                    </div>
                    {/* 刷新按钮 */}
                    <button
                      onClick={async () => {
                        try {
                          const filesData = await api.getStepFiles(caseId!, currentStep)
                          if (Array.isArray(filesData)) {
                            setFiles(filesData.map((f: any) => ({
                              id: f.id, name: f.name, size: f.size, status: f.status || 'pending', source: f.source,
                            })))
                            setProgress('文件列表已刷新')
                            setTimeout(() => setProgress(''), 1500)
                          }
                        } catch (err) {
                          console.error('刷新文件列表失败:', err)
                        }
                      }}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px', marginLeft: '8px' }}
                      title="刷新文件列表"
                    >
                      <RefreshCw className="w-4 h-4" color="#86868b" />
                    </button>
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
                          <CheckSquare className="w-4 h-4" color="var(--macos-accent)" />
                        ) : (
                          <Square className="w-4 h-4" color="#86868b" />
                        )}
                      </button>
                      <h4 style={{ fontSize: '14px', fontWeight: '600' }}>
                        {currentStep === 0 ? '原始文件' :
                         'MD 文件'}
                      </h4>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)' }}>
                        已选 {getSelectedFiles().length}/{files.length}
                      </div>
                      {/* 刷新按钮 */}
                      <button
                        onClick={async () => {
                          try {
                            const filesData = await api.getStepFiles(caseId!, currentStep)
                            if (Array.isArray(filesData)) {
                              setFiles(filesData.map((f: any) => ({
                                id: f.id, name: f.name, size: f.size, status: f.status || 'pending', source: f.source,
                              })))
                              setProgress('文件列表已刷新')
                              setTimeout(() => setProgress(''), 1500)
                            }
                          } catch (err) {
                            console.error('刷新文件列表失败:', err)
                          }
                        }}
                        style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
                        title="刷新文件列表"
                      >
                        <RefreshCw className="w-4 h-4" color="#86868b" />
                      </button>
                    </div>
                  </div>
                )}
                
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {files.map(file => {
                    // 显示当前步骤的输入文件
                    const inputFile = currentStep === 0 ? file :
                                      currentStep === 1 ? { ...file, name: file.processedPath || file.name } :
                                      file;
                    
                    return (
                      <div key={file.id} style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '12px',
                        padding: '12px',
                        background: file.splitResults ? 'rgba(59, 89, 152, 0.06)' :
                                   file.status === 'done' ? 'rgba(59, 89, 152, 0.04)' :
                                   file.status === 'processing' ? 'var(--macos-accent-surface)' :
                                   file.status === 'error' ? 'rgba(102, 102, 102, 0.04)' : 'var(--macos-bg-secondary)',
                        borderRadius: '8px',
                        border: file.splitResults ? '1px solid rgba(59, 89, 152, 0.15)' :
                                  file.selected ? '1px solid var(--macos-accent-border)' : '1px solid transparent'
                      }}>
                        {/* 步骤 1：状态指示（无复选框，转换是单文件操作） */}
                        {currentStep === 1 ? (
                          file.status === 'done' ? (
                            <div style={{
                              width: '32px', height: '32px', borderRadius: '8px',
                              background: 'rgba(59, 89, 152, 0.1)',
                              display: 'flex', alignItems: 'center', justifyContent: 'center'
                            }}>
                              <CheckCircle className="w-4 h-4" color="#3b5998" />
                            </div>
                          ) : file.status === 'processing' ? (
                            <div style={{
                              width: '32px', height: '32px', borderRadius: '8px',
                              background: 'var(--macos-accent-light)',
                              display: 'flex', alignItems: 'center', justifyContent: 'center'
                            }}>
                              <Loader2 className="w-4 h-4 animate-spin" color="var(--macos-accent)" />
                            </div>
                          ) : file.status === 'error' ? (
                            <div style={{
                              width: '32px', height: '32px', borderRadius: '8px',
                              background: 'rgba(102, 102, 102, 0.1)',
                              display: 'flex', alignItems: 'center', justifyContent: 'center'
                            }}>
                              <XCircle className="w-4 h-4" color="#666666" />
                            </div>
                          ) : (
                            <div style={{
                              width: '32px', height: '32px', borderRadius: '8px',
                              background: 'var(--macos-accent-light)',
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
                              <CheckSquare className="w-4 h-4" color="var(--macos-accent)" />
                            ) : (
                              <Square className="w-4 h-4" color="#86868b" />
                            )}
                          </button>
                        )}
                        {/* 步骤 0：通用状态图标 */}
                        {currentStep === 0 && (
                          <div style={{
                            width: '32px',
                            height: '32px',
                            borderRadius: '8px',
                            background: file.status === 'done' ? 'rgba(59, 89, 152, 0.1)' : 'var(--macos-accent-light)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center'
                          }}>
                            {file.status === 'done' ? (
                              <CheckCircle className="w-4 h-4" color="#3b5998" />
                            ) : file.status === 'processing' ? (
                              <Loader2 className="w-4 h-4 animate-spin" color="var(--macos-accent)" />
                            ) : file.status === 'error' ? (
                              <AlertCircle className="w-4 h-4" color="#666666" />
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
                             currentStep === 1 ? (
                               file.status === 'done' ? '已转换 MD' :
                               file.status === 'processing' ? '转换中...' :
                               '待转换'
                             ) :
                             currentStep === 2 ? '已拆分 MD' :
                             'MD 格式'}
                            {file.error && ` - ${file.error}`}
                          </div>
                        </div>

                        {/* 步骤 1：转换完成标记 */}
                        {currentStep === 1 && file.status === 'done' && (
                          <span style={{
                            padding: '4px 10px', borderRadius: '12px',
                            background: 'rgba(59, 89, 152, 0.1)', color: '#3b5998',
                            fontSize: '12px', fontWeight: '500', whiteSpace: 'nowrap'
                          }}>
                            已转换
                          </span>
                        )}
                        {currentStep === 1 && file.status === 'processing' && (
                          <span style={{
                            padding: '8px 12px',
                            background: 'rgba(255, 149, 0, 0.1)',
                            color: '#ff9500',
                            fontSize: '12px',
                            fontWeight: '500',
                            whiteSpace: 'nowrap'
                          }}>
                            ⏳ 转换中...
                          </span>
                        )}

                        {/* 步骤 1：MD 文件操作按钮 */}
                        {currentStep === 1 && file.status === 'done' && (
                          <>
                            <button
                              onClick={() => handleDeleteMd(file.name)}
                              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
                              title="删除此 MD 文件，删除后可重新转换"
                            >
                              <Trash2 className="w-4 h-4" color="#86868b" />
                            </button>
                            <button
                              onClick={() => handleReconvertMd(file.name)}
                              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
                              title="从 PDF 重新转换此文件"
                            >
                              <RefreshCw className="w-4 h-4" color="#86868b" />
                            </button>
                          </>
                        )}
                        {/* 步骤 1：待转换文件显示删除按钮 */}
                        {currentStep === 1 && file.status !== 'processing' && file.status !== 'done' && (
                          <button
                            onClick={() => handleDeletePdf(file.name)}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
                            title="删除此 PDF，删除后可重新上传转换"
                          >
                            <Trash2 className="w-4 h-4" color="#86868b" />
                          </button>
                        )}

                        {file.status !== 'processing' && currentStep === 0 && (
                          <button
                            onClick={() => handleRemoveFile(file)}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
                          >
                            <Trash2 className="w-4 h-4" color="#86868b" />
                          </button>
                        )}

                        {/* 步骤 2：MD 文件删除和重新转换按钮 */}
                        {file.status !== 'processing' && currentStep >= 2 && file.name.endsWith('.md') && (
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
                              background: 'var(--macos-accent-light)',
                              color: 'var(--macos-accent)',
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

            {/* 证据提取卡片（步骤 1：在文件列表之后显示） */}
            {currentStep === 1 && (() => {
              const mdConversionComplete = files.length > 0 && files.every(f => f.status === 'done')
              return (
              <MacOSCard style={{ marginTop: 12 }}>
                <div className="flex-between mb-md">
                  <div className="flex-row gap-md">
                    <h4 className="text-md font-semibold">证据提取</h4>
                    {evidenceList.length > 0 && <span className="text-xs text-secondary">{evidenceList.length} 份</span>}
                  </div>
                  <div className="flex-row gap-sm">
                    {processing ? (
                      <MacOSButton variant="secondary" onClick={handleStopExtract} style={{ color: '#ff9500', borderColor: '#ff9500' }}>停止</MacOSButton>
                    ) : evidenceExtracted ? (
                      <>
                        <MacOSButton variant="secondary" onClick={handleExtractEvidence}>重新提取</MacOSButton>
                        <MacOSButton variant="secondary" onClick={handleClearEvidence} style={{ color: 'var(--macos-danger)', borderColor: 'var(--macos-danger)' }}>清除</MacOSButton>
                      </>
                    ) : mdConversionComplete ? (
                      <MacOSButton variant="primary" onClick={handleExtractEvidence}>提取证据</MacOSButton>
                    ) : (
                      <MacOSButton variant="secondary" disabled>请先转换 PDF</MacOSButton>
                    )}
                  </div>
                </div>
                {evidenceList.length > 0 && (
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
                            background: 'var(--macos-accent-light)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            fontSize: '12px', fontWeight: '600', color: 'var(--macos-accent)'
                          }}>{ev.id}</div>
                          <div style={{ flex: 1, overflow: 'hidden' }}>
                            <div style={{ fontSize: '13px', fontWeight: '500', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {ev.name}
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--macos-text-secondary)' }}>
                              {ev.type} · {ev.source}{ev.page_range ? ' · ' + ev.page_range : ''}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                )}
                {!evidenceExtracted && !mdConversionComplete && (
                  <div style={{ fontSize: '12px', color: 'var(--macos-text-tertiary)', padding: '12px 0' }}>
                    请先完成全部文件的转换，然后再提取证据
                  </div>
                )}
                {evidenceExtracted && (
                  <div style={{ fontSize: '12px', color: '#3b5998', padding: '12px 0' }}>
                    已完成证据提取
                  </div>
                )}
              </MacOSCard>
              )
            })()}

            {/* 步骤 2：案卷分析 */}
            {currentStep === 2 && (
              <>
                {/* 证据文件列表 */}
                {evidenceList.length > 0 && (
                  <MacOSCard style={{ marginBottom: 12 }}>
                    <div className="flex-between mb-sm">
                      <div className="flex-row gap-sm">
                        <h4 className="text-md font-semibold">证据文件</h4>
                        <span className="text-xs text-secondary">{evidenceList.length} 份</span>
                      </div>
                      <button
                        onClick={async () => {
                          try {
                            const data = await api.getEvidenceIndex(caseId!)
                            if (data.total_evidence > 0) {
                              setEvidenceList(data.evidence || [])
                              setProgress('证据列表已刷新')
                              setTimeout(() => setProgress(''), 1500)
                            }
                          } catch (err) {
                            console.error('刷新证据列表失败:', err)
                          }
                        }}
                        style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
                        title="刷新证据列表"
                      >
                        <RefreshCw className="w-4 h-4" color="#86868b" />
                      </button>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '180px', overflowY: 'auto' }}>
                      {evidenceList.map((ev: any) => (
                        <div key={ev.id} style={{
                          display: 'flex', alignItems: 'center', gap: '10px',
                          padding: '8px 10px',
                          background: 'var(--macos-bg-secondary)',
                          borderRadius: '6px',
                          fontSize: '12px'
                        }}>
                          <div style={{
                            width: '24px', height: '24px', borderRadius: '6px',
                            background: 'var(--macos-accent-light)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            fontSize: '11px', fontWeight: '600', color: 'var(--macos-accent)'
                          }}>{ev.id}</div>
                          <div style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {ev.name}
                          </div>
                          <div style={{ color: 'var(--macos-text-tertiary)', fontSize: '11px' }}>
                            {ev.type}
                          </div>
                          {ev.md_file && (
                            <button
                              onClick={async () => {
                                const mdPath = `${API_BASE}/cases/${caseId}/serve-file?file_path=${encodeURIComponent(ev.md_file)}&dir=evidence`
                                setPreviewFile({ id: ev.id, name: ev.md_file, size: 0, status: 'done', path: mdPath })
                              }}
                              style={{
                                padding: '4px 8px',
                                background: 'var(--macos-accent-light)',
                                border: 'none',
                                borderRadius: '4px',
                                cursor: 'pointer',
                                fontSize: '11px',
                                color: 'var(--macos-accent)'
                              }}
                            >预览</button>
                          )}
                        </div>
                      ))}
                    </div>
                  </MacOSCard>
                )}

                {/* 被告人信息 + 罪名输入 */}
                <MacOSCard style={{ marginBottom: '12px' }}>
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
                <MacOSCard style={{ marginBottom: '12px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                    <h4 style={{ fontSize: '14px', fontWeight: '600', margin: 0 }}>分析阶段（不建议并行处理）</h4>
                    <button
                      onClick={handleRunAllAnalysis}
                      disabled={!evidenceExtracted || runningStage !== null}
                      style={{
                        padding: '6px 14px', borderRadius: '6px',
                        border: 'none',
                        background: (!evidenceExtracted || runningStage !== null) ? '#d1d1d6' : 'var(--macos-accent)',
                        color: '#fff', fontSize: '13px', fontWeight: '500',
                        cursor: (!evidenceExtracted || runningStage !== null) ? 'not-allowed' : 'pointer'
                      }}
                    >
                      全部分析
                    </button>
                  </div>
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
                  {evidenceExtracted && !pipelineStatus[4] && (
                    <div style={{
                      padding: '8px 12px', borderRadius: '8px', marginBottom: '12px',
                      background: 'rgba(0,122,255,0.08)', border: '1px solid rgba(0,122,255,0.2)',
                      fontSize: '12px', color: 'var(--macos-accent)',
                      display: 'flex', alignItems: 'center', gap: '8px'
                    }}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
                      证据已提取，请等待阶段 4（法律法规）分析完成后即可查看报告
                    </div>
                  )}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {STAGES.map(stage => {
                      const status = stageStatus[stage.num] || 'idle'
                      const isRunning = runningStage === stage.num
                      const msg = stageMessages[stage.num]
                      const errMsg = stageErrors[stage.num]

                      // 检查前置阶段是否完成
                      const canStartStage = (num: number) => {
                        const idx = STAGES.findIndex(s => s.num === num)
                        if (idx <= 0) return true // 阶段 1 无前置
                        for (let i = 0; i < idx; i++) {
                          if (stageStatus[STAGES[i].num] !== 'completed') return false
                        }
                        return true
                      }

                      const evidenceDisabled = !evidenceExtracted
                      const seqDisabled = !canStartStage(stage.num)
                      // 整体禁用：证据未完成 或 前序阶段未完成
                      const analysisDisabled = evidenceDisabled || seqDisabled

                      return (
                        <div key={stage.num} style={{
                          display: 'flex', alignItems: 'center', gap: '12px',
                          padding: '12px',
                          borderRadius: '8px',
                          border: `1px solid ${status === 'completed' ? 'rgba(59,89,152,0.2)' : status === 'error' ? 'rgba(102,102,102,0.15)' : 'var(--macos-border)'}`,
                          background: status === 'completed' ? 'rgba(59,89,152,0.04)' : status === 'error' ? 'rgba(102,102,102,0.03)' : 'var(--macos-bg-secondary)',
                          opacity: analysisDisabled ? 0.5 : 1,
                          transition: 'opacity 0.2s'
                        }}>
                          {/* 状态图标 */}
                          <div style={{
                            width: '28px', height: '28px', borderRadius: '50%',
                            background: status === 'completed' ? 'rgba(59,89,152,0.1)' : isRunning ? 'var(--macos-accent-light)' : status === 'error' ? 'rgba(102,102,102,0.1)' : 'var(--macos-bg-tertiary)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            fontSize: '14px', fontWeight: '600', flexShrink: 0,
                            color: status === 'completed' ? '#3b5998' : isRunning ? 'var(--macos-accent)' : status === 'error' ? '#666666' : '#86868b'
                          }}>
                            {status === 'completed' ? '✓' : isRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : stage.num}
                          </div>

                          {/* 阶段信息 */}
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontSize: '13px', fontWeight: '500' }}>{stage.name}</div>
                            {msg && <div style={{ fontSize: '11px', color: 'var(--macos-accent)' }}>{msg}</div>}
                            {errMsg && <div style={{ fontSize: '11px', color: '#666666' }}>{errMsg}</div>}
                            {!msg && !errMsg && seqDisabled && (
                              <div style={{ fontSize: '11px', color: '#86868b' }}>
                                等待前序阶段完成
                              </div>
                            )}
                            {!msg && !errMsg && !seqDisabled && !isRunning && <div style={{ fontSize: '11px', color: 'var(--macos-text-tertiary)' }}>{stage.desc}</div>}
                          </div>

                          {/* 操作按钮 */}
                          <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
                            {status === 'completed' && (
                              <>
                                <button onClick={() => handleViewStage(stage.num)} disabled={analysisDisabled} style={{
                                  padding: '4px 10px', borderRadius: '6px',
                                  border: '1px solid var(--macos-border)', background: 'transparent',
                                  color: analysisDisabled ? '#d1d1d6' : 'var(--macos-accent)',
                                  fontSize: '12px', cursor: analysisDisabled ? 'not-allowed' : 'pointer'
                                }}>查看</button>
                                <button onClick={() => handleClearStage(stage.num)} disabled={analysisDisabled} style={{
                                  padding: '4px 10px', borderRadius: '6px',
                                  border: analysisDisabled ? '1px solid #d1d1d6' : '1px solid #666666', background: 'transparent',
                                  color: analysisDisabled ? '#d1d1d6' : '#666666',
                                  fontSize: '12px', cursor: analysisDisabled ? 'not-allowed' : 'pointer'
                                }}>清除</button>
                              </>
                            )}
                            {status === 'error' && (
                              <button onClick={() => handleRunStage(stage.num)} disabled={analysisDisabled} style={{
                                padding: '4px 10px', borderRadius: '6px',
                                border: 'none', background: analysisDisabled ? '#d1d1d6' : '#666666',
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
                                background: (!defendant.trim() || analysisDisabled) ? '#d1d1d6' : 'var(--macos-accent)',
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
                color: 'var(--macos-accent)',
                fontWeight: '500'
              }}
            >
              ← 返回
            </button>
            <span style={{ fontSize: '13px', color: 'var(--macos-text-secondary)', flex: 1 }}>
              {previewFile.name}
            </span>
          </div>

          {previewFile.name.endsWith('.md') ? (
            <div style={{ flex: 1, overflow: 'auto', background: '#fff', padding: '24px' }}>
              <MDPreview url={previewFile.path!} />
            </div>
          ) : (
            /* PDF 文件：直接用浏览器内嵌 PDF 预览 */
            <div style={{ flex: 1, overflow: 'hidden', background: '#1a1a1e' }}>
              <iframe
                src={previewFile.path!}
                style={{ width: '100%', height: '100%', border: 'none' }}
                title={previewFile.name}
              />
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
    </PageLayout>
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
        // 把 md 中的图片相对路径（如 ./xxx_images/yyy.jpg）改写成 serve-file URL
        // url 形如 /api/cases/<caseId>/serve-file?file_path=<mdName>&dir=md
        const m = url.match(/\/cases\/([^/]+)\/serve-file/)
        const caseId = m ? m[1] : null
        const rewritten = caseId
          ? text.replace(
              /(!\[[^\]]*\]\(|<img[^>]+src=["'])\.?\/?([^/"')\s]+_images)\/([^)"'\s]+)/g,
              (_full, prefix, imagesDir, fileName) => {
                const u = `${API_BASE}/cases/${caseId}/serve-file?file_path=${encodeURIComponent(fileName)}&dir=${encodeURIComponent(`md/${imagesDir}`)}`
                return `${prefix}${u}`
              }
            )
          : text
        setHtml(marked.parse(rewritten) as string)
        setLoading(false)
      })
      .catch(err => {
        setError(err.message)
        setLoading(false)
      })
  }, [url])

  if (loading) return <div style={{ color: '#86868b', fontSize: '14px' }}>加载中...</div>
  if (error) return <div style={{ color: '#666666', fontSize: '14px' }}>加载失败：{error}</div>

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
