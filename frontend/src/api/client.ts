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

/** API 基础地址：开发模式走 Vite 代理，生产模式读端口文件 */
export function getApiBase(): string {
  if (!import.meta.env.PROD) return '/api'
  // 生产模式：从 localStorage 读后端端口（Tauri 命令写入）
  const stored = typeof localStorage !== 'undefined' ? localStorage.getItem('backend_port') : null
  if (stored) return `http://localhost:${stored}/api`
  return 'http://localhost:8080/api'  // fallback
}

export let API_BASE = getApiBase()

/** 启动时调用：从 Rust 拿后端端口，写入 localStorage 并刷新 API_BASE */
export async function initApiBase(): Promise<void> {
  if (import.meta.env.PROD && isTauri()) {
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      const port = await invoke<number>('get_backend_port')
      localStorage.setItem('backend_port', String(port))
      API_BASE = `http://localhost:${port}/api`
    } catch {
      // fallback：保持 8080
    }
  }
}

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

/** 打开外部链接（Tauri 环境下调用 shell.open，浏览器降级 window.open） */
export function openUrl(url: string): void {
  if (isTauri()) {
    tauriOpen(url)
  } else {
    window.open(url, '_blank')
  }
}