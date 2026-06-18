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

// ========== 证据质证意见 ==========

// 审查发现（结构化问题）
export interface EvidenceFinding {
  issue: string
  legal_basis: string
  details: string
}

// 单项审查结果（合法性/真实性/关联性）
export interface PropertyReview {
  conclusion: '采信' | '存疑' | '不采信'
  score: number
  findings?: EvidenceFinding[]
  issues?: string[]  // 兼容旧格式
  cross_opinion?: string
  strategy?: string[]
}

export interface EvidenceReviewItem {
  evidence_id?: number
  evidence_name: string
  evidence_ref?: string
  evidence_type?: string
  authenticity?: PropertyReview
  legality?: PropertyReview
  relevance?: PropertyReview
  review_summary?: string
  final_conclusion?: '采信' | '存疑' | '不采信'
  cross_examination_summary?: string
  // 组合质证字段（组合审查时存在，独立审查时为空）
  group_id?: string
  group_label?: string
  group_type?: string
  member_refs?: number[]
  group_findings?: Array<{
    finding_type: string
    issue: string
    legal_basis: string
    details: string
    evidence_refs?: string[]
  }>
  per_member_notes?: Record<string, { cross_opinion: string; strategy: string[] }>
  group_cross_summary?: string
  repeated_statement_exclusion?: boolean
  error?: string
}

export interface EvidenceReviewResult {
  case_id: string
  total_evidence: number
  total_groups?: number
  reviews: EvidenceReviewItem[]
  generated_at?: string
  error?: string
}

/** 对全部证据进行三性审查（触发异步任务，立即返回状态） */
export async function reviewEvidence(caseId: string): Promise<{ status: string; message?: string }> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/review-evidence`, {
    method: 'POST',
  })
  if (!res.ok) {
    throw new Error('证据审查失败')
  }
  return res.json()
}

/** 获取证据审查/质证/阅卷笔录任务状态（供轮询） */
export async function getReviewTaskStatus(caseId: string): Promise<{
  status: string
  task_type?: string
  total_evidence?: number
  processed?: number
  current_evidence?: string
  elapsed_seconds?: number
  error?: string | null
}> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/review-evidence-status`)
  if (!res.ok) {
    throw new Error('获取任务状态失败')
  }
  return res.json()
}

/** 获取证据三性审查结果 */
export async function getEvidenceReview(caseId: string): Promise<EvidenceReviewResult> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/evidence-review`)
  if (!res.ok) {
    return { case_id: caseId, total_evidence: 0, reviews: [], error: '获取失败' }
  }
  return res.json()
}

// ========== 阅卷笔录 API ==========

export interface ReviewNotesResult {
  case_id: string
  content: string
  generated_at?: string
  error?: string
}

/** 生成阅卷笔录 */
export async function generateReviewNotes(caseId: string): Promise<ReviewNotesResult> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/review-notes`, {
    method: 'POST',
  })
  if (!res.ok) {
    throw new Error('阅卷笔录生成失败')
  }
  return res.json()
}

/** 获取阅卷笔录 */
export async function getReviewNotes(caseId: string): Promise<ReviewNotesResult> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/review-notes`)
  if (!res.ok) {
    return { case_id: caseId, content: '', error: '获取失败' }
  }
  return res.json()
}

// ========== 质证意见 API ==========

export interface CrossExaminationResult {
  case_id: string
  content: string
  total_evidence?: number
  problematic_count?: number
  generated_at?: string
  error?: string
}

/** 生成质证意见 */
export async function generateCrossExamination(caseId: string): Promise<CrossExaminationResult> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/cross-examination`, {
    method: 'POST',
  })
  if (!res.ok) {
    throw new Error('质证意见生成失败')
  }
  return res.json()
}

