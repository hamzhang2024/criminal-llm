// 原生对话框 & 通知 & 工作流持久化（Tauri 专属）

import { isTauri, tauriInvoke } from './client'

// 原生文件对话框
export async function pickFiles(title?: string, extensions?: string[]): Promise<string[]> {
  if (isTauri()) {
    return tauriInvoke<string[]>('pick_files', { title, extensions })
  }
  throw new Error('浏览器模式下不支持原生文件选择')
}

export async function pickFolder(title?: string): Promise<string | null> {
  if (isTauri()) {
    return tauriInvoke<string | null>('pick_folder', { title })
  }
  throw new Error('浏览器模式下不支持原生目录选择')
}

export async function pickMultiple(title?: string): Promise<string[]> {
  if (isTauri()) {
    return tauriInvoke<string[]>('pick_multiple', { title })
  }
  throw new Error('浏览器模式下不支持原生多选')
}

export async function sendNotification(title: string, body: string): Promise<void> {
  if (isTauri()) {
    return tauriInvoke('send_notification', { title, body })
  }
  if ('Notification' in window && Notification.permission === 'granted') {
    new Notification(title, { body })
  }
}

export async function showNativeConfirm(
  title: string,
  message: string,
  okLabel?: string,
  cancelLabel?: string,
): Promise<boolean> {
  if (isTauri()) {
    return tauriInvoke<boolean>('show_confirm_dialog', { title, message, okLabel, cancelLabel })
  }
  return window.confirm(`${title}\n${message}`)
}

// 工作流持久化（SQLite）
export async function createWorkflow(id: string, name: string, config: string): Promise<void> {
  if (isTauri()) {
    return tauriInvoke('create_workflow', { id, name, config })
  }
  throw new Error('浏览器模式下不支持工作流')
}

export async function updateWorkflowStatus(id: string, status: string, currentStep: number): Promise<void> {
  if (isTauri()) {
    return tauriInvoke('update_workflow_status', { id, status, currentStep })
  }
}

export async function listWorkflows(): Promise<any> {
  if (isTauri()) {
    return tauriInvoke('list_workflows')
  }
  return []
}

export async function getWorkflow(id: string): Promise<any> {
  if (isTauri()) {
    return tauriInvoke('get_workflow', { id })
  }
  return null
}

export async function addStep(id: string, workflowId: string, stepType: string, input: string): Promise<void> {
  if (isTauri()) {
    return tauriInvoke('add_step', { id, workflowId, stepType, input })
  }
}

export async function updateStep(id: string, status: string, progress: number, output?: string, error?: string): Promise<void> {
  if (isTauri()) {
    return tauriInvoke('update_step', { id, status, progress, output, error })
  }
}

export async function getSteps(workflowId: string): Promise<any> {
  if (isTauri()) {
    return tauriInvoke('get_steps', { workflowId })
  }
  return []
}

export async function addFile(id: string, workflowId: string, originalPath: string, fileType: string): Promise<void> {
  if (isTauri()) {
    return tauriInvoke('add_file', { id, workflowId, originalPath, fileType })
  }
}

export async function updateFilePaths(id: string, processedPath?: string, mdPath?: string): Promise<void> {
  if (isTauri()) {
    return tauriInvoke('update_file_paths', { id, processedPath, mdPath })
  }
}

export async function getFiles(workflowId: string): Promise<any> {
  if (isTauri()) {
    return tauriInvoke('get_files', { workflowId })
  }
  return []
}

export async function logOperation(workflowId: string, operation: string, detail: string): Promise<void> {
  if (isTauri()) {
    return tauriInvoke('log_operation', { workflowId, operation, detail })
  }
}

export async function deleteWorkflow(id: string): Promise<void> {
  if (isTauri()) {
    return tauriInvoke('delete_workflow', { id })
  }
}