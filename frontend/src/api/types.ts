// API 数据类型定义

export interface Case {
  id: string
  name: string
  defendant: string
  created_at: string
  file_count: number
  status: 'new' | 'processing' | 'done'
  owner?: string
}

export interface PendingFolder {
  path: string
  name: string
  pdf_count: number
  size_mb: number
}

export interface TrashItem {
  id: string
  name: string
  defendant: string
  deleted_at: string
  days_left: number
  size_mb: number
}

export interface FileInfo {
  id: string
  name: string
  size: number
  size_human: string
  path: string
  source?: string
  modified_at?: string
  md_name?: string
  status?: string
}

export interface BatchProcessResult {
  success: boolean
  results?: Array<{
    success: boolean
    file?: string
    output?: string
    error?: string
    suggested_splits?: Array<{
      name?: string
      doc_type?: string
      start?: number
      end?: number
      start_page?: number
      end_page?: number
      type?: string
    }>
    num_pages?: number
    split_files?: Array<{ name: string; start: number; end: number }>
    md_name?: string
  }>
  error?: string
}

export interface ThumbnailResult {
  success: boolean
  thumbnails?: Array<{ page: number; url: string } | string>
  total?: number
  error?: string
}

export interface SplitTextPreview {
  text: string
  total_lines: number
}

export interface AnalysisCreateResult {
  success: boolean
  case_id?: string
  evidence_count?: number
  error?: string
}

export interface AnalysisProgress {
  stage?: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  current?: number
  total?: number
  progress?: number
  message?: string
  report?: string
}

export interface ChatResponse {
  success: boolean
  message?: string
  answer?: string
  error?: string
}

export interface ReportResponse {
  success: boolean
  report?: {
    raw_markdown?: string
    sections?: Record<string, string>
    defense_points?: string[]
    [key: string]: unknown
  }
  defendant?: string
  error?: string
}

export interface StageStatus {
  stage_1: { name: string; completed: boolean }
  stage_2: { name: string; completed: boolean }
  stage_3: { name: string; completed: boolean }
  stage_4: { name: string; completed: boolean }
  stage_5: { name: string; completed: boolean }
}

export interface StageProgress {
  running: boolean
  stage?: number
  message?: string
  status?: string
}

export interface StageResult {
  stage: number
  name: string
  defendant?: string
  crime_type?: string
  evidence_count?: number
  generated_at?: string
  [key: string]: unknown
}

export interface ApiResponse<T = unknown> {
  success: boolean
  data?: T
  error?: string
  message?: string
}
