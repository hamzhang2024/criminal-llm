// 案例库检索 API（刑事审判参考）
// 后端路由前缀 /api/case-search（本地代理，见 backend/case_search_api.py）

import { API_BASE } from './client'

export interface CaseSummary {
  case_no: string
  title: string
  charges: string[]
  issue: string
  holding_summary: string
}

export interface CaseSearchResult {
  total: number
  page: number
  size: number
  results: CaseSummary[]
}

export interface CaseCard extends CaseSummary {
  reasoning_excerpt: string
  keywords: string[]
}

export interface CaseFull {
  case_no: string
  title: string
  full_text: string
}

export interface CaseKeyValidation {
  valid: boolean
  prefix?: string
  used_today?: number
  quota_per_day?: number
}

async function request<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.detail || `请求失败（${res.status}）`)
  }
  return data as T
}

export function searchCases(q: string, charge: string, page: number, size = 20): Promise<CaseSearchResult> {
  const params = new URLSearchParams({ q, charge, page: String(page), size: String(size) })
  return request<CaseSearchResult>(`/case-search/search?${params}`)
}

export function getCharges(): Promise<{ charges: string[] }> {
  return request<{ charges: string[] }>(`/case-search/charges`)
}

export function getCaseFull(caseNo: string): Promise<CaseFull> {
  return request<CaseFull>(`/case-search/${encodeURIComponent(caseNo)}/full`)
}

export async function validateCaseKey(apiKey: string, serviceUrl?: string): Promise<CaseKeyValidation> {
  const res = await fetch(`${API_BASE}/case-search/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey, service_url: serviceUrl || undefined }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.detail || `请求失败（${res.status}）`)
  }
  return data as CaseKeyValidation
}
