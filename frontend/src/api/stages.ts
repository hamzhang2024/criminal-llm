// 5 阶段分析引擎 API

import { API_BASE } from './client'

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
  if (!res.ok) {
    return { content: '' }
  }
  return res.json()
}

export async function getFullReport(caseId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/full-report`)
  if (!res.ok) {
    return { content: '' }
  }
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