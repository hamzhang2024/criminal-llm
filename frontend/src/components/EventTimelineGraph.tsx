/**
 * EventTimelineGraph - 纯 SVG + CSS 事件时间线组件
 *
 * 功能：
 * - 水平时间轴 + 事件节点
 * - 按时间排序，支持缩放查看细节
 * - 悬停显示事件详情
 * - 拖拽平移时间轴
 *
 * 零依赖，纯手写交互
 */
import { useState, useRef, useEffect, useCallback } from 'react'

// 事件节点
export interface EventNode {
  id: string
  date: string           // 日期字符串，如 "2024-05-01" 或 "2024年5月"
  title: string          // 事件标题
  description?: string   // 事件描述
  type?: 'crime' | 'evidence' | 'procedure' | 'defense' | 'other'  // 事件类型
  persons?: string[]     // 涉及人物
  evidenceRefs?: string[] // 相关证据
}

// 时间线数据
export interface TimelineData {
  events: EventNode[]
  error?: string
}

interface Props {
  data: TimelineData
  onEventClick?: (event: EventNode) => void
}

// 事件类型颜色映射
const EVENT_COLORS: Record<string, string> = {
  crime: '#ef4444',      // 红 - 犯罪行为
  evidence: '#3b82f6',   // 蓝 - 证据相关
  procedure: '#6b7280',  // 灰 - 程序性事件
  defense: '#10b981',    // 绿 - 辩护相关
  other: '#f59e0b',      // 橙 - 其他
}

// 事件类型中文映射
const EVENT_LABELS: Record<string, string> = {
  crime: '犯罪',
  evidence: '证据',
  procedure: '程序',
  defense: '辩护',
  other: '其他',
}

