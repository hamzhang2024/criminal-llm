import React from 'react'
import { EvidenceChainData, EvidenceChainNode, EvidenceChainEdge } from '../api/stages'

interface Props {
  data: EvidenceChainData
  onNodeClick?: (node: EvidenceChainNode) => void
}

/**
 * 证据链可视化组件
 * 使用 SVG 渲染证据节点和关系边
 */
export function EvidenceChainGraph({ data, onNodeClick }: Props) {
  const { nodes, edges, groups, total_evidence, total_relations, error } = data

  if (error) {
    return (
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        color: '#666',
        gap: '12px',
      }}>
        <div style={{ fontSize: '14px', color: '#e74c3c' }}>{error}</div>
      </div>
    )
  }

  if (!nodes.length) {
    return (
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        color: '#666',
        gap: '12px',
      }}>
        <div>无证据数据</div>
      </div>
    )
  }

  // 计算布局参数（自适应容器）
  const width = 760
  const height = 450
  const padding = 60
  const nodeRadius = Math.max(20, Math.min(30, 200 / Math.sqrt(nodes.length)))

  // 简单圆形布局
  const centerX = width / 2
  const centerY = height / 2
  const radius = Math.min(width, height) / 2 - padding - nodeRadius

  // 计算节点位置
  const nodePositions: Map<number, { x: number; y: number }> = new Map()
  nodes.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2
    const x = centerX + radius * Math.cos(angle)
    const y = centerY + radius * Math.sin(angle)
    nodePositions.set(node.id, { x, y })
  })

  // 颜色映射
  const groupColors: Record<string, string> = {}
  groups.forEach(g => {
    groupColors[g.id] = g.color || '#7f8c8d'
  })

  // 边样式
  const getEdgeStyle = (type: string) => {
    switch (type) {
      case 'corroborate':
        return { color: '#27ae60', dasharray: '', label: '印证' }
      case 'contradict':
        return { color: '#e74c3c', dasharray: '5,5', label: '矛盾' }
      case 'supplement':
        return { color: '#3498db', dasharray: '3,3', label: '补充' }
      default:
        return { color: '#95a5a6', dasharray: '', label: '' }
    }
  }

  // 渲染节点
  const renderNodes = () => {
    return nodes.map(node => {
      const pos = nodePositions.get(node.id)
      if (!pos) return null

      const color = groupColors[node.group] || '#7f8c8d'

      return (
        <g key={node.id} onClick={() => onNodeClick?.(node)} style={{ cursor: 'pointer' }}>
          {/* 节点圆形 */}
          <circle
            cx={pos.x}
            cy={pos.y}
            r={nodeRadius}
            fill={color}
            stroke="#fff"
            strokeWidth="2"
          />
          {/* 节点编号 */}
          <text
            x={pos.x}
            y={pos.y}
            textAnchor="middle"
            dominantBaseline="middle"
            fill="#fff"
            fontSize="12"
            fontWeight="bold"
          >
            {node.id}
          </text>
          {/* 节点名称（下方） */}
          <text
            x={pos.x}
            y={pos.y + nodeRadius + 15}
            textAnchor="middle"
            fill="#333"
            fontSize="10"
          >
            {node.name.length > 15 ? node.name.slice(0, 15) + '...' : node.name}
          </text>
        </g>
      )
    })
  }

  // 渲染边
  const renderEdges = () => {
    return edges.map((edge, i) => {
      const sourcePos = nodePositions.get(edge.source)
      const targetPos = nodePositions.get(edge.target)
      if (!sourcePos || !targetPos) return null

      const style = getEdgeStyle(edge.type)

      return (
        <g key={`edge-${i}`}>
          {/* 边线 */}
          <line
            x1={sourcePos.x}
            y1={sourcePos.y}
            x2={targetPos.x}
            y2={targetPos.y}
            stroke={style.color}
            strokeWidth="2"
            strokeDasharray={style.dasharray}
            opacity="0.6"
          />
          {/* 边标签 */}
          {style.label && (
            <text
              x={(sourcePos.x + targetPos.x) / 2}
              y={(sourcePos.y + targetPos.y) / 2 - 5}
              textAnchor="middle"
              fill={style.color}
              fontSize="9"
            >
              {style.label}
            </text>
          )}
        </g>
      )
    })
  }

  // 渲染分组图例
  const renderLegend = () => {
    return (
      <div style={{
        display: 'flex',
        gap: '16px',
        marginTop: '12px',
        fontSize: '12px',
      }}>
        {groups.map(g => (
          <div key={g.id} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <div style={{
              width: '12px',
              height: '12px',
              borderRadius: '50%',
              background: g.color,
            }} />
            <span>{g.name} ({g.count})</span>
          </div>
        ))}
        {/* 边类型图例 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginLeft: '16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <div style={{ width: '20px', height: '2px', background: '#27ae60' }} />
            <span>印证</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <div style={{ width: '20px', height: '2px', background: '#e74c3c', borderStyle: 'dashed' }} />
            <span>矛盾</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <div style={{ width: '20px', height: '2px', background: '#3498db', borderStyle: 'dotted' }} />
            <span>补充</span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ height: '100%' }}>
      {/* 统计信息 */}
      <div style={{
        marginBottom: '12px',
        fontSize: '13px',
        color: '#666',
      }}>
        共 {total_evidence} 份证据，{total_relations} 个关系
      </div>

      {/* SVG 图 */}
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ border: '1px solid #eee', borderRadius: '4px' }}
      >
        {/* 背景 */}
        <rect width={width} height={height} fill="#fafafa" />

        {/* 边 */}
        {renderEdges()}

        {/* 节点 */}
        {renderNodes()}
      </svg>

      {/* 图例 */}
      {renderLegend()}
    </div>
  )
}