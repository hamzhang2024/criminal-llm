// API 服务层 - 连接前端与 Python 后端
// 所有 API 调用通过 HTTP 直接访问 FastAPI 后端

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

export const API_BASE = '/api'

// ========== 案件管理 ==========

export async function listCases(): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/list`)
  return res.json()
}

export async function getPendingCases(): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/pending`)
  return res.json()
}

export async function getTrash(): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/trash`)
  return res.json()
}

export async function getCaseInfo(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}`)
  return res.json()
}

export async function getCaseFiles(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/files`)
  return res.json()
}

export async function getStepFiles(caseId: string, step: number): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/step-files/${step}`)
  return res.json()
}

export async function createCase(name: string, defendant: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, defendant })
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

// ========== 文件处理 ==========

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

export async function batchProcess(caseId: string, step: number, fileNames: string[], options: Record<string, unknown> = {}): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/batch-process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ step, file_names: fileNames, ...options })
  })
  return res.json()
}

export async function convertToMd(caseId: string, fileName: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/convert-to-md`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_name: fileName })
  })
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

// ========== 案卷分析 ==========

export async function createAnalysis(caseDir: string): Promise<any> {
  const res = await fetch(`${API_BASE}/analyze-case/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ case_dir: caseDir })
  })
  return res.json()
}

export async function analyzeCase(caseId: string, defendant: string, crimeType?: string): Promise<any> {
  const res = await fetch(`${API_BASE}/analyze-case/analyze/${caseId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ defendant, use_ai: true, crime_type: crimeType })
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

// ========== 分析流水线 ==========

export async function runPipelineStep(caseId: string, step: number, defendant: string, crimeType?: string): Promise<any> {
  const controller = new AbortController()
  // 根据步骤类型设置不同超时：步骤 2/3/4 需要大量 LLM 串行调用，需要更长时间
  // 步骤 2（逐次总结）：每人每次笔录都要单独 LLM 总结，可能耗时很长
  const timeoutMs = step >= 2 ? 7200000 : 600000 // 步骤 2+ 120 分钟，其余 10 分钟
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(`${API_BASE}/pipeline/${caseId}/step/${step}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ defendant, crime_type: crimeType }),
      signal: controller.signal
    })
    return res.json()
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      throw new Error(`步骤执行超时（${Math.round(timeoutMs / 60000)} 分钟），请检查后端是否正常运行`)
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
  }
}

export async function getPipelineStatus(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/status`)
  return res.json()
}

export async function getPipelineProgress(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/progress`)
  return res.json()
}

export async function getStepResult(caseId: string, step: number): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/step/${step}/result`)
  return res.json()
}

// ========== Wiki 相关 ==========

export async function getWikiIndex(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/wiki/index`)
  return res.json()
}

export async function getWikiPage(caseId: string, path: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/wiki/pages/${encodeURIComponent(path)}`)
  return res.json()
}

export async function getMdFile(caseId: string, filename: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/md-files/${encodeURIComponent(filename)}`)
  return res.json()
}

export async function getPdfText(caseId: string, filename: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/pdf-text/${encodeURIComponent(filename)}`)
  return res.json()
}

export async function uploadWikiReference(caseId: string, file: File): Promise<any> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/wiki/upload-reference`, {
    method: 'POST',
    body: formData,
  })
  return res.json()
}

export async function clearWiki(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/wiki/clear`, {
    method: 'DELETE',
  })
  return res.json()
}

// ========== 证据提取 ==========

let _extractController: AbortController | null = null

export async function extractEvidence(caseId: string): Promise<any> {
  _extractController = new AbortController()
  const controller = _extractController
  const timeoutId = setTimeout(() => controller.abort(), 300000) // 5 分钟超时
  try {
    const res = await fetch(`${API_BASE}/cases/${caseId}/extract-evidence`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
    })
    return res.json()
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

export function stopExtractEvidence() {
  _extractController?.abort()
}

export async function getEvidenceIndex(caseId: string): Promise<any> {
  // 直接从文件系统读取证据索引
  const res = await fetch(`${API_BASE}/cases/${caseId}/evidence-index`)
  return res.json()
}

// ========== 5 阶段分析引擎 ==========

export async function runAllStages(caseId: string, defendant: string, crimeType?: string): Promise<any> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/run-all`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ defendant, crime_type: crimeType })
  })
  return res.json()
}

export async function getStageStatus(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/status`)
  return res.json()
}

export async function getStageProgress(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/progress`)
  return res.json()
}

