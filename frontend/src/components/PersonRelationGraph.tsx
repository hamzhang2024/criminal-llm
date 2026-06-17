/**
 * PersonRelationGraph - 纯 SVG + CSS 人物关系图组件
 *
 * 功能：
 * - 节点是人物，边是关系类型
 * - 按角色分组（被告/被害人/证人等）
 * - 拖拽、缩放、悬停高亮
 * - 点击显示详情
 *
 * 零依赖，纯手写交互
 */
import { useState, useRef, useEffect, useCallback } from 'react'

// 人物节点
export interface PersonNode {
  id: string
  name: string
  role: 'defendant' | 'co defendant' | 'victim' | 'witness' | 'other'
  description?: string
  evidenceRefs?: string[]
}

// 关系边
export interface RelationEdge {
  source: string
  target: string
  type: 'cooperation' | 'fraud' | 'friend' | 'family' | 'business' | 'debt' | 'other'
  label: string
}

// 关系图数据
export interface RelationGraphData {
  nodes: PersonNode[]
  edges: RelationEdge[]
  error?: string
}

interface Props {
  data: RelationGraphData
  onNodeClick?: (node: PersonNode) => void
}

// 角色颜色映射
const ROLE_COLORS: Record<string, string> = {
  defendant: '#ef4444',      // 红 - 被告人
  'co defendant': '#f97316', // 橙 - 同案犯
  victim: '#3b82f6',         // 蓝 - 被害人
  witness: '#10b981',        // 绿 - 证人
  other: '#6b7280',          // 灰 - 其他
}

// 角色中文映射
const ROLE_LABELS: Record<string, string> = {
  defendant: '被告人',
  'co defendant': '同案犯',
  victim: '被害人',
  witness: '证人',
  other: '其他',
}

// 关系类型颜色映射
const RELATION_COLORS: Record<string, string> = {
  cooperation: '#3b82f6',  // 蓝 - 合作
  fraud: '#ef4444',        // 红 - 诈骗
  friend: '#10b981',       // 绿 - 朋友
  family: '#8b5cf6',       // 紫 - 家人
  business: '#f59e0b',     // 橙 - 商业
  debt: '#dc2626',         // 深红 - 债务
  other: '#6b7280',        // 灰 - 其他
}

