// API 服务层 - 连接前端与 Python 后端
// 认证 API 在 Tauri 环境下通过 Tauri 命令调用，浏览器环境下直连后端

/** 是否在 Tauri 环境下运行 */
function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

/** 懒加载 Tauri invoke（避免浏览器环境下模块加载报错） */
async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import('@tauri-apps/api/core')
  return invoke<T>(cmd, args)
}

/** 懒加载 Tauri shell.open */
async function tauriOpen(url: string): Promise<void> {
  const { open } = await import('@tauri-apps/plugin-shell')
  open(url).catch(() => {})
}

/** API 基础地址：开发模式走 Vite 代理，生产模式直连后端 */
export const API_BASE = import.meta.env.PROD ? 'http://localhost:8080/api' : '/api'

/** 安全的 fetch 包装：将网络错误转为友好的中文提示 */
async function safeFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init)
  } catch (err) {
    // fetch 底层失败（后端未启动/崩溃/网络不通）
    // 在 WebKit/Tauri 中错误消息通常是 "Load failed"
    throw new Error('后端未启动或连接失败，请重新启动应用')
  }
}

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

// ========== 认证（通过 Tauri 命令）==========

interface AuthResult {
  success: boolean
  token?: string
  email?: string
  error?: string
}

export interface LoginResponse {
  success: boolean
  token: string
  email: string
}

export interface VerifyResponse {
  success: boolean
  email: string
  sub: string
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  const result: AuthResult = isTauri()
    ? await tauriInvoke('auth_login', { email, password })
    : { success: false, error: '浏览器模式下不支持 Tauri 认证' }
  if (!result.success) {
    throw new Error(result.error || '登录失败')
  }
  return {
    success: true,
    token: result.token || '',
    email: result.email || '',
  }
}

export async function verifyToken(token: string): Promise<VerifyResponse> {
  const result: AuthResult = isTauri()
    ? await tauriInvoke('auth_verify', { token })
    : { success: false, error: '浏览器模式下不支持 Tauri 认证' }
  if (!result.success) {
    throw new Error(result.error || 'Token 无效')
  }
  return {
    success: true,
    email: result.email || '',
    sub: '',
  }
}

export async function register(email: string, password: string): Promise<{ success: boolean; message?: string }> {
  const result: AuthResult = isTauri()
    ? await tauriInvoke('auth_register', { email, password })
    : { success: false, error: '浏览器模式下不支持 Tauri 认证' }
  if (!result.success) {
    throw new Error(result.error || '注册失败')
  }
  return { success: true, message: '注册成功' }
}

export async function resetPassword(email: string, oldPassword: string, newPassword: string): Promise<{ success: boolean; message?: string }> {
  const result: AuthResult = isTauri()
    ? await tauriInvoke('auth_reset_password', { email, oldPassword, newPassword })
    : { success: false, error: '浏览器模式下不支持 Tauri 认证' }
  if (!result.success) {
    throw new Error(result.error || '重置失败')
  }
  return { success: true, message: '密码已重置' }
}

export async function sendResetCode(email: string): Promise<{ success: boolean; message?: string }> {
  const result: AuthResult = isTauri()
    ? await tauriInvoke('auth_send_reset_code', { email })
    : { success: false, error: '浏览器模式下不支持 Tauri 认证' }
  if (!result.success) {
    throw new Error(result.error || '发送验证码失败')
  }
  return { success: true, message: result.error || '验证码已发送' }
}

export async function resetWithCode(email: string, code: string, newPassword: string): Promise<{ success: boolean; message?: string }> {
  const result: AuthResult = isTauri()
    ? await tauriInvoke('auth_reset_with_code', { email, code, newPassword })
    : { success: false, error: '浏览器模式下不支持 Tauri 认证' }
  if (!result.success) {
    throw new Error(result.error || '重置失败')
  }
  return { success: true, message: '密码已重置' }
}

export function getToken(): string | null {
  return localStorage.getItem('auth_token')
}

export function setToken(token: string): void {
  localStorage.setItem('auth_token', token)
}

export function clearToken(): void {
  localStorage.removeItem('auth_token')
}

export function getAuthEmail(): string | null {
  return localStorage.getItem('auth_email')
}

export function setAuthEmail(email: string): void {
  localStorage.setItem('auth_email', email)
}

export function clearAuthEmail(): void {
  localStorage.removeItem('auth_email')
}

// ========== 版本与更新 ==========

export interface UpdateInfo {
  has_update: boolean
  current_version: string
  latest_version: string
  download_url: string
  release_notes: string
}

export function getAppVersion(): Promise<string> {
  if (isTauri()) {
    return tauriInvoke('get_app_version')
  }
  return Promise.resolve('0.0.0-web')
}

