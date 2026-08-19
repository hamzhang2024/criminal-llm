// 批注数据层：后端持久化 + localStorage 旧数据迁移 + 失败降级
import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE } from '../../api'
import type { PdfAnnotation } from './types'

interface AnnotationsFile {
  version: number
  annotations: PdfAnnotation[]
}

const legacyKey = (caseId: string) => `annotations-${caseId}`
const legacyBackupKey = (caseId: string) => `annotations-${caseId}.bak`

async function fetchRemote(caseId: string): Promise<PdfAnnotation[]> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/annotations`)
  if (!res.ok) throw new Error(`批注读取失败: ${res.status}`)
  const data: AnnotationsFile = await res.json()
  return data.annotations ?? []
}

async function pushRemote(caseId: string, annotations: PdfAnnotation[]): Promise<void> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/annotations`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version: 1, annotations }),
  })
  if (!res.ok) throw new Error(`批注保存失败: ${res.status}`)
}

// localStorage 旧数据迁移：后端为空且本地有 → 上传后旧 key 改名 .bak（幂等）
async function migrateLegacy(caseId: string, remote: PdfAnnotation[]): Promise<PdfAnnotation[]> {
  if (remote.length > 0) return remote
  const raw = localStorage.getItem(legacyKey(caseId))
  if (!raw || localStorage.getItem(legacyBackupKey(caseId))) return remote
  try {
    const legacy: PdfAnnotation[] = JSON.parse(raw)
    if (!Array.isArray(legacy) || legacy.length === 0) return remote
    await pushRemote(caseId, legacy)
    localStorage.setItem(legacyBackupKey(caseId), raw)
    localStorage.removeItem(legacyKey(caseId))
    return legacy
  } catch {
    return remote
  }
}

export interface UseAnnotationsResult {
  annotations: PdfAnnotation[]
  loaded: boolean
  saveError: boolean          // 后端写入失败（已降级 localStorage 兜底）
  addAnnotation: (a: PdfAnnotation) => void
  updateAnnotation: (id: string, text: string) => void
  updateAnnotationPosition: (id: string, x: number, y: number) => void
  deleteAnnotation: (id: string) => void
}

export function useAnnotations(caseId: string | undefined): UseAnnotationsResult {
  const [annotations, setAnnotations] = useState<PdfAnnotation[]>([])
  const [loaded, setLoaded] = useState(false)
  const [saveError, setSaveError] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 初始加载：后端优先，空则尝试迁移 localStorage 旧数据
  useEffect(() => {
    if (!caseId) return
    let cancelled = false
    setLoaded(false)
    fetchRemote(caseId)
      .then(remote => migrateLegacy(caseId, remote))
      .then(list => { if (!cancelled) { setAnnotations(list); setLoaded(true) } })
      .catch(() => {
        // 后端不可达时降级 localStorage，保证批注可用
        if (cancelled) return
        try {
          setAnnotations(JSON.parse(localStorage.getItem(legacyKey(caseId)) ?? '[]'))
        } catch { setAnnotations([]) }
        setSaveError(true)
        setLoaded(true)
      })
    return () => { cancelled = true }
  }, [caseId])

  // 变更后 500ms 防抖写后端；失败写 localStorage 兜底（下次加载按迁移逻辑重试）
  useEffect(() => {
    if (!caseId || !loaded) return
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      pushRemote(caseId, annotations)
        .then(() => setSaveError(false))
        .catch(() => {
          localStorage.setItem(legacyKey(caseId), JSON.stringify(annotations))
          setSaveError(true)
        })
    }, 500)
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [annotations, caseId, loaded])

  const addAnnotation = useCallback((a: PdfAnnotation) => {
    setAnnotations(prev => [...prev, a])
  }, [])

  const updateAnnotation = useCallback((id: string, text: string) => {
    setAnnotations(prev => prev.map(a => a.id === id ? { ...a, text } : a))
  }, [])

  const updateAnnotationPosition = useCallback((id: string, x: number, y: number) => {
    setAnnotations(prev => prev.map(a => a.id === id ? { ...a, x, y } : a))
  }, [])

  const deleteAnnotation = useCallback((id: string) => {
    setAnnotations(prev => prev.filter(a => a.id !== id))
  }, [])

  return { annotations, loaded, saveError, addAnnotation, updateAnnotation, updateAnnotationPosition, deleteAnnotation }
}