export function PersonRelationGraph({ data, onNodeClick }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  // 画布状态
  const [scale, setScale] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })

  // 拖拽状态
  const [dragging, setDragging] = useState<string | null>(null)
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 })

  // 画布平移
  const [panning, setPanning] = useState(false)
  const [panStart, setPanStart] = useState({ x: 0, y: 0 })

  // 高亮状态
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)
  const [selectedNode, setSelectedNode] = useState<string | null>(null)

  // 节点位置
  const [nodePositions, setNodePositions] = useState<Map<string, { x: number; y: number }>>(new Map())

  // 计算初始布局（按角色分组排列）+ 自动适配 viewBox
  const [viewBox, setViewBox] = useState('0 0 1000 700')

  useEffect(() => {
    if (!data.nodes.length) return

    const positions = new Map<string, { x: number; y: number }>()
    const centerX = 500
    const centerY = 350

    // 按角色分组
    const roleGroups = new Map<string, PersonNode[]>()
    for (const node of data.nodes) {
      const role = node.role || 'other'
      if (!roleGroups.has(role)) {
        roleGroups.set(role, [])
      }
      roleGroups.get(role)!.push(node)
    }

    // 每个角色组占一个扇形区域
    const roleOrder = ['defendant', 'co defendant', 'victim', 'witness', 'other']
    const presentRoles = roleOrder.filter(r => roleGroups.has(r))
    const groupCount = presentRoles.length

    presentRoles.forEach((role, groupIndex) => {
      const nodes = roleGroups.get(role) || []
      const isCenter = role === 'defendant'
      const groupCenterX = isCenter ? centerX : centerX + Math.cos((groupIndex / groupCount) * 2 * Math.PI - Math.PI / 2) * 250
      const groupCenterY = isCenter ? centerY : centerY + Math.sin((groupIndex / groupCount) * 2 * Math.PI - Math.PI / 2) * 250

      nodes.forEach((node, i) => {
        if (isCenter && nodes.length === 1) {
          positions.set(node.id, { x: groupCenterX, y: groupCenterY })
        } else {
          const nodeAngle = (i / nodes.length) * 2 * Math.PI
          const nodeRadius = isCenter ? 50 : 40 + nodes.length * 5
          positions.set(node.id, {
            x: groupCenterX + Math.cos(nodeAngle) * nodeRadius,
            y: groupCenterY + Math.sin(nodeAngle) * nodeRadius,
          })
        }
      })
    })

    setNodePositions(positions)

    // 计算 viewBox 使得所有节点都在可视范围内
    if (positions.size > 0) {
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
      for (const pos of positions.values()) {
        minX = Math.min(minX, pos.x)
        minY = Math.min(minY, pos.y)
        maxX = Math.max(maxX, pos.x)
        maxY = Math.max(maxY, pos.y)
      }
      const padding = 150
      const vbX = minX - padding
      const vbY = minY - padding
      const vbW = maxX - minX + padding * 2
      const vbH = maxY - minY + padding * 2
      setViewBox(`${vbX} ${vbY} ${vbW} ${vbH}`)
    }
  }, [data.nodes])

  // 滚轮缩放
  const handleWheel = useCallback((e: WheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? 0.9 : 1.1
    setScale(s => Math.min(Math.max(s * delta, 0.3), 3))
  }, [])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    container.addEventListener('wheel', handleWheel, { passive: false })
    return () => container.removeEventListener('wheel', handleWheel)
  }, [handleWheel])

  // 开始拖拽节点
  const handleNodeMouseDown = (nodeId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    const pos = nodePositions.get(nodeId)
    if (!pos) return

    setDragging(nodeId)
    setDragOffset({
      x: e.clientX / scale - pos.x - offset.x,
      y: e.clientY / scale - pos.y - offset.y,
    })
  }

  // 开始画布平移
  const handleCanvasMouseDown = (e: React.MouseEvent) => {
    if (e.target === svgRef.current || (e.target as Element).classList?.contains('canvas-bg')) {
      setPanning(true)
      setPanStart({ x: e.clientX - offset.x * scale, y: e.clientY - offset.y * scale })
    }
  }

  // 鼠标移动
  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (dragging !== null) {
      const newX = e.clientX / scale - dragOffset.x - offset.x
      const newY = e.clientY / scale - dragOffset.y - offset.y
      setNodePositions(prev => {
        const newPositions = new Map(prev)
        newPositions.set(dragging, { x: newX, y: newY })
        return newPositions
      })
    } else if (panning) {
      setOffset({
        x: (e.clientX - panStart.x) / scale,
        y: (e.clientY - panStart.y) / scale,
      })
    }
  }, [dragging, dragOffset, panning, panStart, scale, offset])

  // 鼠标释放
  const handleMouseUp = useCallback(() => {
    setDragging(null)
    setPanning(false)
  }, [])

  useEffect(() => {
    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [handleMouseMove, handleMouseUp])

  // 点击节点
  const handleNodeClick = (node: PersonNode, e: React.MouseEvent) => {
    e.stopPropagation()
    setSelectedNode(node.id)
    if (onNodeClick) {
      onNodeClick(node)
    }
  }

  // 重置视图
  const handleReset = () => {
    setScale(1)
    setOffset({ x: 0, y: 0 })
    setSelectedNode(null)
    setNodePositions(new Map())
  }

  // 缩放控制
  const handleZoomIn = () => {
    setScale(s => Math.min(s + 0.1, 3))
  }

  const handleZoomOut = () => {
    setScale(s => Math.max(s - 0.1, 0.3))
  }

  // 获取相关边
  const getRelatedEdges = (nodeId: string) => {
    return data.edges.filter(e => e.source === nodeId || e.target === nodeId)
  }

  // 判断节点是否高亮
  const isNodeHighlighted = (nodeId: string) => {
    if (hoveredNode === nodeId) return true
    if (selectedNode === nodeId) return true
    if (hoveredNode !== null) {
      const relatedEdges = getRelatedEdges(hoveredNode)
      return relatedEdges.some(e => e.source === nodeId || e.target === nodeId)
    }
    return false
  }

  // 判断边是否高亮
  const isEdgeHighlighted = (edge: RelationEdge) => {
    if (hoveredNode !== null) {
      return edge.source === hoveredNode || edge.target === hoveredNode
    }
    if (selectedNode !== null) {
      return edge.source === selectedNode || edge.target === selectedNode
    }
    return false
  }

  // 渲染边
  const renderEdge = (edge: RelationEdge) => {
    const sourcePos = nodePositions.get(edge.source)
    const targetPos = nodePositions.get(edge.target)
    if (!sourcePos || !targetPos) return null

    const highlighted = isEdgeHighlighted(edge)
    const color = RELATION_COLORS[edge.type] || RELATION_COLORS.other

    // 计算中点
    const midX = (sourcePos.x + targetPos.x) / 2
    const midY = (sourcePos.y + targetPos.y) / 2

    // 计算角度（用于旋转标签）
    const angle = Math.atan2(targetPos.y - sourcePos.y, targetPos.x - sourcePos.x)

    return (
      <g key={`${edge.source}-${edge.target}`}>
        {/* 连线 */}
        <line
          x1={sourcePos.x}
          y1={sourcePos.y}
          x2={targetPos.x}
          y2={targetPos.y}
          stroke={color}
          strokeWidth={highlighted ? 3 : 1.5}
          opacity={highlighted ? 1 : 0.5}
        />
        {/* 标签 */}
        {(highlighted || scale > 1.2) && (
          <text
            x={midX}
            y={midY}
            textAnchor="middle"
            fontSize="10"
            fill={color}
            fontWeight={highlighted ? 'bold' : 'normal'}
            transform={`rotate(${angle * 180 / Math.PI}, ${midX}, ${midY})`}
          >
            {edge.label}
          </text>
        )}
      </g>
    )
  }

  // 渲染节点
  const renderNode = (node: PersonNode) => {
    const pos = nodePositions.get(node.id)
    if (!pos) return null

    const color = ROLE_COLORS[node.role] || ROLE_COLORS.other
    const highlighted = isNodeHighlighted(node.id)
    const isDragging = dragging === node.id

    return (
      <g
        key={node.id}
        transform={`translate(${pos.x}, ${pos.y})`}
        onMouseEnter={() => setHoveredNode(node.id)}
        onMouseLeave={() => setHoveredNode(null)}
        onMouseDown={(e) => handleNodeMouseDown(node.id, e)}
        onClick={(e) => handleNodeClick(node, e)}
        style={{ cursor: 'grab' }}
      >
        {/* 圆形节点 */}
        <circle
          r={highlighted ? 28 : 22}
          fill={color}
          opacity={isDragging ? 0.8 : highlighted ? 1 : 0.7}
          stroke={highlighted ? '#fff' : 'none'}
          strokeWidth={highlighted ? 3 : 0}
          filter={highlighted ? 'drop-shadow(0 2px 6px rgba(0,0,0,0.3))' : 'none'}
        />
        {/* 人名 */}
        <text
          y={-2}
          textAnchor="middle"
          fontSize="11"
          fontWeight="bold"
          fill="#fff"
        >
          {node.name.slice(0, 4)}
        </text>
        {/* 角色标签 */}
        <text
          y={38}
          textAnchor="middle"
          fontSize="9"
          fill={highlighted ? '#1f2937' : '#6b7280'}
        >
          {ROLE_LABELS[node.role] || '其他'}
        </text>
      </g>
    )
  }

  if (data.error) {
    return (
      <div className="relation-graph-error">
        <p>{data.error}</p>
      </div>
    )
  }

  if (!data.nodes.length) {
    return (
      <div className="relation-graph-empty">
        <p>暂无人物数据</p>
      </div>
    )
  }

  // 关系类型中文映射
  const RELATION_LABELS: Record<string, string> = {
    cooperation: '合作',
    fraud: '诈骗',
    friend: '朋友',
    family: '家人',
    business: '商业',
    debt: '债务',
    other: '其他',
  }

  // 统计各角色人数
  const roleStats = new Map<string, number>()
  for (const node of data.nodes) {
    const role = node.role || 'other'
    roleStats.set(role, (roleStats.get(role) || 0) + 1)
  }

  // 统计各关系类型出现次数（动态生成图例）
  const relationStats = new Map<string, number>()
  for (const edge of data.edges) {
    const type = edge.type || 'other'
    relationStats.set(type, (relationStats.get(type) || 0) + 1)
  }

  return (
    <div className="relation-graph-container" ref={containerRef}>
      {/* 工具栏 */}
      <div className="relation-graph-toolbar">
        <div className="relation-graph-stats">
          <span>人物：{data.nodes.length}</span>
          <span>关系：{data.edges.length}</span>
        </div>
        <div className="relation-graph-controls">
          <button onClick={handleZoomOut} title="缩小10%">−</button>
          <span className="scale-display">{Math.round(scale * 100)}%</span>
          <button onClick={handleZoomIn} title="放大10%">+</button>
          <button onClick={handleReset} title="重置视图">
            重置
          </button>
        </div>
      </div>

      {/* 角色图例 */}
      <div className="role-legend">
        {['defendant', 'co defendant', 'victim', 'witness', 'other']
          .filter(r => roleStats.has(r))
          .map(role => (
            <div key={role} className="role-item">
              <span className="role-color" style={{ background: ROLE_COLORS[role] }} />
              <span className="role-name">{ROLE_LABELS[role]}</span>
              <span className="role-count">{roleStats.get(role)}</span>
            </div>
          ))}
      </div>

      {/* 关系类型图例（动态生成，只显示数据中实际存在的关系类型） */}
      {relationStats.size > 0 && (
        <div className="relation-type-legend">
          {Array.from(relationStats.entries())
            .sort((a, b) => b[1] - a[1]) // 按出现次数降序排列
            .map(([type, count]) => (
              <div key={type} className="relation-type-item">
                <span className="relation-type-color" style={{ background: RELATION_COLORS[type] || RELATION_COLORS.other }} />
                <span>{RELATION_LABELS[type] || type}</span>
                <span className="relation-count">{count}</span>
              </div>
            ))}
        </div>
      )}

      {/* SVG 画布 */}
      <svg
        ref={svgRef}
        className="relation-graph-svg"
        viewBox={viewBox}
        onMouseDown={handleCanvasMouseDown}
        style={{
          transform: `scale(${scale}) translate(${offset.x}px, ${offset.y}px)`,
          transformOrigin: 'center center',
        }}
      >
        {/* 背景 */}
        <rect className="canvas-bg" x="0" y="0" width="100%" height="100%" fill="transparent" />

        {/* 边 */}
        <g className="edges-layer">
          {data.edges.map(renderEdge)}
        </g>
        {/* 节点 */}
        <g className="nodes-layer">
          {data.nodes.map(renderNode)}
        </g>
      </svg>

      {/* 详情面板 */}
      {selectedNode !== null && (
        <div className="relation-detail">
          {(() => {
            const node = data.nodes.find(n => n.id === selectedNode)
            if (!node) return null
            const relatedEdges = getRelatedEdges(selectedNode)
            return (
              <>
                <h4>{node.name}</h4>
                <p className="detail-role" style={{ color: ROLE_COLORS[node.role] }}>
                  {ROLE_LABELS[node.role] || '其他'}
                </p>
                {node.description && (
                  <p className="detail-desc">{node.description}</p>
                )}
                {node.evidenceRefs && node.evidenceRefs.length > 0 && (
                  <p className="detail-evidence">证据：{node.evidenceRefs.join(', ')}</p>
                )}
                {relatedEdges.length > 0 && (
                  <div className="detail-relations">
                    <h5>相关关系 ({relatedEdges.length})</h5>
                    {relatedEdges.map(e => {
                      const otherId = e.source === selectedNode ? e.target : e.source
                      const otherNode = data.nodes.find(n => n.id === otherId)
                      return (
                        <div key={`${e.source}-${e.target}`} className="relation-item">
                          <span className="relation-type-label" style={{ color: RELATION_COLORS[e.type] }}>
                            {e.label}
                          </span>
                          <span className="relation-target">
                            {otherNode?.name || otherId}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )}
                <button className="detail-close" onClick={() => setSelectedNode(null)}>
                  关闭
                </button>
              </>
            )
          })()}
        </div>
      )}

      <style>{`
        .relation-graph-container {
          position: relative;
          width: 100%;
          height: 100%;
          min-height: 500px;
          overflow: visible;
          background: linear-gradient(135deg, #fafafa 0%, #f0f4f8 100%);
          border-radius: 8px;
          user-select: none;
        }

        .relation-graph-toolbar {
          position: absolute;
          top: 12px;
          left: 12px;
          z-index: 10;
          display: flex;
          gap: 16px;
          align-items: center;
          background: white;
          padding: 8px 12px;
          border-radius: 6px;
          box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }

        .relation-graph-stats {
          display: flex;
          gap: 12px;
          font-size: 12px;
          color: #6b7280;
        }

        .relation-graph-controls {
          display: flex;
          gap: 8px;
          align-items: center;
        }

        .relation-graph-controls button {
          padding: 4px 12px;
          font-size: 12px;
          background: #3b82f6;
          color: white;
          border: none;
          border-radius: 4px;
          cursor: pointer;
        }

        .relation-graph-controls button:hover {
          background: #2563eb;
        }

        .scale-display {
          font-size: 12px;
          color: #6b7280;
          min-width: 40px;
        }

        .role-legend {
          position: absolute;
          top: 12px;
          right: 12px;
          z-index: 10;
          background: white;
          padding: 8px 12px;
          border-radius: 6px;
          box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }

        .role-item {
          display: flex;
          align-items: center;
          gap: 6px;
          margin-bottom: 4px;
          font-size: 11px;
        }

        .role-color {
          width: 14px;
          height: 14px;
          border-radius: 50%;
        }

        .role-name {
          color: #374151;
        }

        .role-count {
          color: #9ca3af;
          font-size: 10px;
        }

        .relation-type-legend {
          position: absolute;
          bottom: 12px;
          left: 12px;
          z-index: 10;
          display: flex;
          gap: 12px;
          background: white;
          padding: 8px 12px;
          border-radius: 6px;
          box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }

        .relation-type-item {
          display: flex;
          align-items: center;
          gap: 4px;
          font-size: 11px;
          color: #374151;
        }

        .relation-type-color {
          width: 12px;
          height: 3px;
          border-radius: 1px;
        }

        .relation-count {
          color: #9ca3af;
          font-size: 10px;
        }

        .relation-graph-svg {
          width: 100%;
          height: 100%;
          cursor: grab;
        }

        .relation-graph-svg:active {
          cursor: grabbing;
        }

        .relation-detail {
          position: absolute;
          bottom: 60px;
          right: 12px;
          z-index: 20;
          background: white;
          padding: 12px 16px;
          border-radius: 8px;
          box-shadow: 0 4px 12px rgba(0,0,0,0.15);
          max-width: 260px;
          font-size: 13px;
        }

        .relation-detail h4 {
          margin: 0 0 8px 0;
          font-size: 16px;
          color: #1f2937;
        }

        .detail-role {
          font-weight: bold;
          margin: 0 0 6px 0;
        }

        .detail-desc {
          color: #6b7280;
          margin: 0 0 6px 0;
          font-size: 12px;
          line-height: 1.5;
        }

        .detail-evidence {
          color: #6b7280;
          margin: 0 0 4px 0;
          font-size: 11px;
        }

        .detail-relations {
          margin-top: 10px;
        }

        .detail-relations h5 {
          margin: 0 0 6px 0;
          font-size: 12px;
          color: #6b7280;
        }

        .relation-item {
          padding: 4px 8px;
          margin-bottom: 4px;
          background: #f3f4f6;
          border-radius: 4px;
          font-size: 11px;
        }

        .relation-type-label {
          font-weight: bold;
        }

        .relation-target {
          color: #6b7280;
          margin-left: 6px;
        }

        .detail-close {
          margin-top: 10px;
          padding: 4px 12px;
          font-size: 12px;
          background: #e5e7eb;
          color: #374151;
          border: none;
          border-radius: 4px;
          cursor: pointer;
        }

        .detail-close:hover {
          background: #d1d5db;
        }

        .relation-graph-error, .relation-graph-empty {
          display: flex;
          align-items: center;
          justify-content: center;
          height: 100%;
          min-height: 400px;
          color: #6b7280;
        }
      `}</style>
    </div>
  )
}