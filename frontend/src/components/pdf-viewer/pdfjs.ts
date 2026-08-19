// pdfjs-dist 共享加载器：动态 import + worker 配置，全局只加载一次
let cached: typeof import('pdfjs-dist') | null = null

export async function getPdfjs(): Promise<typeof import('pdfjs-dist')> {
  if (!cached) {
    const lib = await import('pdfjs-dist')
    // worker 指向 Vite 打包的本地资源（?url 由 Vite 5 原生支持）
    const workerUrl = (await import('pdfjs-dist/build/pdf.worker.min.js?url')).default
    lib.GlobalWorkerOptions.workerSrc = workerUrl
    cached = lib
  }
  return cached
}
