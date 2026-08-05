// 案卷分析 Hook — 6 阶段状态管理 + 流水线状态

import { useState, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, API_BASE } from '../../../api'
import { showAlert } from '../../../components/MacOSDialog'

export const STAGES = [
  { num: 1, name: '指控要素', desc: '读取起诉书，提取指控要素' },
  { num: 2, name: '人物关系', desc: '构建人物关系图谱' },
  { num: 3, name: '事件拆解', desc: '梳理事件时间线，拆解事件' },
  { num: 4, name: '法律法规', desc: '梳理涉案法律法规' },
  { num: 5, name: '综合辩护', desc: '证据分析 + 矛盾分析 + 三阶层辩护' },
  { num: 6, name: '控辩对抗', desc: '红蓝对抗，生成攻防对照表' },
]

export const PIPELINE_STEPS = [
  { num: 1, name: '合并笔录', desc: '按人名+类型合并笔录' },
  { num: 2, name: '逐次总结', desc: '每次笔录单独 LLM 总结' },
  { num: 3, name: '矛盾分析', desc: '多次笔录者对比差异' },
  { num: 4, name: '案件 Wiki', desc: 'LLM Wiki 模式构建证据知识库' },
  { num: 4.5, name: '控辩对抗', desc: '红蓝对抗，生成攻防对照表' },
  { num: 4.75, name: '辩护思路确认', desc: '律师确认辩护思路（卡点，确认后才生成辩护意见）' },
  { num: 5, name: '辩护意见', desc: '综合前 4 步形成辩护意见' },
]