export async function runSingleStage(caseId: string, stageNum: number, defendant: string, crimeType?: string): Promise<any> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/run-stage/${stageNum}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ defendant, crime_type: crimeType })
  })
  return res.json()
}

export async function getStageResult(caseId: string, stageNum: number): Promise<any> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/stage/${stageNum}/result`)
  return res.json()
}

export async function getStageMarkdown(caseId: string, stageNum: number): Promise<any> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/stage/${stageNum}/markdown`)
  return res.json()
}

export async function getFullReport(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/full-report`)
  return res.json()
}

// ========== 证据浏览 ==========

export async function getEvidenceSummaries(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/evidence/summaries`)
  return res.json()
}

export async function getEvidenceOther(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/evidence/other`)
  return res.json()
}

export async function getSummaryContent(caseId: string, category: string, filename: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/evidence/summary/${encodeURIComponent(category)}/${encodeURIComponent(filename)}`)
  return res.json()
}

export async function getEvidenceFiles(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/evidence/files`)
  return res.json()
}

export async function getMdFiles(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/md-files`)
  return res.json()
}

export async function getEvidenceSummary(caseId: string, filename: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/evidence-summary/${encodeURIComponent(filename)}`)
  return res.json()
}

export async function getProcessedPdfs(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/processed-pdfs`)
  return res.json()
}

// ========== 矛盾分析 ==========

export async function getContradictionFiles(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/evidence/contradictions`)
  return res.json()
}

export async function getContradictionContent(caseId: string, filename: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/evidence/contradiction/${encodeURIComponent(filename)}`)
  return res.json()
}

// ========== 法律知识库 ==========

export async function listLegalKB(): Promise<any> {
  const res = await fetch(`${API_BASE}/legal-knowledge`)
  return res.json()
}

export async function getLegalKBItem(itemId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/legal-knowledge/${encodeURIComponent(itemId)}`)
  return res.json()
}

export async function createLegalKBItem(title: string, content: string, crimeType: string = '', itemId?: string): Promise<any> {
  const res = await fetch(`${API_BASE}/legal-knowledge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, content, crime_type: crimeType, id: itemId })
  })
  return res.json()
}

export async function updateLegalKBItem(itemId: string, updates: { title?: string; content?: string; crime_type?: string }): Promise<any> {
  const res = await fetch(`${API_BASE}/legal-knowledge/${encodeURIComponent(itemId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates)
  })
  return res.json()
}

export async function deleteLegalKBItem(itemId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/legal-knowledge/${encodeURIComponent(itemId)}`, {
    method: 'DELETE'
  })
  return res.json()
}

export async function searchLaws(crimeType: string): Promise<any> {
  const res = await fetch(`${API_BASE}/legal-knowledge/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ crime_type: crimeType })
  })
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

// ========== 导出默认对象 ==========

export const api = {
  // 案件管理
  listCases,
  getPendingCases,
  getTrash,
  getCaseInfo,
  getCaseFiles,
  getStepFiles,
  createCase,
  importCase,
  deleteCase,
  restoreCase,
  permanentDeleteCase,
  // 文件处理
  uploadFiles,
  batchProcess,
  convertToMd,
  openFile,
  getLlmSegmentNames,
  getThumbnails,
  deleteFile,
  cleanupProcessed,
  // 案卷分析
  createAnalysis,
  analyzeCase,
  getAnalysisProgress,
  chatAboutCase,
  getReport,
  selectEvidence,
  // 分析流水线
  runPipelineStep,
  getPipelineStatus,
  getPipelineProgress,
  getStepResult,
  // Wiki
  getWikiIndex,
  getWikiPage,
  getMdFile,
  getPdfText,
  uploadWikiReference,
  clearWiki,
  // 证据浏览
  getEvidenceSummaries,
  getEvidenceOther,
  getSummaryContent,
  getEvidenceFiles,
  getEvidenceSummary,
  getProcessedPdfs,
  getMdFiles,
  // 矛盾分析
  getContradictionFiles,
  getContradictionContent,
  // URL 工具
  thumbnailUrl,
  serveFileUrl,
  thumbCacheUrl,
  // 5 阶段分析
  runAllStages,
  runSingleStage,
  getStageProgress,
  getStageStatus,
  getStageResult,
  getStageMarkdown,
  getFullReport,
  // 证据提取
  extractEvidence,
  stopExtractEvidence,
  getEvidenceIndex,
  // 法律知识库
  listLegalKB,
  getLegalKBItem,
  createLegalKBItem,
  updateLegalKBItem,
  deleteLegalKBItem,
  searchLaws,
}
