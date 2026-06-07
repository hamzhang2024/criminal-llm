// 证据提取 API

import { API_BASE, safeFetch, isTauri, tauriInvoke } from './client'

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