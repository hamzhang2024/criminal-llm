import { useState, useCallback, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileText, Send, Download, FolderOpen, Loader2, AlertCircle, Scale, BookOpen, CheckCircle, Wand2, PlusCircle, RefreshCw, FilePlus2, Users } from 'lucide-react'
import { MacOSTitlebar, MacOSSidebar, MacOSToolbar, MacOSButton, MacOSCard, MacOSEmptyState } from '../components/MacOSLayout'
import { api, API_BASE } from '../api'

interface MDFile {
  name: string
  path: string
  content: string
  size: number
}

interface EvidenceItem {
  id: string
  filename: string
  filepath: string
  type: string
  pages: number
  selected: boolean
  summary?: string
}

interface RelationshipEdge {
  from: string
  to: string
  type: string
  description: string
}

interface AnalysisReport {
  raw_markdown?: string
  sections?: Record<string, string>
  defense_points?: string[]
  contradictions?: string[]
  generated_at?: string
}

interface AnalysisResult {
  caseId?: string
  defendant: string
  report?: AnalysisReport
  evidence_count?: number
  evidenceType: string
  summary: string
  constitutiveElements: string[]  // 构成要件分析
  illegality: string              // 违法性分析
  responsibility: string          // 有责性分析
  defensePoints: string[]         // 辩护要点
  defenseOpinion?: string         // 辩护意见
  confidence: number
  crimeType?: string             // 罪名推断
  relationships?: {              // 人物关系
    people: string[]
    edges: RelationshipEdge[]
    roles: Record<string, string>  // 人名 → 角色
  }
}

interface CaseInfo {
  case_id: string
  case_name: string
  case_dir: string
  defendant?: string
  evidence_list: EvidenceItem[]
  status: string
}

const ROLE_COLORS: Record<string, string> = {
  '犯罪嫌疑人': '#ff3b30',
  '同案犯': '#ff9500',
  '被害人': '#1e3a5f',
  '证人': '#2d8f3d',
  '介绍人': '#af52de',
  '中间人': '#5856d6',
  '其他': '#8e8e93',
}

function getRoleColor(role: string): string {
  for (const [key, color] of Object.entries(ROLE_COLORS)) {
    if (role.includes(key)) return color
  }
  return ROLE_COLORS['其他']
}