export function useStageAnalysis(caseId: string | undefined, defendant: string, charges: string[]) {
  const navigate = useNavigate()

  // 6 阶段状态
  const [stageStatus, setStageStatus] = useState<Record<number, 'idle' | 'running' | 'completed' | 'error'>>({})
  const [stageMessages, setStageMessages] = useState<Record<number, string>>({})
  const [stageErrors, setStageErrors] = useState<Record<number, string>>({})
  const [runningStage, setRunningStage] = useState<number | null>(null)
  const stageAbortRef = useRef<Record<number, AbortController | null>>({})

  // 流水线状态
  const [pipelineStatus, setPipelineStatus] = useState<Record<number | string, boolean>>({})
  const [pipelineRunning, setPipelineRunning] = useState(false)
  const [currentPipelineStep, setCurrentPipelineStep] = useState<number>(0)
  const [stepResults, setStepResults] = useState<Record<number, any>>({})
  const [analysisState, setAnalysisState] = useState<any>(null)
  const [nextStep, setNextStep] = useState<number | null>(null)
  // 辩护思路（步骤 4.75）：待确认标记 + 面板刷新计数器
  const [strategyAwaiting, setStrategyAwaiting] = useState(false)
  const [strategyRefreshKey, setStrategyRefreshKey] = useState(0)

  // Wiki 浏览状态
  const [wikiPages, setWikiPages] = useState<Array<{path: string; filename: string}>>([])
  const [selectedWikiPage, setSelectedWikiPage] = useState<string>('')
  const [wikiContent, setWikiContent] = useState('')
  const [wikiLoading, setWikiLoading] = useState(false)

  // 证据浏览状态
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

  // 实时进度
  const [liveProgress, setLiveProgress] = useState<{ message: string; current: number; total: number; elapsed: number } | null>(null)

  // 运行单个阶段
  const handleRunStage = useCallback(async (stageNum: number) => {
    if (!defendant.trim() || !caseId) return
    if (stageAbortRef.current[stageNum]) return

    setStageStatus(prev => ({ ...prev, [stageNum]: 'running' }))
    setStageMessages(prev => ({ ...prev, [stageNum]: `正在执行阶段 ${stageNum}：${STAGES.find(s => s.num === stageNum)?.name}...` }))
    setStageErrors(prev => ({ ...prev, [stageNum]: '' }))
    setRunningStage(stageNum)

    const controller = new AbortController()
    stageAbortRef.current[stageNum] = controller

    try {
      const result = await api.runSingleStage(caseId, stageNum, defendant, charges.length > 0 ? charges : [])
      if (!result.success) throw new Error(result.detail || result.error || '阶段执行失败')

      setStageStatus(prev => ({ ...prev, [stageNum]: 'completed' }))
      setStageMessages(prev => ({ ...prev, [stageNum]: '' }))
      setRunningStage(null)

      // 检查是否全部完成
      const status = await api.getStageStatus(caseId)
      const stages = status?.status || {}
      const allDone = [1, 2, 3, 4, 5, 6].every(s => stages[`stage_${s}`]?.completed)
      if (allDone) {
        setTimeout(() => navigate(`/case/${caseId}/report`), 2000)
      }
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        await handleClearStage(stageNum)
        setStageStatus(prev => ({ ...prev, [stageNum]: 'idle' }))
      } else {
        setStageStatus(prev => ({ ...prev, [stageNum]: 'error' }))
        setStageErrors(prev => ({ ...prev, [stageNum]: err instanceof Error ? err.message : '阶段执行失败' }))
      }
      setStageMessages(prev => ({ ...prev, [stageNum]: '' }))
      setRunningStage(null)
    }
  }, [caseId, defendant, charges, navigate])

  // 全部分析
  const handleRunAllAnalysis = useCallback(async () => {
    if (!defendant.trim() || !caseId) return

    const stageStatusData = await api.getStageStatus(caseId)
    const stagesMap = stageStatusData?.status || {}
    const stages = [1, 2, 3, 4, 5, 6]
    const completedStages = stages.filter(i => stagesMap[`stage_${i}`]?.completed)
    const startFrom = completedStages.length === 6 ? 99 : Math.min(...stages.filter(i => !stagesMap[`stage_${i}`]?.completed))

    if (startFrom > 6) {
      showAlert({ title: '提示', message: '全部分析已完成！', variant: 'info' })
      navigate(`/case/${caseId}/report`)
      return
    }

    for (const i of completedStages) {
      setStageStatus(prev => ({ ...prev, [i]: 'completed' }))
    }

    for (let i = startFrom; i <= 6; i++) {
      const stage = STAGES.find(s => s.num === i)
      setStageStatus(prev => ({ ...prev, [i]: 'running' }))
      setStageMessages(prev => ({ ...prev, [i]: `正在执行：${stage?.name}...` }))
      setRunningStage(i)

      try {
        const result = await api.runSingleStage(caseId, i, defendant, charges.length > 0 ? charges : [])
        if (!result.success) throw new Error(result.detail || result.error || '阶段执行失败')
        setStageStatus(prev => ({ ...prev, [i]: 'completed' }))
      } catch (err) {
        setStageStatus(prev => ({ ...prev, [i]: 'error' }))
        setStageErrors(prev => ({ ...prev, [i]: err instanceof Error ? err.message : '阶段执行失败' }))
        setRunningStage(null)
        return
      }
      setRunningStage(null)
    }

    setTimeout(() => navigate(`/case/${caseId}/report`), 2000)
  }, [caseId, defendant, charges, navigate])

  // 停止阶段
  const handleStopStage = useCallback((stageNum: number) => {
    stageAbortRef.current[stageNum]?.abort()
  }, [])

  // 清理阶段输出
  const handleClearStage = useCallback(async (stageNum: number) => {
    if (!caseId) return
    try {
      const res = await fetch(`${API_BASE}/cases/${caseId}/clear-stage/${stageNum}`, { method: 'POST' })
      const data = await res.json()
      if (data.success) {
        setStageStatus(prev => ({ ...prev, [stageNum]: 'idle' }))
        setStageMessages(prev => ({ ...prev, [stageNum]: '' }))
        setStageErrors(prev => ({ ...prev, [stageNum]: '' }))
      }
    } catch { /* ignore */ }
  }, [caseId])

  // 查看阶段 Markdown
  const handleViewStage = useCallback(async (stageNum: number) => {
    if (!caseId) return
    try {
      const result = await api.getStageMarkdown(caseId, stageNum)
      const mdContent = result?.content || '无内容'
      showAlert({
        title: `${STAGES.find(s => s.num === stageNum)?.name} - 分析结果`,
        message: mdContent.substring(0, 3000) + (mdContent.length > 3000 ? '\n\n...内容过长，请在报告中查看完整版本' : ''),
        variant: 'info'
      })
    } catch { /* ignore */ }
  }, [caseId])

  // ========== 流水线操作 ==========

  // 返回步骤结果数据（失败返回 false）；4.75 返回 data.awaiting_confirmation 供调用方判断卡点
  const executePipelineStep = useCallback(async (step: number): Promise<any> => {
    if (!defendant.trim() || !caseId) {
      showAlert({ title: '提示', message: '案件缺少被告人信息，无法开始分析', variant: 'warning' })
      return false
    }
    setPipelineRunning(true)
    setCurrentPipelineStep(step)
    try {
      const result = await api.runPipelineStep(caseId, step, defendant, charges.length > 0 ? charges : [])
      if (!result.success) throw new Error(result.detail || result.error || `步骤 ${step} 执行失败`)
      setStepResults(prev => ({ ...prev, [step]: result.data }))
      setPipelineStatus(prev => ({ ...prev, [step]: true }))
      return result.data
    } catch (err) {
      throw err
    } finally {
      setPipelineRunning(false)
      setCurrentPipelineStep(0)
      setLiveProgress(null)
    }
  }, [caseId, defendant, charges])

  const executeAllSteps = useCallback(async () => {
    if (!defendant.trim() || !caseId) {
      showAlert({ title: '提示', message: '案件缺少被告人信息，无法开始分析', variant: 'warning' })
      return
    }
    // 4.75 是律师确认卡点：返回 awaiting_confirmation 即中断，由确认面板接管步骤 5
    for (const step of [1, 2, 3, 4, 4.5, 4.75]) {
      if (!pipelineStatus[step]) {
        const data = await executePipelineStep(step)
        if (!data) return
        if (step === 4.75 && data?.awaiting_confirmation === true) {
          setStrategyAwaiting(true)
          setStrategyRefreshKey(k => k + 1)  // 触发确认面板重新拉取建议
          return
        }
      }
    }
    // 循环结束（4.75 已确认）：步骤 5 已完成则直达报告
    if (pipelineStatus[5]) {
      navigate(`/case/${caseId}/report`)
    }
  }, [caseId, defendant, charges, pipelineStatus, executePipelineStep, navigate])

  const executeSingleStep = useCallback(async (step: number) => {
    if (pipelineStatus[step] || pipelineRunning) return
    await executePipelineStep(step)
  }, [pipelineStatus, pipelineRunning, executePipelineStep])

  // 从断点恢复
  const handleResumeAnalysis = useCallback(async () => {
    if (!defendant.trim() || !caseId) {
      showAlert({ title: '提示', message: '案件缺少被告人信息，无法继续分析', variant: 'warning' })
      return
    }
    setPipelineRunning(true)
    try {
      const result = await api.resumePipeline(caseId, defendant, charges.length > 0 ? charges : [])
      if (result.success) {
        // 4.75 卡点：待确认时刷新确认面板并停留，由律师确认后驱动步骤 5
        if (result.data?.awaiting_confirmation === true) {
          setStrategyAwaiting(true)
          setStrategyRefreshKey(k => k + 1)
        } else if (result.all_done) {
          navigate(`/case/${caseId}/report`)
        }
      } else {
        throw new Error(result.detail || result.error || '断点恢复失败')
      }
    } catch (err) {
      showAlert({ title: '断点恢复失败', message: err instanceof Error ? err.message : '未知错误', variant: 'danger' })
    } finally {
      setPipelineRunning(false)
      setCurrentPipelineStep(0)
      setLiveProgress(null)
    }
  }, [caseId, defendant, charges, navigate])

  // 加载流水线状态
  const loadPipelineState = useCallback(async () => {
    if (!caseId) return
    try {
      const statusData = await api.getPipelineStatus(caseId)
      const status: Record<number, boolean> = {}
      for (let i = 1; i <= 5; i++) {
        if (statusData.status?.[`step_${i}`]?.completed) status[i] = true
      }
      // 步骤 4.5（控辩对抗）单独透出
      if (statusData.status?.['step_4.5']?.completed) status[4.5] = true
      setPipelineStatus(status)

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
    // 步骤 4.75 待确认状态（驱动步骤指示器高亮 + 确认面板）
    try {
      const ds = await api.getDefenseStrategy(caseId)
      setStrategyAwaiting(ds?.status === 'awaiting_confirmation')
    } catch { /* ignore */ }
  }, [caseId])

  // Wiki
  const loadWikiPages = useCallback(async () => {
    if (!caseId) return
    try {
      const data = await api.getWikiIndex(caseId)
      if (data.pages?.length > 0) {
        setWikiPages(data.pages)
        setSelectedWikiPage(data.pages[0].path)
      }
    } catch { /* ignore */ }
  }, [caseId])

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

  // 证据数据
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
      if (contrData.files?.length > 0) {
        setContradictionFilesList(contrData.files)
        setSelectedContradictionFile(contrData.files[0].filename)
      }
    } catch { /* ignore */ }
  }, [caseId])

  const loadAnalysisContent = useCallback(async (type: number, subPath?: string) => {
    if (!caseId) return
    setAnalysisContent('')
    try {
      let path = ''
      switch (type) {
        case 0: path = '01-指控要素.md'; break
        case 1: path = subPath || ''; break
        case 2:
          if (selectedContradictionFile && contradictionFilesList.length > 0) {
            const data = await api.getContradictionContent(caseId, selectedContradictionFile)
            setAnalysisContent(data.content || '')
            return
          }
          path = '05-矛盾记录.md'; break
        case 3: path = '04-法律依据/适用法条.md'; break
        case 4:
          const step5 = await api.getStepResult(caseId, 5)
          setAnalysisContent(step5.full_report || step5.defense_opinion || '无内容')
          return
      }
      if (!path && type !== 4) return
      const data = await api.getWikiPage(caseId, path)
      setAnalysisContent(data.content || '')
    } catch {
      setAnalysisContent('加载失败')
    }
  }, [caseId, selectedContradictionFile, contradictionFilesList])

  const loadEvidenceContent = useCallback(async (category: string, filename: string, dir?: string) => {
    if (!caseId) return
    setEvidenceContent('')
    setEvidenceLoading(true)
    try {
      if (dir) {
        setEvidenceContent(`__pdf__:${api.serveFileUrl(caseId, filename, dir)}`)
      } else {
        const data = await api.getSummaryContent(caseId, category, filename)
        setEvidenceContent(data.content || '')
      }
    } catch {
      setEvidenceContent('加载失败')
    } finally {
      setEvidenceLoading(false)
    }
  }, [caseId])

  return {
    // 6 阶段
    stageStatus, setStageStatus,
    stageMessages, setStageMessages,
    stageErrors, setStageErrors,
    runningStage, setRunningStage,
    handleRunStage, handleRunAllAnalysis,
    handleStopStage, handleClearStage, handleViewStage,

    // 流水线
    pipelineStatus, setPipelineStatus,
    pipelineRunning, setPipelineRunning,
    currentPipelineStep, setCurrentPipelineStep,
    stepResults, setStepResults,
    analysisState, setAnalysisState,
    nextStep, setNextStep,
    strategyAwaiting, setStrategyAwaiting,
    strategyRefreshKey,
    liveProgress, setLiveProgress,
    executePipelineStep, executeAllSteps,
    executeSingleStep, handleResumeAnalysis,
    loadPipelineState,

    // Wiki
    wikiPages, setWikiPages,
    selectedWikiPage, setSelectedWikiPage,
    wikiContent, setWikiContent,
    wikiLoading, setWikiLoading,
    loadWikiPages, loadWikiPage,

    // 证据浏览
    evidenceSummaries, setEvidenceSummaries,
    evidenceOther, setEvidenceOther,
    evidenceContent, setEvidenceContent,
    evidenceLoading, setEvidenceLoading,
    expandedCategories, setExpandedCategories,
    expandedEvidenceGroups, setExpandedEvidenceGroups,

    // 分析卡片
    selectedAnalysisCard, setSelectedAnalysisCard,
    evidenceAnalysisFiles, setEvidenceAnalysisFiles,
    selectedEvidenceAnalysis, setSelectedEvidenceAnalysis,
    contradictionFilesList, setContradictionFilesList,
    selectedContradictionFile, setSelectedContradictionFile,
    analysisContent, setAnalysisContent,
    loadEvidenceData, loadEvidenceAnalysisFiles,
    loadAnalysisContent, loadEvidenceContent,
  }
}