// 报告页面数据加载 Hook

import { useState, useCallback, useEffect, useRef } from 'react'
import { api } from '../../../api'

export interface EvidenceItem {
  id: string
  displayName: string
  mdFile: string
  type: string
}

export function useReportData(caseId: string | undefined) {
  // 核心数据
  const [caseName, setCaseName] = useState('')
  const [defendant, setDefendant] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 阶段内容缓存
  const [stageContent, setStageContent] = useState<Record<string, string>>({})
  const stageContentRef = useRef(stageContent)
  useEffect(() => { stageContentRef.current = stageContent }, [stageContent])

  // 证据列表
  const [evidenceItems, setEvidenceItems] = useState<EvidenceItem[]>([])

  // 渐进式分析状态
  const [analysisRunning, setAnalysisRunning] = useState(false)
  const [completedStages, setCompletedStages] = useState<Set<string>>(new Set())
  const [nextStep, setNextStep] = useState<number | null | undefined>(undefined)

  // 加载案件信息
  const loadCaseInfo = useCallback(async () => {
    if (!caseId) return
    try {
      const info = await api.getCaseInfo(caseId)
      if (info) {
        setCaseName(info.name || '')
        setDefendant(info.defendant || '')
      }
    } catch (e) {
      setError('加载案件信息失败')
    }
  }, [caseId])

  // 加载证据列表
  const loadEvidenceList = useCallback(async () => {
    if (!caseId) return
    try {
      const data = await api.getEvidenceIndex(caseId)
      if (data && data.evidence) {
        setEvidenceItems(data.evidence.map((e: any) => ({
          id: e.id,
          displayName: e.name,
          mdFile: e.md_file,
          type: e.type,
        })))
      }
    } catch { /* ignore */ }
  }, [caseId])

  // 加载阶段内容
  const loadStageContent = useCallback(async (stageKey: string) => {
    if (!caseId) return
    const cacheKey = stageKey.startsWith('stage_') ? stageKey : `stage_${stageKey}`
    if (stageContentRef.current[cacheKey]) return // 已缓存

    try {
      const stageNum = parseInt(cacheKey.replace('stage_', ''))
      const data = await api.getStageMarkdown(caseId, stageNum)
      if (data?.content) {
        setStageContent(prev => ({ ...prev, [cacheKey]: data.content }))
      }
    } catch { /* ignore */ }
  }, [caseId])

  // 加载分析阶段完成状态
  const loadDefenseStages = useCallback(async () => {
    if (!caseId) return
    try {
      const data = await api.getDefenseStages(caseId)
      if (data && data.stages) {
        const done = new Set<string>()
        for (const [key, status] of Object.entries(data.stages)) {
          if (status === 'done') {
            done.add(key)
            // 加载已完成阶段的内容
            const contentKey = `defense_${key}.md`
            if (!stageContentRef.current[contentKey]) {
              try {
                const stageData = await api.getDefenseStageContent(caseId, key)
                if (stageData?.content) {
                  setStageContent(prev => ({ ...prev, [contentKey]: stageData.content }))
                }
              } catch { /* ignore */ }
            }
          }
        }
        setCompletedStages(done)
      }
    } catch { /* ignore */ }

    // 加载主流水线状态
    try {
      const state = await api.getAnalysisState(caseId)
      if (state) {
        setNextStep(state.next_step ?? null)
      }
    } catch { /* ignore */ }
  }, [caseId])

  // 初始化加载
  useEffect(() => {
    if (!caseId) return
    setLoading(true)
    Promise.all([loadCaseInfo(), loadEvidenceList(), loadDefenseStages()])
      .finally(() => setLoading(false))
  }, [caseId, loadCaseInfo, loadEvidenceList, loadDefenseStages])

  // 轮询分析状态
  useEffect(() => {
    if (!caseId || !analysisRunning) return
    const interval = setInterval(() => {
      loadDefenseStages().then(() => {
        setCompletedStages(prev => {
          if (prev.size >= 5) setAnalysisRunning(false)
          return prev
        })
        setNextStep(prev => {
          if (prev === null) setAnalysisRunning(false)
          return prev
        })
      }).catch(() => { /* ignore */ })
    }, 3000)
    return () => clearInterval(interval)
  }, [caseId, analysisRunning, loadDefenseStages])

  return {
    // 核心数据
    caseName, setCaseName,
    defendant, setDefendant,
    loading, setLoading,
    error, setError,
    // 阶段内容
    stageContent, setStageContent,
    stageContentRef,
    loadStageContent,
    // 证据
    evidenceItems, setEvidenceItems,
    loadEvidenceList,
    // 分析状态
    analysisRunning, setAnalysisRunning,
    completedStages, setCompletedStages,
    nextStep, setNextStep,
    loadDefenseStages,
  }
}
