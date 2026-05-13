// Tauri API 封装层
// 用于调用 Rust Commands（通过 HTTP 代理到 FastAPI 后端）

import { invoke } from '@tauri-apps/api/core'

/**
 * 健康检查 - 确认后端可用
 */
export async function healthCheck(): Promise<Record<string, string>> {
  return await invoke<Record<string, string>>('health_check')
}

/**
 * 列出所有案件
 */
export async function listCases(): Promise<unknown> {
  return await invoke<unknown>('list_cases')
}

/**
 * 获取案件的文件列表
 */
export async function getCaseFiles(caseId: string): Promise<unknown> {
  return await invoke<unknown>('get_case_files', { caseId })
}

/**
 * 获取步骤文件
 */
export async function getStepFiles(caseId: string, step: number): Promise<unknown> {
  return await invoke<unknown>('get_step_files', { caseId, step })
}

/**
 * 批量处理（step=1:PDF处理, step=2:拆分, step=3:转MD）
 */
export async function batchProcess(
  caseId: string,
  step: number,
  fileNames: string[],
  options: Record<string, unknown> = {}
): Promise<unknown> {
  return await invoke<unknown>('batch_process', { caseId, step, fileNames, options })
}

/**
 * 执行案卷分析
 */
export async function executeAnalysis(caseId: string, defendant: string): Promise<unknown> {
  return await invoke<unknown>('execute_analysis', { caseId, defendant })
}

/**
 * 对话分析
 */
export async function chatAnalysis(
  caseId: string,
  message: string,
  history: Record<string, unknown>[] = []
): Promise<unknown> {
  return await invoke<unknown>('chat_analysis', { caseId, message, history })
}

/**
 * 打开文件（macOS 系统默认程序）
 */
export async function openFile(filePath: string): Promise<boolean> {
  return await invoke<boolean>('open_file', { filePath })
}

/**
 * 转换 PDF 为 MD
 */
export async function convertToMd(caseId: string, fileName: string): Promise<unknown> {
  return await invoke<unknown>('convert_to_md', { caseId, fileName })
}

/**
 * 删除案件
 */
export async function deleteCase(caseId: string): Promise<unknown> {
  return await invoke<unknown>('delete_case', { caseId })
}

// 导出默认对象
export const tauriApi = {
  healthCheck,
  listCases,
  getCaseFiles,
  getStepFiles,
  batchProcess,
  executeAnalysis,
  chatAnalysis,
  openFile,
  convertToMd,
  deleteCase,
}
