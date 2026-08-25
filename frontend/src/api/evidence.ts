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

// index.json 顶层 files 字段：文书分类结果（证据 / 非证据）
export interface EvidenceIndexFile {
  name: string
  doc_type: string  // "evidence" | "non_evidence:封面" | ...
}

export interface EvidenceIndexResponse {
  total_evidence: number
  evidence: any[]
  case_charges?: string[]
  files?: EvidenceIndexFile[]
  error_hint?: string
  generated_at?: string
}

export async function getEvidenceIndex(caseId: string): Promise<EvidenceIndexResponse> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/evidence-index`)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`获取证据列表失败: ${res.status} ${text.slice(0, 100)}`)
  }
  return res.json()
}

// 提取完整性报告（evidence/completeness_report.json）
export interface CompletenessEntry {
  source_items: number
  covered: number
  missing: string[]
  llm_checked: boolean
  needs_review?: boolean
  status: 'ok' | 'suspect' | 'failed'
}

export interface CompletenessReport {
  files: Record<string, CompletenessEntry>
  summary: Record<string, number>
}

export async function getEvidenceCompleteness(caseId: string): Promise<CompletenessReport> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/evidence/completeness`)
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

// ========== 选择性 OCR 图片（按卷分组） ==========

export interface OcrImageMeta { w: number; h: number; ocr?: boolean }
export interface OcrImageGroup { [volName: string]: { [imgName: string]: OcrImageMeta } }

export async function getOcrImages(caseId: string): Promise<OcrImageGroup> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/ocr-images`)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`获取 OCR 图片失败: ${res.status} ${text.slice(0, 100)}`)
  }
  const data = await res.json()
  return data.groups || {}
}

export async function startOcrImages(caseId: string, groups: Record<string, string[]>): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/ocr-images`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ groups }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`启动 OCR 失败: ${res.status} ${text.slice(0, 100)}`)
  }
  const data = await res.json()
  // 200 但 success:false（如"OCR 任务进行中"/"未选择任何图片"）必须当错误抛出，
  // 否则前端会进入永不前进的幻影"识别中 0/N"状态（用户看到的"没有反应"）
  if (data && data.success === false) {
    throw new Error(data.error || '启动 OCR 失败')
  }
  return data
}

export async function getOcrStatus(caseId: string): Promise<{ status: string; done: number; total: number; current?: string; failed?: string[] }> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/ocr-status`)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`获取 OCR 状态失败: ${res.status} ${text.slice(0, 100)}`)
  }
  const data = await res.json()
  return data.task || { status: 'idle', done: 0, total: 0 }
}