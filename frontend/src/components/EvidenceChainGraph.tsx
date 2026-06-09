import React, { useState, useRef, useCallback, useEffect } from 'react'
import { EvidenceChainData, EvidenceChainNode } from '../api/stages'

interface Props {
  data: EvidenceChainData
  onNodeClick?: (node: EvidenceChainNode) => void
}

/**
 * 证据链可视化组件（自适应画布 + 可拖动缩放）
 *
 * 布局：
 * - 顶部：待证事实（三阶层）
 * - 中间：证据（按类型分列，自适应高度）
 * - 连线：证明关系
 *
 * 交互：
 * - 节点可拖拽移动
 * - 滚轮缩放画布
 * - 空白处拖拽平移画布
 * - 双击节点重置位置
 */
export function EvidenceChainGraph({ data, onNodeClick }: Props) {
  const { nodes, edges, total_evidence, total_relations, error, contradictions } = data as any

  // 拖拽状态
  const [dragging, setDragging] = useState<string | number | null>(null)
  const [positions, setPositions] = useState<Map<string | number, { x: number; y: number }>>(new Map())

  // 画布状态
  const [scale, setScale] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [isPanning, setIsPanning] = useState(false)
  const [panStart, setPanStart] = useState({ x: 0, y: 0 })

  // 容器尺寸（自适应）
  const [containerSize, setContainerSize] = useState({ width: 800, height: 400 })
  const containerRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  // 监听容器尺寸变化
  useEffect(() => {
    const updateSize = () => {
      if (containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect()
        setContainerSize({ width: rect.width, height: rect.height - 4 })
      }
    }
    updateSize()
    window.addEventListener('resize', updateSize)
    return () => window.removeEventListener('resize', updateSize)
  }, [])

  // 计算虚拟画布尺寸（基于证据数量）
  const virtualWidth = 1200  // 固定宽度，4列证据
  const virtualHeight = useCallback(() => {
    if (!nodes?.length) return 600
    const evidences = nodes.filter((n: any) => n.type !== 'fact' && n.category !== 'fact')
    const categories = ['indictment', 'confession', 'witness', 'objective']
    const maxPerColumn = Math.max(
      ...categories.map(cat =>
        evidences.filter((n: any) => n.category === cat).length
      ),
      1
    )
    // 顶部待证事实区域 + 证据区域 + 底部留白
    return Math.max(400, 100 + maxPerColumn * 45 + 60)
  }, [nodes])

  const vHeight = virtualHeight()

  // 初始化位置（基于虚拟画布）
  useEffect(() => {
    if (!nodes?.length) return

    const facts = nodes.filter((n: any) => n.type === 'fact' || n.category === 'fact')
    const evidences = nodes.filter((n: any) => n.type !== 'fact' && n.category !== 'fact')

    const newPositions = new Map<string | number, { x: number; y: number }>()

    // 待证事实位置（顶部居中排列）
    const factY = 45
    const factSpacing = (virtualWidth - 200) / Math.max(facts.length - 1, 1)
    facts.forEach((fact: any, i: number) => {
      const x = facts.length === 1 ? virtualWidth / 2 : 100 + i * factSpacing
      newPositions.set(fact.id, { x, y: factY })
    })

    // 证据位置（按类型分列）
    const categoryX = [150, 400, 650, 900]  // 4列的X坐标
    const evidenceStartY = 140

    const evidenceByCategory: Record<string, any[]> = {
      indictment: evidences.filter((n: any) => n.category === 'indictment'),
      confession: evidences.filter((n: any) => n.category === 'confession'),
      witness: evidences.filter((n: any) => n.category === 'witness'),
      objective: evidences.filter((n: any) => n.category === 'objective'),
    }

    Object.keys(evidenceByCategory).forEach((cat, colIndex) => {
      const items = evidenceByCategory[cat] || []
      items.forEach((ev: any, i: number) => {
        newPositions.set(ev.id, { x: categoryX[colIndex], y: evidenceStartY + i * 45 })
      })
    })

    setPositions(newPositions)

    // 初始缩放：让虚拟画布填满容器
    const initialScale = Math.min(containerSize.width / virtualWidth, containerSize.height / vHeight)
    setScale(Math.max(0.5, Math.min(1, initialScale)))
  }, [nodes, virtualWidth, vHeight, containerSize])

  // 滚轮缩放
  const handleWheel = useCallback((e: React.WheelEvent) => {
    if (dragging !== null) return
    e.preventDefault()
    const delta = e.deltaY > 0 ? 0.9 : 1.1
    setScale(s => Math.min(Math.max(s * delta, 0.3), 2))
  }, [dragging])

  // 画布平移
  const handlePanStart = useCallback((e: React.MouseEvent) => {
    if (dragging !== null) return
    setIsPanning(true)
    setPanStart({ x: e.clientX - pan.x, y: e.clientY - pan.y })
  }, [dragging, pan])

  const handlePanMove = useCallback((e: React.MouseEvent) => {
    if (!isPanning) return
    setPan({ x: e.clientX - panStart.x, y: e.clientY - panStart.y })
  }, [isPanning, panStart])

  const handlePanEnd = useCallback(() => setIsPanning(false), [])

  // 节点拖拽
  const handleNodeMouseDown = useCallback((id: string | number, e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragging(id)
  }, [])

  const handleNodeMouseMove = useCallback((e: React.MouseEvent) => {
    if (dragging === null || !svgRef.current) return

    const svg = svgRef.current.getBoundingClientRect()
    const mouseX = (e.clientX - svg.left - pan.x) / scale
    const mouseY = (e.clientY - svg.top - pan.y) / scale

    const clampedX = Math.max(80, Math.min(virtualWidth - 80, mouseX))
    const clampedY = Math.max(25, Math.min(vHeight - 20, mouseY))

    setPositions(prev => {
      const next = new Map(prev)
      next.set(dragging, { x: clampedX, y: clampedY })
      return next
    })
  }, [dragging, scale, pan, virtualWidth, vHeight])

  const handleNodeMouseUp = useCallback(() => setDragging(null), [])

  // 双击重置位置
  const handleDoubleClick = useCallback((id: string | number) => {
    const facts = nodes.filter((n: any) => n.type === 'fact' || n.category === 'fact')
    const evidences = nodes.filter((n: any) => n.type !== 'fact' && n.category !== 'fact')
    const categoryX = [150, 400, 650, 900]

    const factIndex = facts.findIndex((f: any) => f.id === id)
    if (factIndex >= 0) {
      const factSpacing = (virtualWidth - 200) / Math.max(facts.length - 1, 1)
      const x = facts.length === 1 ? virtualWidth / 2 : 100 + factIndex * factSpacing
      setPositions(prev => { const n = new Map(prev); n.set(id, { x, y: 45 }); return n })
      return
    }

    const categories = ['indictment', 'confession', 'witness', 'objective']
    for (let colIndex = 0; colIndex < categories.length; colIndex++) {
      const items = evidences.filter((n: any) => n.category === categories[colIndex])
      const idx = items.findIndex((ev: any) => ev.id === id)
      if (idx >= 0) {
        setPositions(prev => { const n = new Map(prev); n.set(id, { x: categoryX[colIndex], y: 140 + idx * 45 }); return n })
        return
      }
    }
  }, [nodes, virtualWidth])

  // 重置视图
  const handleResetView = useCallback(() => {
    setScale(1)
    setPan({ x: 0, y: 0 })
  }, [])

  if (error) {
    return <div style={emptyStyle}>{error}</div>
  }

  if (!nodes?.length) {
    return <div style={emptyStyle}>无证据数据</div>
  }

  const facts = nodes.filter((n: any) => n.type === 'fact' || n.category === 'fact')
  const evidences = nodes.filter((n: any) => n.type !== 'fact' && n.category !== 'fact')

  const evidenceByCategory = {
    indictment: evidences.filter((n: any) => n.category === 'indictment'),
    confession: evidences.filter((n: any) => n.category === 'confession'),
    witness: evidences.filter((n: any) => n.category === 'witness'),
    objective: evidences.filter((n: any) => n.category === 'objective'),
  }

  const categoryConfigs = [
    { key: 'indictment', color: '#dc2626', label: '指控文书' },
    { key: 'confession', color: '#2563eb', label: '供述' },
    { key: 'witness', color: '#16a34a', label: '证言' },
    { key: 'objective', color: '#9333ea', label: '客观证据' },
  ]

  const getEdgeStyle = (type: string) => {
    switch (type) {
      case 'prove': return { color: '#dc2626', width: 2, dash: '' }
      case 'corroborate': return { color: '#16a34a', width: 1.5, dash: '' }
      case 'support': return { color: '#9333ea', width: 1.5, dash: '4,2' }
      default: return { color: '#6b7280', width: 1, dash: '' }
    }
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 工具栏 */}
      <div style={toolbarStyle}>
        <span style={{ fontSize: 12, color: '#6b7280' }}>
          待证事实 <b>{facts.length}</b> | 证据 <b>{total_evidence}</b> | 证明关系 <b>{total_relations}</b>
        </span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <button onClick={() => setScale(s => Math.max(s - 0.1, 0.3))} style={btnStyle}>−</button>
          <span style={{ fontSize: 12, width: 40, textAlign: 'center' }}>{Math.round(scale * 100)}%</span>
          <button onClick={() => setScale(s => Math.min(s + 0.1, 2))} style={btnStyle}>+</button>
          <button onClick={handleResetView} style={{ ...btnStyle, padding: '2px 10px' }}>重置</button>
        </div>
      </div>

      {/* 提示 */}
      <div style={{ fontSize: 11, color: '#9ca3af', marginBottom: 4 }}>
        拖拽节点移动 | 空白处拖拽平移 | 滚轮缩放 | 双击重置位置
      </div>

      {/* SVG 容器 */}
      <div ref={containerRef} style={{ flex: 1, overflow: 'hidden', border: '1px solid #e5e7eb', borderRadius: 6, background: '#fafafa' }}>
        <svg
          ref={svgRef}
          width={containerSize.width}
          height={containerSize.height}
          style={{ cursor: isPanning ? 'grabbing' : 'grab' }}
          onWheel={handleWheel}
          onMouseDown={handlePanStart}
          onMouseMove={(e) => {
            if (dragging !== null) handleNodeMouseMove(e)
            else if (isPanning) handlePanMove(e)
          }}
          onMouseUp={() => { handleNodeMouseUp(); handlePanEnd() }}
          onMouseLeave={() => { handleNodeMouseUp(); handlePanEnd() }}
        >
          <g transform={`translate(${pan.x}, ${pan.y}) scale(${scale})`}>
            {/* 分隔线 */}
            <line x1={30} y1={100} x2={virtualWidth - 30} y2={100} stroke="#e5e7eb" strokeWidth={1} strokeDasharray="4,4" />
            <text x={virtualWidth - 80} y={95} fill="#9ca3af" fontSize="10">证据区域</text>

            {/* 边 */}
            {edges.map((edge: any, i: number) => {
              const src = positions.get(edge.source)
              const tgt = positions.get(edge.target)
              if (!src || !tgt) return null
              const style = getEdgeStyle(edge.type)
              const toFact = edge.target?.startsWith?.('fact_')
              return (
                <line
                  key={i}
                  x1={src.x}
                  y1={src.y - (toFact ? 18 : 0)}
                  x2={tgt.x}
                  y2={tgt.y + (toFact ? 25 : 0)}
                  stroke={style.color}
                  strokeWidth={style.width}
                  strokeDasharray={style.dash}
                  opacity={0.5}
                />
              )
            })}

            {/* 待证事实 */}
            <text x={20} y={45} fill="#374151" fontSize="11" fontWeight="600" dominantBaseline="middle">待证事实</text>
            {facts.map((fact: any) => {
              const pos = positions.get(fact.id)
              if (!pos) return null
              const isActive = dragging === fact.id
              return (
                <g
                  key={fact.id}
                  transform={`translate(${pos.x}, ${pos.y})`}
                  onMouseDown={(e) => handleNodeMouseDown(fact.id, e)}
                  onDoubleClick={() => handleDoubleClick(fact.id)}
                  style={{ cursor: isActive ? 'grabbing' : 'grab' }}
                >
                  <rect x={-65} y={-22} width={130} height={44} rx={8} fill="#1f2937"
                        stroke={isActive ? '#3b82f6' : 'none'} strokeWidth={isActive ? 2 : 0} />
                  <text x={0} y={-4} textAnchor="middle" fill="#fff" fontSize="11" fontWeight="600">{fact.name}</text>
                  <text x={0} y={10} textAnchor="middle" fill="#9ca3af" fontSize="9">{fact.description?.slice(0, 14)}</text>
                </g>
              )
            })}

            {/* 类型标签 */}
            {categoryConfigs.map((cfg, i) => {
              const items = evidenceByCategory[cfg.key as keyof typeof evidenceByCategory] || []
              if (items.length === 0) return null
              return (
                <text key={cfg.key} x={[150, 400, 650, 900][i]} y={125} textAnchor="middle"
                      fill={cfg.color} fontSize="11" fontWeight="600">
                  {cfg.label} ({items.length})
                </text>
              )
            })}

            {/* 证据节点 */}
            {evidences.map((ev: any) => {
              const pos = positions.get(ev.id)
              if (!pos) return null
              const color = ev.color || '#6b7280'
              const name = ev.name.length > 15 ? ev.name.slice(0, 15) + '...' : ev.name
              const isActive = dragging === ev.id
              return (
                <g
                  key={ev.id}
                  transform={`translate(${pos.x}, ${pos.y})`}
                  onMouseDown={(e) => handleNodeMouseDown(ev.id, e)}
                  onMouseUp={() => { if (!dragging) onNodeClick?.(ev) }}
                  onDoubleClick={() => handleDoubleClick(ev.id)}
                  style={{ cursor: isActive ? 'grabbing' : 'grab' }}
                >
                  <rect x={-75} y={-16} width={150} height={32} rx={6} fill={color} opacity={0.9}
                        stroke={isActive ? '#fff' : 'none'} strokeWidth={isActive ? 2 : 0} />
                  <text x={0} y={0} textAnchor="middle" dominantBaseline="middle" fill="#fff" fontSize="10">{name}</text>
                  <title>{ev.name}</title>
                </g>
              )
            })}
          </g>
        </svg>
      </div>

      {/* 图例 */}
      <div style={{ marginTop: 4, display: 'flex', gap: 12, fontSize: 10, color: '#6b7280' }}>
        <span><span style={{ width: 16, height: 2, background: '#dc2626', display: 'inline-block' }} /> 证明</span>
        <span><span style={{ width: 16, height: 2, background: '#16a34a', display: 'inline-block' }} /> 印证</span>
        <span><span style={{ width: 16, height: 2, background: '#9333ea', borderStyle: 'dashed', display: 'inline-block' }} /> 佐证</span>
      </div>

      {/* 矛盾问题 */}
      {contradictions?.length > 0 && (
        <div style={{ marginTop: 6, padding: '6px 10px', background: '#fef2f2', borderRadius: 4, border: '1px solid #fecaca', maxHeight: 60, overflow: 'auto' }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#991b1b', marginBottom: 2 }}>
            ⚠️ 证据问题 ({contradictions.length})
          </div>
          {contradictions.slice(0, 2).map((c: any, i: number) => (
            <div key={i} style={{ fontSize: 10, color: '#7f1d1d' }}>
              • {c.name?.slice(0, 20)}: {c.issues?.[0]?.slice(0, 30)}
            </div>
          ))}
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
  width: 24,
  height: 24,
  border: '1px solid #e5e7eb',
  borderRadius: 4,
  background: '#fff',
  cursor: 'pointer',
  fontSize: 14,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
}