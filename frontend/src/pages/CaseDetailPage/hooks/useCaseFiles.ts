// 案件文件管理 Hook

import { useState, useCallback, useEffect } from 'react'
import { api, serveFileUrl } from '../../../api'
import { showAlert, showConfirm } from '../../../components/MacOSDialog'

export interface CaseFile {
  id: string
  name: string
  size: number
  status: 'pending' | 'processing' | 'done' | 'error'
  selected?: boolean
  error?: string
  path?: string
  processedPath?: string
  mdPath?: string
  splitResults?: any[]
  source?: string
}

export interface PreviewFile extends CaseFile {
  path: string
}

export function useCaseFiles(caseId: string | undefined, currentStep: number) {
  const [files, setFiles] = useState<CaseFile[]>([])
  const [previewFile, setPreviewFile] = useState<PreviewFile | null>(null)

  // 从 localStorage 恢复文件选择状态
  const [uploading, setUploading] = useState(false)

  // 切换步骤时加载文件
  useEffect(() => {
    if (!caseId) return

    const loadFiles = async () => {
      try {
        let data: any
        if (currentStep === 0) {
          data = await api.getCaseFiles(caseId!)
        } else if (currentStep >= 1 && currentStep <= 2) {
          data = await api.getStepFiles(caseId!, currentStep)
        } else {
          data = await api.getStepFiles(caseId!, 3)
        }

        if (Array.isArray(data)) {
          setFiles(data.map((f: any) => ({
            id: f.id,
            name: f.name,
            size: f.size,
            status: f.status || 'pending',
            selected: true,
            source: f.source,
            processedPath: f.processedPath,
          })))
        }
      } catch (err) {
        console.error('加载文件失败:', err)
      }
    }
    loadFiles()
  }, [caseId, currentStep])

  // 选中/取消选中
  const toggleSelect = useCallback((id: string) => {
    setFiles(prev => prev.map(f => f.id === id ? { ...f, selected: !f.selected } : f))
  }, [])

  // 全选/取消全选
  const toggleSelectAll = useCallback(() => {
    setFiles(prev => {
      const allSelected = prev.every(f => f.selected)
      return prev.map(f => ({ ...f, selected: !allSelected }))
    })
  }, [])

  // 获取已选中的 pending 文件
  const getSelectedFiles = useCallback(() => {
    const selected = files.filter(f => f.selected && f.status === 'pending')
    if (selected.length === 0) return files.filter(f => f.status === 'pending')
    return selected
  }, [files])

  // 上传文件
  const handleFileSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files
    if (!selected || selected.length === 0) return
    if (!caseId) {
      showAlert({ title: '错误', message: '案件 ID 不存在，请刷新页面后重试', variant: 'danger' })
      return
    }

    const existingNames = new Set(files.map(f => f.name))
    const newFiles = Array.from(selected).filter(f => !existingNames.has(f.name))
    const dupCount = selected.length - newFiles.length

    if (newFiles.length === 0) {
      showAlert({ title: '提示', message: `所选文件均已存在，无需重复上传`, variant: 'info' })
      return
    }

    setUploading(true)
    try {
      const result = await api.uploadFiles(caseId!, newFiles)
      if (!result.success) throw new Error(result.error || result.detail || '上传失败')

      const filesData = await api.getCaseFiles(caseId!)
      setFiles(filesData.map((f: any) => ({
        id: f.id, name: f.name, size: f.size, status: f.status || 'pending', selected: true
      })))

      showAlert({
        title: '上传成功',
        message: dupCount > 0 ? `已上传 ${newFiles.length} 个新文件，跳过 ${dupCount} 个重复文件` : `已上传 ${newFiles.length} 个文件`,
        variant: 'success'
      })
    } catch (err) {
      showAlert({ title: '上传失败', message: err instanceof Error ? err.message : '未知错误', variant: 'danger' })
    } finally {
      setUploading(false)
    }
  }, [caseId, files])

  // 刷新文件列表
  const refreshFiles = useCallback(async () => {
    if (!caseId) return
    try {
      const data = await api.getStepFiles(caseId!, currentStep)
      if (Array.isArray(data)) {
        setFiles(data.map((f: any) => ({
          id: f.id, name: f.name, size: f.size, status: f.status || 'pending', source: f.source,
        })))
      }
    } catch { /* ignore */ }
  }, [caseId, currentStep])

  // 删除文件
  const handleRemoveFile = useCallback(async (file: CaseFile) => {
    const confirmed = await showConfirm({
      title: '确认删除',
      message: `确定要删除「${file.name}」吗？`,
      variant: 'danger',
    })
    if (!confirmed) return

    try {
      await api.deleteFile(caseId!, file.name)
      const filesData = await api.getCaseFiles(caseId!)
      setFiles(filesData.map((f: any) => ({
        id: f.id, name: f.name, size: f.size, status: f.status || 'pending', selected: true
      })))
    } catch (err) {
      showAlert({ title: '删除失败', message: err instanceof Error ? err.message : '删除失败', variant: 'danger' })
    }
  }, [caseId])

  // 删除 MD 文件
  const handleDeleteMd = useCallback(async (mdFileName: string) => {
    const confirmed = await showConfirm({
      title: '确认删除',
      message: `确定要删除「${mdFileName}」吗？`,
      variant: 'danger',
    })
    if (!confirmed) return

    try {
      await api.deleteMdFile(caseId!, mdFileName)
      const filesData = await api.getStepFiles(caseId!, 3)
      if (Array.isArray(filesData)) {
        setFiles(filesData.map((f: any) => ({
          id: f.id, name: f.name, size: f.size, status: 'pending', source: f.source,
        })))
      }
    } catch (err) {
      showAlert({ title: '删除失败', message: err instanceof Error ? err.message : '删除失败', variant: 'danger' })
    }
  }, [caseId])

  // 删除 PDF 文件（步骤 2）
  const handleDeletePdf = useCallback(async (pdfFileName: string) => {
    const confirmed = await showConfirm({
      title: '确认删除',
      message: `确定要删除「${pdfFileName}」吗？`,
      variant: 'danger',
    })
    if (!confirmed) return

    try {
      await api.deletePdfFile(caseId!, pdfFileName)
      const filesData = await api.getStepFiles(caseId!, 2)
      if (Array.isArray(filesData)) {
        setFiles(filesData.map((f: any) => ({
          id: f.id, name: f.name, size: f.size, status: f.status || 'pending', source: f.source,
        })))
      }
    } catch (err) {
      showAlert({ title: '删除失败', message: err instanceof Error ? err.message : '删除失败', variant: 'danger' })
    }
  }, [caseId])

  // 删除原始 PDF（步骤 1）
  const handleDeleteOriginal = useCallback(async (pdfFileName: string) => {
    const confirmed = await showConfirm({
      title: '确认删除',
      message: `确定要删除「${pdfFileName}」吗？`,
      variant: 'danger',
    })
    if (!confirmed) return

    try {
      await api.deleteFile(caseId!, pdfFileName)
      const stepFiles = await api.getStepFiles(caseId!, 1)
      if (Array.isArray(stepFiles)) {
        setFiles(stepFiles.map((f: any) => ({
          id: f.id, name: f.name, size: f.size, status: f.status || 'pending', source: f.source,
        })))
      }
    } catch (err) {
      showAlert({ title: '删除失败', message: err instanceof Error ? err.message : '删除失败', variant: 'danger' })
    }
  }, [caseId])

  // 重新转换 MD
  const handleReconvertMd = useCallback(async (mdFileName: string) => {
    const pdfName = mdFileName.replace(/\.md$/, '') + '.pdf'
    try {
      const result = await api.convertToMd(caseId!, pdfName)
      if (result.success) {
        if (currentStep >= 3) {
          const filesData = await api.getStepFiles(caseId!, 3)
          if (Array.isArray(filesData)) {
            setFiles(filesData.map((f: any) => ({
              id: f.id, name: f.name, size: f.size, status: 'pending', source: f.source,
            })))
          }
        }
      } else {
        throw new Error(result.error || '转换失败')
      }
    } catch (err) {
      showAlert({ title: '转换失败', message: err instanceof Error ? err.message : '转换失败', variant: 'danger' })
    }
  }, [caseId, currentStep])

  // 预览文件
  const handleOpenFile = useCallback((file: CaseFile) => {
    // 如果传入的 file.path 已经包含完整 URL（如证据预览），直接使用
    if (file.path && (file.path.includes('/serve-file') || file.path.startsWith('http') || file.path.startsWith('/api'))) {
      setPreviewFile({ ...file, path: file.path })  // 确保 path 存在
      return
    }

    if (file.name.endsWith('.md')) {
      const serveUrl = serveFileUrl(caseId!, file.name, 'md')
      setPreviewFile({ ...file, path: serveUrl })
      return
    }

    let dir = 'original'
    if (currentStep === 1) dir = 'processed'
    else if (currentStep >= 2) dir = 'md'

    let previewName = file.name
    if (currentStep === 1 && !file.name.includes('_去水印')) {
      const stem = file.name.replace(/\.pdf$/i, '')
      previewName = `${stem}_去水印.pdf`
    }

    const serveUrl = serveFileUrl(caseId!, previewName, dir)
    setPreviewFile({ ...file, path: serveUrl, name: previewName })
  }, [caseId, currentStep])

  const closePreview = useCallback(() => {
    setPreviewFile(null)
  }, [])

  return {
    files, setFiles,
    previewFile, setPreviewFile,
    uploading, setUploading,
    toggleSelect, toggleSelectAll, getSelectedFiles,
    handleFileSelect, handleRemoveFile,
    handleDeleteMd, handleDeletePdf, handleDeleteOriginal,
    handleReconvertMd, handleOpenFile, closePreview,
    refreshFiles,
  }
}