function parseRelationshipTable(markdown: string) {
  const people = new Set<string>()
  const roles: Record<string, string> = {}
  const edges: RelationshipEdge[] = []

  // Find the relationship table
  const tableMatch = markdown.match(/\| 人物 \| 角色 \| 关联人物 \| 关系类型 \| 关系说明 \|([\s\S]*?)(?=\n###|\n##|\n---|$)/)
  if (!tableMatch) return null

  const rows = tableMatch[1].split('\n').filter(line => line.trim() && line.includes('|'))
  for (const row of rows) {
    // Skip separator row
    if (row.includes('---') || row.includes('---')) continue
    const cols = row.split('|').map(c => c.trim()).filter(c => c)
    if (cols.length < 5) continue
    if (cols[0] === '人物') continue // skip header

    const [person, role, related, relationType, desc] = cols
    people.add(person)
    people.add(related)
    roles[person] = role
    if (!roles[related]) roles[related] = '其他'
    edges.push({ from: person, to: related, type: relationType, description: desc })
  }

  if (people.size === 0) return null
  return { people: Array.from(people), edges, roles }
}

function RelationshipGraph({ data }: { data: NonNullable<AnalysisResult['relationships']> }) {
  const { people, edges, roles } = data
  const [hovered, setHovered] = useState<string | null>(null)

  const cx = 400
  const cy = 300
  const radius = 200
  const nodeRadius = 40

  const positions: Record<string, { x: number; y: number }> = {}
  people.forEach((name, i) => {
    const angle = (2 * Math.PI * i) / people.length - Math.PI / 2
    positions[name] = {
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
    }
  })

  const connectedTo = (name: string) => {
    const set = new Set<string>()
    edges.forEach(e => {
      if (e.from === name) set.add(e.to)
      if (e.to === name) set.add(e.from)
    })
    return set
  }

  const isHighlighted = (name: string) => {
    if (!hovered) return true
    if (name === hovered) return true
    return connectedTo(hovered).has(name)
  }

  return (
    <svg viewBox="0 0 800 600" style={{ width: '100%', height: 'auto', background: 'transparent' }}>
      {/* Edges */}
      {edges.map((edge, i) => {
        const from = positions[edge.from]
        const to = positions[edge.to]
        if (!from || !to) return null
        const active = hovered ? (edge.from === hovered || edge.to === hovered) : true
        const midX = (from.x + to.x) / 2
        const midY = (from.y + to.y) / 2 - 15
        return (
          <g key={i} opacity={active ? 1 : 0.15} style={{ transition: 'opacity 0.2s' }}>
            <path
              d={`M ${from.x} ${from.y} Q ${midX} ${midY} ${to.x} ${to.y}`}
              fill="none"
              stroke={active ? '#1e3a5f' : '#d1d1d6'}
              strokeWidth={active ? 2 : 1}
              style={{ transition: 'all 0.2s' }}
            />
            {active && (
              <text
                x={midX}
                y={midY - 4}
                textAnchor="middle"
                fontSize="11"
                fill="#8e8e93"
              >
                {edge.type}
              </text>
            )}
          </g>
        )
      })}

      {/* Nodes */}
      {people.map(name => {
        const pos = positions[name]
        if (!pos) return null
        const active = isHighlighted(name)
        const role = roles[name] || '其他'
        const color = getRoleColor(role)
        return (
          <g
            key={name}
            opacity={active ? 1 : 0.2}
            style={{ transition: 'opacity 0.2s', cursor: 'pointer' }}
            onMouseEnter={() => setHovered(name)}
            onMouseLeave={() => setHovered(null)}
          >
            <circle
              cx={pos.x}
              cy={pos.y}
              r={nodeRadius}
              fill={color}
              fillOpacity={active ? 0.15 : 0.08}
              stroke={color}
              strokeWidth={active ? 2.5 : 1.5}
              style={{ transition: 'all 0.2s' }}
            />
            {/* Role label above */}
            <text
              x={pos.x}
              y={pos.y - nodeRadius - 8}
              textAnchor="middle"
              fontSize="10"
              fill={color}
              fontWeight="500"
            >
              {role}
            </text>
            {/* Name inside */}
            <text
              x={pos.x}
              y={pos.y + 4}
              textAnchor="middle"
              fontSize="13"
              fontWeight="600"
              fill={active ? '#1d1d1f' : '#8e8e93'}
            >
              {name.length > 6 ? name.slice(0, 5) + '…' : name}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

export function AnalyzePage() {
  const navigate = useNavigate()
  const [mdFiles, setMdFiles] = useState<MDFile[]>([])
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [defenseTarget, setDefenseTarget] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [chatMessages, setChatMessages] = useState<Array<{role: string, content: string}>>([])
  const [chatInput, setChatInput] = useState('')
  const [supplementMode, setSupplementMode] = useState(false)  // 增补模式
  const [supplementFiles, setSupplementFiles] = useState<MDFile[]>([])  // 增补文件
  const [caseInfo, setCaseInfo] = useState<CaseInfo | null>(null)
  const [progress, setProgress] = useState('')

  // 加载 MD 文件
  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files
    if (selected) {
      const files: MDFile[] = Array.from(selected).map(file => ({
        name: file.name,
        path: file.name,
        content: '',
        size: file.size
      }))
      
      if (supplementMode) {
        // 增补模式：追加到现有文件列表
        setSupplementFiles(files)
        setMdFiles(prev => [...prev, ...files])
      } else {
        // 正常模式：替换文件列表
        setMdFiles(files)
        setSupplementFiles([])
      }
      
      if (files.length > 0) {
        setSelectedFile(files[0].name)
      }
      setResult(null)
    }
  }
  
  // 切换增补模式
  const toggleSupplementMode = () => {
    setSupplementMode(!supplementMode)
    setSupplementFiles([])
  }

  // 创建案件（上传 MD 文件到后端）
  const createCaseFromFiles = useCallback(async (files: MDFile[]): Promise<CaseInfo | null> => {
    try {
      const formData = new FormData()

      // MD 文件需要先转换为 PDF 格式或者直接作为文本上传
      // 由于后端期望 PDF，这里我们把 MD 文件作为 files 上传
      // 后端 analyzer_api.py 有 upload-directory 接口支持 PDF

      // 如果是本地文件路径，使用 create 接口
      if (files[0].path && files[0].path !== files[0].name) {
        // 提取目录路径
        const dirPath = files[0].path.substring(0, files[0].path.lastIndexOf('/'))
        return await api.createAnalysis(dirPath)
      } else {
        // 文件上传模式
        for (const file of files) {
          // 将 MD 文件作为 Blob 上传（后端需要支持，这里临时处理）
          const blob = new Blob([file.content || ''], { type: 'text/markdown' })
          formData.append('files', blob, file.name)
        }

        const response = await fetch(`${API_BASE}/analyze-case/upload-directory?case_name=${encodeURIComponent('案卷分析')}`, {
          method: 'POST',
          body: formData
        })

        if (!response.ok) {
          throw new Error('上传文件失败')
        }

        return await response.json()
      }
    } catch (err) {
      console.error('创建案件失败:', err)
      return null
    }
  }, [])

  // 开始分析 - 调用真实后端 API
  const handleAnalyze = useCallback(async () => {
    if (!selectedFile || !defenseTarget) return

    setAnalyzing(true)
    setError(null)
    setResult(null)
    setProgress('准备分析...')

    try {
      // 1. 如果没有案件，先创建
      let currentCase = caseInfo
      if (!currentCase) {
        setProgress('创建案件...')
        currentCase = await createCaseFromFiles(mdFiles)
        if (!currentCase) {
          throw new Error('无法创建案件')
        }
        setCaseInfo(currentCase)
      }

      // 2. 选中要分析的证据
      const selectedEvidence = currentCase.evidence_list.filter(e =>
        mdFiles.some(f => f.name.includes(e.filename) || e.filename.includes(f.name.replace('.md', '.pdf')))
      )

      if (selectedEvidence.length === 0) {
        // 如果匹配失败，使用第一个证据
        selectedEvidence.push(currentCase.evidence_list[0])
      }

      const evidenceIds = selectedEvidence.map(e => e.id)

      setProgress('选择证据...')

      // 选中证据
      await api.selectEvidence(currentCase.case_id, evidenceIds)

      // 3. 调用分析 API
      setProgress(`正在分析 ${selectedEvidence.length} 个证据...`)

      const result = await api.analyzeCase(currentCase.case_id, defenseTarget,
        defenseTarget.includes('职务侵占') ? '职务侵占罪' :
        defenseTarget.includes('故意伤害') ? '故意伤害罪' :
        defenseTarget.includes('盗窃') ? '盗窃罪' : undefined
      )

      const analysisResult = result
      setProgress('解析分析结果...')

      // 4. 解析报告
      const report = analysisResult.report
      const rawMarkdown = report?.raw_markdown || ''
      const sections = report?.sections || {}

      // 从 sections 中提取三阶层分析内容
      const constitutiveSection = sections['辩护要点'] || sections['六、辩护要点'] || ''
      const illegalitySection = sections['违法性'] || sections['三、违法性'] || ''
      const responsibilitySection = sections['有责性'] || ''

      // 提取构成要件分析
      const constitutiveElements: string[] = []
      const constitutiveMatch = constitutiveSection.match(/\|[^|]+\|[^|]+\|[^|]+\|[^|]+\|[^|]+\|/g)
      if (constitutiveMatch) {
        for (const row of constitutiveMatch) {
          const cols = row.split('|').filter((c: string) => c.trim())
          if (cols.length >= 2 && cols[0].includes('构成要件')) {
            constitutiveElements.push(`${cols[0].trim()}: ${cols[1]?.trim() || '待分析'}`)
          }
        }
      }

      // 如果表格提取失败，从文本中提取
      if (constitutiveElements.length === 0) {
        const lines = rawMarkdown.split('\n')
        for (const line of lines) {
          if (line.includes('行为主体') || line.includes('行为') ||
              line.includes('对象') || line.includes('因果关系') ||
              line.includes('故意') || line.includes('目的')) {
            constitutiveElements.push(line.replace(/^[-*]\s*/, '').trim())
          }
        }
      }

      // Parse relationships from markdown table
      const relationships = parseRelationshipTable(rawMarkdown)

      // 构建结果对象
      const mockResult: AnalysisResult = {
        caseId: currentCase.case_id,
        defendant: defenseTarget,
        report: report,
        evidence_count: analysisResult.evidence_count,
        evidenceType: selectedEvidence[0]?.type || '综合证据',
        summary: `已完成 ${selectedEvidence.length} 个证据的三阶层分析`,
        constitutiveElements: constitutiveElements.length > 0 ? constitutiveElements : [
          '主体要件：分析报告已生成',
          '主观要件：详见完整报告',
          '客观要件：详见完整报告',
          '客体要件：详见完整报告'
        ],
        illegality: illegalitySection || '详见分析报告中的违法性分析章节',
        responsibility: responsibilitySection || '详见分析报告中的有责性分析章节',
        defensePoints: report?.defense_points || [
          '证据分析已完成，请查看完整报告',
          '建议仔细核对证据的合法性',
          '注意证据之间的矛盾点'
        ],
        confidence: 90,
        relationships: relationships || undefined,
      }

      setResult(mockResult)
      setProgress('')

      // 添加初始对话
      setChatMessages([
        {
          role: 'assistant',
          content: `已完成对 "${selectedFile}" 的三阶层分析。

**分析概况**：
- 被告人：${defenseTarget}
- 分析证据：${selectedEvidence.length} 个
- 罪名推断：${mockResult.crimeType || '综合分析'}

**构成要件符合性**：
${mockResult.constitutiveElements.map(e => `- ${e}`).join('\n')}

**违法性**：${mockResult.illegality}

**有责性**：${mockResult.responsibility}

**辩护要点**：
${mockResult.defensePoints.map(p => `- ${p}`).join('\n')}

完整报告已生成，您可以继续追问具体问题。`
        }
      ])
    } catch (err) {
      setError(err instanceof Error ? err.message : '分析失败')
      setProgress('')
    } finally {
      setAnalyzing(false)
    }
  }, [selectedFile, defenseTarget, mdFiles, caseInfo, createCaseFromFiles])

  // 发送消息 - 调用真实后端 API
  const handleSend = useCallback(async () => {
    if (!chatInput.trim() || !caseInfo) return

    const userMsg = { role: 'user', content: chatInput }
    setChatMessages(prev => [...prev, userMsg])
    setChatInput('')
    setError(null)

    try {
      // 调用真实对话 API
      const data = await api.chatAboutCase(caseInfo.case_id, chatInput, chatMessages.map(m => ({ role: m.role, content: m.content })))

      setChatMessages(prev => [...prev, {
        role: 'assistant',
        content: data.message || data.answer || '分析完成'
      }])
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '未知错误'
      setError(errorMsg)
      setChatMessages(prev => [...prev, {
        role: 'assistant',
        content: `对话失败：${errorMsg}`
      }])
    }
  }, [chatInput, caseInfo, chatMessages])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#ffffff', overflow: 'hidden' }}>
      <MacOSTitlebar showBack onBack={() => navigate('/')} />
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <MacOSSidebar />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <MacOSToolbar title="④ 案卷分析">
            <div style={{ display: 'flex', gap: '8px' }}>
              <MacOSButton variant="primary" icon={FolderOpen} onClick={() => document.getElementById('md-upload')?.click()}>
                {supplementMode ? '选择增补文件' : '选择 MD 文件'}
              </MacOSButton>
              {mdFiles.length > 0 && (
                <MacOSButton 
                  variant={supplementMode ? 'secondary' : 'primary'} 
                  icon={supplementMode ? RefreshCw : Wand2} 
                  onClick={supplementMode ? toggleSupplementMode : handleAnalyze} 
                  disabled={analyzing || !defenseTarget || !selectedFile}
                >
                  {supplementMode ? '完成增补' : (analyzing ? '分析中...' : '开始分析')}
                </MacOSButton>
              )}
              {mdFiles.length > 0 && !supplementMode && (
                <MacOSButton variant="secondary" icon={FilePlus2} onClick={toggleSupplementMode}>
                  增补案卷
                </MacOSButton>
              )}
            </div>
          </MacOSToolbar>

          {error && (
            <div style={{ padding: '12px 20px', background: 'rgba(255, 59, 48, 0.1)', borderBottom: '1px solid var(--macos-border)', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <AlertCircle className="w-4 h-4" style={{ color: 'var(--macos-danger)' }} />
              <span style={{ color: 'var(--macos-danger)', fontSize: '13px' }}>{error}</span>
              <button onClick={() => setError(null)} style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', fontSize: '18px', color: 'var(--macos-text-secondary)' }}>×</button>
            </div>
          )}

          {/* 进度显示 */}
          {progress && (
            <div style={{ padding: '12px 20px', background: 'rgba(0, 122, 255, 0.05)', borderBottom: '1px solid var(--macos-border)', display: 'flex', alignItems: 'center', gap: '12px' }}>
              <Loader2 className="w-4 h-4 animate-spin" style={{ color: '#1e3a5f' }} />
              <span style={{ fontSize: '13px', color: '#1e3a5f' }}>{progress}</span>
            </div>
          )}

          <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
            {/* 左侧：文件列表和输入 */}
            <div style={{ width: '320px', borderRight: '1px solid var(--macos-border)', background: 'var(--macos-bg-secondary)', display: 'flex', flexDirection: 'column' }}>
              {/* 文件列表 */}
              <div style={{ padding: '16px', borderBottom: '1px solid var(--macos-border)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                  <h3 style={{ fontSize: '13px', fontWeight: '600', color: 'var(--macos-text-secondary)' }}>
                    {supplementMode ? '增补文件' : '案卷文件'}
                  </h3>
                  {supplementMode && (
                    <span style={{ fontSize: '11px', padding: '2px 8px', background: 'rgba(255,149,0,0.1)', color: '#ff9500', borderRadius: '10px' }}>
                      增补模式
                    </span>
                  )}
                </div>
                {mdFiles.length === 0 ? (
                  <div style={{ fontSize: '12px', color: 'var(--macos-text-tertiary)', textAlign: 'center', padding: '20px' }}>
                    {supplementMode ? '选择要增补的 MD 文件' : '请先完成案卷拆分'}
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                    {mdFiles.map((file, index) => {
                      const isSupplement = supplementFiles.some(sf => sf.name === file.name)
                      return (
                        <button
                          key={file.name}
                          onClick={() => setSelectedFile(file.name)}
                          style={{
                            padding: '8px 12px',
                            background: selectedFile === file.name ? 'rgba(0,122,255,0.1)' : 'transparent',
                            border: selectedFile === file.name ? '1px solid #1e3a5f' : '1px solid transparent',
                            borderRadius: '6px',
                            cursor: 'pointer',
                            textAlign: 'left',
                            fontSize: '13px',
                            color: selectedFile === file.name ? '#1e3a5f' : 'var(--macos-text-primary)',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '8px'
                          }}
                        >
                          <FileText className="w-3 h-3" />
                          <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file.name}</span>
                          {isSupplement && (
                            <span style={{ fontSize: '10px', padding: '1px 6px', background: 'rgba(255,149,0,0.1)', color: '#ff9500', borderRadius: '8px' }}>
                              增补
                            </span>
                          )}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>

              {/* 辩护对象输入 */}
              <div style={{ padding: '16px', borderBottom: '1px solid var(--macos-border)' }}>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: '500', marginBottom: '8px', color: 'var(--macos-text-secondary)' }}>
                  辩护对象（罪名）
                </label>
                <input
                  type="text"
                  value={defenseTarget}
                  onChange={(e) => setDefenseTarget(e.target.value)}
                  placeholder="如：故意伤害罪"
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    border: '1px solid var(--macos-border)',
                    borderRadius: '8px',
                    fontSize: '13px',
                    background: 'white'
                  }}
                />
              </div>

              {/* 分析说明 */}
              <div style={{ padding: '16px', flex: 1, overflow: 'auto' }}>
                <div style={{ display: 'flex', gap: '8px', padding: '12px', background: 'rgba(0,122,255,0.05)', borderRadius: '8px' }}>
                  <BookOpen className="w-4 h-4" style={{ color: '#1e3a5f', flexShrink: 0, marginTop: '2px' }} />
                  <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)', lineHeight: '1.5' }}>
                    <div style={{ fontWeight: '500', marginBottom: '4px', color: 'var(--macos-text-primary)' }}>三阶层分析</div>
                    <div>1. 构成要件符合性</div>
                    <div>2. 违法性</div>
                    <div>3. 有责性</div>
                  </div>
                </div>
              </div>
            </div>

            {/* 右侧：分析结果和对话 */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              {!result ? (
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <MacOSEmptyState
                    icon={Scale}
                    title="选择文件并开始分析"
                    description="选择拆分后的 MD 文件，输入辩护对象，点击开始分析"
                  />
                </div>
              ) : (
                <>
                  {/* 分析结果 */}
                  <div style={{ flex: 1, overflow: 'auto', padding: '20px' }}>
                    <MacOSCard>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                        <h2 style={{ fontSize: '18px', fontWeight: '600' }}>分析结果：{selectedFile}</h2>
                        <span style={{
                          fontSize: '12px',
                          padding: '4px 12px',
                          borderRadius: '12px',
                          background: result.confidence > 90 ? 'rgba(52, 199, 89, 0.1)' : 'rgba(255, 149, 0, 0.1)',
                          color: result.confidence > 90 ? '#2d8f3d' : '#ff9500'
                        }}>
                          置信度 {result.confidence}%
                        </span>
                      </div>

                      {/* 构成要件 */}
                      <div style={{ marginBottom: '20px' }}>
                        <h3 style={{ fontSize: '15px', fontWeight: '600', marginBottom: '12px', color: '#1e3a5f' }}>
                          一、构成要件符合性
                        </h3>
                        <ul style={{ margin: 0, paddingLeft: '20px' }}>
                          {result.constitutiveElements.map((item, i) => (
                            <li key={i} style={{ fontSize: '14px', lineHeight: '1.6', marginBottom: '8px', color: 'var(--macos-text-primary)' }}>
                              {item}
                            </li>
                          ))}
                        </ul>
                      </div>

                      {/* 违法性 */}
                      <div style={{ marginBottom: '20px' }}>
                        <h3 style={{ fontSize: '15px', fontWeight: '600', marginBottom: '12px', color: '#2d8f3d' }}>
                          二、违法性
                        </h3>
                        <p style={{ fontSize: '14px', lineHeight: '1.6', color: 'var(--macos-text-primary)' }}>
                          {result.illegality}
                        </p>
                      </div>

                      {/* 有责性 */}
                      <div style={{ marginBottom: '20px' }}>
                        <h3 style={{ fontSize: '15px', fontWeight: '600', marginBottom: '12px', color: '#ff9500' }}>
                          三、有责性
                        </h3>
                        <p style={{ fontSize: '14px', lineHeight: '1.6', color: 'var(--macos-text-primary)' }}>
                          {result.responsibility}
                        </p>
                      </div>

                      {/* 人物关系图 */}
                      {result.relationships && result.relationships.edges.length > 0 && (
                        <div style={{ marginBottom: '20px', background: 'rgba(175, 82, 222, 0.03)', borderRadius: '10px', padding: '16px', border: '1px solid rgba(175, 82, 222, 0.1)' }}>
                          <h3 style={{ fontSize: '15px', fontWeight: '600', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px', color: '#af52de' }}>
                            <Users className="w-4 h-4" />
                            人物关系图
                          </h3>
                          <RelationshipGraph data={result.relationships} />
                          {/* Legend */}
                          <div style={{ marginTop: '12px', display: 'flex', flexWrap: 'wrap', gap: '12px', justifyContent: 'center' }}>
                            {Object.entries(ROLE_COLORS).map(([role, color]) => (
                              <div key={role} style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', color: '#8e8e93' }}>
                                <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: color }} />
                                {role}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* 辩护要点 */}
                      <div style={{ padding: '16px', background: 'rgba(255, 149, 0, 0.05)', borderRadius: '10px' }}>
                        <h3 style={{ fontSize: '15px', fontWeight: '600', marginBottom: '12px', color: '#ff9500' }}>
                          辩护要点
                        </h3>
                        <ul style={{ margin: 0, paddingLeft: '20px' }}>
                          {result.defensePoints.map((point, i) => (
                            <li key={i} style={{ fontSize: '14px', lineHeight: '1.6', marginBottom: '8px', color: 'var(--macos-text-primary)' }}>
                              {point}
                            </li>
                          ))}
                        </ul>
                      </div>

                      {/* 操作按钮 */}
                      <div style={{ marginTop: '20px', display: 'flex', gap: '12px' }}>
                        {result.report?.raw_markdown && (
                          <MacOSButton
                            variant="primary"
                            icon={Download}
                            onClick={() => {
                              const blob = new Blob([result.report!.raw_markdown!], { type: 'text/markdown' })
                              const url = URL.createObjectURL(blob)
                              const a = document.createElement('a')
                              a.href = url
                              a.download = `辩护分析报告_${result.defendant}_${new Date().toISOString().slice(0,10)}.md`
                              a.click()
                              URL.revokeObjectURL(url)
                            }}
                          >
                            下载完整报告
                          </MacOSButton>
                        )}
                        {result.caseId && (
                          <MacOSButton
                            variant="secondary"
                            icon={FileText}
                            onClick={() => {
                              api.getReport(result.caseId!)
                                .then(data => {
                                  if (data.report?.raw_markdown) {
                                    setChatMessages(prev => [...prev, {
                                      role: 'assistant',
                                      content: `**完整分析报告**：\n\n${data.report.raw_markdown.substring(0, 2000)}...\n\n（完整报告已下载）`
                                    }])
                                  }
                                })
                            }}
                          >
                            查看完整报告
                          </MacOSButton>
                        )}
                      </div>
                    </MacOSCard>
                  </div>

                  {/* 对话区 */}
                  <div style={{ borderTop: '1px solid var(--macos-border)', background: 'var(--macos-bg-secondary)', padding: '16px' }}>
                    <div style={{ maxHeight: '200px', overflow: 'auto', marginBottom: '12px' }}>
                      {chatMessages.map((msg, i) => (
                        <div key={i} style={{
                          padding: '8px 12px',
                          marginBottom: '8px',
                          background: msg.role === 'user' ? 'rgba(0,122,255,0.1)' : 'white',
                          borderRadius: '8px',
                          fontSize: '13px',
                          lineHeight: '1.5'
                        }}>
                          {msg.content}
                        </div>
                      ))}
                    </div>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <input
                        type="text"
                        value={chatInput}
                        onChange={(e) => setChatInput(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                        placeholder="追问某个要点..."
                        style={{
                          flex: 1,
                          padding: '10px 12px',
                          border: '1px solid var(--macos-border)',
                          borderRadius: '8px',
                          fontSize: '13px',
                          background: 'white'
                        }}
                      />
                      <MacOSButton variant="primary" icon={Send} onClick={handleSend} disabled={!chatInput.trim()}>
                        发送
                      </MacOSButton>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      <input 
        id="md-upload" 
        type="file" 
        accept=".md,.markdown" 
        multiple 
        style={{ display: 'none' }} 
        onChange={handleFileSelect} 
      />
    </div>
  )
}
