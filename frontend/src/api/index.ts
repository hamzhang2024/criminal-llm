// API 服务层 — barrel 导出，保持与旧 index.ts 完全兼容
// 所有命名函数和 api 对象保持不变，调用方无需修改

export { waitForBackend, safeFetch, API_BASE, isTauri, tauriInvoke, openUrl, timeoutSignal } from './client'
export { thumbnailUrl, serveFileUrl, thumbCacheUrl } from './cases'
export type { Thumbnail, SplitItem } from './cases'

// 认证
export { login, verifyToken, register, resetPassword, sendResetCode, resetWithCode, getToken, setToken, clearToken, getAuthEmail, setAuthEmail, clearAuthEmail } from './auth'
export type { LoginResponse, VerifyResponse } from './auth'

// 版本与更新
export { getAppVersion, checkUpdate } from './config'
export type { UpdateInfo } from './config'

// 案件管理
export { listCases, getPendingCases, getTrash, getCaseInfo, getCaseFiles, getStepFiles, createCase, importCase, updateCaseCharges, updateCaseSearchKeywords, deleteCase, restoreCase, permanentDeleteCase, claimCases, uploadFiles, deleteFile, deleteOriginalFileOnly, batchProcess, convertToMd, deleteMdFile, deletePdfFile, openFile, getLlmSegmentNames, getThumbnails, cleanupProcessed } from './cases'

// 案卷分析（旧 analyze-case API）
export { createAnalysis, analyzeCase, getAnalysisProgress, chatAboutCase, getReport, selectEvidence } from './cases'

// 证据提取
export { extractEvidence, stopExtractEvidence, getEvidenceIndex, getExtractStatus, getEvidenceSummary, getMdFiles, getProcessedPdfs, getEvidenceCompleteness } from './evidence'
export type { EvidenceIndexFile, EvidenceIndexResponse, CompletenessEntry, CompletenessReport } from './evidence'

// 分析流水线
export { runPipelineStep, getPipelineStatus, getPipelineProgress, getStepResult, getAnalysisState, resumePipeline, getDefenseStages, getDefenseStageContent, getDefenseStrategy, confirmDefenseStrategy, getWikiIndex, getWikiPage, getMdFile, getPdfText, uploadWikiReference, clearWiki, getEvidenceSummaries, getEvidenceOther, getSummaryContent, getEvidenceFiles, getContradictionFiles, getContradictionContent } from './pipeline'
export type { StrategyDirection, DefenseStrategy, ConfirmStrategyBody } from './pipeline'

// 5 阶段分析引擎
export { getIndictmentCandidates, runAllStages, getStageStatus, getStageProgress, runSingleStage, getStageResult, getStageMarkdown, getFullReport, saveStageMarkdown, saveFullReport, reviewEvidence, getEvidenceReview, generateReviewNotes, getReviewNotes, generateCrossExamination, getCrossExamination, getEvidenceChain, getPersonRelation, getEventTimeline, searchSimilarCases } from './stages'
export type { IndictmentCandidate, EvidenceReviewItem, EvidenceReviewResult, ReviewNotesResult, CrossExaminationResult, EvidenceChainNode, EvidenceChainEdge, EvidenceChainGroup, EvidenceChainData, PersonNode, RelationEdge, RelationGraphData, EventNode, TimelineData, SimilarCase, SimilarCasesData } from './stages'

// 法律知识库
export { listLegalKB, getLegalKBItem, createLegalKBItem, updateLegalKBItem, deleteLegalKBItem, searchLaws } from './legal'

// 原生对话框 & 通知 & 工作流
export { pickFiles, pickFolder, pickMultiple, sendNotification, showNativeConfirm, createWorkflow, updateWorkflowStatus, listWorkflows, getWorkflow, addStep, updateStep, getSteps, addFile, updateFilePaths, getFiles, logOperation, deleteWorkflow } from './native'

// ========== 默认导出 api 对象（兼容 api.getCaseInfo() 调用方式）==========

