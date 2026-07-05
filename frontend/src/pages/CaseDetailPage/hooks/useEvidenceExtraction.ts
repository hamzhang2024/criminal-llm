// 证据提取 Hook — 合并证据提取 + 轮询逻辑

import { useState, useCallback, useRef } from 'react'
import { api, API_BASE } from '../../../api'
import { showAlert, showConfirm } from '../../../components/MacOSDialog'

export function useEvidenceExtraction(caseId: string | undefined) {
  const [evidenceList, setEvidenceList] = useState<any[]>([])
  const [evidenceExtracted, setEvidenceExtracted] = useState(false)
  const [extracting, setExtracting] = useState(false)

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
      if (data.total_evidence > 0) {
        setEvidenceList(data.evidence || [])
        setEvidenceExtracted(true)
      } else if (data.error_hint) {
        // 之前提取失败
      }
    } catch { /* 无证据 */ }
  }, [caseId])

  // 轮询证据提取进度
  const pollExtractProgress = useCallback(() => {
    if (!caseId) return

    extractUserStoppedRef.current = false
    extractPollFailuresRef.current = 0

    extractPollRef.current = setInterval(async () => {
      if (extractUserStoppedRef.current) {
        stopPolling()
        return
      }
      try {
        const st = await api.getExtractStatus(caseId!)
        extractPollFailuresRef.current = 0

        if (st.status !== 'running') {
          stopPolling()
          const data = await api.getEvidenceIndex(caseId!)
          const total = data.total_evidence || 0
          if (total > 0) {
            setEvidenceList(data.evidence || [])
            setEvidenceExtracted(true)
          }
          setExtracting(false)
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
        }
      }
    }, 3000)
  }, [caseId, stopPolling])

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
      if (data.total_evidence > 0) {
        setEvidenceList(data.evidence || [])
        setEvidenceExtracted(true)
      }
    } catch { /* ignore */ }
  }, [caseId])

  return {
    evidenceList, setEvidenceList,
    evidenceExtracted, setEvidenceExtracted,
    extracting, setExtracting,
    checkExtractStatus, loadEvidence, pollExtractProgress,
    handleExtractEvidence,
    handleStopExtract,
    handleClearEvidence,
    handleRefreshEvidence,
    stopPolling,
  }
}