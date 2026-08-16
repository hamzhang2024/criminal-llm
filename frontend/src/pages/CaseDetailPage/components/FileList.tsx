// 文件列表组件

import React from 'react'
import { RefreshCw, CheckSquare, Square, FileText, CheckCircle, Loader2, AlertCircle, XCircle, Trash2 } from 'lucide-react'
import { MacOSCard, MacOSButton } from '../../../components/MacOSLayout'
import type { CaseFile } from '../hooks/useCaseFiles'
import type { MdIssue } from '../../../api/cases'

interface FileListProps {
  files: CaseFile[]
  currentStep: number
  uploading: boolean
  toggleSelect: (id: string) => void
  toggleSelectAll: () => void
  getSelectedFiles: () => CaseFile[]
  refreshFiles: () => Promise<void>
  onRemoveFile: (file: CaseFile) => Promise<void>
  onDeleteMd: (name: string) => Promise<void>
  onDeletePdf: (name: string) => Promise<void>
  onReconvertMd: (name: string) => Promise<void>
  onOpenFile: (file: CaseFile) => void
  onUploadClick: () => void
  // md 识别异常列表（倒置/异常扫描页），已转换文件命中时显示 ⚠️
  mdIssues: MdIssue[]
  // 步骤 1 且存在未转换 PDF 时，在列表上方提示先做页面方向检查
  showRotationHint: boolean
}