import { openUrl, waitForBackend, API_BASE, safeFetch } from './client'
import { thumbnailUrl, serveFileUrl, thumbCacheUrl } from './cases'
import { login, verifyToken, register, resetPassword, sendResetCode, resetWithCode, getToken, setToken, clearToken, getAuthEmail, setAuthEmail, clearAuthEmail } from './auth'
import { getAppVersion, checkUpdate } from './config'
import { listCases, getPendingCases, getTrash, getCaseInfo, getCaseFiles, getStepFiles, createCase, importCase, updateCaseCharges, updateCaseSearchKeywords, deleteCase, restoreCase, permanentDeleteCase, claimCases, uploadFiles, deleteFile, deleteOriginalFileOnly, batchProcess, convertToMd, deleteMdFile, deletePdfFile, openFile, getLlmSegmentNames, getThumbnails, cleanupProcessed, createAnalysis, analyzeCase, getAnalysisProgress, chatAboutCase, getReport, selectEvidence } from './cases'
import { extractEvidence, stopExtractEvidence, getEvidenceIndex, getExtractStatus, getEvidenceSummary, getMdFiles, getProcessedPdfs, getEvidenceCompleteness } from './evidence'
import { runPipelineStep, getPipelineStatus, getPipelineProgress, getStepResult, getAnalysisState, resumePipeline, getDefenseStages, getDefenseStageContent, getDefenseStrategy, confirmDefenseStrategy, getWikiIndex, getWikiPage, getMdFile, getPdfText, uploadWikiReference, clearWiki, getEvidenceSummaries, getEvidenceOther, getSummaryContent, getEvidenceFiles, getContradictionFiles, getContradictionContent } from './pipeline'
import { getIndictmentCandidates, runAllStages, getStageStatus, getStageProgress, runSingleStage, getStageResult, getStageMarkdown, getFullReport, saveStageMarkdown, saveFullReport, reviewEvidence, getEvidenceReview, generateReviewNotes, getReviewNotes, generateCrossExamination, getCrossExamination, getEvidenceChain, getPersonRelation, getEventTimeline, searchSimilarCases } from './stages'
import { listLegalKB, getLegalKBItem, createLegalKBItem, updateLegalKBItem, deleteLegalKBItem, searchLaws } from './legal'
import { pickFiles, pickFolder, pickMultiple, sendNotification, showNativeConfirm, createWorkflow, updateWorkflowStatus, listWorkflows, getWorkflow, addStep, updateStep, getSteps, addFile, updateFilePaths, getFiles, logOperation, deleteWorkflow } from './native'

// 获取后端日志
export async function getBackendLog(lines: number = 500): Promise<{ success: boolean; lines?: string[]; total_lines?: number; error?: string }> {
  const res = await fetch(`${API_BASE}/logs/backend?lines=${lines}`)
  return res.json()
}

export const api = {
  // 案件管理
  listCases,
  getPendingCases,
  getTrash,
  getCaseInfo,
  getCaseFiles,
  getStepFiles,
  createCase,
  importCase,
  updateCaseCharges,
  updateCaseSearchKeywords,
  deleteCase,
  restoreCase,
  permanentDeleteCase,
  // 文件处理
  uploadFiles,
  batchProcess,
  convertToMd,
  deleteMdFile,
  deletePdfFile,
  openFile,
  getLlmSegmentNames,
  getThumbnails,
  deleteFile,
  deleteOriginalFileOnly,
  cleanupProcessed,
  // 案卷分析
  createAnalysis,
  analyzeCase,
  getAnalysisProgress,
  chatAboutCase,
  getReport,
  selectEvidence,
  // 分析流水线
  runPipelineStep,
  getPipelineStatus,
  getPipelineProgress,
  getStepResult,
  getAnalysisState,
  resumePipeline,
  // 辩护意见子阶段
  getDefenseStages,
  getDefenseStageContent,
  // 辩护思路确认（步骤 4.75）
  getDefenseStrategy,
  confirmDefenseStrategy,
  // Wiki
  getWikiIndex,
  getWikiPage,
  getMdFile,
  getPdfText,
  uploadWikiReference,
  clearWiki,
  // 证据浏览
  getEvidenceSummaries,
  getEvidenceOther,
  getSummaryContent,
  getEvidenceFiles,
  getEvidenceSummary,
  getProcessedPdfs,
  getMdFiles,
  // 矛盾分析
  getContradictionFiles,
  getContradictionContent,
  // URL 工具
  thumbnailUrl,
  serveFileUrl,
  thumbCacheUrl,
  // 5 阶段分析
  getIndictmentCandidates,
  runAllStages,
  runSingleStage,
  getStageProgress,
  getStageStatus,
  getStageResult,
  getStageMarkdown,
  getFullReport,
  saveStageMarkdown,
  saveFullReport,
  // 证据三性审查
  reviewEvidence,
  getEvidenceReview,
  // 阅卷笔录
  generateReviewNotes,
  getReviewNotes,
  // 质证意见
  generateCrossExamination,
  getCrossExamination,
  // 证据链可视化
  getEvidenceChain,
  getPersonRelation,
  getEventTimeline,
  // 类案检索
  searchSimilarCases,
  // 证据提取
  extractEvidence,
  stopExtractEvidence,
  getEvidenceIndex,
  getExtractStatus,
  getEvidenceCompleteness,
  // 法律知识库
  listLegalKB,
  getLegalKBItem,
  createLegalKBItem,
  updateLegalKBItem,
  deleteLegalKBItem,
  searchLaws,
  // 原生对话框 & 通知
  pickFiles,
  pickFolder,
  pickMultiple,
  sendNotification,
  showConfirm: showNativeConfirm,
  // 工作流持久化
  createWorkflow,
  updateWorkflowStatus,
  listWorkflows,
  getWorkflow,
  addStep,
  updateStep,
  getSteps,
  addFile,
  updateFilePaths,
  getFiles,
  logOperation,
  deleteWorkflow,
  // 日志
  getBackendLog,
}