/** 获取质证意见 */
export async function getCrossExamination(caseId: string): Promise<CrossExaminationResult> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/cross-examination`)
  if (!res.ok) {
    return { case_id: caseId, content: '', error: '获取失败' }
  }
  return res.json()
}

// ========== 证据链可视化 ==========

export interface EvidenceChainNode {
  id: number | string
  name: string
  type: string
  description?: string
  category?: string
  color?: string
  persons?: string
  group?: string
  required?: boolean
  evidence_count?: number
  strength?: string
  proves?: string[]
  proves_strength?: Record<string, string>
}

export interface EvidenceChainEdge {
  source: number | string
  target: number | string
  type: 'prove' | 'corroborate' | 'contradict' | 'support' | 'basis'
  label: string
  strength?: string
  detail?: string
}

export interface EvidenceChainGroup {
  id: string
  name: string
  color: string
  count: number
}

export interface EvidenceChainAccusation {
  id?: string
  name: string
  description: string
  source: string
}

export interface EvidenceChainWeakPoint {
  fact_id: string
  fact_name: string
  issue: string
  risk: 'high' | 'medium' | 'low'
}

export interface EvidenceChainSummary {
  total_evidence: number
  displayed_evidence?: number
  total_relations: number
  strong_chains: string[]
  weak_chains: string[]
}

export interface EvidenceChainData {
  accusation?: EvidenceChainAccusation
  nodes: EvidenceChainNode[]
  edges: EvidenceChainEdge[]
  groups: EvidenceChainGroup[]
  facts_to_prove?: EvidenceChainNode[]
  evidence_groups?: EvidenceChainGroup[]
  weak_points?: EvidenceChainWeakPoint[]
  contradictions?: { evidence: string; name: string; issues: string[] }[]
  summary?: EvidenceChainSummary
  total_evidence: number
  total_relations: number
  error?: string
}

/** 获取证据链可视化数据 */
export async function getEvidenceChain(caseId: string): Promise<EvidenceChainData> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/evidence-chain`)
  if (!res.ok) {
    return { nodes: [], edges: [], groups: [], total_evidence: 0, total_relations: 0, error: '获取失败' }
  }
  const data = await res.json()
  // 处理后端错误格式 {"detail": "..."}
  if (data.detail && !data.nodes) {
    return { nodes: [], edges: [], groups: [], total_evidence: 0, total_relations: 0, error: data.detail }
  }
  return data
}

// ========== 人物关系图（SVG 可视化）==========

export interface PersonNode {
  id: string
  name: string
  role: 'defendant' | 'co_defendant' | 'victim' | 'witness' | 'other'
  description?: string
  evidenceRefs?: string[]
}

export interface RelationEdge {
  source: string
  target: string
  type: 'participation' | 'cooperation' | 'family' | 'friendship' | 'conflict' | 'introduction' | 'financial' | 'other'
  label: string
}

export interface RelationGraphData {
  nodes: PersonNode[]
  edges: RelationEdge[]
  error?: string
}

/** 获取人物关系图数据 */
export async function getPersonRelation(caseId: string): Promise<RelationGraphData> {
  const url = `${API_BASE}/stage-analysis/${caseId}/person-relation`
  console.log('[API] getPersonRelation URL:', url, 'PROD:', import.meta.env.PROD)
  const res = await fetch(url)
  console.log('[API] getPersonRelation response status:', res.status, res.url)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    console.error('[API] getPersonRelation error body:', text)
    return { nodes: [], edges: [], error: '获取失败' }
  }
  const json = await res.json()
  console.log('[API] getPersonRelation data nodes:', json.nodes?.length, 'edges:', json.edges?.length)
  return json
}

// ========== 事件时间线（SVG 可视化）==========

export interface EventNode {
  id: string
  date: string
  title: string
  description?: string
  type?: 'crime' | 'evidence' | 'procedure' | 'defense' | 'other'
  persons?: string[]
  evidenceRefs?: string[]
}

export interface TimelineData {
  events: EventNode[]
  error?: string
}

/** 获取事件时间线数据 */
export async function getEventTimeline(caseId: string): Promise<TimelineData> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/event-timeline`)
  if (!res.ok) {
    return { events: [], error: '获取失败' }
  }
  return res.json()
}

// ========== 类案检索 ==========

export interface SimilarCase {
  title: string
  court: string
  crime_type: string
  amount?: string
  result: string
  key_point: string
  fact_summary?: string
  priority_note?: string
  link?: string
}

export interface SimilarCasesData {
  crime_type: string
  key_facts: string[]
  similar_cases: SimilarCase[]
  error?: string
}

/** 搜索类似案例 */
export async function searchSimilarCases(caseId: string): Promise<SimilarCasesData> {
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/similar-cases`, {
    method: 'POST',
  })
  if (!res.ok) {
    return { crime_type: '', key_facts: [], similar_cases: [], error: '搜索失败' }
  }
  return res.json()
}