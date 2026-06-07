// 版本、更新、配置 API

import { isTauri, tauriInvoke, openUrl } from './client'

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