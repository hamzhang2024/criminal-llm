// 案件详情 — orchestrator，保留全部业务逻辑，UI 委托给子组件

import { useState, useCallback, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Upload, FileDown, Scale, Loader2, CheckCircle, FileText } from 'lucide-react'
import { MacOSToolbar, MacOSButton, MacOSCard, PageLayout, StatusBar } from '../components/MacOSLayout'
import { api, API_BASE } from '../api'
import type { ConvertStatus } from '../api'
import { showConfirm, showAlert } from '../components/MacOSDialog'
import { FileList, Step0Upload, Step1Extract, Step2Analyze, Preview } from './CaseDetailPage/components'
import type { CaseFile, PreviewFile } from './CaseDetailPage/hooks/useCaseFiles'
import { useCaseFiles } from './CaseDetailPage/hooks/useCaseFiles'
import { useEvidenceExtraction } from './CaseDetailPage/hooks/useEvidenceExtraction'
import { useStageAnalysis } from './CaseDetailPage/hooks/useStageAnalysis'

export function CaseDetailPage() {
  const { caseId } = useParams()
  const navigate = useNavigate()
  const [caseName, setCaseName] = useState('加载中...')
  const [defendant, setDefendant] = useState('')
  const [currentStep, setCurrentStep] = useState(0)
  const [password, setPassword] = useState('')
  const [processing, setProcessing] = useState(false)
  const [progress, setProgress] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [optDecrypt, setOptDecrypt] = useState(false)
  const [optWatermark, setOptWatermark] = useState(false)
  const [optDeleteOriginal, setOptDeleteOriginal] = useState(true)
  const [crimeType, setCrimeType] = useState('')
  const processAbortRef = useRef<AbortController | null>(null)
  /** 转换任务 SSE 订阅清理函数（取消订阅 + 超时定时器） */
  const convertSubRef = useRef<(() => void) | null>(null)
  /** 证据提取 SSE 订阅清理函数（取消订阅 + 超时定时器） */
  const extractSubRef = useRef<(() => void) | null>(null)
  /** 流水线进度 SSE 订阅清理函数 */
  const pipelineSubRef = useRef<(() => void) | null>(null)
  const [analysisCompleted, setAnalysisCompleted] = useState(false)

  // 安全获取 caseId 的辅助函数
  const requireCaseId = useCallback(() => {
    if (!caseId) {
      setError('案件 ID 无效')
      navigate('/')
      return null
    }
    return caseId
  }, [caseId, navigate])

  // 子 hooks
  const {
    files, previewFile, uploading,
    toggleSelect, toggleSelectAll, getSelectedFiles,
    refreshFiles, handleFileSelect,
    handleRemoveFile, handleDeleteMd, handleDeletePdf, handleReconvertMd,
    handleOpenFile, closePreview,
  } = useCaseFiles(caseId, currentStep)

  const {
    evidenceList, evidenceExtracted, extracting, stopping, setEvidenceExtracted, setEvidenceList,
    loadEvidence, handleExtractEvidence: extractEvidenceFn,
    handleStopExtract, handleClearEvidence, handleRefreshEvidence,
    checkExtractStatus, stopPolling: stopExtractPolling,
  } = useEvidenceExtraction(caseId)

  const stageHooks = useStageAnalysis(caseId, defendant, crimeType)
  const {
    stageStatus, setStageStatus, stageMessages, stageErrors, runningStage, setRunningStage,
    handleRunStage, handleRunAllAnalysis, handleStopStage,
    handleClearStage, handleViewStage,
    pipelineStatus, pipelineRunning, currentPipelineStep, stepResults,
    analysisState, setAnalysisState, nextStep, setNextStep, liveProgress, setLiveProgress,
    executePipelineStep, executeAllSteps, executeSingleStep,
    handleResumeAnalysis, loadPipelineState,
    wikiPages, selectedWikiPage, wikiContent, wikiLoading,
    loadWikiPages, loadWikiPage,
    evidenceSummaries, evidenceOther, evidenceContent, evidenceLoading,
    expandedCategories, expandedEvidenceGroups,
    selectedAnalysisCard, evidenceAnalysisFiles, selectedEvidenceAnalysis,
    contradictionFilesList, selectedContradictionFile, analysisContent,
    loadEvidenceData, loadEvidenceAnalysisFiles, loadAnalysisContent, loadEvidenceContent,
  } = stageHooks

  // 步骤常量
  const steps = [
    { id: 'upload', name: '上传文件', icon: Upload, description: '上传 PDF 文件，可选解密/去水印处理' },
    { id: 'convert', name: '证据提取', icon: FileDown, description: '转换为结构化格式 → LLM 提取证据' },
    { id: 'analyze', name: '案卷分析', icon: Scale, description: '6 阶段智能分析（指控要素 → 人物关系 → 事件拆解 → 法律法规 → 控辩对抗 → 三阶层辩护）' },
  ]
  const doneCount = files.filter(f => f.status === 'done').length

  // === localStorage 持久化 ===
  useEffect(() => {
    if (caseId) {
      const saved = localStorage.getItem(`case_${caseId}_step`)
      if (saved !== null) { const s = parseInt(saved, 10); if (!isNaN(s) && s >= 0 && s <= 2) setCurrentStep(s) }
    }
  }, [caseId])
  useEffect(() => { if (caseId) localStorage.setItem(`case_${caseId}_step`, String(currentStep)) }, [caseId, currentStep])

  // === 页面加载：案件信息 + 状态恢复 ===
  useEffect(() => {
    if (!caseId) return
    api.getCaseInfo(caseId).then(d => { if (d.id) { setCaseName(d.name); setDefendant(d.defendant) } }).catch(() => {})
    // 恢复阶段状态
    api.getStageStatus(caseId).then(status => {
      const stages = status?.status || {}
      const newStatus: Record<number, 'idle' | 'completed'> = {}
      for (const [key, num] of Object.entries({ stage_1: 1, stage_2: 2, stage_3: 3, stage_4: 4, stage_5: 5, stage_6: 6 })) {
        if (stages[key]?.completed) newStatus[num] = 'completed'
      }
      if (Object.keys(newStatus).length > 0) setStageStatus(prev => ({ ...prev, ...newStatus }))
      if (stages.stage_51?.completed) setEvidenceExtracted(true)
      if (stages.stage_5?.completed && stages.stage_51?.completed && stages.stage_52?.completed && stages.stage_53?.completed && stages.stage_6?.completed) setAnalysisCompleted(true)
      if (status?.task?.status === 'running') {
        const rs = status.task.current_stage
        if (rs) { setStageStatus(prev => ({ ...prev, [rs]: 'running' })); setRunningStage(rs) }
      }
    }).catch(() => {})
    // 恢复证据状态
    loadEvidence()
    // 恢复断点状态
    api.getAnalysisState(caseId).then(s => { if (s.state) { setAnalysisState(s.state); setNextStep(s.next_step) } }).catch(() => {})
  }, [caseId])

  /**
   * 订阅转换任务进度（SSE），返回 Promise。
   * - completed → resolve(status)
   * - failed/cancelled/interrupted → reject(Error)
   * - running → 调用 onProgress(status)
   * 超时（2h）→ reject。订阅清理存入 convertSubRef。
   */
  const streamConvert = useCallback(async (
    onProgress: (sd: ConvertStatus) => void,
  ): Promise<ConvertStatus> => {
    if (!caseId) throw new Error('案件 ID 无效')
    return new Promise<ConvertStatus>((resolve, reject) => {
      if (convertSubRef.current) { convertSubRef.current(); convertSubRef.current = null }
      const unsubscribe = api.subscribeConvertStatus(
        caseId,
        (sd) => {
          if (sd.status === 'running' || sd.status === 'pending') {
            onProgress(sd)
          } else if (sd.status === 'completed') {
            if (convertSubRef.current) { convertSubRef.current(); convertSubRef.current = null }
            resolve(sd)
          } else if (sd.status === 'failed' || sd.status === 'cancelled') {
            if (convertSubRef.current) { convertSubRef.current(); convertSubRef.current = null }
            // 优先用结构化错误详情
            const errDetail = sd.error_details as any
            const errMsg = errDetail && typeof errDetail === 'object'
              ? (errDetail.message || errDetail.raw || sd.message || '转换失败')
              : (typeof errDetail === 'string' && errDetail ? errDetail : (sd.message || '转换失败'))
            reject(new Error(errMsg))
          } else if (sd.status === 'interrupted') {
            if (convertSubRef.current) { convertSubRef.current(); convertSubRef.current = null }
            reject(new Error('上次任务被中断，请点击「转换并提取」重新开始'))
          }
        },
        () => { /* EventSource 自动重连，忽略单次错误 */ },
      )
      const timeoutId = setTimeout(() => {
        if (convertSubRef.current) { convertSubRef.current(); convertSubRef.current = null }
        reject(new Error('转换超时（2小时），后端可能仍在运行'))
      }, 7200000)
      convertSubRef.current = () => { unsubscribe(); clearTimeout(timeoutId) }
    })
  }, [caseId])

  // === 步骤切换时轮询检测（恢复运行态）===
  useEffect(() => {
    if (!caseId || currentStep === 0) return () => { stopExtractPolling(); if (convertSubRef.current) { convertSubRef.current(); convertSubRef.current = null } }
    checkExtractStatus().then(running => {
      if (running) {
        // 后端仍在提取，恢复 processing 状态（轮询由下方专用 useEffect 启动）
        setProcessing(true); setProgress('正在提取证据...')
        return
      }
      // 检查转换任务是否在运行（SSE 恢复进度）
      if (currentStep >= 1) {
        fetch(`${API_BASE}/tasks/${caseId}/convert-status`).then(r => r.json()).then(async (d) => {
          if (d.status === 'running' || d.status === 'pending') {
            setProcessing(true)
            try {
              await streamConvert((sd) => {
                const c = sd.current || 0, t = sd.total || 0
                setProgress(`转换中：${c}/${t} (${t > 0 ? Math.round(c/t*100) : 0}%)`)
              })
              setProcessing(false); setProgress('')
            } catch (e) {
              setProcessing(false)
              setError(e instanceof Error ? e.message : '转换失败')
            }
          } else if (d.status === 'interrupted') {
            // 显示提示让用户知道需要重新转换
            setError('上次转换任务被中断，请点击「转换并提取」重新开始')
          }
        }).catch(() => {})
      }
    })
    return () => { stopExtractPolling(); if (convertSubRef.current) { convertSubRef.current(); convertSubRef.current = null } }
  }, [currentStep, caseId, streamConvert])

  // === 步骤 2 加载流水线状态 ===
  useEffect(() => { if (currentStep === 2 && caseId) loadPipelineState() }, [currentStep, caseId, loadPipelineState])

  // === 流水线实时进度（SSE 推送） ===
  useEffect(() => {
    if (!pipelineRunning || !caseId || currentPipelineStep < 2) return
    if (pipelineSubRef.current) { pipelineSubRef.current(); pipelineSubRef.current = null }
    pipelineSubRef.current = api.subscribePipelineProgress(
      caseId,
      (p) => { if (p.running) setLiveProgress({ message: p.message || '', current: p.current || 0, total: p.total || 0, elapsed: p.elapsed_seconds || 0 }) },
    )
    return () => { if (pipelineSubRef.current) { pipelineSubRef.current(); pipelineSubRef.current = null } }
  }, [pipelineRunning, caseId, currentPipelineStep])

  // === Wiki 和证据数据加载 ===
  useEffect(() => { if (pipelineStatus[4]) { loadWikiPages(); loadEvidenceData(); loadEvidenceAnalysisFiles() } }, [pipelineStatus[4], loadWikiPages, loadEvidenceData, loadEvidenceAnalysisFiles])
  useEffect(() => { if (selectedWikiPage) loadWikiPage(selectedWikiPage) }, [selectedWikiPage, loadWikiPage])
  useEffect(() => {
    if (selectedAnalysisCard >= 0 && selectedAnalysisCard <= 4 && pipelineStatus[4])
      loadAnalysisContent(selectedAnalysisCard, selectedEvidenceAnalysis)
  }, [selectedAnalysisCard, selectedEvidenceAnalysis, selectedContradictionFile, contradictionFilesList, pipelineStatus[4], loadAnalysisContent])

  // === 分析进度轮询 ===
  const pollAnalysisProgress = useCallback(() => {
    if (!caseId) return () => {}
    let finished = false
    // SSE 推送 {progress, task} 合并对象，一次拿到进度与任务终态
    const unsubscribe = api.subscribeStageProgress(
      caseId,
      async (data) => {
        if (finished) return
        const pr = data.progress
        const task = data.task
        if (pr.running) { setProgress(pr.message || ''); return }
        // progress 不运行时查 status 判断阶段完成（task 终态或 stage_53 完成）
        const st = await api.getStageStatus(caseId).catch(() => null)
        if (task?.status === 'completed' || (st?.status || {}).stage_53?.completed) {
          finished = true; unsubscribe(); clearTimeout(timeoutId)
          setAnalysisCompleted(true); setProgress('6 阶段分析全部完成！'); setProcessing(false)
          setTimeout(() => navigate(`/case/${caseId}/report`), 1500)
        } else if (task?.status === 'error') {
          finished = true; unsubscribe(); clearTimeout(timeoutId)
          setError(task?.error || '分析出错'); setProgress(''); setProcessing(false)
        }
      },
    )
    // 超时保护（30 分钟，原 600 次 * 3s）
    const timeoutId = setTimeout(() => {
      if (!finished) { finished = true; unsubscribe(); setProcessing(false); setProgress(''); setError('分析耗时过长') }
    }, 1800000)
    return () => { finished = true; unsubscribe(); clearTimeout(timeoutId) }
  }, [caseId, navigate])

  // === 业务操作 ===

  const handleConvertAllToMd = useCallback(async () => {
    if (!caseId) { setError('案件 ID 无效'); return }
    setProcessing(true); setError(null); setProgress('正在转换...')
    try {
      const res = await fetch(`${API_BASE}/tasks/${caseId}/convert-all-to-md`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '转换失败')
      const sd = await streamConvert((d) => {
        const c = d.current || 0, t = d.total || 0
        setProgress(`转换中：${c}/${t} (${t > 0 ? Math.round(c/t*100) : 0}%)${d.message ? ' — ' + d.message : ''}`)
      })
      const sc = sd.results?.filter((r: any) => r.success).length || 0
      setProgress(`已转换 ${sc}/${sd.total || 0} 个文件，请点击「提取证据」继续`); setProcessing(false)
      const fd = await api.getStepFiles(caseId, 2)
      if (Array.isArray(fd)) refreshFiles()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '转换失败'
      if (msg.includes('被中断')) { setProcessing(false); setError(msg); setProgress('') }
      else { setError(msg); setProgress(''); setProcessing(false) }
    }
  }, [caseId, streamConvert, refreshFiles])

  // 检查模型是否支持证据提取/分析（仅显示警告，不阻止操作）
  const checkModelSupport = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/config`)
      const config = await res.json()
      // 显示警告信息（如果有）
      if (config.model_warning) {
        const proceed = await showConfirm({
          title: '模型能力提示',
          message: `${config.model_warning}\n\n是否继续？`,
          confirmText: '继续',
          cancelText: '取消',
          variant: 'warning',
        })
        if (!proceed) return false
      }
      return true
    } catch {
      return true // 无法检测时允许继续
    }
  }, [])

  const handleExtractEvidence = useCallback(async () => {
    if (!caseId) { setError('案件 ID 无效'); return }
    // 检查模型能力
    if (!(await checkModelSupport())) return

    extractUserStoppedRef.current = false; extractPollFailuresRef.current = 0
    stopExtractPolling()
    try {
      const sf = await api.getStepFiles(caseId, 2)
      if (!Array.isArray(sf) || sf.length === 0) {
        const ok = await showConfirm({ title: '需要先进行文件转换', message: '还没有 MD 文件，是否开始转换？', confirmText: '开始转换', cancelText: '取消', variant: 'info' })
        if (ok) handleConvertAllToMd()
        return
      }
    } catch { }
    setProcessing(true); setError(null); setProgress('正在提取证据...')
    try {
      await api.extractEvidence(caseId)
      startExtractPoll()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '提取失败'
      if (msg.includes('无 MD 文件') || msg.includes('请先完成 PDF 转 MD')) {
        setProcessing(false); setProgress('')
        const ok = await showConfirm({ title: '需要先进行文件转换', message: '是否立即开始转换？', confirmText: '开始转换', cancelText: '取消', variant: 'info' })
        if (ok) handleConvertAllToMd()
      } else { setError(msg); setProgress(''); setProcessing(false) }
    }
  }, [caseId, handleConvertAllToMd, checkModelSupport])

  /**
   * 订阅转换任务进度（SSE），返回 Promise。
   * - completed → resolve(status)
   * - failed/cancelled/interrupted → reject(Error)
   * - running → 调用 onProgress(status)
   * 超时（2h）→ reject。订阅清理存入 convertSubRef。
   */
  const extractUserStoppedRef = useRef(false)
  const extractPollFailuresRef = useRef(0)
  const startExtractPoll = useCallback(() => {
    if (!caseId) return
    extractUserStoppedRef.current = false; extractPollFailuresRef.current = 0
    // 关闭已有订阅
    if (extractSubRef.current) { extractSubRef.current(); extractSubRef.current = null }

    // 处理单条状态：返回 true 表示进入终态（应停止订阅）
    // st 含动态错误结构（error_details），用 any 保留原容错收窄逻辑
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handleStatus = async (st: any): Promise<boolean> => {
      if (extractUserStoppedRef.current) return true
      extractPollFailuresRef.current = 0
      if (st.status !== 'running') {
        const data = await api.getEvidenceIndex(caseId)
        if (data.total_evidence > 0) { setEvidenceList(data.evidence || []); setEvidenceExtracted(true); setProgress(`已提取 ${data.total_evidence} 份证据`) }
        else {
          // 优先使用后端结构化错误详情（type + message + hint）
          const errDetail = st.error_details
          let errMsg = '提取未成功'
          if (errDetail) {
            if (Array.isArray(errDetail) && errDetail[0]) {
              const e = errDetail[0]
              errMsg = e.hint ? `${e.message}（${e.hint}）` : (e.message || errMsg)
            } else if (typeof errDetail === 'object') {
              const e = errDetail as any
              errMsg = e.hint ? `${e.message}（${e.hint}）` : (e.message || errMsg)
            } else if (typeof errDetail === 'string') {
              errMsg = errDetail
            }
          }
          setError(errMsg || data.error_hint || '提取未成功'); setProgress('')
        }
        setProcessing(false)
        return true
      }
      const tf = st.total_files || 0, pf = st.processed_files || 0
      const pct = tf > 0 ? Math.round(pf/tf*100) : 0
      // 构建详细进度信息：文件名 + 耗时 + LLM 等待状态
      const parts: string[] = [`正在提取证据... ${pf}/${tf} (${pct}%)`]
      if (st.current_file) {
        // 截断过长的文件名
        const fname = st.current_file.length > 40 ? st.current_file.slice(0, 37) + '...' : st.current_file
        parts.push(`当前: ${fname}`)
      }
      if (st.llm_waiting) {
        const latency = st.llm_latency ? `${Math.round(st.llm_latency)}s` : ''
        parts.push(`等待 LLM 响应${latency ? ` (${latency})` : ''}`)
      }
      // 重试状态可见化
      if (st.retry_count > 0) {
        const reasonMap: Record<string, string> = {
          timeout: '超时',
          rate_limit: '限流',
          general_error: '错误',
        }
        const reason = reasonMap[st.retry_reason] || st.retry_reason || '错误'
        const wait = st.retry_wait_seconds ? `${st.retry_wait_seconds}s 后重试` : '重试中'
        parts.push(`重试中（第 ${st.retry_count} 次，原因：${reason}，${wait}）`)
      }
      if (st.elapsed_seconds) {
        const mins = Math.floor(st.elapsed_seconds / 60)
        const secs = st.elapsed_seconds % 60
        parts.push(`已耗时 ${mins}:${secs.toString().padStart(2, '0')}`)
      }
      if (st.eta_seconds != null && st.eta_seconds > 0) {
        const etaMins = Math.floor(st.eta_seconds / 60)
        const etaSecs = st.eta_seconds % 60
        parts.push(`预计剩余 ${etaMins}:${etaSecs.toString().padStart(2, '0')}`)
      }
      setProgress(parts.join(' · '))
      try { const d = await api.getEvidenceIndex(caseId); if (d.total_evidence > 0) setEvidenceList(d.evidence || []) } catch {}
      return false
    }

    // SSE 订阅：后端主动推送状态，EventSource 自动重连
    const unsubscribe = api.subscribeExtractStatus(
      caseId,
      async (st) => {
        const terminal = await handleStatus(st)
        if (terminal && extractSubRef.current) { extractSubRef.current(); extractSubRef.current = null }
      },
      () => {
        // 连续错误兜底（EventSource 会自动重连，此处仅做超时保护下的失败计数）
        extractPollFailuresRef.current++
        if (extractPollFailuresRef.current >= 10 && extractSubRef.current) {
          extractSubRef.current(); extractSubRef.current = null
          setProcessing(false); setError('提取过程出错（SSE 连接持续失败）'); setProgress('')
        }
      },
    )
    // 2 小时超时保护
    const timeoutId = setTimeout(() => {
      if (extractSubRef.current) { extractSubRef.current(); extractSubRef.current = null; setProcessing(false); setProgress('⚠️ 提取超时（2小时），后端可能仍在运行，请稍后刷新查看结果') }
    }, 7200000)
    extractSubRef.current = () => { unsubscribe(); clearTimeout(timeoutId) }
  }, [caseId])

  // 页面挂载/刷新时：若后端仍在提取，恢复轮询（修复刷新后一直显示"提取中"不更新）
  useEffect(() => {
    if (!caseId || currentStep === 0) return
    let cancelled = false
    checkExtractStatus().then(running => {
      if (cancelled || !running) return
      // 后端在跑但本地没有订阅，启动 SSE 订阅恢复进度
      if (!extractSubRef.current) {
        setProcessing(true)
        setProgress('正在提取证据...')
        startExtractPoll()
      }
    }).catch(() => {})
    return () => { cancelled = true }
  }, [caseId, currentStep, startExtractPoll, checkExtractStatus])

  const handleConvertAndExtract = useCallback(async () => {
    if (!caseId) { setError('案件 ID 无效'); return }
    // 检查模型能力
    if (!(await checkModelSupport())) return

    setProcessing(true); setError(null); setProgress('正在转换并提取证据...')
    try {
      const cr = await fetch(`${API_BASE}/tasks/${caseId}/convert-all-to-md`, { method: 'POST' })
      const cd = await cr.json()
      if (!cr.ok) throw new Error(cd.detail || '转换失败')
      await streamConvert((sd) => {
        const c = sd.current || 0, t = sd.total || 0
        const pct = t > 0 ? Math.round(c/t*100) : 0
        const parts: string[] = [`正在转换并提取证据（第 1/2 步）${c}/${t} (${pct}%)`]
        // 展示当前文件名
        if (sd.current_file) {
          const fname = typeof sd.current_file === 'string' ? sd.current_file : (Array.isArray(sd.current_file) && sd.current_file[0]) || ''
          if (fname) {
            const shortName = fname.length > 35 ? fname.slice(0, 32) + '...' : fname
            parts.push(`当前: ${shortName}`)
          }
        }
        // 展示后端 message（含成功/失败计数）
        if (sd.message) parts.push(sd.message)
        setProgress(parts.join(' · '))
      })
      setProgress('正在转换并提取证据（第 2/2 步：提取证据）...')
      await api.extractEvidence(caseId)
      await new Promise<void>((resolve, reject) => {
        const unsubscribe = api.subscribeExtractStatus(
          caseId,
          async (st) => {
            if (st.status !== 'running' && st.status !== 'idle') {
              unsubscribe()
              clearTimeout(timeoutId)
              const d = await api.getEvidenceIndex(caseId)
              if (d.total_evidence > 0) { setEvidenceList(d.evidence || []); setEvidenceExtracted(true) }
              resolve()
            }
          },
        )
        const timeoutId = setTimeout(() => { unsubscribe(); reject(new Error('提取超时（2小时），后端可能仍在运行')) }, 7200000)
      })
      setCurrentStep(2); setProcessing(false)
    } catch (err) { setError(err instanceof Error ? err.message : '操作失败'); setProgress(''); setProcessing(false) }
  }, [caseId, checkModelSupport, streamConvert])

  const handleRunAnalysis = useCallback(async () => {
    if (!caseId) { setError('案件 ID 无效'); return }
    // 检查模型能力
    if (!(await checkModelSupport())) return

    if (!defendant.trim()) { showAlert({ title: '提示', message: '缺少被告人信息', variant: 'warning' }); return }
    setError(null); setProgress('正在触发分析...'); setProcessing(true)
    try {
      const r = await api.runAllStages(caseId, defendant, crimeType || undefined)
      if (!r.success) throw new Error(r.detail || '触发失败')
      setProgress('分析已启动'); pollAnalysisProgress()
    } catch (err) { setError(err instanceof Error ? err.message : '触发失败'); setProgress(''); setProcessing(false) }
  }, [caseId, defendant, crimeType, checkModelSupport])

  const handleStart = useCallback(async () => {
    if (!caseId) { setError('案件 ID 无效'); return }
    if (currentStep === 0) {
      if (files.length === 0) return
      const needProcess = optDecrypt || optWatermark
      if (!needProcess) {
        setProcessing(true); setProgress('准备文件...')
        try {
          await api.batchProcess(caseId, 1, files.filter(f => f.status === 'pending').map(f => f.name), { delete_original: optDeleteOriginal })
          setCurrentStep(1)
        } catch (err) { setError(err instanceof Error ? err.message : '失败'); setProgress('') }
        finally { setProcessing(false) }
        return
      }
      setProcessing(true); setProgress('正在处理...')
      try {
        const r = await api.batchProcess(caseId, 1, files.filter(f => f.status === 'pending').map(f => f.name), { password: password || undefined, remove_watermark: optWatermark, delete_original: optDeleteOriginal })
        if (r.results?.every((x: any) => x.success)) { setCurrentStep(1) }
        else { setError(r.results?.find((x: any) => !x.success)?.error || '处理失败'); setProgress('') }
      } catch (err) { setError(err instanceof Error ? err.message : '处理失败'); setProgress('') }
      finally { setProcessing(false) }
    } else if (currentStep === 1) await handleConvertAndExtract()
    else if (currentStep === 2) navigate(`/case/${caseId}/report`)
  }, [currentStep, files, password, optDecrypt, optWatermark, optDeleteOriginal, handleConvertAndExtract, caseId])

  const StepIcon = steps[currentStep]?.icon || FileText

  return (
    <PageLayout>
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* 左侧导航 */}
        <div className="frosted-subtle" style={{ width: 220, borderRight: '1px solid var(--macos-border)', padding: 16, display: 'flex', flexDirection: 'column' }}>
          <button onClick={() => navigate('/')} className="flex-center cursor-pointer" style={{
            width: '100%', gap: 6, padding: '10px 12px', marginBottom: 16,
            background: 'var(--macos-accent-light)', border: 'none', borderRadius: 8,
            fontSize: 13, color: 'var(--macos-accent)', fontWeight: 500
          }}>← 案件管理</button>
          <MacOSCard style={{ marginBottom: 16, padding: 14, background: 'linear-gradient(135deg, rgba(255,255,255,0.98) 0%, rgba(247,247,247,0.95) 100%)' }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--macos-text-tertiary)', textTransform: 'uppercase', marginBottom: 8, letterSpacing: '0.5px' }}>案件</div>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{caseName || '未命名案件'}</div>
            <div style={{ fontSize: 12, color: 'var(--macos-text-secondary)', display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: defendant ? '#3b5998' : '#f0a500' }}></span>
              被告人：{defendant || '未指定'}
            </div>
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--macos-border)', display: 'flex', gap: 16, fontSize: 11, color: 'var(--macos-text-tertiary)' }}>
              <span className="flex-row gap-xs"><FileText className="w-3 h-3" />{files.length} 文件</span>
              <span className="flex-row gap-xs"><CheckCircle className="w-3 h-3" color={doneCount > 0 ? '#3b5998' : '#86868b'} />{doneCount} 已处理</span>
            </div>
          </MacOSCard>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--macos-text-tertiary)', textTransform: 'uppercase', marginBottom: 8, padding: '0 4px' }}>流程</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {steps.map((step, i) => {
                const Icon = step.icon, act = i === currentStep, done = i < currentStep
                return (
                  <button key={step.id} onClick={() => setCurrentStep(i)} className="flex-row cursor-pointer" style={{
                    gap: 10, padding: '10px 12px', background: act ? 'var(--macos-accent-light)' : 'transparent',
                    border: act ? '1px solid var(--macos-accent-border)' : '1px solid transparent',
                    borderRadius: 8, textAlign: 'left', fontSize: 13,
                    color: act ? 'var(--macos-accent)' : 'var(--macos-text-primary)',
                  }}>
                    <div style={{ width: 24, height: 24, borderRadius: 6, background: done ? 'rgba(59,89,152,0.12)' : act ? 'var(--macos-accent)' : 'var(--macos-bg-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      {done ? <CheckCircle className="w-4 h-4" color="#3b5998" /> : <Icon className="w-4 h-4" color={act ? '#fff' : '#86868b'} />}
                    </div>
                    <span className="font-medium">{step.name}</span>
                  </button>
                )
              })}
            </div>
          </div>
        </div>

        {/* 右侧内容 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <MacOSToolbar title={caseName}>
            <span className="text-sm text-secondary" style={{ marginRight: 16 }}>被告人：{defendant}</span>
            <div className="flex-row gap-sm">
              {currentStep === 0 && (
                <MacOSButton variant="primary" icon={uploading ? Loader2 : Upload} disabled={uploading} onClick={() => document.getElementById('case-upload')?.click()}>
                  {uploading ? '上传中...' : '添加文件'}
                </MacOSButton>
              )}
              <MacOSButton variant="primary" icon={processing ? Loader2 : StepIcon}
                disabled={processing || (currentStep === 0 && files.length === 0) || (currentStep === 2 && !pipelineStatus[4] && stageStatus[4] !== 'completed' && stageStatus[5] !== 'completed')}
                onClick={handleStart}
              >
                {processing ? '处理中...' : currentStep === 0 ? (files.length === 0 ? '开始处理' : (optDecrypt || optWatermark) ? '处理并继续' : '跳过并继续') : currentStep === 1 ? '转换并提取' : currentStep === 2 ? '查看报告' : '开始'}
              </MacOSButton>
            </div>
          </MacOSToolbar>

          <StatusBar message={error || progress} variant={error ? 'error' : processing ? 'processing' : 'success'} onDismiss={error ? () => setError(null) : undefined} processing={processing} />

          <div style={{ flex: 1, overflow: 'auto', padding: '20px' }}>
            <div className="flex-between" style={{ marginBottom: 12 }}>
              <div>
                <h3 className="text-lg font-semibold">{steps[currentStep]?.name}</h3>
                <p className="text-sm text-secondary">{steps[currentStep]?.description}</p>
              </div>
            </div>

            {currentStep === 0 && files.length > 0 && (
              <Step0Upload {...{ optDecrypt, setOptDecrypt, optWatermark, setOptWatermark, optDeleteOriginal, setOptDeleteOriginal, password, setPassword }} />
            )}

            {currentStep < 2 && files.length > 0 && (
              <FileList files={files} currentStep={currentStep} uploading={uploading}
                toggleSelect={toggleSelect} toggleSelectAll={toggleSelectAll} getSelectedFiles={getSelectedFiles}
                refreshFiles={refreshFiles} onRemoveFile={handleRemoveFile}
                onDeleteMd={handleDeleteMd} onDeletePdf={handleDeletePdf} onReconvertMd={handleReconvertMd}
                onOpenFile={handleOpenFile} onUploadClick={() => document.getElementById('case-upload')?.click()} />
            )}

            {currentStep === 1 && (
              <Step1Extract caseId={caseId || undefined} files={files} evidenceList={evidenceList} evidenceExtracted={evidenceExtracted}
                processing={extracting} stopping={stopping} onExtract={handleExtractEvidence} onStop={handleStopExtract}
                onClear={handleClearEvidence} onRefreshEvidence={handleRefreshEvidence} />
            )}

            {currentStep === 2 && caseId && (
              <Step2Analyze caseId={caseId} defendant={defendant} crimeType={crimeType} setCrimeType={setCrimeType}
                evidenceList={evidenceList} evidenceExtracted={evidenceExtracted}
                stageStatus={stageStatus} runningStage={runningStage} stageMessages={stageMessages} stageErrors={stageErrors}
                onRunStage={handleRunStage} onRunAll={handleRunAllAnalysis} onStopStage={handleStopStage}
                onClearStage={handleClearStage} onViewStage={handleViewStage}
                onPreviewEvidence={(mdFile, evId) => {
                  const mdPath = `${API_BASE}/cases/${caseId}/serve-file?file_path=${encodeURIComponent(mdFile)}&dir=evidence`
                  handleOpenFile({ id: String(evId), name: mdFile, size: 0, status: 'done', path: mdPath } as unknown as CaseFile)
                }}
                onRefreshEvidence={handleRefreshEvidence} onRefreshFiles={refreshFiles}
                pipelineStatus={pipelineStatus} />
            )}
          </div>
        </div>
      </div>

      {previewFile && <Preview file={previewFile as unknown as PreviewFile} onClose={closePreview} />}
      <input id="case-upload" type="file" accept=".pdf" multiple style={{ display: 'none' }} onChange={handleFileSelect} />
    </PageLayout>
  )
}