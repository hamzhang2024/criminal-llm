// 案件管理 API

import { API_BASE, safeFetch, isTauri, tauriInvoke } from './client'

export interface Thumbnail { page: number; data: string }
export interface SplitItem {
  id: string
  name: string
  evidenceType?: string
  doc_type?: string
  type?: string
  start_page?: number
  end_page?: number
  startLine?: number
  endLine?: number
  confidence?: number
  summary?: string
  pages?: number[]
  sequence_number?: number
}

// ========== 案件 CRUD ==========

export async function listCases(owner?: string): Promise<any> {
  const params = owner ? `?owner=${encodeURIComponent(owner)}` : ''
  const res = await safeFetch(`${API_BASE}/cases/list${params}`)
  return res.json()
}

export async function getPendingCases(): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/pending`)
  return res.json()
}

export async function getTrash(): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/trash`)
  return res.json()
}

export async function getCaseInfo(caseId: string): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}`)
  return res.json()
}

export async function getCaseFiles(caseId: string): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/files`)
  return res.json()
}

export async function getStepFiles(caseId: string, step: number): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/step-files/${step}`)
  return res.json()
}

export async function createCase(name: string, defendant: string, owner?: string, charges?: string[]): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, defendant, owner, charges })
  })
  return res.json()
}

export async function importCase(folderPath: string, name: string, defendant: string): Promise<any> {
  const params = new URLSearchParams({ folder_path: folderPath, name, defendant })
  const res = await fetch(`${API_BASE}/cases/import?${params.toString()}`, {
    method: 'POST'
  })
  return res.json()
}

export async function updateCaseCharges(caseId: string, charges: string[]): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ charges })
  })
  return res.json()
}

// 保存类案检索关键词（写 case.json 的 search_keywords）
export async function updateCaseSearchKeywords(caseId: string, keywords: string[]): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ search_keywords: keywords })
  })
  return res.json()
}

export async function deleteCase(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}`, { method: 'DELETE' })
  return res.json()
}

export async function restoreCase(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/trash/${caseId}/restore`, {
    method: 'POST'
  })
  return res.json()
}

export async function permanentDeleteCase(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/trash/${caseId}`, {
    method: 'DELETE'
  })
  return res.json()
}

export async function claimCases(owner: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/claim-cases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ owner })
  })
  return res.json()
}

// ========== 文件操作 ==========

export async function uploadFiles(caseId: string, files: File[]): Promise<any> {
  const formData = new FormData()
  files.forEach(f => formData.append('files', f))
  const res = await fetch(`${API_BASE}/cases/${caseId}/upload`, {
    method: 'POST',
    body: formData
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || res.statusText)
  }
  const data = await res.json().catch(() => null)
  if (!data) throw new Error('服务器返回了无效的响应')
  return data
}

export async function deleteFile(caseId: string, fileName: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/file/${encodeURIComponent(fileName)}`, {
    method: 'DELETE'
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || res.statusText)
  }
  return res.json()
}

export async function deleteOriginalFileOnly(caseId: string, fileName: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/original-file/${encodeURIComponent(fileName)}`, {
    method: 'DELETE'
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || res.statusText)
  }
  return res.json()
}

export async function batchProcess(caseId: string, step: number, fileNames: string[], options: Record<string, unknown> = {}): Promise<any> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 300000)
  try {
    const res = await safeFetch(`${API_BASE}/cases/${caseId}/batch-process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ step, file_names: fileNames, ...options }),
      signal: controller.signal
    })
    return res.json()
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error('PDF 处理超时（5 分钟），请检查文件是否正常或降低处理选项')
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
  }
}

export async function convertToMd(caseId: string, fileName: string): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/convert-to-md`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_name: fileName })
  })
  return res.json()
}

export async function deleteMdFile(caseId: string, mdFileName: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/md-file/${encodeURIComponent(mdFileName)}`, {
    method: 'DELETE'
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || res.statusText)
  }
  return res.json()
}

export async function deletePdfFile(caseId: string, pdfFileName: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/pdf-file/${encodeURIComponent(pdfFileName)}`, {
    method: 'DELETE'
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || res.statusText)
  }
  return res.json()
}

