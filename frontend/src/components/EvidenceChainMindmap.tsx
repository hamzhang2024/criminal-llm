import React, { useState, useCallback, useEffect } from 'react'
import { EvidenceChainData, EvidenceChainNode } from '../api/stages'

interface Props {
  data: EvidenceChainData
  onNodeClick?: (node: EvidenceChainNode) => void
}

interface TreeNode {
  id: string | number
  name: string
  type: 'root' | 'accusation' | 'fact' | 'category' | 'evidence'
  color?: string
  description?: string
  evidence_count?: number
  strength?: string
  children?: TreeNode[]
  collapsed?: boolean
  x?: number
  y?: number
  width?: number
  height?: number
  originalNode?: any
  summary?: string  // 证据摘要
  factId?: string   // 该证据所属的待证事实ID（用于显示相关内容）
}

/**
 * 证据链思维导图组件
 *
 * 布局：树形结构，可折叠展开
 * - 根节点：指控事实（从起诉书提取）
 * - 第一层：待证事实（主体、主观、行为、结果、情节）
 * - 第二层：证据类别
 * - 第三层：具体证据
 */
export function EvidenceChainMindmap({ data, onNodeClick }: Props) {
  const { nodes, edges, accusation, weak_points, summary, error } = data as any

  // 折叠状态
  const [collapsedNodes, setCollapsedNodes] = useState<Set<string | number>>(new Set())

  // 拖拽状态
  const [scale, setScale] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [isPanning, setIsPanning] = useState(false)
  const [panStart, setPanStart] = useState({ x: 0, y: 0 })

  // 构建树形结构
  const buildTree = useCallback((): TreeNode => {
    if (!nodes?.length) {
      return { id: 'root', name: '证据链', type: 'root', children: [] }
    }

    const facts = nodes.filter((n: any) => n.type === 'fact')
    const evidences = nodes.filter((n: any) => n.type !== 'fact' && n.type !== 'accusation')

    // 证据类型配置
    const categoryConfigs = [
      { key: 'indictment', label: '指控文书', color: '#dc2626' },
      { key: 'confession', label: '供述', color: '#2563eb' },
      { key: 'witness', label: '证言', color: '#16a34a' },
      { key: 'victim', label: '被害人陈述', color: '#f59e0b' },
      { key: 'documentary', label: '书证', color: '#9333ea' },
      { key: 'physical', label: '物证', color: '#78716c' },
      { key: 'expert', label: '鉴定', color: '#ea580c' },
      { key: 'inspection', label: '勘验', color: '#0891b2' },
      { key: 'electronic', label: '电子数据', color: '#6366f1' },
      { key: 'audiovisual', label: '视听资料', color: '#14b8a6' },
      { key: 'procedural', label: '程序文书', color: '#6b7280' },
      { key: 'other', label: '其他', color: '#6b7280' },
    ]

    // 为每个待证事实构建子树
    const factNodes: TreeNode[] = facts.map((fact: any) => {
      // 找出与该事实相关的证据（通过边关系）
      const relatedEdges = edges.filter((e: any) => e.target === fact.id && e.type === 'prove')
      const relatedEvidenceIds = new Set(relatedEdges.map((e: any) => e.source))

      // 按类别组织相关证据
      const categoryNodes: TreeNode[] = categoryConfigs.map(cfg => {
        const categoryEvidences = evidences.filter((ev: any) =>
          ev.category === cfg.key && relatedEvidenceIds.has(ev.id)
        )

        if (categoryEvidences.length === 0) return null

        return {
          id: `${fact.id}_${cfg.key}`,
          name: `${cfg.label} (${categoryEvidences.length})`,
          type: 'category' as const,
          color: cfg.color,
          collapsed: collapsedNodes.has(`${fact.id}_${cfg.key}`),
          children: categoryEvidences.map((ev: any) => {
            // 获取该证据针对当前待证事实的相关内容
            const provesDetails = ev.proves_details || {}
            const factRelatedContent = provesDetails[fact.id] || []
            const relatedSummary = factRelatedContent.length > 0
              ? factRelatedContent.join('；')
              : ev.summary || ''

            return {
              id: ev.id,
              name: ev.name,
              type: 'evidence' as const,
              color: cfg.color,
              originalNode: ev,
              summary: relatedSummary.slice(0, 100),  // 与该待证事实相关的内容
              factId: fact.id,  // 标记所属待证事实
            }
          }),
        }
      }).filter(Boolean) as TreeNode[]

      // 节点颜色根据证据强度
      const strength = fact.strength || 'weak'
      let nodeColor = '#6b7280'
      if (strength === 'strong') nodeColor = '#16a34a'
      else if (strength === 'medium') nodeColor = '#ca8a04'
      else nodeColor = '#dc2626'

      return {
        id: fact.id,
        name: fact.name,
        type: 'fact' as const,
        color: nodeColor,
        description: fact.description,
        evidence_count: fact.evidence_count,
        strength: strength,
        children: categoryNodes,
        collapsed: collapsedNodes.has(fact.id),
      }
    })

    // 根节点：指控事实
    const rootName = accusation?.name || '案件证据链'
    const rootDescription = accusation?.description || ''

    // 未关联到任何事实的证据
    const assignedIds = new Set(edges.filter((e: any) => e.type === 'prove').map((e: any) => e.source))
    const unassigned = evidences.filter((ev: any) => !assignedIds.has(ev.id))
    const unassignedCategoryNodes: TreeNode[] = categoryConfigs.map(cfg => {
      const catUnassigned = unassigned.filter((ev: any) => ev.category === cfg.key)
      if (catUnassigned.length === 0) return null
      return {
        id: `unassigned_${cfg.key}`,
        name: `${cfg.label} (${catUnassigned.length})`,
        type: 'category' as const,
        color: cfg.color,
        children: catUnassigned.map((ev: any) => ({
          id: ev.id,
          name: ev.name,
          type: 'evidence' as const,
          color: cfg.color,
          originalNode: ev,
          summary: ev.summary || '',  // 传递摘要
        })),
      }
    }).filter(Boolean) as TreeNode[]

    return {
      id: 'root',
      name: rootName,
      type: 'root' as const,
      description: rootDescription,
      children: [
        ...factNodes,
        ...(unassignedCategoryNodes.length > 0 ? [{
          id: 'unassigned',
          name: '其他证据',
          type: 'fact' as const,
          color: '#6b7280',
          children: unassignedCategoryNodes,
        }] : []),
      ],
    }
  }, [nodes, edges, accusation, collapsedNodes])

  // 计算布局
  const calculateLayout = useCallback((tree: TreeNode) => {
    const nodeWidth = 180  // 加宽节点
    const nodeHeight = 32
    const factNodeHeight = 72  // 待证事实节点更高，显示描述
    const evidenceNodeHeight = 48  // 证据节点显示摘要
    const levelGapX = 220  // 加大层级间距
    const nodeGapY = 55  // 加大节点间距

    const positions = new Map<string | number, { x: number; y: number; width: number; height: number }>()

    const layoutSubtree = (node: TreeNode, depth: number, startY: number): number => {
      const x = 40 + depth * levelGapX
      // 根据节点类型设置高度
      let height = nodeHeight
      if (node.type === 'fact') height = factNodeHeight
      else if (node.type === 'evidence') height = evidenceNodeHeight

      positions.set(node.id, { x, y: startY, width: nodeWidth, height: height })

      if (node.collapsed || !node.children?.length) {
        return startY + nodeGapY
      }

      let currentY = startY
      node.children.forEach(child => {
        currentY = layoutSubtree(child, depth + 1, currentY)
      })

      // 居中父节点
      const firstChild = node.children[0]
      const lastChild = node.children[node.children.length - 1]
      const firstPos = positions.get(firstChild.id)
      const lastPos = positions.get(lastChild.id)
      if (firstPos && lastPos) {
        const centerY = (firstPos.y + lastPos.y) / 2
        positions.set(node.id, { x, y: centerY, width: nodeWidth, height: height })
      }

      return currentY
    }

    const calculateSubtreeHeight = (node: TreeNode): number => {
      if (!node.children?.length || node.collapsed) return nodeGapY
      return node.children.reduce((sum, child) => sum + calculateSubtreeHeight(child), 0)
    }

    layoutSubtree(tree, 0, 40)
    return positions
  }, [])

  const tree = buildTree()
  const positions = calculateLayout(tree)

  // 计算画布尺寸
  const calculateCanvasSize = () => {
    let maxX = 0, maxY = 0
    positions.forEach(pos => {
      maxX = Math.max(maxX, pos.x + pos.width)
      maxY = Math.max(maxY, pos.y + pos.height)
    })
    return { width: Math.max(800, maxX + 100), height: Math.max(400, maxY + 100) }
  }

  const { width, height } = calculateCanvasSize()

  // 切换折叠
  const toggleCollapse = useCallback((id: string | number) => {
    setCollapsedNodes(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  // 全部展开/折叠
  const expandAll = useCallback(() => setCollapsedNodes(new Set()), [])
  const collapseAll = useCallback(() => {
    const allIds = new Set<string | number>()
    const collect = (node: TreeNode) => {
      if (node.type === 'fact' || node.type === 'category') {
        allIds.add(node.id)
      }
      node.children?.forEach(collect)
    }
    collect(tree)
    setCollapsedNodes(allIds)
  }, [tree])

  // 滚轮缩放
  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? 0.9 : 1.1
    setScale(s => Math.min(Math.max(s * delta, 0.3), 2))
  }, [])

  // 平移
  const handlePanStart = useCallback((e: React.MouseEvent) => {
    setIsPanning(true)
    setPanStart({ x: e.clientX - pan.x, y: e.clientY - pan.y })
  }, [pan])

  const handlePanMove = useCallback((e: React.MouseEvent) => {
    if (!isPanning) return
    setPan({ x: e.clientX - panStart.x, y: e.clientY - panStart.y })
  }, [isPanning, panStart])

  const handlePanEnd = useCallback(() => setIsPanning(false), [])

  // 重置
  const handleReset = useCallback(() => {
    setScale(1)
    setPan({ x: 0, y: 0 })
  }, [])

  if (error) {
    return <div style={emptyStyle}>{error}</div>
  }

  if (!nodes?.length) {
    return <div style={emptyStyle}>无证据数据</div>
  }

  // 渲染节点和连线
  const renderTree = (node: TreeNode): React.ReactNode => {
    const pos = positions.get(node.id)
    if (!pos) return null

    const isCollapsed = collapsedNodes.has(node.id)
    const hasChildren = node.children && node.children.length > 0

    // 节点样式
    let bg = '#f3f4f6'
    let textColor = '#374151'
    let borderColor = '#e5e7eb'

    if (node.type === 'root') {
      bg = '#1e3a5f'
      textColor = '#fff'
    } else if (node.type === 'fact') {
      bg = node.color || '#1f2937'
      textColor = '#fff'
    } else if (node.type === 'category') {
      bg = node.color || '#3b82f6'
      textColor = '#fff'
    } else if (node.type === 'evidence') {
      bg = '#fff'
      borderColor = node.color || '#6b7280'
    }

    // 显示证据数量
    let displayName = node.name
    if (node.type === 'fact' && node.evidence_count !== undefined) {
      displayName = `${node.name} (${node.evidence_count})`
    }

    return (
      <React.Fragment key={node.id}>
        {/* 连线到子节点 */}
        {!isCollapsed && node.children?.map(child => {
          const childPos = positions.get(child.id)
          if (!childPos) return null
          return (
            <path
              key={`edge-${node.id}-${child.id}`}
              d={`M ${pos.x + pos.width} ${pos.y + pos.height / 2}
                  L ${pos.x + pos.width + 20} ${pos.y + pos.height / 2}
                  L ${pos.x + pos.width + 20} ${childPos.y + childPos.height / 2}
                  L ${childPos.x} ${childPos.y + childPos.height / 2}`}
              fill="none"
              stroke="#d1d5db"
              strokeWidth={1.5}
            />
          )
        })}

        {/* 节点 */}
        <g
          transform={`translate(${pos.x}, ${pos.y})`}
          onClick={() => {
            if (node.type === 'fact' || node.type === 'category') {
              toggleCollapse(node.id)
            } else if (node.type === 'evidence' && node.originalNode) {
              onNodeClick?.(node.originalNode)
            }
          }}
          style={{ cursor: 'pointer' }}
        >
          <rect
            x={0}
            y={0}
            width={pos.width}
            height={pos.height}
            rx={node.type === 'evidence' ? 4 : 6}
            fill={bg}
            stroke={borderColor}
            strokeWidth={node.type === 'evidence' ? 2 : 0}
          />
          {/* 节点名称 */}
          <text
            x={node.type === 'root' ? pos.width / 2 : hasChildren ? 18 : pos.width / 2}
            y={node.type === 'fact' ? 16 : node.type === 'evidence' ? 14 : pos.height / 2}
            textAnchor={node.type === 'root' ? 'middle' : hasChildren ? 'start' : 'middle'}
            dominantBaseline="middle"
            fill={textColor}
            fontSize={node.type === 'root' ? 13 : 11}
            fontWeight={node.type === 'evidence' ? 'normal' : '600'}
          >
            {displayName.length > 14 ? displayName.slice(0, 14) + '...' : displayName}
          </text>
          {/* 待证事实节点：显示具体描述（两行） */}
          {node.type === 'fact' && node.description && (
            <>
              <text
                x={10}
                y={32}
                textAnchor="start"
                dominantBaseline="middle"
                fill="rgba(255,255,255,0.9)"
                fontSize={9}
              >
                {node.description.length > 24 ? node.description.slice(0, 24) + '...' : node.description}
              </text>
              {/* 第二行描述（如果有） */}
              {node.description.length > 24 && (
                <text
                  x={10}
                  y={46}
                  textAnchor="start"
                  dominantBaseline="middle"
                  fill="rgba(255,255,255,0.75)"
                  fontSize={9}
                >
                  {node.description.length > 48 ? node.description.slice(24, 48) + '...' : node.description.slice(24)}
                </text>
              )}
            </>
          )}
          {/* 证据节点：显示摘要 */}
          {node.type === 'evidence' && node.summary && (
            <text
              x={10}
              y={32}
              textAnchor="start"
              dominantBaseline="middle"
              fill="#6b7280"
              fontSize={8}
            >
              {node.summary.length > 28 ? node.summary.slice(0, 28) + '...' : node.summary}
            </text>
          )}
          <title>{node.name}{node.description ? `\n${node.description}` : ''}{node.summary ? `\n\n摘要：${node.summary}` : ''}</title>

          {/* 展开/折叠图标 */}
          {hasChildren && (
            <text
              x={8}
              y={node.type === 'fact' ? 62 : pos.height / 2}
              textAnchor="middle"
              dominantBaseline="middle"
              fill={textColor}
              fontSize={12}
            >
              {isCollapsed ? '▶' : '▼'}
            </text>
          )}
        </g>

        {/* 递归渲染子节点 */}
        {!isCollapsed && node.children?.map(child => renderTree(child))}
      </React.Fragment>
    )
  }

  // 统计数据
  const totalEvidence = summary?.total_evidence || data.total_evidence || 0
  const totalRelations = summary?.total_relations || data.total_relations || 0

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 工具栏 */}
      <div style={toolbarStyle}>
        <span style={{ fontSize: 12, color: '#6b7280' }}>
          证据 <b>{totalEvidence}</b> 份 | 证明关系 <b>{totalRelations}</b> 个
        </span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <button onClick={expandAll} style={btnStyle}>全部展开</button>
          <button onClick={collapseAll} style={btnStyle}>全部折叠</button>
          <button onClick={() => setScale(s => Math.max(s - 0.1, 0.3))} style={btnStyle}>−</button>
          <span style={{ fontSize: 12, width: 40, textAlign: 'center' }}>{Math.round(scale * 100)}%</span>
          <button onClick={() => setScale(s => Math.min(s + 0.1, 2))} style={btnStyle}>+</button>
          <button onClick={handleReset} style={btnStyle}>重置</button>
        </div>
      </div>

      {/* 提示 */}
      <div style={{ fontSize: 11, color: '#9ca3af', marginBottom: 4 }}>
        点击节点展开/折叠 | 滚轮缩放 | 拖拽平移
      </div>

      {/* SVG */}
      <div style={{ flex: 1, overflow: 'hidden', border: '1px solid #e5e7eb', borderRadius: 6, background: '#fafafa' }}>
        <svg
          width="100%"
          height="100%"
          style={{ cursor: isPanning ? 'grabbing' : 'grab' }}
          onWheel={handleWheel}
          onMouseDown={handlePanStart}
          onMouseMove={handlePanMove}
          onMouseUp={handlePanEnd}
          onMouseLeave={handlePanEnd}
        >
          <g transform={`translate(${pan.x}, ${pan.y}) scale(${scale})`}>
            {renderTree(tree)}
          </g>
        </svg>
      </div>

      {/* 图例 */}
      <div style={{ marginTop: 4, display: 'flex', gap: 12, fontSize: 10, color: '#6b7280' }}>
        <span><span style={{ width: 12, height: 12, background: '#16a34a', borderRadius: 2, display: 'inline-block' }} /> 证据充分</span>
        <span><span style={{ width: 12, height: 12, background: '#ca8a04', borderRadius: 2, display: 'inline-block' }} /> 证据一般</span>
        <span><span style={{ width: 12, height: 12, background: '#dc2626', borderRadius: 2, display: 'inline-block' }} /> 证据薄弱</span>
        <span><span style={{ width: 12, height: 12, background: '#2563eb', borderRadius: 2, display: 'inline-block' }} /> 供述</span>
        <span><span style={{ width: 12, height: 12, background: '#16a34a', borderRadius: 2, display: 'inline-block' }} /> 证言</span>
        <span><span style={{ width: 12, height: 12, background: '#9333ea', borderRadius: 2, display: 'inline-block' }} /> 书证</span>
      </div>

      {/* 证据链薄弱环节 */}
      {weak_points?.length > 0 && (
        <div style={{ marginTop: 6, padding: '6px 10px', background: '#fef2f2', borderRadius: 4, border: '1px solid #fecaca', maxHeight: 80, overflow: 'auto' }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#991b1b' }}>
            ⚠️ 证据链薄弱环节 ({weak_points.length})
          </div>
          {weak_points.map((wp: any, i: number) => (
            <div key={i} style={{ fontSize: 10, color: '#7f1d1d' }}>
              • {wp.fact_name}：{wp.issue}
            </div>
          ))}
        </div>
      )}

      {/* 强弱链总结 */}
      {summary && (summary.strong_chains?.length > 0 || summary.weak_chains?.length > 0) && (
        <div style={{ marginTop: 4, fontSize: 10, color: '#6b7280', display: 'flex', gap: 16 }}>
          {summary.strong_chains?.length > 0 && (
            <span style={{ color: '#16a34a' }}>✓ 强链：{summary.strong_chains.join('、')}</span>
          )}
          {summary.weak_chains?.length > 0 && (
            <span style={{ color: '#dc2626' }}>✗ 弱链：{summary.weak_chains.join('、')}</span>
          )}
        </div>
      )}
    </div>
  )
}

const emptyStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  height: '100%',
  color: '#666',
  fontSize: 14,
}

const toolbarStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: 4,
  padding: '6px 10px',
  background: '#fff',
  borderRadius: 4,
  border: '1px solid #e5e7eb',
}

const btnStyle: React.CSSProperties = {
  padding: '2px 8px',
  border: '1px solid #e5e7eb',
  borderRadius: 4,
  background: '#fff',
  cursor: 'pointer',
  fontSize: 11,
  color: '#374151',
}
