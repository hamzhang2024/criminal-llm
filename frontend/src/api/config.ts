// 版本、更新、配置 API

import { isTauri, tauriInvoke, openUrl, API_BASE } from './client'

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

export { openUrl }

// LLM 用量统计（后端进程级累计：每次调用都统计，与供应商无关）
export interface CacheStats {
  calls: number
  input_tokens: number
  output_tokens: number
  cache_hit_tokens: number
  hit_rate: number  // 0~1，缓存命中 tokens / 输入 tokens
}

export async function getCacheStats(): Promise<CacheStats> {
  const res = await fetch(`${API_BASE}/llm/cache-stats`)
  return res.json()
}