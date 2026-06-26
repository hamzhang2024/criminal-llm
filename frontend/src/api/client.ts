// API 客户端基础工具

/** 是否在 Tauri 环境下运行 */
export function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

/** 懒加载 Tauri invoke（避免浏览器环境下模块加载报错） */
export async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import('@tauri-apps/api/core')
  return invoke<T>(cmd, args)
}

/** 懒加载 Tauri shell.open */
export async function tauriOpen(url: string): Promise<void> {
  const { open } = await import('@tauri-apps/plugin-shell')
  open(url).catch(() => {})
}

/** API 基础地址：开发模式走 Vite 代理，生产模式直连后端 */
export const API_BASE = import.meta.env.PROD ? 'http://127.0.0.1:8080/api' : '/api'

/** AbortSignal.timeout polyfill（兼容旧版浏览器） */
export function timeoutSignal(ms: number): AbortSignal {
  if (typeof AbortSignal.timeout === 'function') {
    return AbortSignal.timeout(ms)
  }
  // Polyfill: 手动创建 AbortController 并设置超时
  const controller = new AbortController()
  setTimeout(() => controller.abort(new DOMException('TimeoutError', 'TimeoutError')), ms)
  return controller.signal
}

/** 后端就绪检测：轮询 /api/health 直到后端启动完成
 * @param timeout 最大等待时间（毫秒），默认 30 秒
 * @param interval 轮询间隔（毫秒），默认 500ms
 * @returns Promise<boolean> 后端是否就绪
 */
export async function waitForBackend(timeout = 30000, interval = 500): Promise<boolean> {
  const startTime = Date.now()

  while (Date.now() - startTime < timeout) {
    try {
      const res = await fetch(`${API_BASE}/health`, {
        signal: timeoutSignal(2000)
      })
      if (res.ok) {
        return true
      }
    } catch {
      // 后端未就绪，继续等待
    }
    await new Promise(resolve => setTimeout(resolve, interval))
  }

  return false
}

/** 安全的 fetch 包装：将网络错误转为友好的中文提示 */
export async function safeFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init)
  } catch {
    throw new Error('后端未启动或连接失败，请重新启动应用')
  }
}

/**
 * 通用 SSE 订阅：连接后端的 Server-Sent Events 流。
 *
 * 后端在状态变化时推送 `status` 事件，任务终态后自动关闭流。
 * EventSource 内置自动重连，无需手动处理。
 *
 * @param url SSE 端点完整 URL
 * @param onStatus 收到状态事件的回调
 * @param onError 连接错误回调（可选，EventSource 会自动重连）
 * @returns 关闭订阅的函数（组件卸载时调用）
 */
export function subscribeSSE<T = unknown>(
  url: string,
  onStatus: (status: T) => void,
  onError?: (error: Event) => void,
): () => void {
  const source = new EventSource(url)

  source.addEventListener('status', (e: MessageEvent) => {
    try {
      onStatus(JSON.parse(e.data) as T)
    } catch {
      // 解析失败忽略，下一条事件会覆盖
    }
  })

  if (onError) {
    source.onerror = onError
  }

  return () => source.close()
}

/** 打开外部链接（Tauri 环境下调用 shell.open，浏览器降级 window.open） */
export function openUrl(url: string): void {
  if (isTauri()) {
    tauriOpen(url)
  } else {
    window.open(url, '_blank')
  }
}