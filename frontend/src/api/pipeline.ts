// 分析流水线 API

import { API_BASE, subscribeSSE } from './client'

export async function runPipelineStep(caseId: string, step: number, defendant: string, crimeType?: string, indictmentFile?: string): Promise<any> {
  const controller = new AbortController()
  const timeoutMs = step >= 2 ? 7200000 : 600000
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

/** 流水线进度（SSE 推送 payload） */
export interface PipelineProgressStream {
  case_id: string
  running: boolean
  message?: string
  current?: number
  total?: number
  elapsed_seconds?: number
}

/**
 * 订阅流水线进度（SSE 推送，替代轮询）。
 * 持续推送当前进度，由调用方控制订阅生命周期（任务结束时主动关闭）。
 */
export function subscribePipelineProgress(
  caseId: string,
  onStatus: (status: PipelineProgressStream) => void,
  onError?: (error: Event) => void,
): () => void {
  return subscribeSSE<PipelineProgressStream>(
    `${API_BASE}/pipeline/${caseId}/progress/stream`,
    onStatus,
    onError,
  )
}

/** 辩护意见子阶段状态（SSE 推送 payload） */
export interface DefenseStagesStream {
  case_id: string
  stages: Record<string, string>
  full_report: boolean
  defense_dir: string | null
  error?: string
}

/** 订阅辩护意见子阶段状态（SSE）。全部完成或完整报告生成后流自动关闭。 */
export function subscribeDefenseStages(
  caseId: string,
  onStatus: (status: DefenseStagesStream) => void,
  onError?: (error: Event) => void,
): () => void {
  return subscribeSSE<DefenseStagesStream>(
    `${API_BASE}/pipeline/${caseId}/defense-stages/stream`,
    onStatus,
    onError,
  )
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
  const timeoutId = setTimeout(() => controller.abort(), 7200000)
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

// 辩护意见子阶段
export async function getDefenseStages(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/defense-stages`)
  return res.json()
}

export async function getDefenseStageContent(caseId: string, stageName: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/defense-stage/${encodeURIComponent(stageName)}`)
  return res.json()
}

// Wiki
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

// 证据浏览
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

// 矛盾分析
export async function getContradictionFiles(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/evidence/contradictions`)
  return res.json()
}

export async function getContradictionContent(caseId: string, filename: string): Promise<any> {
  const res = await fetch(`${API_BASE}/pipeline/${caseId}/evidence/contradiction/${encodeURIComponent(filename)}`)
  return res.json()
}