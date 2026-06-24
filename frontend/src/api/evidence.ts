// 证据提取 API

import { API_BASE, safeFetch, subscribeSSE, isTauri, tauriInvoke } from './client'

let _extractController: AbortController | null = null

export async function extractEvidence(caseId: string): Promise<any> {
  _extractController = new AbortController()
  const controller = _extractController
  const timeoutId = setTimeout(() => controller.abort(), 300000)
  try {
    const res = await safeFetch(`${API_BASE}/cases/${caseId}/extract-evidence`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
    })
    const data = await res.json()
    if (!res.ok) {
      throw new Error(data.detail || data.error || '提取失败')
    }
    return data
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      throw new Error('用户已停止提取')
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
    _extractController = null
  }
}

export async function stopExtractEvidence(caseId: string) {
  _extractController?.abort()
  if (isTauri()) {
    await tauriInvoke('stop_extract', { caseId }).catch(() => {})
  } else {
    try {
      await fetch(`${API_BASE}/cases/${caseId}/stop-extract`, { method: 'POST' })
    } catch { /* 忽略 */ }
  }
}

export async function getEvidenceIndex(caseId: string): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/evidence-index`)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`获取证据列表失败: ${res.status} ${text.slice(0, 100)}`)
  }
  return res.json()
}

export async function getExtractStatus(caseId: string): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/extract-status`)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`获取提取状态失败: ${res.status} ${text.slice(0, 100)}`)
  }
  return res.json()
}

/** 证据提取状态（与后端 _build_extract_status 对应） */
export interface ExtractStatus {
  case_id: string
  status: string
  total_files?: number
  processed_files?: number
  current_file?: string
  elapsed_seconds?: number
  eta_seconds?: number | null
  llm_waiting?: boolean
  llm_latency_ms?: number
  retry_count?: number
  retry_reason?: string
  retry_wait_seconds?: number
  stopped_by_user?: boolean
  recoverable?: boolean
  error_details?: unknown
  error_detail?: unknown
}

/**
 * 订阅证据提取进度（SSE 推送，替代轮询）。
 *
 * 后端在状态变化时主动推送，任务进入终态后自动关闭流。
 * EventSource 内置自动重连，无需手动处理。
 *
 * @returns 关闭订阅的函数（在组件卸载时调用）
 */
export function subscribeExtractStatus(
  caseId: string,
  onStatus: (status: ExtractStatus) => void,
  onError?: (error: Event) => void,
): () => void {
  return subscribeSSE<ExtractStatus>(
    `${API_BASE}/cases/${caseId}/extract-status/stream`,
    onStatus,
    onError,
  )
}

export async function getEvidenceSummary(caseId: string, filename: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/evidence-summary/${encodeURIComponent(filename)}`)
  return res.json()
}

export async function getMdFiles(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/md-files`)
  return res.json()
}

export async function getProcessedPdfs(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/processed-pdfs`)
  return res.json()
}

export interface EvidenceReviewPayload {
  name?: string
  type?: string
  persons?: string
  key_facts?: string
  contradiction_hints?: string
}

/** 人工校对单条证据（编辑 name/type/persons/key_facts/contradiction_hints） */
export async function reviewEvidenceItem(caseId: string, evidenceId: number, payload: EvidenceReviewPayload): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/evidence/${evidenceId}/review`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || '校对失败')
  }
  return data
}