export async function openFile(caseId: string, filePath: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/open-file`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_path: filePath })
  })
  return res.json()
}

export async function getLlmSegmentNames(caseId: string, fileName: string, segments: Array<{ start: number; end: number }>): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/llm-name-segments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_name: fileName, segments })
  })
  return res.json()
}

export async function getThumbnails(caseId: string, filePath: string, dir: string, width = 500): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/pdf-thumbnails?file_path=${encodeURIComponent(filePath)}&dir=${dir}&width=${width}`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || res.statusText)
  }
  return res.json()
}

export async function cleanupProcessed(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/cleanup-processed`, {
    method: 'POST'
  })
  return res.json()
}

// ========== 页面旋转干预 ==========

// 旋转 processed/ 下 PDF 的指定页（degrees 为 90/180/270，顺时针累加）
export async function rotatePage(caseId: string, filePath: string, page: number, degrees: number, dir = 'processed'): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/rotate-page`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_path: filePath, dir, page, degrees })
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || res.statusText)
  }
  return res.json()
}

// md 识别异常页（MinerU 把倒置/异常页误判为表格的乱码块）
export interface MdIssue {
  md_file: string
  page_label: string
  start_line: number
  end_line: number
  preview: string
  estimated_page: number | null  // 按行数比例估算的 PDF 页码（误差 ±2 页，无法估算为 null）
  total_pages: number | null     // 对应 PDF 总页数（未知为 null）
}

export async function getMdIssues(caseId: string): Promise<{ issues: MdIssue[] }> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/md-issues`)
  return res.json()
}

// 单页重转修复：抽页 → MinerU 转换 → 替换 md 乱码块 → 可选证据失效
export async function reconvertBlock(caseId: string, params: {
  file_path: string; page: number; md_file: string;
  start_line: number; end_line: number; invalidate_evidence?: boolean
}): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/reconvert-block`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params)
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || res.statusText)
  }
  return res.json()
}

// 整卷重转：重建该卷 md + 失效该卷证据（多页倒置/转换质量差时用，不依赖乱码定位）
export async function reconvertVolume(caseId: string, filePath: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/reconvert-volume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_path: filePath })
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || res.statusText)
  }
  return res.json()
}

// ========== 案卷分析（旧 analyze-case API）==========

export async function createAnalysis(caseDir: string): Promise<any> {
  const res = await fetch(`${API_BASE}/analyze-case/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ case_dir: caseDir })
  })
  return res.json()
}

export async function analyzeCase(caseId: string, defendant: string, charges?: string[]): Promise<any> {
  const res = await fetch(`${API_BASE}/analyze-case/analyze/${caseId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ defendant, use_ai: true, charges: charges })
  })
  return res.json()
}

export async function getAnalysisProgress(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/analyze-case/progress/${caseId}`)
  return res.json()
}

export async function chatAboutCase(caseId: string, message: string, history: Array<{role: string, content: string}> = []): Promise<any> {
  const res = await fetch(`${API_BASE}/analyze-case/chat/${caseId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, history, use_ai: true })
  })
  if (!res.ok) {
    const errText = await res.text()
    let errMsg = '对话失败'
    try {
      const errData = JSON.parse(errText)
      errMsg = errData.detail || errData.error || errMsg
    } catch {
      errMsg = errText || errMsg
    }
    throw new Error(errMsg)
  }
  return res.json()
}

export async function getReport(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/analyze-case/report/${caseId}`)
  return res.json()
}

export async function selectEvidence(caseId: string, evidenceIds: string[]): Promise<any> {
  const res = await fetch(`${API_BASE}/analyze-case/evidence/${caseId}/select`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ evidence_ids: evidenceIds })
  })
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

export async function getEvidenceSummary(caseId: string, filename: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/evidence-summary/${encodeURIComponent(filename)}`)
  return res.json()
}

// ========== URL 工具函数 ==========

/** 获取缩略图 URL（用于 img src） */
export function thumbnailUrl(caseId: string, filePath: string, dir: string, width = 500): string {
  return `${API_BASE}/cases/${caseId}/pdf-thumbnails?file_path=${encodeURIComponent(filePath)}&dir=${dir}&width=${width}`
}

/** 获取文件服务 URL（用于 img src 或 fetch） */
export function serveFileUrl(caseId: string, filename: string, dir: string): string {
  return `${API_BASE}/cases/${caseId}/serve-file?file_path=${encodeURIComponent(filename)}&dir=${dir}`
}

/** 获取缓存缩略图 URL（用于 img src） */
export function thumbCacheUrl(jobId: string, page: number): string {
  return `${API_BASE}/thumb-cache/${jobId}/${page}.png`
}