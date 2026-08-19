// PDF 查看器共享类型
import type { PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist'

export type { PDFDocumentProxy, PDFPageProxy }

export interface PdfAnnotation {
  id: string
  type?: 'note' | 'rect'   // 缺省为 note（兼容旧数据）
  pageNum?: number
  x: number           // 页面百分比坐标（0-100）
  y: number           // 页面百分比坐标（0-100）
  rect?: { x: number; y: number; w: number; h: number }  // 框选区域（百分比，type=rect 时有效）
  text: string
  color: string
  pdfFile?: string
  createdAt: string
}

// 批注工具类型
export type AnnotationTool = 'note' | 'rect'

export interface PdfViewerProps {
  caseId: string
  pdfFilename: string
  annotations: PdfAnnotation[]
  onAddAnnotation: (annotation: PdfAnnotation) => void
  onUpdateAnnotation: (id: string, text: string) => void
  onDragAnnotation: (id: string, x: number, y: number) => void
  onDeleteAnnotation: (id: string) => void
  annotationMode: boolean
}

// 页面尺寸（scale=1 基准）
export interface PageSize {
  width: number
  height: number
}