export function EventTimelineGraph({ data, onEventClick }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  // 画布状态
  const [scale, setScale] = useState(1)
  const [offsetX, setOffsetX] = useState(0)

  // 拖拽状态
  const [dragging, setDragging] = useState(false)
  const [dragStartX, setDragStartX] = useState(0)

  // 高亮状态
  const [hoveredEvent, setHoveredEvent] = useState<string | null>(null)
  const [selectedEvent, setSelectedEvent] = useState<string | null>(null)

  // 解析日期为可比较的数值
  const parseDate = (dateStr: string): number => {
    // 支持多种格式：2024-05-01, 2024年5月, 2024.05
    const match = dateStr.match(/(\d{4})[-年.](\d{1,2})(?:[-月.](\d{1,2}))?/)
    if (match) {
      const year = parseInt(match[1])
      const month = parseInt(match[2])
      const day = match[3] ? parseInt(match[3]) : 1
      return year * 10000 + month * 100 + day
    }
    // 无法解析的返回 0
    return 0
  }

  // 格式化日期显示
  const formatDate = (dateStr: string): string => {
    return dateStr.replace(/[-]/g, '/').replace(/年/, '/').replace(/月/, '').replace(/[.]/, '/')
  }

  // 计算事件位置（按时间排序）
  const getEventPositions = useCallback(() => {
    if (!data.events.length) return { positions: new Map(), axisWidth: 800, axisY: 200 }

    // 按日期排序
    const sortedEvents = [...data.events].sort((a, b) => parseDate(a.date) - parseDate(b.date))

    // 计算时间范围
    const dates = sortedEvents.map(e => parseDate(e.date))
    const minDate = Math.min(...dates)
    const maxDate = Math.max(...dates)
    const dateRange = maxDate - minDate || 1

    // 每个事件最小间距（确保密集事件不会被挤在一起）
    const minGap = 120
    // 基础轴宽
    const baseAxisWidth = 800 + sortedEvents.length * 80
    // 实际轴宽取两者较大值
    const axisWidth = Math.max(baseAxisWidth, minGap * (sortedEvents.length - 1) + 200)
    const axisY = 200
    const padding = 100

    const positions = new Map<string, { x: number; y: number; above: boolean }>()

    // 分配位置（交替上下避免重叠）
    let aboveToggle = true
    sortedEvents.forEach((event, i) => {
      const dateNum = parseDate(event.date)
      // 按比例计算位置，但确保与前一个事件的最小间距
      let x = padding + (dateNum - minDate) / dateRange * (axisWidth - 2 * padding)

      // 与前一个事件的最小间距检查
      if (i > 0) {
        const prevPos = positions.get(sortedEvents[i - 1].id)
        if (prevPos) {
          x = Math.max(x, prevPos.x + minGap)
        }
      }

      const above = aboveToggle
      aboveToggle = !aboveToggle

      positions.set(event.id, {
        x,
        y: axisY + (above ? -60 : 60),
        above,
      })
    })

    return { positions, axisWidth, axisY }
  }, [data.events])

  const { positions, axisWidth, axisY } = getEventPositions()

  // 滚轮缩放
  const handleWheel = useCallback((e: WheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? 0.9 : 1.1
    setScale(s => Math.min(Math.max(s * delta, 0.5), 5))
  }, [])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    container.addEventListener('wheel', handleWheel, { passive: false })
    return () => container.removeEventListener('wheel', handleWheel)
  }, [handleWheel])

  // 开始拖拽
  const handleMouseDown = (e: React.MouseEvent) => {
    setDragging(true)
    setDragStartX(e.clientX - offsetX * scale)
  }

  // 鼠标移动
  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (dragging) {
      setOffsetX((e.clientX - dragStartX) / scale)
    }
  }, [dragging, dragStartX, scale])

  // 鼠标释放
  const handleMouseUp = useCallback(() => {
    setDragging(false)
  }, [])

  useEffect(() => {
    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [handleMouseMove, handleMouseUp])

  // 点击事件
  const handleEventClick = (event: EventNode, e: React.MouseEvent) => {
    e.stopPropagation()
    setSelectedEvent(event.id)
    if (onEventClick) {
      onEventClick(event)
    }
  }

  // 重置视图
  const handleReset = () => {
    setScale(1)
    setOffsetX(0)
    setSelectedEvent(null)
  }

  // 缩放控制
  const handleZoomIn = () => {
    setScale(s => Math.min(s + 0.1, 5))
  }

  const handleZoomOut = () => {
    setScale(s => Math.max(s - 0.1, 0.5))
  }

  // 渲染时间轴
  const renderAxis = () => {
    if (!data.events.length) return null

    // 计算时间刻度
    const sortedEvents = [...data.events].sort((a, b) => parseDate(a.date) - parseDate(b.date))
    const dates = sortedEvents.map(e => parseDate(e.date))
    const minDate = Math.min(...dates)
    const maxDate = Math.max(...dates)
    const dateRange = maxDate - minDate || 1
    const padding = 100

    // 生成刻度（按年/月）
    const ticks: { x: number; label: string }[] = []
    const startYear = Math.floor(minDate / 10000)
    const endYear = Math.ceil(maxDate / 10000)

    for (let year = startYear; year <= endYear; year++) {
      const yearStart = year * 10000
      const yearEnd = (year + 1) * 10000

      // 如果跨度超过一年，只显示年份
      if (endYear - startYear >= 2) {
        const x = padding + (yearStart - minDate) / dateRange * (axisWidth - 2 * padding)
        ticks.push({ x, label: `${year}年` })
      } else {
        // 显示月份刻度
        for (let month = 1; month <= 12; month++) {
          const monthDate = year * 10000 + month * 100
          if (monthDate >= minDate && monthDate <= maxDate) {
            const x = padding + (monthDate - minDate) / dateRange * (axisWidth - 2 * padding)
            ticks.push({ x, label: `${month}月` })
          }
        }
      }
    }

    return (
      <g>
        {/* 主轴线 */}
        <line
          x1={50}
          y1={axisY}
          x2={axisWidth - 50}
          y2={axisY}
          stroke="#374151"
          strokeWidth={2}
        />
        {/* 刻度 */}
        {ticks.map((tick, i) => (
          <g key={i}>
            <line
              x1={tick.x}
              y1={axisY - 8}
              x2={tick.x}
              y2={axisY + 8}
              stroke="#6b7280"
              strokeWidth={1}
            />
            <text
              x={tick.x}
              y={axisY + 22}
              textAnchor="middle"
              fontSize="11"
              fill="#6b7280"
            >
              {tick.label}
            </text>
          </g>
        ))}
      </g>
    )
  }

  // 渲染事件节点
  const renderEvent = (event: EventNode) => {
    const pos = positions.get(event.id)
    if (!pos) return null

    const color = EVENT_COLORS[event.type || 'other']
    const highlighted = hoveredEvent === event.id || selectedEvent === event.id

    // 截断标题
    const displayTitle = event.title.length > 15 ? event.title.slice(0, 15) + '...' : event.title

    return (
      <g
        key={event.id}
        onMouseEnter={() => setHoveredEvent(event.id)}
        onMouseLeave={() => setHoveredEvent(null)}
        onClick={(e) => handleEventClick(event, e)}
        style={{ cursor: 'pointer' }}
      >
        {/* 连接线 */}
        <line
          x1={pos.x}
          y1={axisY}
          x2={pos.x}
          y2={pos.y}
          stroke={color}
          strokeWidth={highlighted ? 2 : 1}
          opacity={highlighted ? 1 : 0.6}
        />

        {/* 事件节点 */}
        <g transform={`translate(${pos.x}, ${pos.y})`}>
          {/* 圆点 */}
          <circle
            r={highlighted ? 10 : 6}
            fill={color}
            opacity={highlighted ? 1 : 0.7}
            stroke={highlighted ? '#fff' : 'none'}
            strokeWidth={highlighted ? 2 : 0}
            filter={highlighted ? 'drop-shadow(0 2px 4px rgba(0,0,0,0.2))' : 'none'}
          />
          {/* 日期 */}
          <text
            y={pos.above ? -20 : 20}
            textAnchor="middle"
            fontSize="10"
            fill="#6b7280"
          >
            {formatDate(event.date)}
          </text>
          {/* 标题 */}
          <text
            y={pos.above ? -34 : 34}
            textAnchor="middle"
            fontSize="11"
            fill={highlighted ? '#1f2937' : '#6b7280'}
            fontWeight={highlighted ? 'bold' : 'normal'}
          >
            {displayTitle}
          </text>
        </g>
      </g>
    )
  }

  if (data.error) {
    return (
      <div className="timeline-error">
        <p>{data.error}</p>
      </div>
    )
  }

  if (!data.events.length) {
    return (
      <div className="timeline-empty">
        <p>暂无事件数据</p>
      </div>
    )
  }

  // 统计各类型事件数
  const typeStats = new Map<string, number>()
  for (const event of data.events) {
    const type = event.type || 'other'
    typeStats.set(type, (typeStats.get(type) || 0) + 1)
  }

  return (
    <div className="timeline-container" ref={containerRef}>
      {/* 工具栏 */}
      <div className="timeline-toolbar">
        <div className="timeline-stats">
          <span>事件：{data.events.length}</span>
        </div>
        <div className="timeline-controls">
          <button onClick={handleZoomOut} title="缩小10%">−</button>
          <span className="scale-display">{Math.round(scale * 100)}%</span>
          <button onClick={handleZoomIn} title="放大10%">+</button>
          <button onClick={handleReset} title="重置视图">
            重置
          </button>
        </div>
      </div>

      {/* 事件类型图例 */}
      <div className="event-type-legend">
        {['crime', 'evidence', 'procedure', 'defense', 'other']
          .filter(t => typeStats.has(t))
          .map(type => (
            <div key={type} className="event-type-item">
              <span className="event-type-dot" style={{ background: EVENT_COLORS[type] }} />
              <span>{EVENT_LABELS[type]}</span>
              <span className="event-type-count">{typeStats.get(type)}</span>
            </div>
          ))}
      </div>

      {/* 提示 */}
      <div className="timeline-hint">
        滚轮缩放 | 拖拽平移 | 点击查看详情
      </div>

      {/* SVG 画布 */}
      <svg
        ref={svgRef}
        className="timeline-svg"
        onMouseDown={handleMouseDown}
        viewBox={`0 0 ${axisWidth} 350`}
        preserveAspectRatio="xMidYMid meet"
        style={{
          transform: `scale(${scale}) translateX(${offsetX}px)`,
          transformOrigin: '0 center',
          width: `${axisWidth * scale}px`,
          minWidth: '100%',
        }}
      >
        {/* 背景 */}
        <rect x="0" y="0" width={axisWidth} height="350" fill="transparent" />

        {/* 时间轴 */}
        {renderAxis()}

        {/* 事件节点 */}
        {data.events.map(renderEvent)}
      </svg>

      {/* 详情面板 */}
      {selectedEvent !== null && (
        <div className="event-detail">
          {(() => {
            const event = data.events.find(e => e.id === selectedEvent)
            if (!event) return null
            return (
              <>
                <div className="event-detail-header">
                  <span className="event-type-badge" style={{ background: EVENT_COLORS[event.type || 'other'] }}>
                    {EVENT_LABELS[event.type || 'other']}
                  </span>
                  <span className="event-date">{formatDate(event.date)}</span>
                </div>
                <h4>{event.title}</h4>
                {event.description && (
                  <p className="event-desc">{event.description}</p>
                )}
                {event.persons && event.persons.length > 0 && (
                  <p className="event-persons">涉及人物：{event.persons.join(', ')}</p>
                )}
                {event.evidenceRefs && event.evidenceRefs.length > 0 && (
                  <p className="event-evidence">相关证据：{event.evidenceRefs.join(', ')}</p>
                )}
                <button className="event-detail-close" onClick={() => setSelectedEvent(null)}>
                  关闭
                </button>
              </>
            )
          })()}
        </div>
      )}

      <style>{`
        .timeline-container {
          position: relative;
          width: 100%;
          height: 100%;
          min-height: 400px;
          overflow: hidden;
          background: linear-gradient(180deg, #f8fafc 0%, #e2e8f0 100%);
          border-radius: 8px;
          user-select: none;
        }

        .timeline-toolbar {
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

        .timeline-stats {
          display: flex;
          gap: 12px;
          font-size: 12px;
          color: #6b7280;
        }

        .timeline-controls {
          display: flex;
          gap: 8px;
          align-items: center;
        }

        .timeline-controls button {
          padding: 4px 12px;
          font-size: 12px;
          background: #3b82f6;
          color: white;
          border: none;
          border-radius: 4px;
          cursor: pointer;
        }

        .timeline-controls button:hover {
          background: #2563eb;
        }

        .scale-display {
          font-size: 12px;
          color: #6b7280;
          min-width: 40px;
        }

        .event-type-legend {
          position: absolute;
          top: 12px;
          right: 12px;
          z-index: 10;
          display: flex;
          gap: 8px;
          background: white;
          padding: 8px 12px;
          border-radius: 6px;
          box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }

        .event-type-item {
          display: flex;
          align-items: center;
          gap: 4px;
          font-size: 11px;
          color: #374151;
        }

        .event-type-dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;
        }

        .event-type-count {
          color: #9ca3af;
          font-size: 10px;
        }

        .timeline-hint {
          position: absolute;
          bottom: 12px;
          left: 50%;
          transform: translateX(-50%);
          z-index: 10;
          font-size: 11px;
          color: #9ca3af;
          background: white;
          padding: 4px 12px;
          border-radius: 4px;
          box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        }

        .timeline-svg {
          width: 100%;
          height: auto;
          min-height: 350px;
          cursor: grab;
        }

        .timeline-svg:active {
          cursor: grabbing;
        }

        .event-detail {
          position: absolute;
          bottom: 50px;
          right: 12px;
          z-index: 20;
          background: white;
          padding: 12px 16px;
          border-radius: 8px;
          box-shadow: 0 4px 12px rgba(0,0,0,0.15);
          max-width: 280px;
          font-size: 13px;
        }

        .event-detail-header {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 8px;
        }

        .event-type-badge {
          padding: 2px 8px;
          font-size: 10px;
          color: white;
          border-radius: 4px;
        }

        .event-date {
          color: #6b7280;
          font-size: 12px;
        }

        .event-detail h4 {
          margin: 0 0 8px 0;
          font-size: 14px;
          color: #1f2937;
        }

        .event-desc {
          color: #6b7280;
          margin: 0 0 6px 0;
          font-size: 12px;
          line-height: 1.5;
        }

        .event-persons, .event-evidence {
          color: #6b7280;
          margin: 0 0 4px 0;
          font-size: 11px;
        }

        .event-detail-close {
          margin-top: 10px;
          padding: 4px 12px;
          font-size: 12px;
          background: #e5e7eb;
          color: #374151;
          border: none;
          border-radius: 4px;
          cursor: pointer;
        }

        .event-detail-close:hover {
          background: #d1d5db;
        }

        .timeline-error, .timeline-empty {
          display: flex;
          align-items: center;
          justify-content: center;
          height: 100%;
          min-height: 300px;
          color: #6b7280;
        }
      `}</style>
    </div>
  )
}