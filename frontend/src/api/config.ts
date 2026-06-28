// 版本、更新、配置 API

import { isTauri, tauriInvoke, apiCall, API_BASE, openUrl } from './client'

export interface UpdateInfo {
  has_update: boolean
  current_version: string
  latest_version: string
  download_url: string
  release_notes: string
}

export interface ConfigStatus {
  mineru_token: boolean
  paddleocr_token: boolean
  mineru_mode: string
  mineru_local_url: string
  pdf_engine: string
  llm_model: string
  llm_base_url: string
  llm_api_key: boolean
  evidence_concurrency: number
  model_context_limit?: number
  model_strategy?: string
  yuandian_token: boolean
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

/** 读取配置（Tauri IPC 替代 HTTP GET /api/config） */
export async function getConfig(): Promise<ConfigStatus> {
  return apiCall<ConfigStatus>('get_config')
}

/** 保存配置（Tauri IPC 替代 HTTP PUT /api/config） */
export async function setConfig(config: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiCall<Record<string, unknown>>('set_config', config, { method: 'POST' })
}

/** 测试配置（Ping LLM / OCR API）— 通过 Python worker */
export async function testConfig(engine: 'llm' | 'mineru' | 'paddleocr', config: Record<string, unknown>): Promise<{ success: boolean, message?: string }> {
  if (isTauri()) {
    try {
      const result = await tauriInvoke<{ result?: { success: boolean, message?: string } }>('config_test', {
        engine,
        ...config,
      })
      return result?.result || result as unknown as { success: boolean, message?: string }
    } catch (e) {
      return { success: false, message: String(e) }
    }
  }
  // 浏览器开发模式 HTTP 降级
  const res = await fetch(`${API_BASE}/config/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ engine, ...config }),
  })
  return res.json()
}

export { openUrl }