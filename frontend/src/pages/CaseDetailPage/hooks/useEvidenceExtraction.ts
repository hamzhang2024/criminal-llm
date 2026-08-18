// 证据提取 Hook — 合并证据提取 + 轮询逻辑

import { useState, useCallback, useRef } from 'react'
import { api, API_BASE } from '../../../api'
import type { EvidenceIndexFile, CompletenessReport } from '../../../api'
import { showAlert, showConfirm } from '../../../components/MacOSDialog'

export type ExtractResult = 'success' | 'cancelled' | 'failed'

// 提取/摘要实时进度（后端 extract-status 返回）
export interface ExtractProgress {
  phase: 'extracting' | 'summarizing'
  totalFiles: number
  processedFiles: number
  currentFile: string
  currentFileDone: number   // 当前卷内已完成笔录份数
  currentFileTotal: number  // 当前卷笔录总份数
  currentFileStage: string  // 当前卷阶段：目录清点中/按份提取中（total 为 0 时避免误显"0份笔录"）
  llmLatencyMs: number      // 当前 LLM 调用的等待毫秒数（判断"在等模型"而非"卡死"）
  summaryDone: number
  summaryTotal: number
}

export function useEvidenceExtraction(caseId: string | undefined, onExtractComplete?: (result: ExtractResult) => void) {
  const [evidenceList, setEvidenceList] = useState<any[]>([])
  const [evidenceFiles, setEvidenceFiles] = useState<EvidenceIndexFile[]>([])  // index.json files（文书分类）
  const [completeness, setCompleteness] = useState<CompletenessReport | null>(null)  // 完整性报告
  const [evidenceExtracted, setEvidenceExtracted] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [extractProgress, setExtractProgress] = useState<ExtractProgress | null>(null)

  const extractPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const extractUserStoppedRef = useRef(false)
  const extractPollFailuresRef = useRef(0)

  // 停止提取轮询
  const stopPolling = useCallback(() => {
    if (extractPollRef.current) {
      clearInterval(extractPollRef.current)
      extractPollRef.current = null
    }
  }, [])

  // 检查证据提取是否正在运行（页面加载时恢复状态）
  const checkExtractStatus = useCallback(async (): Promise<boolean> => {
    if (!caseId) return false
    try {
      const st = await api.getExtractStatus(caseId)
      return st.status === 'running'
    } catch {
      return false
    }
  }, [caseId])

  // 加载已有证据（页面加载时）
  const loadEvidence = useCallback(async () => {
    if (!caseId) return
    try {
      const data = await api.getEvidenceIndex(caseId)
      if (Array.isArray(data.files)) setEvidenceFiles(data.files)
      if (data.total_evidence > 0) {
        setEvidenceList(data.evidence || [])
        setEvidenceExtracted(true)
      } else if (data.error_hint) {
        // 之前提取失败
      }
    } catch { /* 无证据 */ }
  }, [caseId])

  // 加载提取完整性报告（进入证据列表时调用；无报告时返回空 files/summary）
  const loadCompleteness = useCallback(async () => {
    if (!caseId) return
    try {
      const report = await api.getEvidenceCompleteness(caseId)
      setCompleteness(report && report.files ? report : null)
    } catch {
      setCompleteness(null)
    }
  }, [caseId])

  // 轮询证据提取进度
  const pollExtractProgress = useCallback(() => {
    if (!caseId) return

    extractUserStoppedRef.current = false
    extractPollFailuresRef.current = 0

    // 进展停滞检测：30 分钟无任何进度变化才判定失败（提取+摘要可持续数小时，
    // 不能用总时长硬超时——14 卷提取约 2-3 小时）
    let lastSignature = ''
    let lastChangeAt = Date.now()
    const STALL_MS = 30 * 60 * 1000

    extractPollRef.current = setInterval(async () => {
      if (extractUserStoppedRef.current) {
        stopPolling()
        return
      }
      // 停滞检查：进度签名长时间不变才停
      if (Date.now() - lastChangeAt > STALL_MS) {
        stopPolling()
        setExtracting(false)
        if (onExtractComplete) onExtractComplete('failed')
        return
      }
      try {
        const st = await api.getExtractStatus(caseId!)
        extractPollFailuresRef.current = 0

        // 更新实时进度（进度条数据源）
        const signature = `${st.status}|${st.phase}|${st.processed_files}|${st.current_file_done}|${st.summary_done}`
        if (signature !== lastSignature) {
          lastSignature = signature
          lastChangeAt = Date.now()
        }
        setExtractProgress({
          phase: st.phase === 'summarizing' ? 'summarizing' : 'extracting',
          totalFiles: st.total_files || 0,
          processedFiles: st.processed_files || 0,
          currentFile: st.current_file || '',
          currentFileDone: st.current_file_done || 0,
          currentFileTotal: st.current_file_total || 0,
          currentFileStage: st.current_file_stage || '',
          llmLatencyMs: st.llm_latency_ms || 0,
          summaryDone: st.summary_done || 0,
          summaryTotal: st.summary_total || 0,
        })

        if (st.status !== 'running') {
          stopPolling()
          const data = await api.getEvidenceIndex(caseId!)
          if (Array.isArray(data.files)) setEvidenceFiles(data.files)
          const total = data.total_evidence || 0
          if (total > 0) {
            setEvidenceList(data.evidence || [])
            setEvidenceExtracted(true)
          }
          // 提取结束后刷新完整性报告
          loadCompleteness()
          setExtracting(false)
          // 区分取消/失败/成功：cancelled 状态或用户主动停止且无证据 = 取消
          const result: ExtractResult = st.status === 'cancelled' || extractUserStoppedRef.current
            ? (total > 0 ? 'success' : 'cancelled')
            : (total > 0 ? 'success' : 'failed')
          if (onExtractComplete) onExtractComplete(result)
        } else {
          // 运行时刷新已提取的证据
          const data = await api.getEvidenceIndex(caseId!)
          if (data.total_evidence > 0) setEvidenceList(data.evidence || [])
        }
      } catch {
        extractPollFailuresRef.current += 1
        if (extractPollFailuresRef.current >= 3) {
          stopPolling()
          setExtracting(false)
          if (onExtractComplete) onExtractComplete('failed')
        }
      }
    }, 3000)
  }, [caseId, stopPolling, onExtractComplete, loadCompleteness])

  // 提取证据
  const handleExtractEvidence = useCallback(async () => {
    if (!caseId) return

    // 先检查 MD 文件
    try {
      const stepFiles = await api.getStepFiles(caseId, 2)
      if (!Array.isArray(stepFiles) || stepFiles.length === 0) {
        return { needConvert: true }
      }
    } catch { /* ignore */ }

    extractUserStoppedRef.current = false
    extractPollFailuresRef.current = 0
    stopPolling()

    setExtracting(true)
    try {
      await api.extractEvidence(caseId)
      pollExtractProgress()
      return { started: true }
    } catch (err) {
      setExtracting(false)
      throw err
    }
  }, [caseId, stopPolling, pollExtractProgress])

  // 停止提取
  const handleStopExtract = useCallback(async () => {
    extractUserStoppedRef.current = true
    stopPolling()
    if (caseId) {
      await api.stopExtractEvidence(caseId)
    }
    setExtracting(false)
  }, [caseId, stopPolling])

  // 清除证据
  const handleClearEvidence = useCallback(async () => {
    if (!caseId) return
    try {
      const res = await fetch(`${API_BASE}/cases/${caseId}/clear-evidence`, { method: 'POST' })
      const data = await res.json()
      if (data.success) {
        setEvidenceList([])
        setEvidenceFiles([])
        setCompleteness(null)
        setEvidenceExtracted(false)
      }
    } catch { /* ignore */ }
  }, [caseId])

  // 刷新证据
  const handleRefreshEvidence = useCallback(async () => {
    if (!caseId) return
    try {
      const st = await api.getExtractStatus(caseId)
      if (st.status === 'running') {
        setExtracting(true)
        return
      }
      const data = await api.getEvidenceIndex(caseId)
      if (Array.isArray(data.files)) setEvidenceFiles(data.files)
      if (data.total_evidence > 0) {
        setEvidenceList(data.evidence || [])
        setEvidenceExtracted(true)
      }
      loadCompleteness()
    } catch { /* ignore */ }
  }, [caseId, loadCompleteness])

  return {
    evidenceList, setEvidenceList,
    evidenceFiles,
    completeness,
    loadCompleteness,
    evidenceExtracted, setEvidenceExtracted,
    extracting, setExtracting,
    extractProgress,
    checkExtractStatus, loadEvidence, pollExtractProgress,
    handleExtractEvidence,
    handleStopExtract,
    handleClearEvidence,
    handleRefreshEvidence,
    stopPolling,
  }
}