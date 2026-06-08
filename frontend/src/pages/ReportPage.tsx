import { useState, useCallback, useRef, useEffect, useReducer } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { FileText, Loader2, Send, Download, Check,
  AlertCircle, Scale, MessageCircle, Bookmark, PanelLeft,
  PanelLeftClose, Trash2, CheckSquare, RefreshCw,
  FileBarChart, GitCompareArrows, Clock, Users, BookOpen, Eye,
  Gavel, Phone, Mail, Building2, StickyNote, Edit3, Swords, Network, Search, ExternalLink, Printer
} from 'lucide-react'
import { api, reviewEvidence, getEvidenceReview, EvidenceReviewItem, EvidenceReviewResult, generateReviewNotes, getReviewNotes, generateCrossExamination, getCrossExamination, getEvidenceChain, EvidenceChainData, getPersonRelation, RelationGraphData, getEventTimeline, TimelineData, searchSimilarCases, SimilarCasesData } from '../api'
import { showAlert } from '../components/MacOSDialog'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

// 配置 marked 使用同步解析
marked.setOptions({ async: false })
import { MermaidRenderer } from '../components/MermaidRenderer'
import { PdfViewer } from '../components/PdfViewer'
import { StickyNoteOverlay } from '../components/StickyNoteOverlay'
import { ReportRenderer } from '../components/report/ReportRenderer'
import { EvidenceChainGraph } from '../components/EvidenceChainGraph'
import { PersonRelationGraph } from '../components/PersonRelationGraph'
import { EventTimelineGraph } from '../components/EventTimelineGraph'

// ====== 设计令牌 ======

const colors = {
  surface: '#ffffff',
  surfaceAlt: '#f8f9fa',
  surfaceElevated: '#ffffff',
  border: '#e5e5ea',
  borderStrong: '#d1d1d6',
  textPrimary: '#1d1d1f',
  textSecondary: '#6e6e73',
  textTertiary: '#86868b',
  accent: '#007AFF',
  accentLight: 'rgba(0,122,255,0.08)',
  accentBorder: 'rgba(0,122,255,0.2)',
  gold: '#b8860b',
  goldBg: 'rgba(184,134,11,0.08)',
  goldBorder: 'rgba(184,134,11,0.25)',
  userBubble: '#007AFF',
  assistantBubble: '#f5f5f7',
  systemBubble: 'rgba(184,134,11,0.08)',
}

// 13 个文件卡标签 — 重新配色为沉稳的律师风格
const TABS = [
  { key: 'stage_1', label: '指控要素', icon: FileBarChart, color: '#007AFF', bgColor: 'rgba(0,122,255,0.08)' },
  { key: 'stage_2', label: '人物关系', icon: Users, color: '#2d6a4f', bgColor: 'rgba(45,106,79,0.08)' },
  { key: 'stage_3', label: '事件拆解', icon: Clock, color: '#9c661b', bgColor: 'rgba(156,102,27,0.08)' },
  { key: 'stage_4', label: '法律法规', icon: BookOpen, color: '#6b2765', bgColor: 'rgba(107,39,101,0.08)' },
  { key: 'stage_51', label: '证据列表', icon: Eye, color: '#1a6b6a', bgColor: 'rgba(26,107,106,0.08)' },
  { key: 'stage_52', label: '矛盾分析', icon: GitCompareArrows, color: '#991b1b', bgColor: 'rgba(153,27,27,0.08)' },
  { key: 'evidence_chain', label: '证据链', icon: Network, color: '#0891b2', bgColor: 'rgba(8,145,178,0.08)' },
  { key: 'stage_6', label: '控辩对抗', icon: Swords, color: '#7c3aed', bgColor: 'rgba(124,58,237,0.08)' },
  { key: 'stage_53', label: '三阶层分析', icon: Scale, color: '#831843', bgColor: 'rgba(131,24,67,0.08)' },
  { key: 'similar_cases', label: '类案参考', icon: Search, color: '#059669', bgColor: 'rgba(5,150,105,0.08)' },
  { key: 'full', label: '完整报告', icon: FileText, color: '#007AFF', bgColor: 'rgba(0,122,255,0.08)' },
  { key: 'review_notes', label: '阅卷笔录', icon: StickyNote, color: '#4a5568', bgColor: 'rgba(74,85,104,0.08)' },
  { key: 'cross_exam', label: '质证意见', icon: Gavel, color: '#b45309', bgColor: 'rgba(180,83,9,0.08)' },
]

// 左侧证据项
interface EvidenceItem {
  id: string
  displayName: string
  mdFile: string
  type: string
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: string
}

const generateId = () => Math.random().toString(36).substr(2, 9)

// ====== 证据类型图标映射 ======
const evidenceIconMap: Record<string, React.ReactNode> = {
  '讯问笔录': <Gavel className="w-3 h-3" />,
  '询问笔录': <Gavel className="w-3 h-3" />,
  '证人证言': <Users className="w-3 h-3" />,
  '鉴定意见': <FileBarChart className="w-3 h-3" />,
  '勘验笔录': <Building2 className="w-3 h-3" />,
  '辨认笔录': <FileText className="w-3 h-3" />,
  '书证': <Mail className="w-3 h-3" />,
  '程序性文书': <FileText className="w-3 h-3" />,
}

function getEvidenceIcon(type: string) {
  return evidenceIconMap[type] || <FileText className="w-3 h-3" />
}