export function FileList({
  files, currentStep, uploading,
  toggleSelect, toggleSelectAll, getSelectedFiles,
  refreshFiles,
  onRemoveFile, onDeleteMd, onDeletePdf, onReconvertMd, onOpenFile,
  onUploadClick,
  mdIssues, showRotationHint,
}: FileListProps) {
  const doneCount = files.filter(f => f.status === 'done').length
  const allDone = doneCount === files.length && files.length > 0

  if (files.length === 0) return null

  return (
    <MacOSCard>
      {/* 标题栏 */}
      <div className="flex-between" style={{ marginBottom: '12px' }}>
        <div className="flex-row gap-md">
          {currentStep === 0 && (
            <button onClick={toggleSelectAll} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px', display: 'flex', alignItems: 'center' }}>
              {files.every(f => f.selected) ? (
                <CheckSquare className="w-4 h-4" color="var(--macos-accent)" />
              ) : (
                <Square className="w-4 h-4" color="#86868b" />
              )}
            </button>
          )}
          <h4 style={{ fontSize: '14px', fontWeight: '600', margin: 0 }}>
            {currentStep === 0 ? '原始文件' :
             currentStep === 1 ? (allDone ? `已转换 MD` : doneCount > 0 ? `已转换 ${doneCount}/${files.length}` : `待转换 PDF`) :
             '文件列表'}
          </h4>
        </div>
        <div className="flex-row gap-sm">
          <span className="text-xs text-secondary">
            {currentStep === 0 ? `已选 ${getSelectedFiles().length}/${files.length}` :
             currentStep === 1 ? `共 ${files.length} 个文件` :
             currentStep === 2 ? `${files.length} 个文件` : ''}
          </span>
          <button onClick={refreshFiles} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }} title="刷新">
            <RefreshCw className="w-4 h-4" color="#86868b" />
          </button>
          {currentStep === 0 && (
            <MacOSButton variant="primary" icon={uploading ? Loader2 : undefined} disabled={uploading} onClick={onUploadClick}>
              {uploading ? '上传中...' : '添加文件'}
            </MacOSButton>
          )}
        </div>
      </div>

      {/* 转换前提示：步骤 1 且仍有未转换 PDF 时，建议先检查页面方向 */}
      {showRotationHint && (
        <div style={{ background: '#e8f0fe', color: '#1a56db', borderRadius: 8, padding: '8px 14px', margin: '8px 0', fontSize: 13 }}>
          提示：转换前建议先「预览」→「页面管理」浏览缩略图，确认无倒置页面（倒置页会导致识别乱码）
        </div>
      )}

      {/* 文件行 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {files.map(file => {
          const inputFile = currentStep === 0 ? file :
            currentStep === 1 ? { ...file, name: file.processedPath || file.name } : file
          // 该文件已转换且其 md 命中识别异常列表时，文件名旁显示 ⚠️
          // 注意步骤 1 行显示名可能是 processedPath，需用替换后的实际文件名推导 md 名
          const hasMdIssue = currentStep === 1 && file.status === 'done' &&
            mdIssues.some(i => i.md_file === inputFile.name.replace(/\.pdf$/i, '.md'))

          return (
            <div key={file.id} style={{
              display: 'flex', alignItems: 'center', gap: '12px',
              padding: '12px',
              background: file.status === 'done' ? 'rgba(59, 89, 152, 0.04)' :
                         file.status === 'processing' ? 'var(--macos-accent-surface)' :
                         file.status === 'error' ? 'rgba(102, 102, 102, 0.04)' : 'var(--macos-bg-secondary)',
              borderRadius: '8px',
              border: file.selected ? '1px solid var(--macos-accent-border)' : '1px solid transparent'
            }}>
              {/* 步骤 1：状态图标（无复选框） */}
              {currentStep === 1 ? (
                <StatusIcon status={file.status} />
              ) : (
                <button
                  onClick={() => toggleSelect(file.id)}
                  disabled={file.status !== 'pending'}
                  style={{ background: 'none', border: 'none', cursor: file.status === 'pending' ? 'pointer' : 'default', padding: '2px', opacity: file.status !== 'pending' ? 0.3 : 1 }}
                >
                  {file.selected ? (
                    <CheckSquare className="w-4 h-4" color="var(--macos-accent)" />
                  ) : (
                    <Square className="w-4 h-4" color="#86868b" />
                  )}
                </button>
              )}

              {/* 步骤 0 状态图标 */}
              {currentStep === 0 && <StatusIcon status={file.status} />}

              {/* 文件名 */}
              <div style={{ flex: 1, overflow: 'hidden' }}>
                <div className="truncate text-sm font-medium">
                  {inputFile.name}
                  {hasMdIssue && (
                    <span title="检测到识别异常页，请在「预览 → 页面管理」中处理" style={{ color: '#b7791f' }}> ⚠️</span>
                  )}
                </div>
                <div className="text-xs text-secondary">
                  {currentStep === 0 ? `${(file.size / 1024).toFixed(1)} KB` :
                   currentStep === 1 ? (file.status === 'done' ? '已转换 MD' : file.status === 'processing' ? '转换中...' : '待转换') :
                   'MD 格式'}
                  {file.error && ` - ${file.error}`}
                </div>
              </div>

              {/* 步骤 1 状态标签 */}
              {currentStep === 1 && file.status === 'done' && (
                <span className="text-sm font-medium" style={{ padding: '4px 10px', borderRadius: '12px', background: 'rgba(59,89,152,0.1)', color: '#3b5998', whiteSpace: 'nowrap' }}>已转换</span>
              )}
              {currentStep === 1 && file.status === 'processing' && (
                <span style={{ padding: '8px 12px', background: 'rgba(255,149,0,0.1)', color: '#ff9500', fontSize: '12px', fontWeight: '500', whiteSpace: 'nowrap' }}>⏳ 转换中...</span>
              )}

              {/* 操作按钮 */}
              {currentStep === 1 && file.status === 'done' && (
                <>
                  <IconBtn onClick={() => onDeleteMd(file.name)} title="删除此 MD 文件"><Trash2 className="w-4 h-4" color="#86868b" /></IconBtn>
                  <IconBtn onClick={() => onReconvertMd(file.name)} title="重新转换"><RefreshCw className="w-4 h-4" color="#86868b" /></IconBtn>
                </>
              )}
              {currentStep === 1 && file.status !== 'processing' && file.status !== 'done' && (
                <IconBtn onClick={() => onDeletePdf(file.name)} title="删除此 PDF"><Trash2 className="w-4 h-4" color="#86868b" /></IconBtn>
              )}
              {file.status !== 'processing' && currentStep === 0 && (
                <IconBtn onClick={() => onRemoveFile(file)} title="删除"><Trash2 className="w-4 h-4" color="#86868b" /></IconBtn>
              )}
              {file.status !== 'processing' && currentStep >= 2 && file.name.endsWith('.md') && (
                <>
                  <IconBtn onClick={() => onReconvertMd(file.name)} title="重新转换"><RefreshCw className="w-4 h-4" color="#86868b" /></IconBtn>
                  <IconBtn onClick={() => onDeleteMd(file.name)} title="删除 MD"><Trash2 className="w-4 h-4" color="#86868b" /></IconBtn>
                </>
              )}

              {/* 预览 */}
              {file.status !== 'processing' && (
                <button onClick={() => onOpenFile(file)} style={{
                  padding: '6px 12px', background: 'var(--macos-accent-light)',
                  color: 'var(--macos-accent)', border: 'none', borderRadius: '6px',
                  cursor: 'pointer', fontSize: '12px', fontWeight: '500'
                }}>预览</button>
              )}
            </div>
          )
        })}
      </div>
    </MacOSCard>
  )
}

function StatusIcon({ status }: { status: string }) {
  const iconStyle: React.CSSProperties = {
    width: '32px', height: '32px', borderRadius: '8px',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    flexShrink: 0
  }
  if (status === 'done') return <div style={{ ...iconStyle, background: 'rgba(59,89,152,0.1)' }}><CheckCircle className="w-4 h-4" color="#3b5998" /></div>
  if (status === 'processing') return <div style={{ ...iconStyle, background: 'var(--macos-accent-light)' }}><Loader2 className="w-4 h-4 animate-spin" color="var(--macos-accent)" /></div>
  if (status === 'error') return <div style={{ ...iconStyle, background: 'rgba(102,102,102,0.1)' }}><XCircle className="w-4 h-4" color="#666666" /></div>
  return <div style={{ ...iconStyle, background: 'var(--macos-accent-light)' }}><FileText className="w-4 h-4" color="#86868b" /></div>
}

function IconBtn({ onClick, title, children }: { onClick: () => void; title?: string; children: React.ReactNode }) {
  return (
    <button onClick={onClick} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px', display: 'flex', alignItems: 'center' }} title={title}>
      {children}
    </button>
  )
}