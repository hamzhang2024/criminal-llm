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

/** 后端就绪检测结果 */
export interface BackendReadyResult {
  ready: boolean
  /** 最后一次探测的错误信息（ready=true 时为 null）。供 UI 展示真实失败原因。 */
  lastError: string | null
}

/** 后端就绪检测：轮询 /api/health 直到后端启动完成
 * @param timeout 最大等待时间（毫秒），默认 30 秒
 * @param interval 轮询间隔（毫秒），默认 500ms
 * @returns Promise<BackendReadyResult> 含就绪状态与最后一次错误信息
 */
export async function waitForBackend(timeout = 30000, interval = 500): Promise<BackendReadyResult> {
  const startTime = Date.now()
  let lastError: string | null = null

  while (Date.now() - startTime < timeout) {
    try {
      const res = await fetch(`${API_BASE}/health`, {
        signal: timeoutSignal(2000)
      })
      if (res.ok) {
        return { ready: true, lastError: null }
      }
      // 非 2xx：连上了但应用层异常，记录状态码
      lastError = `后端响应 HTTP ${res.status}`
    } catch (e) {
      // 捕获真实错误并归一化为可读中文，避免空 catch 吞掉诊断信号
      lastError = normalizeBackendError(e)
    }
    await new Promise(resolve => setTimeout(resolve, interval))
  }

  return { ready: false, lastError }
}

/** 将 fetch 探测的网络错误归一化为可读中文 */
function normalizeBackendError(e: unknown): string {
  if (e instanceof DOMException && e.name === 'TimeoutError') {
    return '连接超时（2 秒内后端无响应，可能后端启动极慢或请求被丢弃）'
  }
  const msg = e instanceof Error ? e.message : String(e)
  // 浏览器 fetch 无法区分「连接被拒绝/进程已退出」与「防火墙拦截」，统一归为连接失败
  if (msg.includes('Failed to fetch') || msg.includes('NetworkError') || msg.includes('Load failed')) {
    return '无法连接到后端（连接被拒绝或被拦截——常见于后端进程已退出、防火墙拦截或端口未监听）'
  }
  return msg
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

/** 统一 API 调用层 — 透明切换 Tauri IPC vs HTTP
 *
 * Tauri 环境：cmd 映射到 Tauri command（无 HTTP，进程内 IPC）
 * 浏览器环境：cmd 转换为 HTTP 路径后 fetch
 *
 * @example
 * // Tauri: invoke('list_cases', { owner: 'admin' })
 * // HTTP:  GET /api/list-cases?owner=admin
 * const result = await apiCall<CasesResponse>('list_cases', { owner: 'admin' })
 */
export async function apiCall<T>(
  cmd: string,
  args?: Record<string, unknown>,
  options?: { method?: 'GET' | 'POST' | 'PUT' | 'DELETE'; body?: Record<string, unknown> }
): Promise<T> {
  if (isTauri()) {
    // Tauri 环境：直接 invoke（无 HTTP）
    return tauriInvoke<T>(cmd, args)
  }

  // 浏览器环境（开发用）：转换为 HTTP 调用
  const cmdPath = cmd.replace(/_/g, '/').toLowerCase()
  const url = `${API_BASE}/${cmdPath}`
  const method = options?.method || 'GET'

  const fetchOptions: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }

  if (method !== 'GET' && (args || options?.body)) {
    fetchOptions.body = JSON.stringify(args || options?.body)
  }

  const res = await fetch(url, fetchOptions)
  if (!res.ok) {
    throw new Error(`API ${cmd} failed: ${res.status} ${res.statusText}`)
  }
  return res.json()
}