export function ReportPage() {
  const { caseId } = useParams()
  const navigate = useNavigate()
  const chatInputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Core
  const [caseName, setCaseName] = useState('')
  const [defendant, setDefendant] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Stage content cache (keyed by tab key: stage_1, stage_2, etc.)
  const [stageContent, setStageContent] = useState<Record<string, string>>({})
  const stageContentRef = useRef(stageContent)
  useEffect(() => { stageContentRef.current = stageContent }, [stageContent])

  // View mode is no longer needed — keeping for potential future use

  // Active tab
  const [activeTab, setActiveTab] = useState<string>('stage_1')

  // Evidence browsing (left sidebar)
  const [evidenceItems, setEvidenceItems] = useState<EvidenceItem[]>([])
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string>('')
  const [selectedEvidenceContent, setSelectedEvidenceContent] = useState('')

  // Chat
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [chatSelectMode, setChatSelectMode] = useState(false)
  const [selectedChatIds, setSelectedChatIds] = useState<Set<string>>(new Set())

  // PDF 浏览放大
  const [pdfThumbnails, setPdfThumbnails] = useState<string[]>([])
  const [pdfEnlargedPage, setPdfEnlargedPage] = useState<number>(100)
  const [pdfThumbLoading, setPdfThumbLoading] = useState(false)

  // 对话证据列表
  const [chatEvidenceList, setChatEvidenceList] = useState<Array<{name: string; dir: string}>>([])
  const [chatEvidenceFilter, setChatEvidenceFilter] = useState<Set<string>>(new Set())
  const [showChatEvidencePanel, setShowChatEvidencePanel] = useState(false)

  // 右侧面板标签
  const [rightPanelTab, setRightPanelTab] = useState<string>('chat')

  // 报告修改意见输入 & 步骤选择
  const [reportUpdateInput, setReportUpdateInput] = useState('')
  const [updateStepsSelected, setUpdateStepsSelected] = useState<Set<number>>(new Set([1, 2, 3, 4, 6]))

  // 法律知识库管理
  const [legalKBItems, setLegalKBItems] = useState<Array<{id: string; title: string; crime_type: string; size: number}>>([])
  const [legalKBLoading, setLegalKBLoading] = useState(false)
  const [showLegalKBForm, setShowLegalKBForm] = useState(false)
  const [editingKBItem, setEditingKBItem] = useState<{id: string; title: string; content: string} | null>(null)
  const [kbFormTitle, setKbFormTitle] = useState('')
  const [kbFormContent, setKbFormContent] = useState('')
  const [regeneratingLegal, setRegeneratingLegal] = useState(false)

  // 渐进式分析状态
  const [analysisRunning, setAnalysisRunning] = useState(false)
  const [completedStages, setCompletedStages] = useState<Set<string>>(new Set())
  const [nextStep, setNextStep] = useState<number | null | undefined>(undefined)

  // 证据三性审查状态
  const [evidenceReview, setEvidenceReview] = useState<EvidenceReviewResult | null>(null)
  const [evidenceReviewLoading, setEvidenceReviewLoading] = useState(false)

  // 阅卷笔录状态
  const [reviewNotes, setReviewNotes] = useState<string>('')
  const [reviewNotesLoading, setReviewNotesLoading] = useState(false)

  // 质证意见状态
  const [crossExamination, setCrossExamination] = useState<string>('')
  const [crossExaminationLoading, setCrossExaminationLoading] = useState(false)

  // 证据链状态
  const [evidenceChainData, setEvidenceChainData] = useState<EvidenceChainData | null>(null)
  const [evidenceChainLoading, setEvidenceChainLoading] = useState(false)

  // 人物关系图状态
  const [personRelationData, setPersonRelationData] = useState<RelationGraphData | null>(null)
  const [personRelationLoading, setPersonRelationLoading] = useState(false)

  // 事件时间线状态
  const [timelineData, setTimelineData] = useState<TimelineData | null>(null)
  const [timelineLoading, setTimelineLoading] = useState(false)

  // 类案检索状态
  const [similarCasesData, setSimilarCasesData] = useState<SimilarCasesData | null>(null)
  const [similarCasesLoading, setSimilarCasesLoading] = useState(false)

  // 加载分析阶段完成状态 + 主流水线状态
  const loadDefenseStages = useCallback(async () => {
    if (!caseId) return
    try {
      const data = await api.getDefenseStages(caseId)
      if (data && data.stages) {
        const done = new Set<string>()
        // 加载已完成阶段的内容（渐进式报告）
        for (const [key, status] of Object.entries(data.stages)) {
          if (status === 'done') {
            done.add(key)
            // 将 defense stage 内容加载到 stageContent 中（使用 defense_ 前缀避免与旧 stage 冲突）
            const contentKey = `defense_${key}`
            if (!stageContentRef.current[contentKey]) {
              try {
                const stageData = await api.getDefenseStageContent(caseId, key)
                if (stageData?.content) {
                  setStageContent(prev => ({ ...prev, [contentKey]: stageData.content }))
                }
              } catch { /* ignore — 下次轮询重试 */ }
            }
          }
        }
        setCompletedStages(done)
      }
    } catch { /* ignore */ }
    // 加载主流水线状态，判断是否有待完成的步骤
    try {
      const state = await api.getAnalysisState(caseId)
      if (state) {
        setNextStep(state.next_step ?? null)
      }
    } catch { /* ignore */ }
  }, [caseId])

  // 轮询分析状态
  useEffect(() => {
    if (!caseId) return
    loadDefenseStages()
    const interval = setInterval(() => {
      loadDefenseStages().then(() => {
        // 如果所有阶段都完成，停止轮询
        setCompletedStages(prev => {
          if (prev.size >= 5) {
            setAnalysisRunning(false)
          }
          return prev
        })
        setNextStep(prev => {
          if (prev === null) {
            setAnalysisRunning(false)
          }
          return prev
        })
      })
    }, 3000)
    return () => clearInterval(interval)
  }, [caseId, loadDefenseStages])

  // 标签对应的阶段映射
  const tabStageMap: Record<string, string> = {
    stage_1: '1',
    stage_2: '2',
    stage_3: '3',
    stage_4: '4',
    stage_51: '5a',
    stage_52: '5b',
    stage_6: '6',
    stage_53: '5f',
    full: 'final',
  }

  // Panels
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const [leftWidth, setLeftWidth] = useState(280)
  const [rightWidth, setRightWidth] = useState(360)
  const scrollContentRef = useRef<HTMLDivElement>(null)

  // PDF 浏览 - 默认 PDF（有 PDF 时）或 MD（有证据时）
  const [viewModeState, viewModeDispatch] = useReducer(
    (_state: 'md' | 'pdf', action: 'md' | 'pdf') => action,
    'pdf'
  )
  const [processedPdfs, setProcessedPdfs] = useState<string[]>([])
  const [selectedPdf, setSelectedPdf] = useState<string>('')

  // 批注
  interface Annotation {
    id: string
    pageNum?: number
    x: number
    y: number
    text: string
    color: string
    pdfFile?: string
    createdAt: string
  }
  const [annotationMode, setAnnotationMode] = useState(false)
  const [annotations, setAnnotations] = useState<Annotation[]>(() => {
    if (!caseId) return []
    try {
      const saved = localStorage.getItem(`annotations-${caseId}`)
      return saved ? JSON.parse(saved) : []
    } catch { return [] }
  })

  // 编辑模式
  const [editMode, setEditMode] = useState(false)
  const [editContent, setEditContent] = useState('')
  const [saving, setSaving] = useState(false)

  // Load data
  useEffect(() => {
    if (!caseId) return
    loadData()
  }, [caseId])

  // 切换至法律法规 tab 时加载法律知识库
  useEffect(() => {
    if (activeTab === 'stage_4' && caseId) {
      loadLegalKB()
    }
  }, [activeTab, caseId])

  // 切换 tab 时退出编辑模式
  useEffect(() => {
    if (editMode) {
      setEditMode(false)
      setEditContent('')
    }
  }, [activeTab])

  // 加载证据内容
  const loadEvidenceContent = useCallback(async (item: EvidenceItem) => {
    if (!caseId) return
    setPdfThumbnails([])
    setPdfEnlargedPage(100)
    setPdfThumbLoading(false)
    setSelectedEvidenceContent('')
    try {
      const data = await api.getEvidenceSummary(caseId, item.mdFile)
      setSelectedEvidenceContent(data.content || '')
    } catch { /* ignore */ }
  }, [caseId])

  // 选中证据切换时加载内容
  useEffect(() => {
    if (selectedEvidenceId && caseId) {
      const item = evidenceItems.find(i => i.id === selectedEvidenceId)
      if (item) loadEvidenceContent(item)
    }
  }, [selectedEvidenceId, caseId, evidenceItems, loadEvidenceContent])

  const loadData = useCallback(async () => {
    if (!caseId) return
    setLoading(true)
    setError(null)

    try {
      const caseInfo = await api.getCaseInfo(caseId)
      if (caseInfo.id) {
        setCaseName(caseInfo.name || '')
        setDefendant(caseInfo.defendant || '')
      }

      // 尝试从新 5 阶段 API 加载
      const content: Record<string, string> = {}
      let hasAnyStage = false
      for (let s = 1; s <= 5; s++) {
        if (s === 5) {
          for (const sub of [1, 2, 3]) {
            try {
              const md = await api.getStageMarkdown(caseId, sub === 1 ? 51 : sub === 2 ? 52 : 53)
              content[`stage_5${sub}`] = md.content || ''
              hasAnyStage = true
            } catch { /* ignore */ }
          }
        } else {
          try {
            const md = await api.getStageMarkdown(caseId, s)
            content[`stage_${s}`] = md.content || ''
            hasAnyStage = true
          } catch { /* ignore */ }
        }
      }

      // 加载控辩对抗（阶段 6）
      try {
        const md = await api.getStageMarkdown(caseId, 6)
        content['stage_6'] = md.content || ''
      } catch { /* ignore */ }

      try {
        const fullReport = await api.getFullReport(caseId)
        content.full = fullReport.content || ''
      } catch { /* ignore */ }

      if (Object.keys(content).length > 0) {
        setStageContent(content)
        const availableTabs = TABS.filter(t => content[t.key])
        if (availableTabs.length > 0) {
          setActiveTab(availableTabs[0].key)
        }
      }

      if (!hasAnyStage) {
        const results: Record<number, any> = {}
        let lastCompleted = 0
        for (let i = 1; i <= 5; i++) {
          try {
            const r = await api.getStepResult(caseId, i)
            results[i] = r
            lastCompleted = i
          } catch { /* ignore */ }
        }
        if (lastCompleted > 0) {
          hasAnyStage = true
          setActiveTab('full')
        }
      }

      try {
        const evIndex = await api.getEvidenceIndex(caseId)
        if (evIndex && evIndex.total_evidence > 0) {
          const items: EvidenceItem[] = (evIndex.evidence || []).map((ev: any) => {
            const fileName = ev.md_file || `${ev.name}.md`
            const displayName = fileName.replace(/\.md$/, '')
            return {
              id: `evidence__${fileName}`,
              displayName,
              mdFile: fileName,
              type: ev.type || '',
            }
          })
          setEvidenceItems(items)
          // 有证据时默认 MD 模式
          if (viewModeState !== 'md') viewModeDispatch('md')
          setSelectedEvidenceId(items[0].id)
          loadEvidenceContent(items[0])
        }
      } catch { /* ignore */ }

      try {
        const evFiles: Array<{name: string; dir: string; displayName: string; type: string}> = []
        const evIndex = await api.getEvidenceIndex(caseId)
        if (evIndex && evIndex.total_evidence > 0) {
          for (const ev of evIndex.evidence || []) {
            evFiles.push({
              name: ev.md_file || ev.name,
              dir: 'evidence',
              displayName: (ev.md_file || ev.name).replace(/\.md$/, ''),
              type: ev.type || '',
            })
          }
        }
        try {
          const mdData = await api.getMdFiles(caseId)
          if (mdData && mdData.files) {
            for (const f of mdData.files) {
              evFiles.push({
                name: f.name,
                dir: 'md',
                displayName: f.name.replace(/\.md$/, ''),
                type: f.type || '',
              })
            }
          }
        } catch { /* md/ 目录可能不存在或无数据 */ }

        setChatEvidenceList(evFiles)
        const initialFilter = new Set<string>()
        for (const f of evFiles) {
          initialFilter.add(f.name)
        }
        setChatEvidenceFilter(initialFilter)
      } catch { /* ignore */ }

      // 加载 processed/ 目录下的 PDF 列表
      try {
        const pdfData = await api.getProcessedPdfs(caseId)
        console.log('[loadData] getProcessedPdfs 返回:', pdfData)
        if (pdfData && pdfData.files && pdfData.files.length > 0) {
          const pdfNames = pdfData.files.map((f: any) => f.name)
          setProcessedPdfs(pdfNames)
          setSelectedPdf(pdfNames[0])
          console.log('[loadData] 已设置 processedPdfs:', pdfNames.length, '个文件')
        } else {
          console.log('[loadData] PDF 列表为空或格式不正确')
        }
      } catch (e) {
        console.error('[loadData] getProcessedPdfs 失败:', e)
      }

      const savedChat = localStorage.getItem(`report-chat-${caseId}`)
      if (savedChat) {
        try { setChatMessages(JSON.parse(savedChat)) } catch { /* ignore */ }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [caseId])

  // 加载证据三性审查结果
  const loadEvidenceReview = useCallback(async () => {
    if (!caseId) return
    try {
      const result = await getEvidenceReview(caseId)
      if (result && result.reviews && result.reviews.length > 0) {
        setEvidenceReview(result)
      }
    } catch { /* ignore */ }
  }, [caseId])

  // 触发三性审查
  const handleRunEvidenceReview = useCallback(async () => {
    if (!caseId || evidenceReviewLoading) return
    setEvidenceReviewLoading(true)
    try {
      const result = await reviewEvidence(caseId)
      setEvidenceReview(result)
      if (result.error) {
        showAlert({
          title: '审查失败',
          message: result.error,
          variant: 'warning',
        })
      }
    } catch (e) {
      console.error('三性审查失败:', e)
      showAlert({
        title: '审查失败',
        message: `证据三性审查失败：${e instanceof Error ? e.message : '未知错误'}`,
        variant: 'danger',
      })
    } finally {
      setEvidenceReviewLoading(false)
    }
  }, [caseId, evidenceReviewLoading])

  // 切换到证据列表 tab 时加载三性审查结果
  useEffect(() => {
    if (activeTab === 'stage_51' && caseId && !evidenceReview) {
      loadEvidenceReview()
    }
  }, [activeTab, caseId, evidenceReview, loadEvidenceReview])

  // 加载阅卷笔录
  const loadReviewNotes = useCallback(async () => {
    if (!caseId) return
    try {
      const result = await getReviewNotes(caseId)
      if (result.content) {
        setReviewNotes(result.content)
      }
    } catch { /* ignore */ }
  }, [caseId])

  // 生成阅卷笔录
  const handleGenerateReviewNotes = useCallback(async () => {
    if (!caseId || reviewNotesLoading) return
    setReviewNotesLoading(true)
    try {
      const result = await generateReviewNotes(caseId)
      setReviewNotes(result.content)
    } catch (e) {
      console.error('阅卷笔录生成失败:', e)
      showAlert({
        title: '生成失败',
        message: `阅卷笔录生成失败：${e instanceof Error ? e.message : '未知错误'}`,
        variant: 'danger',
      })
    } finally {
      setReviewNotesLoading(false)
    }
  }, [caseId, reviewNotesLoading])

  // 切换到阅卷笔录 tab 时加载
  useEffect(() => {
    if (activeTab === 'review_notes' && caseId && !reviewNotes) {
      loadReviewNotes()
    }
  }, [activeTab, caseId, reviewNotes, loadReviewNotes])

  // 加载质证意见
  const loadCrossExamination = useCallback(async () => {
    if (!caseId) return
    try {
      const result = await getCrossExamination(caseId)
      if (result.content) {
        setCrossExamination(result.content)
      }
    } catch { /* ignore */ }
  }, [caseId])

  // 生成质证意见
  const handleGenerateCrossExamination = useCallback(async () => {
    if (!caseId || crossExaminationLoading) return
    setCrossExaminationLoading(true)
    try {
      const result = await generateCrossExamination(caseId)
      setCrossExamination(result.content)
      if (result.error) {
        showAlert({
          title: '提示',
          message: result.error,
          variant: 'warning',
        })
      }
    } catch (e) {
      console.error('质证意见生成失败:', e)
      showAlert({
        title: '生成失败',
        message: `质证意见生成失败：${e instanceof Error ? e.message : '未知错误'}`,
        variant: 'danger',
      })
    } finally {
      setCrossExaminationLoading(false)
    }
  }, [caseId, crossExaminationLoading])

  // 切换到质证意见 tab 时加载
  useEffect(() => {
    if (activeTab === 'cross_exam' && caseId && !crossExamination) {
      loadCrossExamination()
    }
  }, [activeTab, caseId, crossExamination, loadCrossExamination])

  // 加载证据链数据
  const loadEvidenceChain = useCallback(async () => {
    if (!caseId) return
    setEvidenceChainLoading(true)
    try {
      const data = await getEvidenceChain(caseId)
      setEvidenceChainData(data)
    } catch (e) {
      console.error('证据链加载失败:', e)
    } finally {
      setEvidenceChainLoading(false)
    }
  }, [caseId])

  // 切换到证据链 tab 时加载
  useEffect(() => {
    if (activeTab === 'evidence_chain' && caseId && !evidenceChainData) {
      loadEvidenceChain()
    }
  }, [activeTab, caseId, evidenceChainData, loadEvidenceChain])

  // 加载人物关系图数据
  const loadPersonRelation = useCallback(async () => {
    if (!caseId) return
    setPersonRelationLoading(true)
    try {
      console.log('[DEBUG] getPersonRelation 请求:', caseId)
      const data = await getPersonRelation(caseId)
      console.log('[DEBUG] getPersonRelation 响应:', JSON.stringify(data).slice(0, 300))
      setPersonRelationData(data)
    } catch (e) {
      console.error('人物关系图加载失败:', e)
    } finally {
      setPersonRelationLoading(false)
    }
  }, [caseId])

  // 切换到人物关系 tab 时加载
  useEffect(() => {
    console.log('[DEBUG] useEffect 人物关系:', { activeTab, caseId, hasData: !!personRelationData, loading: personRelationLoading })
    if (activeTab === 'stage_2' && caseId && !personRelationData) {
      console.log('[DEBUG] 开始加载人物关系, caseId:', caseId)
      loadPersonRelation()
    }
  }, [activeTab, caseId, personRelationData, loadPersonRelation])

  // 加载事件时间线数据
  const loadTimeline = useCallback(async () => {
    if (!caseId) return
    setTimelineLoading(true)
    try {
      const data = await getEventTimeline(caseId)
      setTimelineData(data)
    } catch (e) {
      console.error('事件时间线加载失败:', e)
    } finally {
      setTimelineLoading(false)
    }
  }, [caseId])

  // 切换到事件拆解 tab 时加载
  useEffect(() => {
    if (activeTab === 'stage_3' && caseId && !timelineData) {
      loadTimeline()
    }
  }, [activeTab, caseId, timelineData, loadTimeline])

  // 类案检索
  const handleSearchSimilarCases = useCallback(async () => {
    if (!caseId || similarCasesLoading) return
    setSimilarCasesLoading(true)
    try {
      const data = await searchSimilarCases(caseId)
      setSimilarCasesData(data)
      if (data.error) {
        showAlert({ title: '提示', message: data.error, variant: 'warning' })
      }
    } catch (e) {
      console.error('类案检索失败:', e)
      showAlert({ title: '检索失败', message: `类案检索失败：${e instanceof Error ? e.message : '未知错误'}`, variant: 'danger' })
    } finally {
      setSimilarCasesLoading(false)
    }
  }, [caseId, similarCasesLoading])

  // Save chat
  useEffect(() => {
    if (caseId && chatMessages.length > 0) {
      localStorage.setItem(`report-chat-${caseId}`, JSON.stringify(chatMessages))
    }
  }, [chatMessages, caseId])

  // Save annotations
  useEffect(() => {
    if (caseId) {
      localStorage.setItem(`annotations-${caseId}`, JSON.stringify(annotations))
    }
  }, [annotations, caseId])

  // 批注操作
  const addAnnotation = useCallback((annotation: Annotation) => {
    setAnnotations(prev => [...prev, annotation])
    setAnnotationMode(false)
  }, [])
  const updateAnnotation = useCallback((id: string, text: string) => {
    setAnnotations(prev => prev.map(a => a.id === id ? { ...a, text } : a))
  }, [])
  const deleteAnnotation = useCallback((id: string) => {
    setAnnotations(prev => prev.filter(a => a.id !== id))
  }, [])
  const updateAnnotationPosition = useCallback((id: string, x: number, y: number) => {
    setAnnotations(prev => prev.map(a => a.id === id ? { ...a, x, y } : a))
  }, [])

  // 编辑模式：进入编辑
  const handleStartEdit = useCallback(() => {
    setEditContent(stageContent[activeTab] || '')
    setEditMode(true)
  }, [stageContent, activeTab])

  // 编辑模式：取消
  const handleCancelEdit = useCallback(() => {
    setEditMode(false)
    setEditContent('')
  }, [])

  // 编辑模式：保存
  const handleSaveEdit = useCallback(async () => {
    if (!caseId || !editContent || editMode === false) return
    setSaving(true)
    try {
      if (activeTab === 'full') {
        await api.saveFullReport(caseId, editContent)
      } else {
        const stageNumMap: Record<string, number> = {
          stage_1: 1, stage_2: 2, stage_3: 3, stage_4: 4,
          stage_51: 51, stage_52: 52, stage_53: 53,
        }
        const stageNum = stageNumMap[activeTab]
        if (stageNum) {
          await api.saveStageMarkdown(caseId, stageNum, editContent)
        }
      }
      setStageContent(prev => ({ ...prev, [activeTab]: editContent }))
      setEditMode(false)
      setEditContent('')
    } catch {
      // 保存失败，保持编辑模式
    } finally {
      setSaving(false)
    }
  }, [caseId, editContent, activeTab, editMode])

  // 分类批注
  const pdfAnnotations = annotations.filter(a => a.pdfFile === selectedPdf)
  const viewportAnnotations = annotations.filter(a => !a.pdfFile)

  // Esc 退出批注模式 / 取消便签编辑
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (annotationMode) setAnnotationMode(false)
      }
    }
    window.addEventListener('keydown', handleEsc)
    return () => window.removeEventListener('keydown', handleEsc)
  }, [annotationMode])

  const scrollToBottom = useCallback(() => {
    setTimeout(() => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 100)
  }, [])

  // Resize
  const leftPanelRef = useRef<HTMLDivElement>(null)
  const rightPanelRef = useRef<HTMLDivElement>(null)
  const resizingRef = useRef<{ side: 'left' | 'right'; startX: number; startWidth: number } | null>(null)

  const startResizingLeft = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    resizingRef.current = { side: 'left', startX: e.clientX, startWidth: leftWidth }
  }, [leftWidth])

  const startResizingRight = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    resizingRef.current = { side: 'right', startX: e.clientX, startWidth: rightWidth }
  }, [rightWidth])

  const stopResizing = useCallback(() => {
    resizingRef.current = null
  }, [])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!resizingRef.current) return
      const { side, startX, startWidth } = resizingRef.current
      const delta = side === 'left' ? e.clientX - startX : startX - e.clientX
      const minW = side === 'left' ? 200 : 260
      const maxW = side === 'left' ? 600 : 500
      const newWidth = Math.min(Math.max(startWidth + delta, minW), maxW)
      if (side === 'left') {
        setLeftWidth(newWidth)
        leftPanelRef.current && (leftPanelRef.current.style.width = `${newWidth}px`)
      } else {
        setRightWidth(newWidth)
        rightPanelRef.current && (rightPanelRef.current.style.width = `${newWidth}px`)
      }
    }
    const handleMouseUp = () => { resizingRef.current = null }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => { document.removeEventListener('mousemove', handleMouseMove); document.removeEventListener('mouseup', handleMouseUp) }
  }, [])

  // Export report
  const handleExportReport = useCallback(() => {
    const report = stageContent.full || stageContent.stage_53
    if (!report) {
      // 没有报告内容时提示用户
      showAlert({
        title: '无法导出',
        message: '报告内容尚未生成，请先完成分析后再导出。',
        variant: 'warning',
      })
      return
    }
    const blob = new Blob([report], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `辩护分析报告_${defendant || '案卷'}_${new Date().toISOString().slice(0, 10)}.md`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }, [stageContent, defendant])

  // Update report from chat
  const handleUpdateReport = useCallback(async () => {
    const instruction = reportUpdateInput.trim()
    const report = stageContent.stage_53
    if (!instruction || !report || !caseId) return
    setChatLoading(true)

    try {
      const userMsg: ChatMessage = { id: generateId(), role: 'user', content: `请结合以下分析结果修改三阶层分析报告：\n${instruction}`, timestamp: new Date().toISOString() }
      setChatMessages(prev => [...prev, userMsg])

      const contextParts: string[] = []
      // 1=指控要素, 2=人物关系, 3=事件拆解, 4=法律法规, 6=矛盾分析
      for (const tabKey of ['stage_1', 'stage_2', 'stage_3', 'stage_4', 'stage_52']) {
        if (stageContent[tabKey]) {
          const tabDef = TABS.find(t => t.key === tabKey)
          contextParts.push(`## ${tabDef?.label || tabKey}分析结果\n${stageContent[tabKey].substring(0, 5000)}`)
        }
      }
      const updateMsg = `请结合全部已有分析结果，按以下意见修改三阶层分析报告：\n${instruction}`

      const response = await fetch('http://localhost:8080/api/analyze-case/chat/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          current_report: report,
          update_instruction: updateMsg,
          context: contextParts.join('\n\n'),
        }),
      })
      if (!response.ok) {
        const errText = await response.text()
        let errMsg = '更新失败'
        try {
          const errData = JSON.parse(errText)
          errMsg = errData.detail || errData.error || errMsg
        } catch {
          errMsg = errText || errMsg
        }
        throw new Error(errMsg)
      }
      const data = await response.json()
      if (data.updated_report) {
        setStageContent(prev => ({ ...prev, stage_53: data.updated_report }))
      }
      setChatMessages(prev => [...prev, { id: generateId(), role: 'assistant', content: `报告已更新。\n\n修改说明：${data.summary || '已按要求更新相关内容'}`, timestamp: new Date().toISOString() }])
      scrollToBottom()
    } catch (err) {
      setChatMessages(prev => [...prev, { id: generateId(), role: 'assistant', content: `更新失败：${err instanceof Error ? err.message : '未知错误'}`, timestamp: new Date().toISOString() }])
    } finally {
      setChatLoading(false)
    }
    setReportUpdateInput('')
  }, [reportUpdateInput, caseId, stageContent, scrollToBottom])

  // Send chat
  const handleSendChat = useCallback(async () => {
    if (!chatInput.trim() || !caseId || chatLoading) return
    const userMsg: ChatMessage = { id: generateId(), role: 'user', content: chatInput, timestamp: new Date().toISOString() }
    const thinkingId = generateId()
    setChatMessages(prev => [...prev, userMsg, { id: thinkingId, role: 'system', content: '思考中...', timestamp: new Date().toISOString() }])
    setChatInput('')
    setChatLoading(true)

    try {
      const report = stageContent.full || stageContent.stage_53 || ''
      const selectedFiles = chatEvidenceList.filter(f => chatEvidenceFilter.has(f.name))
      const evidenceParts: string[] = []
      for (const file of selectedFiles.slice(0, 20)) {
        try {
          if (file.dir === 'evidence') {
            const data = await api.getEvidenceSummary(caseId, file.name)
            evidenceParts.push(`## ${file.name}\n${data.content || ''}`)
          } else if (file.dir === 'md') {
            const data = await api.getMdFile(caseId, file.name)
            evidenceParts.push(`## ${file.name}\n${data.content || ''}`)
          } else if (file.dir === 'processed') {
            const pdfData = await api.getPdfText(caseId, file.name)
            if (pdfData.content) {
              evidenceParts.push(`## ${file.name}\n${pdfData.content}`)
            } else {
              evidenceParts.push(`## ${file.name}（PDF 文件，文本提取为空）`)
            }
          }
        } catch { /* ignore */ }
      }
      const selectedDropdown = evidenceItems.find(i => i.id === selectedEvidenceId)
      if (selectedDropdown?.mdFile) {
        try {
          const data = await api.getEvidenceSummary(caseId, selectedDropdown.mdFile)
          evidenceParts.push(`## ${selectedDropdown.displayName}\n${data.content || ''}`)
        } catch { /* ignore */ }
      }
      const evidenceContext = evidenceParts.join('\n\n').substring(0, 30000)

      const response = await fetch('http://localhost:8080/api/analyze-case/chat/report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMsg.content,
          report_context: report.substring(0, 10000),
          evidence_context: evidenceContext,
          history: chatMessages.slice(-10).map(m => ({ role: m.role, content: m.content })),
        }),
      })
      if (!response.ok) {
        const errText = await response.text()
        let errMsg = '对话失败'
        try {
          const errData = JSON.parse(errText)
          errMsg = errData.detail || errData.error || errMsg
        } catch {
          errMsg = errText || errMsg
        }
        throw new Error(errMsg)
      }
      const data = await response.json()
      setChatMessages(prev => prev
        .filter(m => m.id !== thinkingId)
        .concat({ id: generateId(), role: 'assistant', content: data.answer || '抱歉，未能获取回复。', timestamp: new Date().toISOString() }))
      scrollToBottom()
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '未知错误'
      setChatMessages(prev => prev
        .filter(m => m.id !== thinkingId)
        .concat({ id: generateId(), role: 'assistant', content: `对话失败：${errorMsg}`, timestamp: new Date().toISOString() }))
    } finally {
      setChatLoading(false)
    }
  }, [chatInput, caseId, chatLoading, chatMessages, chatEvidenceList, chatEvidenceFilter, evidenceItems, selectedEvidenceId, stageContent, scrollToBottom])

  // Export chat
  const handleExportChat = useCallback(() => {
    if (chatMessages.length === 0) return
    const msgs = selectedChatIds.size > 0 ? chatMessages.filter(m => selectedChatIds.has(m.id)) : chatMessages
    let content = `# ${defendant || '案卷'} - 案卷分析对话记录\n导出时间：${new Date().toLocaleString('zh-CN')}\n\n---\n\n`
    msgs.forEach(msg => {
      const label = msg.role === 'user' ? '用户' : msg.role === 'system' ? '系统' : 'AI助手'
      const time = new Date(msg.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
      content += `**${label}** (${time}):\n\n${msg.content}\n\n---\n\n`
    })
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${defendant || '案卷'}-对话记录-${new Date().toISOString().slice(0, 10)}.md`
    document.body.appendChild(a); a.click(); document.body.removeChild(a)
    URL.revokeObjectURL(url)
    if (selectedChatIds.size > 0) { setSelectedChatIds(new Set()); setChatSelectMode(false) }
  }, [chatMessages, selectedChatIds, defendant])

  const handleDeleteChat = useCallback(() => {
    if (selectedChatIds.size === 0) return
    setChatMessages(prev => prev.filter(m => !selectedChatIds.has(m.id)))
    setSelectedChatIds(new Set()); setChatSelectMode(false)
  }, [selectedChatIds])

  const toggleChatSelect = useCallback((id: string) => {
    setSelectedChatIds(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }, [])
  const toggleAllChatSelect = useCallback(() => {
    setSelectedChatIds(prev => prev.size === chatMessages.length ? new Set() : new Set(chatMessages.map(m => m.id)))
  }, [chatMessages])

  const toggleChatEvidence = useCallback((name: string) => {
    setChatEvidenceFilter(prev => { const n = new Set(prev); n.has(name) ? n.delete(name) : n.add(name); return n })
  }, [])
  const toggleAllChatEvidence = useCallback(() => {
    setChatEvidenceFilter(prev => prev.size === chatEvidenceList.length ? new Set() : new Set(chatEvidenceList.map(f => f.name)))
  }, [chatEvidenceList])

  // ===== 法律知识库管理 =====

  const loadLegalKB = useCallback(async () => {
    setLegalKBLoading(true)
    try {
      const data = await api.listLegalKB()
      setLegalKBItems(data.items || [])
    } catch { /* ignore */ }
    finally { setLegalKBLoading(false) }
  }, [])

  const resetKBForm = useCallback(() => {
    setKbFormTitle('')
    setKbFormContent('')
    setShowLegalKBForm(false)
    setEditingKBItem(null)
  }, [])

  const handleEditKBItem = useCallback(async (itemId: string) => {
    try {
      const data = await api.getLegalKBItem(itemId)
      if (data.item) {
        setEditingKBItem({ id: itemId, title: data.item.title, content: data.item.content })
        setKbFormTitle(data.item.title)
        setKbFormContent(data.item.content)
        setShowLegalKBForm(true)
      }
    } catch { /* ignore */ }
  }, [])

  const handleDeleteKBItem = useCallback(async (itemId: string) => {
    try {
      await api.deleteLegalKBItem(itemId)
      setLegalKBItems(prev => prev.filter(i => i.id !== itemId))
    } catch { /* ignore */ }
  }, [])

  const handleSaveKB = useCallback(async () => {
    if (!kbFormTitle.trim() || !kbFormContent.trim()) return
    try {
      if (editingKBItem) {
        await api.updateLegalKBItem(editingKBItem.id, {
          title: kbFormTitle,
          content: kbFormContent,
        })
      } else {
        await api.createLegalKBItem(kbFormTitle, kbFormContent, defendant || '')
      }
      resetKBForm()
      loadLegalKB()
    } catch { /* ignore */ }
  }, [kbFormTitle, kbFormContent, editingKBItem, defendant, resetKBForm, loadLegalKB])

  // 重新生成法律法规（阶段 4）
  const handleRegenerateLegalKB = useCallback(async () => {
    if (!caseId || !defendant) return
    setRegeneratingLegal(true)
    try {
      await api.runSingleStage(caseId, 4, defendant, undefined)
      // 重新加载阶段 4 内容
      const result = await api.getStageResult(caseId, 4)
      if (result.success && result.markdown) {
        setStageContent(prev => ({ ...prev, stage_4: result.markdown }))
      }
      loadLegalKB()
    } catch { /* ignore */ }
    finally { setRegeneratingLegal(false) }
  }, [caseId, defendant, loadLegalKB])

  // ===== 渲染器 =====

  const activeTabDef = TABS.find(t => t.key === activeTab)

  // 移除 mermaid 代码块（人物关系图 / 事件时间线的 JSON 格式已用 SVG 渲染）
  const stripMermaid = (md: string) => md.replace(/```mermaid[\s\S]*?```/g, '').trim()

  // 综合结论 tab：优先使用完整报告，其次组合已完成的 defense stages
  const activeContent = (() => {
    // 旧 API 直接内容
    let directContent = stageContent[activeTab] || ''
    if (directContent) {
      // stage_2（人物关系）和 stage_3（事件拆解）移除 mermaid 代码块
      if (activeTab === 'stage_2' || activeTab === 'stage_3') {
        directContent = stripMermaid(directContent)
      }
      return directContent
    }

    // 综合结论 tab（stage_53）：组合已完成的 defense stages
    if (activeTab === 'stage_53') {
      const defenseKeys = [
        'defense_01-案件概述.md',
        'defense_02-证据评估.md',
        'defense_03-矛盾利用.md',
        'defense_04-三阶层辩护.md',
        'defense_05-量刑情节.md',
        'defense_06-结论建议.md',
      ]
      const parts: string[] = []
      for (const key of defenseKeys) {
        const content = stageContent[key]
        if (content) {
          const label = key.replace('defense_', '').replace('.md', '')
          parts.push(`## ${label}\n\n${content}`)
        }
      }
      return parts.join('\n\n---\n\n')
    }

    return ''
  })()

  // 综合结论 tab 的进度信息
  const defenseProgress = (() => {
    if (activeTab !== 'stage_53') return null
    const allDefenseKeys = [
      'defense_01-案件概述.md',
      'defense_02-证据评估.md',
      'defense_03-矛盾利用.md',
      'defense_04-三阶层辩护.md',
      'defense_05-量刑情节.md',
      'defense_06-结论建议.md',
    ]
    const done = allDefenseKeys.filter(k => stageContent[k]).length
    const total = allDefenseKeys.length
    if (done === 0 && !stageContent.stage_53) return null
    if (done === total) return null // 全部完成，不显示进度
    return { done, total, percent: Math.round(done / total * 100) }
  })()

  const renderActiveTab = () => {
    // 人物关系图 tab - 使用 SVG 组件
    if (activeTab === 'stage_2') {
      return (
        <div style={{ padding: '16px' }}>
          {/* SVG 关系图 */}
          <div style={{
            height: '550px',
            background: colors.surface,
            borderRadius: '8px',
            border: `1px solid ${colors.border}`,
            marginBottom: '16px',
            overflow: 'hidden',
          }}>
            {personRelationLoading ? (
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                height: '100%', color: colors.textTertiary,
              }}>
                <Loader2 className="w-6 h-6 animate-spin" style={{ marginRight: '8px' }} />
                加载人物关系图...
              </div>
            ) : personRelationData ? (
              <PersonRelationGraph
                data={personRelationData}
                onNodeClick={(node) => console.log('点击人物:', node)}
              />
            ) : (
              <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                justifyContent: 'center', height: '100%', color: colors.textTertiary, gap: '12px',
              }}>
                <Users className="w-10 h-10" style={{ opacity: 0.3 }} />
                <div>点击"刷新"加载人物关系图</div>
                <button
                  onClick={loadPersonRelation}
                  style={{
                    padding: '6px 16px', fontSize: '12px',
                    background: '#2d6a4f', color: '#fff',
                    border: 'none', borderRadius: '4px', cursor: 'pointer',
                  }}
                >
                  加载关系图
                </button>
              </div>
            )}
          </div>

          {/* 报告内容 */}
          {activeContent && (
            <div style={{
              padding: '16px',
              background: colors.surface,
              borderRadius: '8px',
              border: `1px solid ${colors.border}`,
            }}>
              <h4 style={{ margin: '0 0 12px 0', fontSize: '14px', color: colors.textPrimary }}>人物详情</h4>
              <ReportRenderer
                markdown={activeContent}
                evidenceItems={evidenceItems}
                onEvidenceClick={(mdFile) => {
                  if (viewModeState !== 'md') viewModeDispatch('md')
                  const item = evidenceItems.find(i => i.mdFile === mdFile)
                  if (item) {
                    setSelectedEvidenceId(item.id)
                    loadEvidenceContent(item)
                  }
                }}
              />
            </div>
          )}
        </div>
      )
    }

    // 事件时间线 tab - 使用 SVG 组件
    if (activeTab === 'stage_3') {
      return (
        <div style={{ padding: '16px' }}>
          {/* SVG 时间线 */}
          <div style={{
            height: '450px',
            background: colors.surface,
            borderRadius: '8px',
            border: `1px solid ${colors.border}`,
            marginBottom: '16px',
            overflow: 'hidden',
          }}>
            {timelineLoading ? (
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                height: '100%', color: colors.textTertiary,
              }}>
                <Loader2 className="w-6 h-6 animate-spin" style={{ marginRight: '8px' }} />
                加载事件时间线...
              </div>
            ) : timelineData ? (
              <EventTimelineGraph
                data={timelineData}
                onEventClick={(event) => console.log('点击事件:', event)}
              />
            ) : (
              <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                justifyContent: 'center', height: '100%', color: colors.textTertiary, gap: '12px',
              }}>
                <Clock className="w-10 h-10" style={{ opacity: 0.3 }} />
                <div>点击"刷新"加载事件时间线</div>
                <button
                  onClick={loadTimeline}
                  style={{
                    padding: '6px 16px', fontSize: '12px',
                    background: '#9c661b', color: '#fff',
                    border: 'none', borderRadius: '4px', cursor: 'pointer',
                  }}
                >
                  加载时间线
                </button>
              </div>
            )}
          </div>

          {/* 报告内容 */}
          {activeContent && (
            <div style={{
              padding: '16px',
              background: colors.surface,
              borderRadius: '8px',
              border: `1px solid ${colors.border}`,
            }}>
              <h4 style={{ margin: '0 0 12px 0', fontSize: '14px', color: colors.textPrimary }}>事件详情</h4>
              <ReportRenderer
                markdown={activeContent}
                evidenceItems={evidenceItems}
                onEvidenceClick={(mdFile) => {
                  if (viewModeState !== 'md') viewModeDispatch('md')
                  const item = evidenceItems.find(i => i.mdFile === mdFile)
                  if (item) {
                    setSelectedEvidenceId(item.id)
                    loadEvidenceContent(item)
                  }
                }}
              />
            </div>
          )}
        </div>
      )
    }

    // 综合结论 tab 进度条
    if (defenseProgress) {
      return (
        <div style={{ padding: '24px 20px' }}>
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            marginBottom: '16px',
          }}>
            <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--macos-text-primary)' }}>
              报告生成中...
            </div>
            <div style={{ fontSize: '12px', color: 'var(--macos-text-tertiary)' }}>
              {defenseProgress.done}/{defenseProgress.total} 已完成
            </div>
          </div>
          <div style={{
            width: '100%', height: '6px', background: 'var(--macos-bg-secondary)',
            borderRadius: '3px', overflow: 'hidden', marginBottom: '20px',
          }}>
            <div style={{
              width: `${defenseProgress.percent}%`, height: '100%',
              background: 'linear-gradient(90deg, #3b5998, #5a7bc0)',
              borderRadius: '3px', transition: 'width 0.5s ease',
            }} />
          </div>
          {/* 已完成的子阶段内容 */}
          <ReportRenderer
            markdown={activeContent}
            evidenceItems={evidenceItems}
            onEvidenceClick={(mdFile) => {
              if (viewModeState !== 'md') viewModeDispatch('md')
              const item = evidenceItems.find(i => i.mdFile === mdFile)
              if (item) {
                setSelectedEvidenceId(item.id)
                loadEvidenceContent(item)
              }
            }}
          />
        </div>
      )
    }

    if (!activeContent) {
      const Icon = activeTabDef?.icon || FileText
      return (
        <div style={{ textAlign: 'center', padding: '48px 20px', color: colors.textTertiary }}>
          <Icon className="w-10 h-10" style={{ opacity: 0.2, display: 'block', margin: '0 auto 12px' }} />
          <div style={{ fontSize: '13px', fontWeight: 500 }}>{activeTabDef?.label || '内容'}尚未生成</div>
          <div style={{ fontSize: '12px', marginTop: '4px', color: colors.textTertiary }}>请先运行对应分析阶段</div>
        </div>
      )
    }

    return (
      <>
        <ReportRenderer
          markdown={activeContent}
        evidenceItems={evidenceItems}
        onEvidenceClick={(mdFile) => {
          // 确保左栏处于 MD 模式（而非 PDF 模式）
          if (viewModeState !== 'md') viewModeDispatch('md')
          const item = evidenceItems.find(i => i.mdFile === mdFile)
          if (item) {
            setSelectedEvidenceId(item.id)
            loadEvidenceContent(item)
          }
        }}
      />
      </>
    )
  }

  // ===== Safari 风格顶部标签栏 =====
  const renderSafariTabs = () => {
    // 所有标签都显示，不管是否完成
    const allTabs = TABS

    if (allTabs.length === 0) return null

    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* 顶部标签栏 */}
        <div className="report-page-tabs" style={{
          display: 'flex', alignItems: 'center', gap: '2px',
          padding: '8px 12px 0',
          background: 'transparent',
          flexShrink: 0,
          overflowX: 'auto',
        }}>
          {allTabs.map(tab => {
            const hasContent = !!stageContent[tab.key]
            const isActive = activeTab === tab.key
            const Icon = tab.icon
            return (
              <div
                key={tab.key}
                onClick={() => hasContent && setActiveTab(tab.key)}
                style={{
                  display: 'flex', alignItems: 'center', gap: '6px',
                  padding: '7px 14px',
                  borderRadius: '8px 8px 0 0',
                  cursor: hasContent ? 'pointer' : 'default',
                  transition: 'all 0.15s ease',
                  background: isActive ? colors.surfaceElevated : hasContent ? colors.surfaceAlt : 'rgba(0,0,0,0.03)',
                  border: isActive ? `1.5px solid ${colors.borderStrong}` : '1.5px solid transparent',
                  borderBottom: isActive ? `1.5px solid ${colors.surfaceElevated}` : '1.5px solid transparent',
                  position: 'relative',
                  zIndex: isActive ? 1 : 0,
                  opacity: hasContent ? 1 : 0.5,
                }}
                onMouseEnter={e => {
                  if (!isActive && hasContent) e.currentTarget.style.background = colors.border
                }}
                onMouseLeave={e => {
                  if (!isActive && hasContent) e.currentTarget.style.background = hasContent ? colors.surfaceAlt : 'rgba(0,0,0,0.03)'
                }}
              >
                <Icon className="w-3.5 h-3.5" style={{ color: tab.color, opacity: hasContent ? 1 : 0.4 }} />
                <span style={{ fontSize: '12px', fontWeight: isActive ? 600 : 400, color: hasContent ? (isActive ? colors.textPrimary : colors.textSecondary) : colors.textTertiary, whiteSpace: 'nowrap' }}>
                  {tab.label}
                </span>
              </div>
            )
          })}
        </div>

        {/* 分隔线 */}
        <div style={{
          height: '1.5px', background: colors.borderStrong, flexShrink: 0,
        }} />

        {/* 内容区 */}
        <div ref={scrollContentRef} style={{ flex: 1, overflow: 'auto', background: colors.surfaceElevated, position: 'relative' }}>
          {editMode ? (
            /* 编辑模式：Markdown 编辑器 */
            <div style={{ padding: '24px 28px', height: '100%', display: 'flex', flexDirection: 'column' }}>
              <div style={{
                fontSize: '11px', color: colors.gold, background: colors.goldBg,
                padding: '6px 12px', borderRadius: '6px', marginBottom: '12px',
                border: `1px solid ${colors.goldBorder}`,
              }}>
                编辑模式：修改将直接保存到磁盘
              </div>
              <textarea
                value={editContent}
                onChange={e => setEditContent(e.target.value)}
                style={{
                  flex: 1, padding: '16px', fontSize: '13px', lineHeight: '1.8',
                  fontFamily: '"SF Mono", "Fira Code", "Cascadia Code", monospace',
                  border: `1px solid ${colors.border}`, borderRadius: '8px',
                  background: colors.surface, outline: 'none', resize: 'none',
                  color: colors.textPrimary,
                  boxSizing: 'border-box',
                }}
              />
            </div>
          ) : (
            <div key={activeTab} className="macOS-animate-page-in" style={{ padding: '24px 28px', maxWidth: '900px', margin: '0 auto' }}>
              {activeTabDef ? (
                <>
                  {/* 标题 */}
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: '10px',
                    marginBottom: '20px',
                    paddingBottom: '12px',
                    borderBottom: `1px solid ${colors.border}`,
                  }}>
                    {(() => { const Icon = activeTabDef!.icon; return <Icon className="w-5 h-5" style={{ color: activeTabDef.color }} /> })()}
                    <h2 style={{ fontSize: '16px', fontWeight: 700, color: colors.textPrimary, margin: 0 }}>
                      {activeTabDef.label}
                    </h2>
                  </div>
                  {renderActiveTab()}
                  {renderLegalKBPanel()}
                  {renderEvidenceReviewPanel()}
                  {renderReviewNotesPanel()}
                  {renderCrossExaminationPanel()}
                  {renderEvidenceChainPanel()}
                  {renderSimilarCasesPanel()}
                </>
              ) : null}
            </div>
          )}
          {/* 便签批注覆盖（在滚动容器内，随内容一起滚动） */}
          {!editMode && (
            <StickyNoteOverlay
              annotations={viewportAnnotations}
              onAdd={addAnnotation}
              onUpdate={updateAnnotation}
              onUpdatePosition={updateAnnotationPosition}
              onDelete={deleteAnnotation}
              active={annotationMode}
              contentRef={scrollContentRef}
            />
          )}
        </div>
      </div>
    )
  }

  // ===== 法律知识库面板 =====
  const renderLegalKBPanel = () => {
    if (activeTab !== 'stage_4') return null

    return (
      <div style={{
        borderTop: '1px solid', borderColor: colors.goldBorder,
        marginTop: '28px', paddingTop: '20px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <BookOpen className="w-4 h-4" style={{ color: colors.gold }} />
            <span style={{ fontSize: '13px', fontWeight: 600, color: colors.textPrimary }}>自定义法律法规</span>
            <span style={{ fontSize: '11px', color: colors.textTertiary }}>
              {legalKBItems.length} 条
            </span>
          </div>
          <button onClick={() => { resetKBForm(); setShowLegalKBForm(v => !v) }}
            style={{
              padding: '4px 12px', fontSize: '11px', borderRadius: '6px',
              background: showLegalKBForm ? colors.gold : colors.goldBg,
              color: showLegalKBForm ? '#fff' : colors.gold,
              border: `1px solid ${showLegalKBForm ? colors.gold : colors.goldBorder}`,
              cursor: 'pointer', fontWeight: 500,
            }}>
            {showLegalKBForm ? '取消' : '+ 新增'}
          </button>
        </div>

        {showLegalKBForm && (
          <div style={{
            background: colors.surfaceAlt, borderRadius: '8px', padding: '14px',
            marginBottom: '14px', border: `1px solid ${colors.goldBorder}`,
          }}>
            <input
              value={kbFormTitle} onChange={e => setKbFormTitle(e.target.value)}
              placeholder="法条/司法解释/案例名称"
              style={{
                width: '100%', padding: '8px 12px', fontSize: '12px',
                border: `1px solid ${colors.border}`, borderRadius: '6px',
                marginBottom: '8px', background: colors.surfaceElevated, outline: 'none',
                boxSizing: 'border-box',
              }} />
            <textarea
              value={kbFormContent} onChange={e => setKbFormContent(e.target.value)}
              placeholder="粘贴法条原文、司法解释内容或类案裁判要旨..."
              rows={6}
              style={{
                width: '100%', padding: '8px 12px', fontSize: '12px',
                border: `1px solid ${colors.border}`, borderRadius: '6px',
                background: colors.surfaceElevated, outline: 'none', resize: 'vertical',
                fontFamily: 'inherit', lineHeight: '1.7',
                boxSizing: 'border-box',
              }} />
            <div style={{ display: 'flex', gap: '6px', marginTop: '8px' }}>
              <button onClick={resetKBForm}
                style={{ padding: '5px 14px', fontSize: '11px', background: colors.surfaceElevated, color: colors.textSecondary, border: `1px solid ${colors.borderStrong}`, borderRadius: '6px', cursor: 'pointer', fontWeight: 500 }}>
                取消
              </button>
              <button onClick={handleSaveKB} disabled={!kbFormTitle.trim() || !kbFormContent.trim()}
                style={{
                  padding: '5px 14px', fontSize: '11px', borderRadius: '6px',
                  background: (kbFormTitle.trim() && kbFormContent.trim()) ? colors.gold : colors.surfaceElevated,
                  color: (kbFormTitle.trim() && kbFormContent.trim()) ? '#fff' : colors.textTertiary,
                  border: `1px solid ${colors.goldBorder}`, cursor: (kbFormTitle.trim() && kbFormContent.trim()) ? 'pointer' : 'default',
                  fontWeight: 500,
                }}>
                {editingKBItem ? '保存修改' : '保存'}
              </button>
            </div>
          </div>
        )}

        {legalKBLoading ? (
          <div style={{ textAlign: 'center', padding: '16px 0', fontSize: '12px', color: colors.textTertiary }}>加载中...</div>
        ) : legalKBItems.length === 0 ? (
          <div style={{
            textAlign: 'center', padding: '20px 0', fontSize: '12px', color: colors.textTertiary,
            border: `1px dashed ${colors.borderStrong}`, borderRadius: '8px',
          }}>
            暂无自定义法条，点击"+ 新增"添加
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {legalKBItems.map(item => (
              <div key={item.id} style={{
                background: colors.surfaceElevated, border: `1px solid ${colors.border}`, borderRadius: '8px',
                padding: '10px 14px', display: 'flex', alignItems: 'flex-start', gap: '10px',
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '12px', fontWeight: 600, color: colors.textPrimary, marginBottom: '4px' }}>
                    {item.title}
                  </div>
                  <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                    {item.crime_type && (
                      <span style={{
                        fontSize: '10px', background: colors.goldBg, color: colors.gold,
                        borderRadius: '4px', padding: '1px 6px', border: `1px solid ${colors.goldBorder}`,
                      }}>{item.crime_type}</span>
                    )}
                    <span style={{ fontSize: '10px', color: colors.textTertiary }}>{(item.size / 1024).toFixed(1)} KB</span>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                  <button onClick={() => handleEditKBItem(item.id)}
                    style={{ padding: '2px 8px', fontSize: '10px', background: 'none', color: colors.accent, border: 'none', borderRadius: '4px', cursor: 'pointer', fontWeight: 500 }}>
                    编辑
                  </button>
                  <button onClick={() => handleDeleteKBItem(item.id)}
                    style={{ padding: '2px 8px', fontSize: '10px', background: 'none', color: '#991b1b', border: 'none', borderRadius: '4px', cursor: 'pointer', fontWeight: 500 }}>
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 重新生成按钮 */}
        <div style={{
          borderTop: `1px solid ${colors.goldBorder}`,
          marginTop: '14px', paddingTop: '14px',
        }}>
          <div style={{ fontSize: '11px', color: colors.textTertiary, marginBottom: '8px' }}>
            新增或修改法律法规后，需重新生成以应用更改
          </div>
          <button onClick={handleRegenerateLegalKB} disabled={regeneratingLegal}
            style={{
              width: '100%', padding: '8px 14px', fontSize: '12px', borderRadius: '6px',
              background: regeneratingLegal ? colors.goldBg : colors.gold,
              color: regeneratingLegal ? colors.textTertiary : '#fff',
              border: `1px solid ${colors.goldBorder}`, cursor: regeneratingLegal ? 'default' : 'pointer',
              fontWeight: 500, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px',
            }}>
            {regeneratingLegal ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin" />
                重新生成中...
              </>
            ) : (
              <>
                <RefreshCw className="w-3 h-3" />
                重新生成法律法规
              </>
            )}
          </button>
        </div>
      </div>
    )
  }

  // ===== 证据三性审查面板 =====
  const renderEvidenceReviewPanel = () => {
    if (activeTab !== 'stage_51') return null

    // 三性评分颜色映射
    const getScoreColor = (score: number) => {
      if (score >= 0.8) return '#16a34a' // 绿色
      if (score >= 0.5) return '#ca8a04' // 黄色
      return '#dc2626' // 红色
    }

    const getScoreBg = (score: number) => {
      if (score >= 0.8) return 'rgba(22,163,74,0.1)'
      if (score >= 0.5) return 'rgba(202,138,4,0.1)'
      return 'rgba(220,38,38,0.1)'
    }

    // 评分徽章组件
    const EvidenceBadge = ({ label, score }: { label: string; score: number }) => (
      <span style={{
        fontSize: '10px', padding: '2px 6px', borderRadius: '4px',
        background: getScoreBg(score),
        color: getScoreColor(score),
        border: `1px solid ${getScoreColor(score)}33`,
      }}>
        {label}:{Math.round(score * 100)}%
      </span>
    )

    return (
      <div style={{
        borderTop: '1px solid', borderColor: '#1a6b6a33',
        marginTop: '28px', paddingTop: '20px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Eye className="w-4 h-4" style={{ color: '#1a6b6a' }} />
            <span style={{ fontSize: '13px', fontWeight: 600, color: colors.textPrimary }}>证据三性审查</span>
            {evidenceReview && (
              <span style={{ fontSize: '11px', color: colors.textTertiary }}>
                {evidenceReview.reviews.length} 条已审查
              </span>
            )}
          </div>
          <button onClick={handleRunEvidenceReview} disabled={evidenceReviewLoading}
            style={{
              padding: '4px 12px', fontSize: '11px', borderRadius: '6px',
              background: evidenceReviewLoading ? 'rgba(26,107,106,0.05)' : '#1a6b6a',
              color: evidenceReviewLoading ? colors.textTertiary : '#fff',
              border: '1px solid #1a6b6a33',
              cursor: evidenceReviewLoading ? 'default' : 'pointer', fontWeight: 500,
              display: 'flex', alignItems: 'center', gap: '4px',
            }}>
            {evidenceReviewLoading ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin" />
                审查中...
              </>
            ) : (
              <>
                <RefreshCw className="w-3 h-3" />
                {evidenceReview ? '重新审查' : '开始审查'}
              </>
            )}
          </button>
        </div>

        {!evidenceReview ? (
          <div style={{
            textAlign: 'center', padding: '20px 0', fontSize: '12px', color: colors.textTertiary,
            border: `1px dashed ${colors.borderStrong}`, borderRadius: '8px',
          }}>
            点击"开始审查"对证据进行三性（真实性、合法性、关联性）审查
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {evidenceReview.reviews.map((review, idx) => (
              <div key={review.evidence_id || idx} style={{
                background: colors.surfaceElevated, border: `1px solid ${colors.border}`, borderRadius: '8px',
                padding: '10px 14px',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                  <div style={{ fontSize: '12px', fontWeight: 600, color: colors.textPrimary, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {review.evidence_name}
                  </div>
                  <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                    <EvidenceBadge label="真" score={review.authenticity?.score || 0} />
                    <EvidenceBadge label="法" score={review.legality?.score || 0} />
                    <EvidenceBadge label="关" score={review.relevance?.score || 0} />
                  </div>
                </div>
                {/* 问题提示 */}
                {((review.authenticity?.issues?.length || 0) > 0 ||
                  (review.legality?.issues?.length || 0) > 0 ||
                  (review.relevance?.issues?.length || 0) > 0) && (
                  <div style={{ fontSize: '11px', color: colors.textSecondary, lineHeight: '1.5' }}>
                    {(review.legality?.issues || []).slice(0, 2).map((issue, i) => (
                      <div key={i} style={{ color: '#991b1b' }}>• {issue}</div>
                    ))}
                  </div>
                )}
                {/* 审查结论 */}
                {review.review_summary && (
                  <div style={{ fontSize: '11px', color: colors.textTertiary, marginTop: '4px', fontStyle: 'italic' }}>
                    {review.review_summary}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  // ===== 阅卷笔录面板 =====
  const renderReviewNotesPanel = () => {
    if (activeTab !== 'review_notes') return null

    return (
      <div style={{ marginTop: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
          <div style={{ fontSize: '13px', color: colors.textSecondary }}>
            阅卷笔录是律师阅卷工作的核心文档，组合案件基本信息、证据目录、三性审查、分析结果生成。
          </div>
          <button onClick={handleGenerateReviewNotes} disabled={reviewNotesLoading}
            style={{
              padding: '6px 14px', fontSize: '12px', borderRadius: '6px',
              background: reviewNotesLoading ? 'rgba(74,85,104,0.05)' : '#4a5568',
              color: reviewNotesLoading ? colors.textTertiary : '#fff',
              border: '1px solid rgba(74,85,104,0.3)',
              cursor: reviewNotesLoading ? 'default' : 'pointer', fontWeight: 500,
              display: 'flex', alignItems: 'center', gap: '5px',
            }}>
            {reviewNotesLoading ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin" />
                生成中...
              </>
            ) : (
              <>
                <RefreshCw className="w-3 h-3" />
                {reviewNotes ? '重新生成' : '生成阅卷笔录'}
              </>
            )}
          </button>
        </div>

        {!reviewNotes ? (
          <div style={{
            textAlign: 'center', padding: '40px 0', fontSize: '13px', color: colors.textTertiary,
            border: `1px dashed ${colors.borderStrong}`, borderRadius: '8px',
          }}>
            <StickyNote className="w-8 h-8" style={{ opacity: 0.3, marginBottom: '12px' }} />
            <div>点击"生成阅卷笔录"组合现有分析结果</div>
          </div>
        ) : (
          <div
            className="report-content"
            style={{
              fontSize: '13px', lineHeight: '1.8',
              padding: '20px', background: colors.surface, borderRadius: '8px',
              border: `1px solid ${colors.border}`,
            }}
            dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(reviewNotes, { async: false }) as string) }}
          />
        )}
      </div>
    )
  }

  // ===== 质证意见面板 =====
  const renderCrossExaminationPanel = () => {
    if (activeTab !== 'cross_exam') return null

    return (
      <div style={{ marginTop: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
          <div style={{ fontSize: '13px', color: colors.textSecondary }}>
            质证意见基于证据三性审查结果，针对有问题的证据制定质证策略。
          </div>
          <button onClick={handleGenerateCrossExamination} disabled={crossExaminationLoading}
            style={{
              padding: '6px 14px', fontSize: '12px', borderRadius: '6px',
              background: crossExaminationLoading ? 'rgba(180,83,9,0.05)' : '#b45309',
              color: crossExaminationLoading ? colors.textTertiary : '#fff',
              border: '1px solid rgba(180,83,9,0.3)',
              cursor: crossExaminationLoading ? 'default' : 'pointer', fontWeight: 500,
              display: 'flex', alignItems: 'center', gap: '5px',
            }}>
            {crossExaminationLoading ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin" />
                生成中...
              </>
            ) : (
              <>
                <RefreshCw className="w-3 h-3" />
                {crossExamination ? '重新生成' : '生成质证意见'}
              </>
            )}
          </button>
        </div>

        {!crossExamination ? (
          <div style={{
            textAlign: 'center', padding: '40px 0', fontSize: '13px', color: colors.textTertiary,
            border: `1px dashed ${colors.borderStrong}`, borderRadius: '8px',
          }}>
            <Gavel className="w-8 h-8" style={{ opacity: 0.3, marginBottom: '12px' }} />
            <div>请先进行证据三性审查，再生成质证意见</div>
          </div>
        ) : (
          <div
            className="report-content"
            style={{
              fontSize: '13px', lineHeight: '1.8',
              padding: '20px', background: colors.surface, borderRadius: '8px',
              border: `1px solid ${colors.border}`,
            }}
            dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(crossExamination, { async: false }) as string) }}
          />
        )}
      </div>
    )
  }

  // ===== 证据链面板 =====
  const renderEvidenceChainPanel = () => {
    if (activeTab !== 'evidence_chain') return null

    return (
      <div style={{
        padding: '16px',
        background: colors.surface,
        borderRadius: '8px',
        border: `1px solid ${colors.border}`,
        height: '600px',
        overflow: 'hidden',
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '12px',
        }}>
          <h3 style={{ margin: 0, fontSize: '15px', color: colors.textPrimary }}>
            证据链关系图
          </h3>
          <button
            onClick={loadEvidenceChain}
            disabled={evidenceChainLoading}
            style={{
              padding: '6px 12px',
              fontSize: '12px',
              background: evidenceChainLoading ? colors.textTertiary : '#0891b2',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: evidenceChainLoading ? 'default' : 'pointer',
            }}
          >
            {evidenceChainLoading ? '加载中...' : '刷新'}
          </button>
        </div>

        {evidenceChainLoading ? (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: '500px',
            color: colors.textTertiary,
          }}>
            <Loader2 className="w-6 h-6 animate-spin" style={{ marginRight: '8px' }} />
            正在分析证据关系...
          </div>
        ) : evidenceChainData ? (
          <EvidenceChainGraph
            data={evidenceChainData}
            onNodeClick={(node) => {
              console.log('点击证据节点:', node)
              // 可以跳转到证据详情或切换到证据列表 tab
            }}
          />
        ) : (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            height: '500px',
            color: colors.textTertiary,
            gap: '12px',
          }}>
            <Network className="w-10 h-10" style={{ opacity: 0.3 }} />
            <div>点击"刷新"加载证据链数据</div>
          </div>
        )}
      </div>
    )
  }

  // ===== 类案参考面板 =====
  const renderSimilarCasesPanel = () => {
    if (activeTab !== 'similar_cases') return null

    return (
      <div style={{
        padding: '16px',
        background: colors.surface,
        borderRadius: '8px',
        border: `1px solid ${colors.border}`,
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '16px',
        }}>
          <h3 style={{ margin: 0, fontSize: '15px', color: colors.textPrimary }}>
            类案参考
          </h3>
          <button
            onClick={handleSearchSimilarCases}
            disabled={similarCasesLoading}
            style={{
              padding: '6px 16px',
              fontSize: '12px',
              background: similarCasesLoading ? colors.textTertiary : '#059669',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: similarCasesLoading ? 'default' : 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            {similarCasesLoading ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin" />
                搜索中...
              </>
            ) : (
              <>
                <Search className="w-3 h-3" />
                搜索类案
              </>
            )}
          </button>
        </div>

        {similarCasesData ? (
          <>
            {/* 罪名和关键事实 */}
            {similarCasesData.crime_type && (
              <div style={{
                padding: '12px',
                background: colors.surfaceAlt,
                borderRadius: '6px',
                marginBottom: '16px',
              }}>
                <div style={{ fontSize: '13px', fontWeight: 600, color: colors.textPrimary, marginBottom: '8px' }}>
                  本案罪名：{similarCasesData.crime_type}
                </div>
                {similarCasesData.key_facts.length > 0 && (
                  <div style={{ fontSize: '12px', color: colors.textSecondary }}>
                    <div style={{ marginBottom: '4px' }}>关键事实：</div>
                    {similarCasesData.key_facts.map((fact, i) => (
                      <div key={i} style={{ marginLeft: '12px', marginBottom: '2px' }}>
                        • {fact.length > 100 ? fact.slice(0, 100) + '...' : fact}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 类案列表 */}
            {similarCasesData.similar_cases.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {similarCasesData.similar_cases.map((c, i) => (
                  <div
                    key={i}
                    style={{
                      padding: '12px',
                      background: colors.surfaceAlt,
                      borderRadius: '6px',
                      border: `1px solid ${colors.border}`,
                    }}
                  >
                    <div style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'flex-start',
                      marginBottom: '8px',
                    }}>
                      <h4 style={{ margin: 0, fontSize: '14px', color: colors.textPrimary, flex: 1 }}>
                        {c.title}
                      </h4>
                      {c.link && (
                        <a
                          href={c.link}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '2px',
                            fontSize: '11px',
                            color: colors.accent,
                            textDecoration: 'none',
                          }}
                        >
                          查看原文 <ExternalLink className="w-3 h-3" />
                        </a>
                      )}
                    </div>
                    <div style={{ fontSize: '12px', color: colors.textSecondary, marginBottom: '6px' }}>
                      <span style={{ marginRight: '12px' }}>审理法院：{c.court}</span>
                      <span style={{ marginRight: '12px' }}>罪名：{c.crime_type}</span>
                      {c.amount && <span>涉案金额：{c.amount}</span>}
                    </div>
                    <div style={{
                      fontSize: '12px',
                      color: colors.gold,
                      fontWeight: 500,
                      marginBottom: '6px',
                    }}>
                      判决结果：{c.result}
                    </div>
                    <div style={{ fontSize: '12px', color: colors.textSecondary, lineHeight: 1.6 }}>
                      裁判要旨：{c.key_point}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{
                textAlign: 'center',
                padding: '40px',
                color: colors.textTertiary,
                fontSize: '13px',
              }}>
                {similarCasesData.error || '未找到相似案例'}
              </div>
            )}
          </>
        ) : (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '60px',
            color: colors.textTertiary,
            gap: '12px',
          }}>
            <Search className="w-10 h-10" style={{ opacity: 0.3 }} />
            <div>点击"搜索类案"查找相似案例</div>
            <div style={{ fontSize: '11px', color: colors.textTertiary }}>
              需要先完成阶段1分析（指控要素提取）
            </div>
          </div>
        )}
      </div>
    )
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: colors.surface }}>
        <Loader2 className="w-7 h-7 animate-spin" style={{ color: colors.accent }} />
        <span style={{ marginLeft: '12px', fontSize: '13px', color: colors.textSecondary }}>加载辩护分析...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100vh', gap: '12px', background: colors.surface }}>
        <AlertCircle className="w-10 h-10" style={{ color: '#991b1b' }} />
        <div style={{ fontSize: '14px', color: colors.textSecondary }}>{error}</div>
        <button onClick={() => navigate(`/case/${caseId}`)} style={{ padding: '8px 16px', background: colors.accent, color: '#fff', border: 'none', borderRadius: '6px', cursor: 'pointer', fontSize: '13px' }}>
          返回案件详情
        </button>
      </div>
    )
  }

  // ====== 主渲染 ======

  const caseInitial = defendant ? defendant.charAt(0) : '案'

  return (
    <>
      {/* 打印样式 */}
      <style>{`
        @media print {
          /* 隐藏左侧栏和右侧栏 */
          .report-page-left-panel,
          .report-page-right-panel,
          .report-page-toolbar {
            display: none !important;
          }
          /* 主内容区全宽 */
          .report-page-content {
            width: 100% !important;
            max-width: 100% !important;
            margin: 0 !important;
            padding: 20px !important;
            border: none !important;
          }
          /* 标签栏只显示当前激活的标签 */
          .report-page-tabs {
            display: none !important;
          }
          /* 打印时显示完整内容 */
          .report-page-content article {
            page-break-inside: avoid;
          }
          /* 打印字体调整 */
          body {
            font-size: 12pt;
            line-height: 1.6;
          }
          /* 表格打印样式 */
          table {
            page-break-inside: avoid;
          }
          /* 链接显示 URL */
          a[href]:after {
            content: " (" attr(href) ")";
            font-size: 0.8em;
            color: #666;
          }
        }
      `}</style>
      <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: colors.surface }}>
      {/* ===== Top bar ===== */}
      <div className="report-page-toolbar" style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 20px', background: colors.surfaceElevated,
        borderBottom: `1px solid ${colors.border}`, flexShrink: 0,
        boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <button onClick={() => navigate(`/case/${caseId}`)} style={{
            padding: '5px 12px', background: colors.accentLight,
            border: `1px solid ${colors.accentBorder}`, borderRadius: '6px',
            cursor: 'pointer', fontSize: '12px', color: colors.accent, fontWeight: 500,
          }}>
            ← 返回
          </button>
          {/* 案件标识 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{
              width: '32px', height: '32px', borderRadius: '8px',
              background: colors.accent, color: '#fff',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '14px', fontWeight: 700, flexShrink: 0,
            }}>{caseInitial}</div>
            <div>
              <div style={{ fontSize: '14px', fontWeight: 600, color: colors.textPrimary, lineHeight: 1.3 }}>
                {caseName || '案件详情'}
              </div>
              <div style={{ fontSize: '11px', color: colors.textTertiary, display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span>被告人：<strong style={{ color: colors.gold }}>{defendant || '未指定'}</strong></span>
                {evidenceItems.length > 0 && (
                  <>
                    <span style={{ color: colors.borderStrong }}>|</span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '3px' }}>
                      <FileText className="w-3 h-3" /> {evidenceItems.length} 份证据
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '6px' }}>
          {editMode ? (
            <>
              <button onClick={handleCancelEdit} disabled={saving} style={{
                padding: '5px 12px',
                background: colors.surfaceAlt,
                color: colors.textSecondary,
                border: `1px solid ${colors.border}`,
                borderRadius: '6px', cursor: saving ? 'default' : 'pointer',
                fontSize: '11px', display: 'flex', alignItems: 'center', gap: '5px', fontWeight: 500,
              }}>
                取消
              </button>
              <button onClick={handleSaveEdit} disabled={saving || !editContent} style={{
                padding: '5px 12px',
                background: saving ? colors.goldBg : colors.gold,
                color: saving ? colors.textTertiary : '#fff',
                border: `1px solid ${saving ? colors.goldBorder : colors.gold}`,
                borderRadius: '6px', cursor: saving ? 'default' : 'pointer',
                fontSize: '11px', display: 'flex', alignItems: 'center', gap: '5px', fontWeight: 500,
              }}>
                {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                保存
              </button>
            </>
          ) : (
            <button onClick={handleStartEdit} disabled={!activeContent} style={{
              padding: '5px 12px',
              background: activeContent ? colors.goldBg : colors.surfaceAlt,
              color: activeContent ? colors.gold : colors.textTertiary,
              border: `1px solid ${activeContent ? colors.goldBorder : colors.border}`,
              borderRadius: '6px', cursor: activeContent ? 'pointer' : 'not-allowed',
              fontSize: '11px', display: 'flex', alignItems: 'center', gap: '5px', fontWeight: 500,
            }}>
              <Edit3 className="w-3 h-3" />编辑
            </button>
          )}
          <button onClick={() => setAnnotationMode(v => !v)} style={{
            padding: '5px 12px',
            background: annotationMode ? colors.gold : colors.goldBg,
            color: annotationMode ? '#fff' : colors.gold,
            border: `1px solid ${annotationMode ? colors.gold : colors.goldBorder}`,
            borderRadius: '6px', cursor: 'pointer',
            fontSize: '11px', display: 'flex', alignItems: 'center', gap: '5px', fontWeight: 500,
          }}>
            <StickyNote className="w-3 h-3" />批注
          </button>
          <button onClick={handleExportReport} disabled={!stageContent.full && !stageContent.stage_53} style={{
            padding: '5px 12px',
            background: (stageContent.full || stageContent.stage_53) ? colors.accent : colors.surfaceAlt,
            color: (stageContent.full || stageContent.stage_53) ? '#fff' : colors.textTertiary,
            border: `1px solid ${(stageContent.full || stageContent.stage_53) ? colors.accent : colors.border}`,
            borderRadius: '6px', cursor: (stageContent.full || stageContent.stage_53) ? 'pointer' : 'not-allowed',
            fontSize: '11px', display: 'flex', alignItems: 'center', gap: '5px', fontWeight: 500,
          }}>
            <Download className="w-3 h-3" />导出报告
          </button>
          <button onClick={() => window.print()} style={{
            padding: '5px 12px',
            background: colors.surfaceElevated,
            color: colors.textSecondary,
            border: `1px solid ${colors.border}`,
            borderRadius: '6px', cursor: 'pointer',
            fontSize: '11px', display: 'flex', alignItems: 'center', gap: '5px', fontWeight: 500,
          }}>
            <Printer className="w-3 h-3" />导出 PDF
          </button>
        </div>
      </div>

      {/* ===== Main content ===== */}

      {/* ===== Three-panel layout ===== */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden', position: 'relative' }}>

        {/* ===== Left: Evidence ===== */}
        <div className="report-page-left-panel" style={{
          width: leftCollapsed ? 36 : rightCollapsed ? '100%' : leftWidth,
          background: colors.surfaceAlt,
          borderRight: `1px solid ${colors.border}`,
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden', flexShrink: 0,
        }}>
          {leftCollapsed ? (
            <div style={{ padding: '8px 4px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
              <button onClick={() => setLeftCollapsed(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px', color: colors.textTertiary }}>
                <PanelLeft className="w-4 h-4" />
              </button>
              <div style={{ width: 24, height: 24, borderRadius: '6px', background: 'rgba(30,58,95,0.08)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '10px', fontWeight: 700, color: colors.accent }}>
                {evidenceItems.length}
              </div>
            </div>
          ) : (
            <>
              {/* 面板头部 */}
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '10px 14px',
                borderBottom: `1px solid ${colors.border}`,
                background: colors.surfaceElevated,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: 0 }}>
                  <div style={{ width: 24, height: 24, borderRadius: '6px', background: colors.accent, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Bookmark className="w-3 h-3" style={{ color: '#fff' }} />
                  </div>
                  <div>
                    <div style={{ fontSize: '12px', fontWeight: 600, color: colors.textPrimary, display: 'flex', alignItems: 'center', gap: '6px' }}>
                      证据浏览
                      {(evidenceItems.length > 0 || processedPdfs.length > 0) && (
                        <select
                          value={viewModeState}
                          onChange={e => viewModeDispatch(e.target.value as 'md' | 'pdf')}
                          style={{
                            padding: '1px 6px', fontSize: '10px', borderRadius: '4px',
                            background: colors.surface, color: colors.textPrimary,
                            border: `1px solid ${colors.border}`, cursor: 'pointer',
                            outline: 'none', fontWeight: 500, lineHeight: '16px',
                          }}
                        >
                          {processedPdfs.length > 0 && <option value="pdf">PDF</option>}
                          {evidenceItems.length > 0 && <option value="md">MD</option>}
                        </select>
                      )}
                    </div>
                    <div style={{ fontSize: '10px', color: colors.textTertiary }}>
                      {viewModeState === 'pdf' ? `${processedPdfs.length} 卷` : `${evidenceItems.length} 份`}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '1px' }}>
                  <button onClick={() => setLeftWidth(w => Math.max(w - 40, 200))} style={resizeBtnStyle}>−</button>
                  <button onClick={() => setLeftWidth(w => Math.min(w + 40, 600))} style={resizeBtnStyle}>+</button>
                  <button onClick={() => setLeftCollapsed(true)} style={resizeBtnStyle}>
                    <PanelLeftClose className="w-3 h-3" />
                  </button>
                </div>
              </div>

              {evidenceItems.length === 0 && processedPdfs.length === 0 ? (
                <div style={{ padding: '32px 16px', fontSize: '12px', color: colors.textTertiary, textAlign: 'center' }}>暂无证据</div>
              ) : (
                <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                    {viewModeState === 'pdf' ? (
                      <>
                        {/* PDF 卷选择 */}
                        {processedPdfs.length > 0 && (
                          <div style={{ padding: '8px 10px', borderBottom: `1px solid ${colors.border}`, background: colors.surfaceElevated }}>
                            <select
                              value={selectedPdf}
                              onChange={e => setSelectedPdf(e.target.value)}
                              style={{
                                width: '100%', padding: '7px 10px', fontSize: '12px',
                                background: colors.surface, color: colors.textPrimary,
                                border: `1px solid ${colors.border}`, borderRadius: '6px',
                                cursor: 'pointer', outline: 'none',
                              }}
                            >
                              {processedPdfs.map(name => (
                                <option key={name} value={name}>{name}</option>
                              ))}
                            </select>
                            {annotationMode && (
                              <div style={{ fontSize: '10px', color: colors.gold, marginTop: '4px' }}>
                                批注模式：点击 PDF 页面任意位置添加批注
                              </div>
                            )}
                          </div>
                        )}
                        {/* PDF 查看器 */}
                        {caseId && selectedPdf ? (
                          <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                            <PdfViewer
                              caseId={caseId}
                              pdfFilename={selectedPdf}
                              annotations={pdfAnnotations}
                              onAddAnnotation={addAnnotation}
                              onUpdateAnnotation={updateAnnotation}
                              onDragAnnotation={updateAnnotationPosition}
                              onDeleteAnnotation={deleteAnnotation}
                              annotationMode={annotationMode}
                            />
                          </div>
                        ) : (
                          <div style={{ padding: '20px 0', fontSize: '12px', color: colors.textTertiary, textAlign: 'center' }}>暂无 PDF 文件</div>
                        )}
                      </>
                    ) : (
                      <>
                        {/* 证据下拉列表 */}
                        <div style={{ padding: '8px 10px', borderBottom: `1px solid ${colors.border}`, background: colors.surfaceElevated }}>
                          <select
                            value={selectedEvidenceId}
                            onChange={e => setSelectedEvidenceId(e.target.value)}
                            style={{
                              width: '100%', padding: '7px 10px', fontSize: '12px',
                              background: colors.surface, color: colors.textPrimary,
                              border: `1px solid ${colors.border}`, borderRadius: '6px',
                              cursor: 'pointer', outline: 'none',
                            }}
                          >
                            {evidenceItems.map(item => (
                              <option key={item.id} value={item.id}>{item.displayName}</option>
                            ))}
                          </select>
                        </div>
                        {/* MD 内容 */}
                        <div style={{ flex: 1, overflow: 'auto', padding: '12px 14px' }}>
                          {selectedEvidenceContent ? (
                            <div
                              className="report-content"
                              style={{ fontSize: '12px', lineHeight: '1.75' }}
                              dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(selectedEvidenceContent, { async: false }) as string) }}
                            />
                          ) : (
                            <div style={{ padding: '20px 0', fontSize: '12px', color: colors.textTertiary, textAlign: 'center' }}>选择证据查看详情</div>
                          )}
                        </div>
                      </>
                    )}
                  </div>
              )}
            </>
          )}
        </div>

        {/* Resize handle - left */}
        {!leftCollapsed && (
          <div onMouseDown={startResizingLeft} style={{
            width: '3px', cursor: 'col-resize',
            background: 'transparent', flexShrink: 0,
            transition: 'background 0.15s',
          }}
            onMouseEnter={e => (e.currentTarget.style.background = colors.accentBorder)}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
          />
        )}

        {/* ===== Middle: Safari-style tabs ===== */}
        <div className="report-page-content" style={{ flex: 1, overflow: 'hidden', background: colors.surfaceElevated }}>
          {renderSafariTabs()}
        </div>

        {/* Resize handle - right */}
        {!rightCollapsed && (
          <div onMouseDown={startResizingRight} style={{
            width: '3px', cursor: 'col-resize',
            background: 'transparent', flexShrink: 0,
            transition: 'background 0.15s',
          }}
            onMouseEnter={e => (e.currentTarget.style.background = colors.accentBorder)}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
          />
        )}

        {/* ===== Right: Chat ===== */}
        <div className="report-page-right-panel" style={{
          width: rightCollapsed ? 36 : rightWidth,
          background: colors.surfaceAlt,
          borderLeft: `1px solid ${colors.border}`,
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden', flexShrink: 0,
        }}>
          {rightCollapsed ? (
            <div style={{ padding: '8px 4px', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <button onClick={() => setRightCollapsed(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px', color: colors.textTertiary }}>
                <MessageCircle className="w-4 h-4" />
              </button>
            </div>
          ) : (
            <>
              {/* Header with tabs */}
              <div style={{
                display: 'flex', alignItems: 'center',
                padding: '0 14px',
                borderBottom: `1px solid ${colors.border}`,
                background: colors.surfaceElevated,
                flexShrink: 0,
              }}>
                {/* 对话分析 tab */}
                <div
                  onClick={() => setRightPanelTab('chat')}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '5px',
                    padding: '10px 12px',
                    cursor: 'pointer',
                    fontSize: '12px',
                    fontWeight: rightPanelTab === 'chat' ? 600 : 400,
                    color: rightPanelTab === 'chat' ? colors.textPrimary : colors.textTertiary,
                    borderBottom: rightPanelTab === 'chat' ? `2px solid ${colors.accent}` : '2px solid transparent',
                    transition: 'all 0.15s ease',
                  }}
                >
                  <MessageCircle className="w-3.5 h-3.5" />
                  对话
                </div>
                {/* 修改报告 tab */}
                {stageContent.stage_53 && (
                  <div
                    onClick={() => setRightPanelTab('update')}
                    style={{
                      display: 'flex', alignItems: 'center', gap: '5px',
                      padding: '10px 12px',
                      cursor: 'pointer',
                      fontSize: '12px',
                      fontWeight: rightPanelTab === 'update' ? 600 : 400,
                      color: rightPanelTab === 'update' ? colors.textPrimary : colors.textTertiary,
                      borderBottom: rightPanelTab === 'update' ? `2px solid ${colors.accent}` : '2px solid transparent',
                      transition: 'all 0.15s ease',
                    }}
                  >
                    <FileText className="w-3.5 h-3.5" />
                    修改报告
                  </div>
                )}
                <div style={{ flex: 1 }} />
                <div style={{ display: 'flex', gap: '1px' }}>
                  <button onClick={() => setRightWidth(w => Math.max(w - 40, 260))} style={resizeBtnStyle}>−</button>
                  <button onClick={() => setRightWidth(w => Math.min(w + 40, 500))} style={resizeBtnStyle}>+</button>
                  <button onClick={() => setRightCollapsed(true)} style={resizeBtnStyle}>
                    <PanelLeftClose className="w-3 h-3" />
                  </button>
                </div>
              </div>

              {/* 对话分析内容 */}
              {rightPanelTab === 'chat' && (
                <>
                  {/* Chat toolbar */}
                  {chatMessages.length > 0 && (
                <div style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '5px 14px', borderBottom: `1px solid ${colors.border}`,
                  fontSize: '11px', background: colors.surfaceElevated,
                }}>
                  <span style={{ color: chatSelectMode ? colors.gold : colors.textTertiary, fontWeight: chatSelectMode ? 600 : 400 }}>
                    {chatSelectMode
                      ? `已选 ${selectedChatIds.size} / ${chatMessages.length} 条`
                      : `${chatMessages.length} 条消息`}
                  </span>
                  <div style={{ display: 'flex', gap: '4px' }}>
                    {chatSelectMode ? (
                      <>
                        <button onClick={toggleAllChatSelect} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '10px', color: colors.textSecondary, fontWeight: 500 }}>
                          {selectedChatIds.size === chatMessages.length ? '取消全选' : '全选'}
                        </button>
                        <button onClick={handleDeleteChat} disabled={selectedChatIds.size === 0} style={{ background: 'none', border: 'none', cursor: selectedChatIds.size > 0 ? 'pointer' : 'default', fontSize: '10px', color: selectedChatIds.size > 0 ? '#991b1b' : colors.textTertiary, display: 'flex', alignItems: 'center', gap: '2px', fontWeight: 500 }}>
                          <Trash2 className="w-3 h-3" />删除
                        </button>
                        <button onClick={handleExportChat} disabled={selectedChatIds.size === 0} style={{ background: 'none', border: 'none', cursor: selectedChatIds.size > 0 ? 'pointer' : 'default', fontSize: '10px', color: selectedChatIds.size > 0 ? colors.accent : colors.textTertiary, display: 'flex', alignItems: 'center', gap: '2px', fontWeight: 500 }}>
                          <Download className="w-3 h-3" />导出
                        </button>
                        <button onClick={() => { setChatSelectMode(false); setSelectedChatIds(new Set()) }} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '10px', color: colors.textSecondary, fontWeight: 500 }}>取消</button>
                      </>
                    ) : (
                      <>
                        <button onClick={handleExportChat} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '10px', color: colors.textSecondary, display: 'flex', alignItems: 'center', gap: '2px', fontWeight: 500 }}>
                          <Download className="w-3 h-3" />导出
                        </button>
                        <button onClick={() => setChatSelectMode(true)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '10px', color: colors.textSecondary, display: 'flex', alignItems: 'center', gap: '2px', fontWeight: 500 }}>
                          <CheckSquare className="w-3 h-3" />选择
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )}

              {/* 证据范围按钮 + 面板 */}
              {chatEvidenceList.length > 0 && (
                <div style={{ position: 'relative', borderBottom: `1px solid ${colors.border}`, background: colors.surfaceElevated }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '5px 12px' }}>
                    <button onClick={() => setShowChatEvidencePanel(v => !v)}
                      style={{
                        display: 'flex', alignItems: 'center', gap: '5px',
                        fontSize: '11px', color: colors.textSecondary, padding: '5px 8px',
                        borderRadius: '5px',
                        background: showChatEvidencePanel ? colors.accentLight : 'transparent',
                        border: `1px solid ${showChatEvidencePanel ? colors.accentBorder : 'transparent'}`,
                        cursor: 'pointer', fontWeight: 500,
                      }}>
                      <Bookmark className="w-3 h-3" />
                      证据范围
                      <span style={{
                        background: colors.accent, color: '#fff',
                        borderRadius: '10px', padding: '1px 7px', fontSize: '10px',
                        fontWeight: 600,
                      }}>
                        {chatEvidenceFilter.size}
                      </span>
                      <span style={{ fontSize: '8px', color: colors.textTertiary, transition: 'transform 0.15s', transform: showChatEvidencePanel ? 'rotate(90deg)' : 'none' }}>▶</span>
                    </button>
                    <span style={{ fontSize: '10px', color: colors.textTertiary }}>共 {chatEvidenceList.length} 个</span>
                  </div>
                  {showChatEvidencePanel && (
                    <div style={{
                      position: 'absolute', left: 0, right: 0, top: '100%', zIndex: 100,
                      background: colors.surfaceElevated, border: `1px solid ${colors.borderStrong}`,
                      borderRadius: '8px', boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
                      maxHeight: '70vh', display: 'flex', flexDirection: 'column',
                      animation: 'slideDown 0.15s ease',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', borderBottom: `1px solid ${colors.border}`, flexShrink: 0 }}>
                        <span style={{ fontSize: '12px', fontWeight: 600, color: colors.textPrimary }}>
                          选择证据对话范围
                        </span>
                        <div style={{ display: 'flex', gap: '6px' }}>
                          <button onClick={() => {
                            const allNames = new Set(chatEvidenceList.map(f => f.name))
                            const excluded = new Set(chatEvidenceList
                              .filter(f => f.name.includes('讯问') || f.name.includes('询问'))
                              .map(f => f.name))
                            const currentlyExcluded = chatEvidenceList
                              .filter(f => !chatEvidenceFilter.has(f.name))
                              .every(f => excluded.has(f.name))
                            if (currentlyExcluded) {
                              setChatEvidenceFilter(allNames)
                            } else {
                              const withoutStatement = new Set<string>()
                              for (const n of allNames) {
                                if (n.includes('讯问') || n.includes('询问')) continue
                                withoutStatement.add(n)
                              }
                              setChatEvidenceFilter(withoutStatement)
                            }
                          }}
                            style={{ padding: '3px 10px', fontSize: '10px', border: `1px solid ${colors.border}`, borderRadius: '4px', cursor: 'pointer',
                              background: colors.surfaceElevated, color: colors.textSecondary, fontWeight: 500 }}>
                            包含讯问/询问
                          </button>
                          <button onClick={() => setChatEvidenceFilter(new Set(chatEvidenceList.map(f => f.name)))}
                            style={{ padding: '3px 10px', fontSize: '10px', background: colors.accent, color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer', fontWeight: 500 }}>
                            全选
                          </button>
                          <button onClick={() => setChatEvidenceFilter(new Set())}
                            style={{ padding: '3px 10px', fontSize: '10px', background: colors.surfaceAlt, color: colors.textSecondary, border: `1px solid ${colors.border}`, borderRadius: '4px', cursor: 'pointer', fontWeight: 500 }}>
                            清空
                          </button>
                          <button onClick={() => setShowChatEvidencePanel(false)}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '14px', color: colors.textTertiary, padding: '0 2px' }}>
                            ✕
                          </button>
                        </div>
                      </div>
                      <div style={{ flex: 1, overflow: 'auto', padding: '8px 14px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
                        {chatEvidenceList.map(f => {
                          const isStatement = f.name.includes('讯问') || f.name.includes('询问')
                          return (
                            <label key={f.name} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '6px 8px', fontSize: '11px', cursor: 'pointer', borderRadius: '6px', background: chatEvidenceFilter.has(f.name) ? colors.accentLight : 'transparent' }}>
                              <input type="checkbox" checked={chatEvidenceFilter.has(f.name)}
                                onChange={e => {
                                  setChatEvidenceFilter(prev => {
                                    const next = new Set(prev)
                                    e.target.checked ? next.add(f.name) : next.delete(f.name)
                                    return next
                                  })
                                }}
                                style={{ accentColor: colors.accent, flexShrink: 0 }} />
                              {isStatement && <span style={{ fontSize: '9px', background: 'rgba(156,102,27,0.1)', color: '#9c661b', borderRadius: '3px', padding: '1px 4px', flexShrink: 0 }}>笔录</span>}
                              <span style={{ color: chatEvidenceFilter.has(f.name) ? colors.accent : colors.textSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {f.name}
                              </span>
                            </label>
                          )
                        })}
                      </div>
                      <div style={{ padding: '8px 14px', borderTop: `1px solid ${colors.border}`, background: colors.surfaceAlt, borderRadius: '0 0 8px 8px', fontSize: '11px', color: colors.textSecondary, flexShrink: 0, display: 'flex', justifyContent: 'space-between' }}>
                        <span>已选 {chatEvidenceFilter.size} / {chatEvidenceList.length} 个</span>
                        <span style={{ fontSize: '10px', color: colors.textTertiary }}>
                          {chatEvidenceList.filter(f => f.name.includes('讯问') || f.name.includes('询问')).length} 个笔录
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Messages */}
              <div style={{ flex: 1, overflow: 'auto', padding: '14px' }}>
                {chatMessages.length === 0 ? (
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: colors.textTertiary, textAlign: 'center' }}>
                    <div style={{
                      width: '48px', height: '48px', borderRadius: '12px',
                      background: colors.accentLight,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      marginBottom: '12px',
                    }}>
                      <MessageCircle className="w-5 h-5" style={{ color: colors.accent, opacity: 0.5 }} />
                    </div>
                    <div style={{ fontSize: '12px', fontWeight: 500 }}>开始对话分析</div>
                    <div style={{ fontSize: '11px', marginTop: '4px', color: colors.textTertiary }}>选择证据范围，深入追问</div>
                  </div>
                ) : (
                  <>
                    {chatMessages.map(msg => {
                      const isSelected = chatSelectMode && selectedChatIds.has(msg.id)
                      return (
                        <div key={msg.id}
                          onPointerDown={chatSelectMode ? () => toggleChatSelect(msg.id) : undefined}
                          style={{
                            marginBottom: '10px',
                            cursor: chatSelectMode ? 'pointer' : 'default',
                            display: 'flex',
                            alignItems: 'flex-start',
                            gap: '8px',
                          }}>
                          {chatSelectMode && (
                            <div style={{ paddingTop: '8px', flexShrink: 0, width: '16px', textAlign: 'center' }}>
                              <span style={{
                                display: 'inline-block',
                                width: '14px', height: '14px',
                                borderRadius: '3px',
                                border: isSelected ? 'none' : `1.5px solid ${colors.textTertiary}`,
                                background: isSelected ? colors.gold : 'transparent',
                                color: '#fff',
                                fontSize: '10px',
                                lineHeight: '14px',
                                textAlign: 'center',
                              }}>
                                {isSelected ? '✓' : ''}
                              </span>
                            </div>
                          )}
                          <div style={{
                            outline: isSelected ? `2px solid ${colors.gold}` : 'none',
                            borderRadius: '8px', padding: '1px',
                            flex: 1,
                          }}>
                            {msg.role === 'user' ? (
                              <div style={{
                                background: colors.userBubble, color: '#fff',
                                borderRadius: '14px 14px 4px 14px',
                                padding: '9px 14px', fontSize: '12px', lineHeight: '1.6',
                                maxWidth: '85%', alignSelf: 'flex-end',
                                boxShadow: '0 1px 3px rgba(30,58,95,0.15)',
                              }}>{msg.content}</div>
                            ) : msg.role === 'system' ? (
                              <div style={{
                                background: colors.systemBubble, color: colors.gold,
                                borderRadius: '8px', padding: '6px 12px',
                                fontSize: '11px', lineHeight: '1.5',
                                display: 'flex', alignItems: 'center', gap: '6px',
                                border: `1px solid ${colors.goldBorder}`,
                              }}>
                                {msg.content === '思考中...' && (
                                  <Loader2 className="w-3 h-3 animate-spin" />
                                )}
                                {msg.content}
                              </div>
                            ) : (
                              <div style={{
                                background: colors.assistantBubble,
                                borderRadius: '14px 14px 14px 4px',
                                padding: '10px 14px', fontSize: '12px', lineHeight: '1.7',
                                border: `1px solid ${colors.border}`,
                                boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
                              }}>
                                <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(msg.content, { async: false }) as string) }} />
                              </div>
                            )}
                          </div>
                        </div>
                      )
                    })}
                    <div ref={messagesEndRef} />
                  </>
                )}
              </div>

              {/* Input */}
              <div style={{
                padding: '10px 14px',
                borderTop: `1px solid ${colors.border}`,
                background: colors.surfaceElevated,
              }}>
                <div style={{ display: 'flex', gap: '6px', marginBottom: '6px' }}>
                  <input ref={chatInputRef} type="text" value={chatInput} onChange={e => setChatInput(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && chatInput.trim()) { e.preventDefault(); handleSendChat() } }}
                    placeholder={'基于当前证据提问...'}
                    disabled={chatLoading}
                    style={{
                      flex: 1, padding: '8px 12px',
                      border: `1px solid ${colors.border}`, borderRadius: '8px',
                      fontSize: '12px', background: colors.surface, outline: 'none',
                      transition: 'border-color 0.15s',
                    }}
                    onFocus={e => e.target.style.borderColor = colors.accentBorder}
                    onBlur={e => e.target.style.borderColor = colors.border}
                  />
                  <button onClick={handleSendChat} disabled={!chatInput.trim() || chatLoading}
                    style={{
                      padding: '8px 12px',
                      background: chatInput.trim() && !chatLoading ? colors.gold : colors.surfaceAlt,
                      color: chatInput.trim() && !chatLoading ? '#fff' : colors.textTertiary,
                      border: `1px solid ${chatInput.trim() && !chatLoading ? colors.gold : colors.border}`,
                      borderRadius: '8px',
                      cursor: chatInput.trim() && !chatLoading ? 'pointer' : 'default',
                      display: 'flex', alignItems: 'center', gap: '3px',
                      transition: 'all 0.15s',
                    }}>
                    {chatLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                  </button>
                </div>
              </div>
                </>
              )}

              {/* 修改报告内容 */}
              {rightPanelTab === 'update' && stageContent.stage_53 && (
                <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
                  {/* 上半区：参考 + 输入 */}
                  <div style={{ padding: '14px 14px 10px', flexShrink: 0 }}>
                    {/* 参考要素 */}
                    <div style={{ marginBottom: '10px' }}>
                      <div style={{ fontSize: '11px', fontWeight: 600, color: colors.textPrimary, marginBottom: '6px' }}>
                        参考分析结果
                      </div>
                      <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                        {([...TABS.slice(0, 4), TABS[5]]).map(tab => {
                          const tabNum = tab.key === 'stage_52' ? 6 : TABS.indexOf(tab) + 1
                          const isSelected = updateStepsSelected.has(tabNum)
                          return (
                            <button key={tab.key} onClick={() => {
                              setUpdateStepsSelected(prev => {
                                const n = new Set(prev)
                                isSelected ? n.delete(tabNum) : n.add(tabNum)
                                return n
                              })
                            }}
                              style={{
                                padding: '3px 10px', fontSize: '10px', borderRadius: '4px',
                                background: isSelected ? tab.bgColor : colors.surfaceAlt,
                                border: `1px solid ${isSelected ? tab.color : colors.border}`,
                                color: isSelected ? tab.color : colors.textTertiary,
                                cursor: 'pointer', fontWeight: isSelected ? 600 : 400,
                                transition: 'all 0.15s ease',
                              }}>
                              {tab.label}
                            </button>
                          )
                        })}
                      </div>
                    </div>

                    {/* 修改输入 */}
                    <textarea
                      value={reportUpdateInput}
                      onChange={e => setReportUpdateInput(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && reportUpdateInput.trim()) { e.preventDefault(); handleUpdateReport() } }}
                      placeholder="输入修改思路，LLM 将结合已有分析结果修改报告&#10;&#10;Shift+Enter 换行，Enter 发送"
                      disabled={chatLoading}
                      rows={8}
                      style={{
                        width: '100%', padding: '12px 14px',
                        border: `1px solid ${colors.border}`, borderRadius: '8px',
                        fontSize: '13px', background: colors.surface, outline: 'none', resize: 'none',
                        fontFamily: 'inherit', color: colors.textPrimary, minHeight: '160px',
                        boxSizing: 'border-box', lineHeight: '1.8',
                      }} />

                    {/* 发送按钮 */}
                    <button onClick={handleUpdateReport} disabled={!reportUpdateInput.trim() || chatLoading}
                      style={{
                        marginTop: '10px', padding: '10px 20px', fontSize: '13px', borderRadius: '8px',
                        background: reportUpdateInput.trim() && !chatLoading ? '#6b2765' : colors.surfaceAlt,
                        color: reportUpdateInput.trim() && !chatLoading ? '#fff' : colors.textTertiary,
                        border: `1px solid ${reportUpdateInput.trim() && !chatLoading ? '#6b2765' : colors.border}`,
                        cursor: reportUpdateInput.trim() && !chatLoading ? 'pointer' : 'default',
                        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', fontWeight: 500,
                        transition: 'all 0.15s', width: '100%',
                      }}>
                      {chatLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                      修改报告
                    </button>
                  </div>

                  {/* 下半区：修改记录 */}
                  <div style={{ flex: 1, overflow: 'auto', borderTop: `1px solid ${colors.border}`, padding: '12px 14px' }}>
                    <div style={{ fontSize: '11px', fontWeight: 600, color: colors.textPrimary, marginBottom: '10px' }}>
                      修改记录
                    </div>
                    {chatMessages.filter(m => m.content.startsWith('报告已更新') || m.content.startsWith('请结合以下分析结果修改报告') || m.content.includes('修改说明')).length === 0 ? (
                      <div style={{ fontSize: '11px', color: colors.textTertiary, textAlign: 'center', padding: '16px 0' }}>
                        暂无修改记录
                      </div>
                    ) : (
                      chatMessages.filter(m => m.content.startsWith('报告已更新') || m.content.startsWith('请结合以下分析结果修改报告') || m.content.includes('修改说明')).map(msg => (
                        <div key={msg.id} style={{ marginBottom: '10px' }}>
                          {msg.role === 'user' ? (
                            <div style={{
                              background: colors.userBubble, color: '#fff',
                              borderRadius: '14px 14px 4px 14px',
                              padding: '9px 14px', fontSize: '12px', lineHeight: '1.6',
                              alignSelf: 'flex-end',
                              boxShadow: '0 1px 3px rgba(30,58,95,0.15)',
                            }}>{msg.content}</div>
                          ) : (
                            <div style={{
                              background: colors.assistantBubble,
                              borderRadius: '14px 14px 14px 4px',
                              padding: '10px 14px', fontSize: '12px', lineHeight: '1.7',
                              border: `1px solid ${colors.border}`,
                              boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
                            }}>
                              <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(msg.content, { async: false }) as string) }} />
                            </div>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
    </>
  )
}

// 按钮样式复用
const resizeBtnStyle: React.CSSProperties = {
  background: 'none', border: 'none', cursor: 'pointer',
  padding: '3px 5px', fontSize: '12px', color: colors.textTertiary,
}