export async function checkUpdate(): Promise<UpdateInfo> {
  if (isTauri()) {
    return tauriInvoke('check_update')
  }
  return { has_update: false, current_version: '0.0.0', latest_version: '0.0.0', download_url: '', release_notes: '' }
}

// ========== 案件管理 ==========

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

export async function createCase(name: string, defendant: string, owner?: string): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, defendant, owner })
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

export async function claimCases(owner: string): Promise<any> {
  const res = await fetch(`${API_BASE}/cases/claim-cases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ owner })
  })
  return res.json()
}

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
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 300000) // 5 分钟超时
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

// ========== 分析流水线 ==========

export async function runPipelineStep(caseId: string, step: number, defendant: string, crimeType?: string, indictmentFile?: string): Promise<any> {
  const controller = new AbortController()
  // 根据步骤类型设置不同超时：步骤 2/3/4 需要大量 LLM 串行调用，需要更长时间
  // 步骤 2（逐次总结）：每人每次笔录都要单独 LLM 总结，可能耗时很长
  const timeoutMs = step >= 2 ? 7200000 : 600000 // 步骤 2+ 120 分钟，其余 10 分钟
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(`${API_BASE}/pipeline/${caseId}/step/${step}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ defendant, crime_type: crimeType, indictment_file: indictmentFile }),
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

export async function getAnalysisState(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/analysis-state`)
  return res.json()
}

export async function resumePipeline(caseId: string, defendant: string, crimeType?: string, indictmentFile?: string): Promise<any> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 7200000) // 120 分钟超时
  try {
    const res = await fetch(`${API_BASE}/pipeline/${caseId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ defendant, crime_type: crimeType, indictment_file: indictmentFile }),
      signal: controller.signal
    })
    return res.json()
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      throw new Error('断点恢复超时，请检查后端是否正常运行')
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
  }
}

// ========== 辩护意见子阶段 ==========

export async function getDefenseStages(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/defense-stages`)
  return res.json()
}

export async function getDefenseStageContent(caseId: string, stageName: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/defense-stage/${encodeURIComponent(stageName)}`)
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
    await tauriInvoke('stop_extract', { case_id: caseId }).catch(() => {})
  } else {
    try {
      await fetch(`${API_BASE}/cases/${caseId}/stop-extract`, { method: 'POST' })
    } catch { /* 忽略 */ }
  }
}

export async function getEvidenceIndex(caseId: string): Promise<any> {
  // 统一使用 fetch，不区分环境（Tauri 的 CORS 已配置允许 localhost）
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/evidence-index`)
  return res.json()
}

export async function getExtractStatus(caseId: string): Promise<any> {
  const res = await safeFetch(`${API_BASE}/cases/${caseId}/extract-status`)
  return res.json()
}

// ========== 5 阶段分析引擎 ==========

export interface IndictmentCandidate {
  filename: string
  doc_type: string
  preview: string
}

export async function getIndictmentCandidates(caseId: string): Promise<{ candidates: IndictmentCandidate[] }> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/indictment-candidates`)
  if (!res.ok) {
    throw new Error('获取候选文件失败')
  }
  return res.json()
}

export async function runAllStages(caseId: string, defendant: string, crimeType?: string, indictmentFile?: string): Promise<any> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/run-all`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ defendant, crime_type: crimeType, indictment_file: indictmentFile })
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

export async function runSingleStage(caseId: string, stageNum: number, defendant: string, crimeType?: string, indictmentFile?: string): Promise<any> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/run-stage/${stageNum}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ defendant, crime_type: crimeType, indictment_file: indictmentFile })
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

export async function saveStageMarkdown(caseId: string, stageNum: number, content: string): Promise<any> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/stage/${stageNum}/markdown`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  return res.json()
}

export async function saveFullReport(caseId: string, content: string): Promise<any> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/full-report`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
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

/** 打开外部链接（Tauri 环境下调用 shell.open，浏览器降级 window.open） */
export function openUrl(url: string): void {
  if (isTauri()) {
    tauriOpen(url)
  } else {
    window.open(url, '_blank')
  }
}

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
  deleteMdFile,
  deletePdfFile,
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
  getAnalysisState,
  resumePipeline,
  // 辩护意见子阶段
  getDefenseStages,
  getDefenseStageContent,
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
  getIndictmentCandidates,
  runAllStages,
  runSingleStage,
  getStageProgress,
  getStageStatus,
  getStageResult,
  getStageMarkdown,
  getFullReport,
  saveStageMarkdown,
  saveFullReport,
  // 证据提取
  extractEvidence,
  stopExtractEvidence,
  getEvidenceIndex,
  getExtractStatus,
  // 法律知识库
  listLegalKB,
  getLegalKBItem,
  createLegalKBItem,
  updateLegalKBItem,
  deleteLegalKBItem,
  searchLaws,
}
