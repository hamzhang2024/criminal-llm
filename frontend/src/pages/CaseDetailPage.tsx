// 案件详情 — orchestrator，保留全部业务逻辑，UI 委托给子组件

import { useState, useCallback, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Upload, FileDown, Scale, Loader2, CheckCircle, FileText } from 'lucide-react'
import { MacOSToolbar, MacOSButton, MacOSCard, PageLayout, StatusBar } from '../components/MacOSLayout'
import { api, API_BASE } from '../api'
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
  // 转换心跳：记录上次进度键（current_pages_done），3 分钟无变化则提示"处理中，请耐心等待"
  const lastConvertKeyRef = useRef('')
  const staleSinceRef = useRef(0)
  const [error, setError] = useState<string | null>(null)
  const [optDecrypt, setOptDecrypt] = useState(false)
  const [optWatermark, setOptWatermark] = useState(false)
  const [optDeleteOriginal, setOptDeleteOriginal] = useState(true)
  const [charges, setCharges] = useState<string[]>([])
  const [notFound, setNotFound] = useState(false)
  const processAbortRef = useRef<AbortController | null>(null)
  const convertPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const extractPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [analysisCompleted, setAnalysisCompleted] = useState(false)

  // 子 hooks
  const {
    files, previewFile, uploading,
    toggleSelect, toggleSelectAll, getSelectedFiles,
    refreshFiles, handleFileSelect,
    handleRemoveFile, handleDeleteMd, handleDeletePdf, handleReconvertMd,
    handleOpenFile, closePreview,
  } = useCaseFiles(caseId, currentStep)

  const {
    evidenceList, evidenceExtracted, extracting, setEvidenceExtracted, setEvidenceList,
    loadEvidence, handleExtractEvidence: extractEvidenceFn,
    handleStopExtract, handleClearEvidence, handleRefreshEvidence,
    checkExtractStatus, pollExtractProgress, stopPolling: stopExtractPolling,
  } = useEvidenceExtraction(caseId)

  const stageHooks = useStageAnalysis(caseId, defendant, charges)
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
    // 验证 caseId 是否有效，防止旧 ID 持续轮询
    api.getCaseInfo(caseId).then(d => {
      if (d.id) {
        setCaseName(d.name); setDefendant(d.defendant)
      }
    }).catch((e: unknown) => {
      // 404 说明案件已被删除，清除 localStorage 中的旧 step 并提示
      if (e instanceof Error && e.message.includes('404')) {
        localStorage.removeItem(`case_${caseId}_step`)
        console.warn(`案件 ${caseId} 已不存在，已清除本地缓存`)
        setNotFound(true)
      }
    })
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

  // === 步骤切换时轮询检测（恢复运行态）===
  useEffect(() => {
    if (!caseId || currentStep === 0) return () => { stopExtractPolling(); if (convertPollRef.current) { clearInterval(convertPollRef.current); convertPollRef.current = null } }
    checkExtractStatus().then(running => {
      if (running) {
        setProcessing(true); setProgress('正在提取证据...')
        // 启动轮询监控提取进度，完成后自动清除状态
        pollExtractProgress()
        return
      }
      // 检查转换轮询
      if (currentStep >= 1) {
        fetch(`${API_BASE}/tasks/${caseId}/convert-status`).then(r => r.json()).then(d => {
          if (d.status === 'running' || d.status === 'pending') {
            setProcessing(true)
            convertPollRef.current = setInterval(async () => {
              try {
                const sr = await fetch(`${API_BASE}/tasks/${caseId}/convert-status`); const sd = await sr.json()
                if (sd.status === 'running') {
                  const c = sd.current || 0, t = sd.total || 0, pd = sd.pages_done || 0, pt = sd.pages_total || 0
                  const pct = t > 0 ? Math.round(c / t * 100) : 0
                  setProgress(`转换中：${c}/${t} 文件${pt > 0 ? ` · ${pd}/${pt} 页` : ''} (${pct}%)`)
                }
                else if (sd.status === 'completed') { clearInterval(convertPollRef.current!); convertPollRef.current = null; setProcessing(false); setProgress('') }
                else if (sd.status === 'failed' || sd.status === 'cancelled') { clearInterval(convertPollRef.current!); convertPollRef.current = null; setProcessing(false); setError(sd.message || '转换失败') }
                else if (sd.status === 'interrupted') { clearInterval(convertPollRef.current!); convertPollRef.current = null; setProcessing(false); setError('上次任务被中断，请点击「转换并提取」重新开始') }
                // pending 或 idle 状态继续等待
              } catch { clearInterval(convertPollRef.current!); convertPollRef.current = null; setProcessing(false) }
            }, 2000)
          } else if (d.status === 'interrupted') {
            // 显示提示让用户知道需要重新转换
            setError('上次转换任务被中断，请点击「转换并提取」重新开始')
          }
        }).catch(() => {})
      }
    })
    return () => { stopExtractPolling(); if (convertPollRef.current) { clearInterval(convertPollRef.current); convertPollRef.current = null } }
  }, [currentStep, caseId])

  // === 步骤 2 加载流水线状态 ===
  useEffect(() => { if (currentStep === 2 && caseId) loadPipelineState() }, [currentStep, caseId, loadPipelineState])

  // === 流水线实时进度轮询 ===
  useEffect(() => {
    if (!pipelineRunning || !caseId || currentPipelineStep < 2) return
    const t = setInterval(async () => {
      try { const p = await api.getPipelineProgress(caseId); if (p.running) setLiveProgress({ message: p.message, current: p.current, total: p.total, elapsed: p.elapsed_seconds || 0 }) } catch { }
    }, 2000)
    return () => clearInterval(t)
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
    let pc = 0
    const pi = setInterval(async () => {
      pc++; if (pc > 600) { clearInterval(pi); setProcessing(false); setProgress(''); setError('分析耗时过长'); return }
      try {
        const pr = await api.getStageProgress(caseId!)
        if (pr.running) { setProgress(pr.message || ''); return }
        const st = await api.getStageStatus(caseId!)
        if (st?.task?.status === 'completed') {
          clearInterval(pi); setAnalysisCompleted(true); setProgress('6 阶段分析全部完成！'); setProcessing(false)
          setTimeout(() => navigate(`/case/${caseId}/report`), 1500)
        } else if (st?.task?.status === 'error') {
          clearInterval(pi); setError(st?.task?.error || '分析出错'); setProgress(''); setProcessing(false)
        } else if ((st?.status || {}).stage_53?.completed) {
          clearInterval(pi); setAnalysisCompleted(true); setProgress('6 阶段分析全部完成！'); setProcessing(false)
          setTimeout(() => navigate(`/case/${caseId}/report`), 1500)
        }
      } catch { }
    }, 3000)
    return () => clearInterval(pi)
  }, [caseId, navigate])

  // === 业务操作 ===

  const handleConvertAllToMd = useCallback(async () => {
    setProcessing(true); setError(null); setProgress('正在转换...')
    try {
      const res = await fetch(`${API_BASE}/tasks/${caseId}/convert-all-to-md`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '转换失败')
      convertPollRef.current = setInterval(async () => {
        try {
          const sr = await fetch(`${API_BASE}/tasks/${caseId}/convert-status`); const sd = await sr.json()
          if (sd.status === 'running') {
            const c = sd.current || 0, t = sd.total || 0, pd = sd.pages_done || 0, pt = sd.pages_total || 0
            const pct = t > 0 ? Math.round(c / t * 100) : 0
            // 心跳：current 与 pages_done 均未变则计时，3 分钟无变化追加提示（大文件转换可能很久）
            const key = `${c}_${pd}`
            if (lastConvertKeyRef.current === key) {
              if (!staleSinceRef.current) staleSinceRef.current = Date.now()
            } else {
              staleSinceRef.current = 0
              lastConvertKeyRef.current = key
            }
            const staleWarn = staleSinceRef.current && (Date.now() - staleSinceRef.current > 180000) ? ' · 处理中，请耐心等待' : ''
            setProgress(`转换中：${c}/${t} 文件${pt > 0 ? ` · ${pd}/${pt} 页` : ''} (${pct}%)${staleWarn}`)
          }
          else if (sd.status === 'completed') {
            clearInterval(convertPollRef.current!); convertPollRef.current = null
            const sc = sd.results?.filter((r: any) => r.success).length || 0
            setProgress(`已转换 ${sc}/${sd.total || 0} 个文件，请点击「提取证据」继续`); setProcessing(false)
            const fd = await api.getStepFiles(caseId!, 2)
            if (Array.isArray(fd)) refreshFiles()
          } else if (sd.status === 'failed' || sd.status === 'cancelled') {
            clearInterval(convertPollRef.current!); convertPollRef.current = null
            throw new Error(sd.message || '转换任务失败')
          } else if (sd.status === 'interrupted') {
            clearInterval(convertPollRef.current!); convertPollRef.current = null
            setProcessing(false); setError('上次任务被中断，请点击「转换并提取」重新开始')
            return // 直接返回，不再 throw
          }
          // pending 或 idle 状态继续等待
        } catch (e) { clearInterval(convertPollRef.current!); convertPollRef.current = null; setProcessing(false); setError(e instanceof Error ? e.message : '转换失败') }
      }, 2000)
      setTimeout(() => { if (convertPollRef.current) { clearInterval(convertPollRef.current); convertPollRef.current = null; setProcessing(false); setProgress('⚠️ 转换超时，请稍后刷新') } }, 900000)
    } catch (err) { setError(err instanceof Error ? err.message : '转换失败'); setProgress(''); setProcessing(false) }
  }, [caseId])

  const handleExtractEvidence = useCallback(async () => {
    extractUserStoppedRef.current = false; extractPollFailuresRef.current = 0
    stopExtractPolling()
    try {
      const sf = await api.getStepFiles(caseId!, 2)
      if (!Array.isArray(sf) || sf.length === 0) {
        const ok = await showConfirm({ title: '需要先进行文件转换', message: '还没有 MD 文件，是否开始转换？', confirmText: '开始转换', cancelText: '取消', variant: 'info' })
        if (ok) handleConvertAllToMd()
        return
      }
    } catch { }
    setProcessing(true); setError(null); setProgress('正在提取证据...')
    try {
      await api.extractEvidence(caseId!)
      startExtractPoll()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '提取失败'
      if (msg.includes('无 MD 文件') || msg.includes('请先完成 PDF 转 MD')) {
        setProcessing(false); setProgress('')
        const ok = await showConfirm({ title: '需要先进行文件转换', message: '是否立即开始转换？', confirmText: '开始转换', cancelText: '取消', variant: 'info' })
        if (ok) handleConvertAllToMd()
      } else { setError(msg); setProgress(''); setProcessing(false) }
    }
  }, [caseId, handleConvertAllToMd])

  const extractUserStoppedRef = useRef(false)
  const extractPollFailuresRef = useRef(0)
  const startExtractPoll = useCallback(() => {
    extractUserStoppedRef.current = false; extractPollFailuresRef.current = 0
    if (extractPollRef.current) clearInterval(extractPollRef.current)
    extractPollRef.current = setInterval(async () => {
      if (extractUserStoppedRef.current) { if (extractPollRef.current) { clearInterval(extractPollRef.current); extractPollRef.current = null } return }
      try {
        const st = await api.getExtractStatus(caseId!)
        extractPollFailuresRef.current = 0
        if (st.status !== 'running') {
          if (extractPollRef.current) { clearInterval(extractPollRef.current); extractPollRef.current = null }
          const data = await api.getEvidenceIndex(caseId!)
          if (data.total_evidence > 0) { setEvidenceList(data.evidence || []); setEvidenceExtracted(true); setProgress(`已提取 ${data.total_evidence} 份证据`) }
          else { setError(data.error_hint || '提取未成功'); setProgress('') }
          setProcessing(false)
        } else {
          const tf = st.total_files || 0, pf = st.processed_files || 0
          setProgress(`正在提取证据... ${pf}/${tf} (${tf > 0 ? Math.round(pf/tf*100) : 0}%)`)
          try { const d = await api.getEvidenceIndex(caseId!); if (d.total_evidence > 0) setEvidenceList(d.evidence || []) } catch {}
        }
      } catch { extractPollFailuresRef.current++; if (extractPollFailuresRef.current >= 3) { if (extractPollRef.current) { clearInterval(extractPollRef.current); extractPollRef.current = null } setProcessing(false); setError('提取过程出错'); setProgress('') } }
    }, 3000)
    setTimeout(() => { if (extractPollRef.current) { clearInterval(extractPollRef.current); extractPollRef.current = null; setProcessing(false); setProgress('⚠️ 提取超时') } }, 900000)
  }, [caseId])

  const handleConvertAndExtract = useCallback(async () => {
    setProcessing(true); setError(null); setProgress('正在转换并提取证据...')
    try {
      const cr = await fetch(`${API_BASE}/tasks/${caseId}/convert-all-to-md`, { method: 'POST' })
      const cd = await cr.json()
      if (!cr.ok) throw new Error(cd.detail || '转换失败')
      await new Promise<void>((resolve, reject) => {
        const pi = setInterval(async () => {
          try {
            const sr = await fetch(`${API_BASE}/tasks/${caseId}/convert-status`); const sd = await sr.json()
            if (sd.status === 'running') {
              const c = sd.current || 0, t = sd.total || 0, pd = sd.pages_done || 0, pt = sd.pages_total || 0
              const pct = t > 0 ? Math.round(c / t * 100) : 0
              setProgress(`正在转换并提取证据（第 1/2 步）：${c}/${t} 文件${pt > 0 ? ` · ${pd}/${pt} 页` : ''} (${pct}%)`)
            }
            else if (sd.status === 'completed') { clearInterval(pi); resolve() }
            else if (sd.status === 'failed' || sd.status === 'cancelled') { clearInterval(pi); reject(new Error(sd.message || '转换失败')) }
            else if (sd.status === 'interrupted') { clearInterval(pi); reject(new Error('上次任务被中断，请点击「转换并提取」重新开始')) }
            // pending 或 idle 状态继续等待
          } catch (e) { clearInterval(pi); reject(e) }
        }, 2000)
        setTimeout(() => { clearInterval(pi); reject(new Error('转换超时')) }, 900000)
      })
      setProgress('正在转换并提取证据（第 2/2 步：提取证据）...')
      await api.extractEvidence(caseId!)
      await new Promise<void>((resolve, reject) => {
        const pi = setInterval(async () => {
          try {
            const st = await api.getExtractStatus(caseId!)
            if (st.status !== 'running') {
              clearInterval(pi); const d = await api.getEvidenceIndex(caseId!)
              if (d.total_evidence > 0) { setEvidenceList(d.evidence || []); setEvidenceExtracted(true) }
              resolve()
            }
          } catch { }
        }, 3000)
        setTimeout(() => { clearInterval(pi); reject(new Error('提取超时')) }, 900000)
      })
      setCurrentStep(2); setProcessing(false)
    } catch (err) { setError(err instanceof Error ? err.message : '操作失败'); setProgress(''); setProcessing(false) }
  }, [caseId])

  const handleRunAnalysis = useCallback(async () => {
    if (!defendant.trim()) { showAlert({ title: '提示', message: '缺少被告人信息', variant: 'warning' }); return }
    setError(null); setProgress('正在触发分析...'); setProcessing(true)
    try {
      const r = await api.runAllStages(caseId!, defendant, charges.length > 0 ? charges : undefined)
      if (!r.success) throw new Error(r.detail || '触发失败')
      setProgress('分析已启动'); pollAnalysisProgress()
    } catch (err) { setError(err instanceof Error ? err.message : '触发失败'); setProgress(''); setProcessing(false) }
  }, [caseId, defendant, charges])

  const handleStart = useCallback(async () => {
    if (currentStep === 0) {
      if (files.length === 0) return
      const needProcess = optDecrypt || optWatermark
      if (!needProcess) {
        setProcessing(true); setProgress('准备文件...')
        try {
          await api.batchProcess(caseId!, 1, files.filter(f => f.status === 'pending').map(f => f.name), { delete_original: optDeleteOriginal })
          setCurrentStep(1)
        } catch (err) { setError(err instanceof Error ? err.message : '失败'); setProgress('') }
        finally { setProcessing(false) }
        return
      }
      setProcessing(true); setProgress('正在处理...')
      try {
        const r = await api.batchProcess(caseId!, 1, files.filter(f => f.status === 'pending').map(f => f.name), { password: password || undefined, remove_watermark: optWatermark, delete_original: optDeleteOriginal })
        if (r.results?.every((x: any) => x.success)) { setCurrentStep(1) }
        else { setError(r.results?.find((x: any) => !x.success)?.error || '处理失败'); setProgress('') }
      } catch (err) { setError(err instanceof Error ? err.message : '处理失败'); setProgress('') }
      finally { setProcessing(false) }
    } else if (currentStep === 1) await handleConvertAndExtract()
    else if (currentStep === 2) navigate(`/case/${caseId}/report`)
  }, [currentStep, files, password, optDecrypt, optWatermark, optDeleteOriginal, handleConvertAndExtract, caseId])

  const StepIcon = steps[currentStep]?.icon || FileText

  // 案件不存在：阻止所有轮询，显示提示
  if (notFound) {
    return (
      <PageLayout>
        <div style={{ display: 'flex', flex: 1, alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 16 }}>
          <div style={{ fontSize: 48 }}>📭</div>
          <h2 style={{ fontSize: 20, fontWeight: 600 }}>案件不存在</h2>
          <p style={{ fontSize: 14, color: 'var(--macos-text-secondary)' }}>该案件可能已被删除或数据已清除</p>
          <button onClick={() => navigate('/')} style={{
            padding: '10px 24px', background: 'var(--macos-accent)', color: '#fff',
            border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 500, cursor: 'pointer'
          }}>← 返回首页</button>
        </div>
      </PageLayout>
    )
  }

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
              <Step1Extract files={files} evidenceList={evidenceList} evidenceExtracted={evidenceExtracted}
                processing={extracting} onExtract={handleExtractEvidence} onStop={handleStopExtract}
                onClear={handleClearEvidence} onRefreshEvidence={handleRefreshEvidence} />
            )}

            {currentStep === 2 && (
              <Step2Analyze caseId={caseId!} defendant={defendant} charges={charges} setCharges={setCharges}
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