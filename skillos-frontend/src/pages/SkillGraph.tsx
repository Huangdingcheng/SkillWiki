import { useEffect, useMemo, useRef, useState } from 'react'
import { Card, Select, Spin, Tag, Button, Space, Segmented } from 'antd'
import { PauseCircleOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import ForceGraph2D from 'react-force-graph-2d'
import { graphApi } from '@/api/client'
import type { GraphData, GraphEdgeData, GraphNodeData } from '@/api/types'

const SKILL_TYPE_COLOR: Record<string, string> = {
  atomic: '#5db6ff',
  functional: '#b98cff',
  strategic: '#ffc56e',
}

const NODE_TYPE_COLOR: Record<string, string> = {
  skill: '#7db7ff',
  task: '#ffb86c',
  trajectory: '#ff6f91',
  document: '#73f2a4',
  api_doc: '#c792ff',
  tool: '#72f6ff',
  script: '#ffe082',
  test: '#8cffc1',
  version: '#c8d4e8',
  feedback: '#ff8d5c',
  agent: '#66ffc2',
  dataset: '#aab4c8',
  host_information: '#3de7ff',
}

const STATE_OPACITY: Record<string, number> = {
  S4: 1.0, S2: 0.55, S3: 0.7,
  S5: 0.9, S6: 0.65, S7: 0.35,
}

const EDGE_COLOR: Record<string, string> = {
  depends_on: '#7db7ff', composes_with: '#b98cff', similar_to: '#73f2a4',
  evolved_from: '#ffc56e', conflicts_with: '#ff6f91', replaces: '#ff82c2',
  specializes: '#72f6ff', generalizes: '#ffe082',
  derived_from: '#ff6f91', belongs_to: '#8491ad', uses: '#72f6ff',
  requires: '#7db7ff', verified_by: '#73f2a4', evolves_from: '#ffc56e',
  produced_by: '#66ffc2', triggered_by: '#ffb86c', feeds_back_to: '#ff8d5c',
  documents: '#73f2a4', tests: '#8cffc1', version_of: '#c8d4e8',
}

const LAYER_LABEL: Record<string, string> = {
  skill: 'Skill Memory',
  task: 'Task Seeds',
  trajectory: 'Trajectories',
  document: 'Documents',
  api_doc: 'API Docs',
  tool: 'Tools',
  script: 'Scripts',
  test: 'Evaluations',
  version: 'Versions',
  feedback: 'Feedback',
  agent: 'Agents',
  dataset: 'Datasets',
  host_information: 'Host Memory',
}

const LAYER_ORBIT: Record<string, number> = {
  skill: 0.18,
  agent: 0.14,
  host_information: 0.28,
  task: 0.36,
  trajectory: 0.42,
  document: 0.48,
  script: 0.54,
  api_doc: 0.58,
  tool: 0.62,
  test: 0.68,
  feedback: 0.72,
  version: 0.76,
  dataset: 0.8,
}

const LAYER_ANCHOR: Record<string, number> = {
  agent: -90,
  skill: -82,
  host_information: -38,
  api_doc: 18,
  tool: 46,
  test: 76,
  version: 112,
  feedback: 142,
  dataset: 172,
  document: 214,
  trajectory: 252,
  task: 286,
  script: 324,
}

function truncateLabel(value: string, max = 24) {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

function seededNoise(id: string, salt = 0) {
  let hash = 2166136261 + salt
  for (let i = 0; i < id.length; i += 1) {
    hash ^= id.charCodeAt(i)
    hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24)
  }
  return ((hash >>> 0) % 10000) / 10000
}

function getOrbitalPosition(id: string, nodeType: string, width: number, height: number, index: number, groupSize: number) {
  const centerX = width / 2
  const centerY = height / 2
  const radiusLimit = Math.min(width, height) * 0.5
  const orbit = LAYER_ORBIT[nodeType] || 0.66
  const groupRatio = groupSize <= 1 ? 0.5 : index / Math.max(1, groupSize - 1)
  const jitter = (seededNoise(id, 7) - 0.5) * 0.34
  const radiusJitter = 0.88 + seededNoise(id, 17) * 0.22

  if (nodeType === 'skill') {
    const goldenAngle = Math.PI * (3 - Math.sqrt(5))
    const spiralRadius = radiusLimit * (0.09 + Math.sqrt((index + 1) / Math.max(2, groupSize)) * 0.34)
    const angle = index * goldenAngle + jitter
    return {
      x: centerX + Math.cos(angle) * spiralRadius * radiusJitter,
      y: centerY + Math.sin(angle) * spiralRadius * radiusJitter,
    }
  }

  const anchor = ((LAYER_ANCHOR[nodeType] ?? seededNoise(nodeType, 23) * 360) * Math.PI) / 180
  const spread = groupSize > 12 ? Math.PI * 0.64 : Math.PI * 0.42
  const angle = anchor + (groupRatio - 0.5) * spread + jitter
  return {
    x: centerX + Math.cos(angle) * radiusLimit * orbit * radiusJitter,
    y: centerY + Math.sin(angle) * radiusLimit * orbit * radiusJitter,
  }
}

function getNodeSize(nodeType: string, usageCount = 0, isFocused = false) {
  if (isFocused) return 78
  if (nodeType === 'skill') return Math.max(34, Math.min(66, 36 + usageCount * 0.55))
  if (nodeType === 'host_information') return 42
  if (nodeType === 'agent') return 44
  return 28
}

function getNodeIcon(nodeType: string, skillType?: string) {
  if (nodeType === 'skill') {
    if (skillType === 'strategic') return 'S'
    if (skillType === 'functional') return 'F'
    return 'A'
  }
  const icons: Record<string, string> = {
    task: 'T',
    trajectory: '↗',
    document: 'D',
    api_doc: 'API',
    tool: '⚙',
    script: '</>',
    test: '✓',
    version: 'V',
    feedback: '!',
    agent: 'AI',
    dataset: 'DB',
    host_information: 'H',
  }
  return icons[nodeType] || '•'
}

function getNeighborIds(edges: GraphData['edges'], focusSkillId?: string | null) {
  if (!focusSkillId) return new Set<string>()
  const ids = new Set<string>([focusSkillId])
  edges.forEach(edge => {
    if (edge.source === focusSkillId) ids.add(edge.target)
    if (edge.target === focusSkillId) ids.add(edge.source)
  })
  return ids
}

interface PositionedGraphNode {
  id: string
  x: number
  y: number
  nodeSize: number
  label: string
}

interface VisibleGraphContext {
  visibleNodes: GraphNodeData[]
  filteredEdges: GraphEdgeData[]
  neighborIds: Set<string>
}

interface LiveNode {
  id: string
  name: string
  label: string
  nodeType: string
  skillType?: string
  state?: string
  color: string
  size: number
  isFocused: boolean
  isFocusContext: boolean
  opacity: number
  x?: number
  y?: number
  fx?: number
  fy?: number
}

interface LiveLink {
  id: string
  source: string | LiveNode
  target: string | LiveNode
  edgeType: string
  color: string
  width: number
  isFocusEdge: boolean
}

interface LiveGraphControls {
  d3Force: (name: string, forceFn?: unknown) => unknown
  d3ReheatSimulation: () => unknown
  zoomToFit: (durationMs?: number, padding?: number) => unknown
  pauseAnimation: () => unknown
  resumeAnimation: () => unknown
}

function estimateLabelBox(node: PositionedGraphNode) {
  const labelWidth = Math.min(220, Math.max(52, node.label.length * 6.4 + 20))
  const labelHeight = 22
  const nodeRadius = node.nodeSize / 2
  const top = node.y - nodeRadius - 4
  const bottom = node.y + nodeRadius + 12 + labelHeight
  const left = Math.min(node.x - nodeRadius, node.x - labelWidth / 2)
  const right = Math.max(node.x + nodeRadius, node.x + labelWidth / 2)
  return { left, right, top, bottom, width: right - left, height: bottom - top }
}

function relaxLabelCollisions(nodes: PositionedGraphNode[], width: number, height: number) {
  const relaxed = nodes.map(node => ({ ...node }))
  const padding = 12
  const centerX = width / 2
  const centerY = height / 2

  for (let iteration = 0; iteration < 90; iteration += 1) {
    let moved = false
    for (let i = 0; i < relaxed.length; i += 1) {
      for (let j = i + 1; j < relaxed.length; j += 1) {
        const a = relaxed[i]
        const b = relaxed[j]
        const boxA = estimateLabelBox(a)
        const boxB = estimateLabelBox(b)
        const overlapX = Math.min(boxA.right, boxB.right) - Math.max(boxA.left, boxB.left)
        const overlapY = Math.min(boxA.bottom, boxB.bottom) - Math.max(boxA.top, boxB.top)
        if (overlapX > 0 && overlapY > 0) {
          const dx = b.x - a.x || seededNoise(`${a.id}:${b.id}`, 31) - 0.5
          const dy = b.y - a.y || seededNoise(`${a.id}:${b.id}`, 41) - 0.5
          const length = Math.hypot(dx, dy) || 1
          const push = Math.min(26, Math.max(overlapX, overlapY) * 0.34 + 2)
          const pushX = (dx / length) * push
          const pushY = (dy / length) * push
          a.x -= pushX
          a.y -= pushY
          b.x += pushX
          b.y += pushY
          moved = true
        }
      }
    }

    relaxed.forEach(node => {
      const box = estimateLabelBox(node)
      if (box.left < padding) node.x += padding - box.left
      if (box.right > width - padding) node.x -= box.right - (width - padding)
      if (box.top < padding) node.y += padding - box.top
      if (box.bottom > height - padding) node.y -= box.bottom - (height - padding)

      const orbitPull = node.id && node.label ? 0.008 : 0
      node.x += (centerX - node.x) * orbitPull
      node.y += (centerY - node.y) * orbitPull
    })

    if (!moved) break
  }

  return relaxed
}

function getVisibleGraphContext(
  graphData: GraphData,
  edgeFilter: string[],
  nodeTypeFilter: string[],
  focusSkillId: string | null | undefined,
  visibility: 'user' | 'kernel' | 'all',
): VisibleGraphContext {
  const connectedIds = new Set<string>()
  graphData.edges.forEach(edge => {
    connectedIds.add(edge.source)
    connectedIds.add(edge.target)
  })

  const visibleNodeIds = new Set(
    graphData.nodes
      .filter(node => {
        const nodeType = node.node_type || 'skill'
        const nodeVisibility = node.visibility || (node.metadata?.visibility as string | undefined) || 'user'
        const visibilityVisible = visibility === 'all' || nodeType !== 'skill' || nodeVisibility === visibility || focusSkillId === node.id
        const typeVisible = nodeTypeFilter.length === 0 || nodeTypeFilter.includes(nodeType)
        const isFocusTarget = focusSkillId === node.id
        const connectedOrEntity = connectedIds.has(node.id) || nodeType !== 'skill' || isFocusTarget
        const focusNeighborhood = focusSkillId
          ? isFocusTarget || graphData.edges.some(edge =>
            (edge.source === focusSkillId && edge.target === node.id)
            || (edge.target === focusSkillId && edge.source === node.id)
          )
          : true
        return visibilityVisible && typeVisible && connectedOrEntity && focusNeighborhood
      })
      .map(n => n.id)
  )

  const filteredEdges = graphData.edges
    .filter(e => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target))
    .filter(e => edgeFilter.length === 0 || edgeFilter.includes(e.edge_type))

  return {
    visibleNodes: graphData.nodes.filter(n => visibleNodeIds.has(n.id)),
    filteredEdges,
    neighborIds: getNeighborIds(graphData.edges, focusSkillId),
  }
}

function getLiveGraphControls(ref: React.MutableRefObject<unknown>): LiveGraphControls | null {
  return ref.current as LiveGraphControls | null
}

interface SkillGraphProps {
  focusSkillId?: string | null
  embedded?: boolean
  visibility?: 'user' | 'kernel' | 'all'
}

export default function SkillGraph({ focusSkillId, embedded = false, visibility = embedded ? 'user' : 'all' }: SkillGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<{ destroy: () => void; stopLayout?: () => void } | null>(null)
  const liveGraphRef = useRef<unknown>(undefined)
  const settleTimerRef = useRef<number | null>(null)
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [edgeFilter, setEdgeFilter] = useState<string[]>([])
  const [nodeTypeFilter, setNodeTypeFilter] = useState<string[]>([])
  const [renderStats, setRenderStats] = useState({ nodes: 0, edges: 0 })
  const [focusTargetName, setFocusTargetName] = useState<string | null>(null)
  const [graphMode, setGraphMode] = useState<'static' | 'live'>('static')
  const [hoverNodeId, setHoverNodeId] = useState<string | null>(null)
  const [livePaused, setLivePaused] = useState(false)

  const loadGraph = async () => {
    setLoading(true)
    try {
      const data = await graphApi.full(300)
      setGraphData(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadGraph() }, [])

  const visibleContext = useMemo(
    () => graphData ? getVisibleGraphContext(graphData, edgeFilter, nodeTypeFilter, focusSkillId, visibility) : null,
    [graphData, edgeFilter, nodeTypeFilter, focusSkillId, visibility],
  )

  const liveGraphData = useMemo(() => {
    if (!visibleContext || !containerRef.current) return { nodes: [] as LiveNode[], links: [] as LiveLink[] }
    const width = containerRef.current.clientWidth || 960
    const height = containerRef.current.clientHeight || 600
    const typeCounts = visibleContext.visibleNodes.reduce<Record<string, number>>((acc, n) => {
      const nodeType = n.node_type || 'skill'
      acc[nodeType] = (acc[nodeType] || 0) + 1
      return acc
    }, {})
    const typeIndexes: Record<string, number> = {}

    const nodes = visibleContext.visibleNodes.map(n => {
      const nodeType = n.node_type || 'skill'
      const nodeIndex = typeIndexes[nodeType] || 0
      typeIndexes[nodeType] = nodeIndex + 1
      const { x, y } = getOrbitalPosition(n.id, nodeType, width, height, nodeIndex, typeCounts[nodeType] || 1)
      const color = nodeType === 'skill'
        ? SKILL_TYPE_COLOR[n.skill_type] || NODE_TYPE_COLOR.skill
        : NODE_TYPE_COLOR[nodeType] || '#8c8c8c'
      const isFocused = focusSkillId === n.id
      const isFocusContext = !focusSkillId || visibleContext.neighborIds.has(n.id)
      const opacity = focusSkillId && !isFocusContext ? 0.16 : 1
      const label = n.version && nodeType === 'skill' ? `${n.name} v${n.version}` : n.name
      return {
        id: n.id,
        name: n.name,
        label,
        nodeType,
        skillType: n.skill_type,
        state: n.state,
        color,
        size: getNodeSize(nodeType, n.usage_count, isFocused),
        isFocused,
        isFocusContext,
        opacity,
        x: x - width / 2,
        y: y - height / 2,
      }
    })

    const links = visibleContext.filteredEdges.map(e => {
      const isFocusEdge = Boolean(focusSkillId && (e.source === focusSkillId || e.target === focusSkillId))
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        edgeType: e.edge_type,
        color: EDGE_COLOR[e.edge_type] || '#8da2c0',
        width: isFocusEdge ? Math.max(1.4, e.weight * 2.6) : Math.max(0.7, e.weight * 1.2),
        isFocusEdge,
      }
    })
    return { nodes, links }
  }, [visibleContext, focusSkillId])

  useEffect(() => {
    if (!graphData || !visibleContext || !containerRef.current || graphMode !== 'static') return

    import('@antv/g6').then((G6) => {
      if (graphRef.current) {
        graphRef.current.destroy()
        graphRef.current = null
      }
      if (settleTimerRef.current) {
        window.clearTimeout(settleTimerRef.current)
        settleTimerRef.current = null
      }

      const { visibleNodes, filteredEdges, neighborIds } = visibleContext
      setRenderStats({ nodes: visibleNodes.length, edges: filteredEdges.length })
      setFocusTargetName(graphData.nodes.find(n => n.id === focusSkillId)?.name || null)
      const width = containerRef.current!.clientWidth || 960
      const height = containerRef.current!.clientHeight || 600
      const typeCounts = visibleNodes.reduce<Record<string, number>>((acc, n) => {
        const nodeType = n.node_type || 'skill'
        acc[nodeType] = (acc[nodeType] || 0) + 1
        return acc
      }, {})
      const typeIndexes: Record<string, number> = {}

      const positionedNodes = visibleNodes.map(n => {
        const nodeType = n.node_type || 'skill'
        const nodeIndex = typeIndexes[nodeType] || 0
        typeIndexes[nodeType] = nodeIndex + 1
        const { x, y } = getOrbitalPosition(n.id, nodeType, width, height, nodeIndex, typeCounts[nodeType] || 1)
        const isFocused = focusSkillId === n.id
        const nodeSize = getNodeSize(nodeType, n.usage_count, isFocused)
        const label = n.version && nodeType === 'skill'
          ? `${n.name} v${n.version}`
          : n.name
        return { id: n.id, x, y, nodeSize, label }
      })
      const relaxedPositionById = new Map(
        relaxLabelCollisions(positionedNodes, width, height).map(node => [node.id, node])
      )

      const nodes = visibleNodes.map(n => {
        const nodeType = n.node_type || 'skill'
        const color = nodeType === 'skill'
          ? SKILL_TYPE_COLOR[n.skill_type] || NODE_TYPE_COLOR.skill
          : NODE_TYPE_COLOR[nodeType] || '#8c8c8c'
        const isFocused = focusSkillId === n.id
        const isFocusContext = !focusSkillId || neighborIds.has(n.id)
        const opacity = focusSkillId && !isFocusContext ? 0.18 : 1
        const nodeSize = getNodeSize(nodeType, n.usage_count, isFocused)
        const label = n.version && nodeType === 'skill'
          ? `${n.name} v${n.version}`
          : n.name
        const relaxed = relaxedPositionById.get(n.id)
        return {
          id: n.id,
          data: {
            label,
            nodeType,
            skillType: n.skill_type,
            state: n.state,
            description: n.description,
            visibility: n.visibility,
          },
          style: {
            x: relaxed?.x,
            y: relaxed?.y,
            fill: color,
            fillOpacity: (nodeType === 'skill' ? (STATE_OPACITY[n.state] || 0.75) : 0.88) * opacity,
            stroke: isFocused ? '#fff4d6' : color,
            strokeOpacity: isFocused ? 1 : 0.72 * opacity,
            lineWidth: isFocused ? 4 : nodeType === 'skill' && n.state === 'S4' ? 2 : 1,
            shadowColor: color,
            shadowBlur: isFocused ? 42 : nodeType === 'skill' ? 22 : 14,
            shadowOffsetX: 0,
            shadowOffsetY: 0,
            size: nodeSize,
            halo: true,
            haloStroke: color,
            haloStrokeOpacity: isFocused ? 0.45 : 0.16 * opacity,
            haloLineWidth: isFocused ? 18 : Math.max(8, nodeSize * 0.24),
            labelText: truncateLabel(label),
            labelFill: isFocusContext ? '#e8f1ff' : 'rgba(232,241,255,0.42)',
            labelFontSize: isFocused ? 13 : nodeType === 'skill' ? 10 : 9,
            labelFontWeight: isFocused ? 800 : 650,
            labelPlacement: 'bottom' as const,
            labelBackground: true,
            labelBackgroundFill: 'rgba(5, 13, 32, 0.68)',
            labelBackgroundRadius: 8,
            labelBackgroundPadding: [3, 6, 3, 6],
            icon: true,
            iconText: getNodeIcon(nodeType, n.skill_type),
            iconFill: '#06111f',
            iconFontSize: nodeType === 'skill' ? 12 : 9,
            iconFontWeight: 800,
          },
        }
      })

      const edges = filteredEdges.map(e => ({
        id: e.id,
        source: e.source,
        target: e.target,
        data: { edgeType: e.edge_type },
        style: {
          stroke: EDGE_COLOR[e.edge_type] || '#aaa',
          strokeOpacity: focusSkillId && !(neighborIds.has(e.source) && neighborIds.has(e.target)) ? 0.08 : 0.34,
          lineWidth: focusSkillId && (e.source === focusSkillId || e.target === focusSkillId)
            ? Math.max(1.6, e.weight * 2.8)
            : Math.max(0.8, e.weight * 1.45),
          shadowColor: EDGE_COLOR[e.edge_type] || '#aaa',
          shadowBlur: focusSkillId && (e.source === focusSkillId || e.target === focusSkillId) ? 12 : 3,
          endArrow: false,
          labelText: '',
          labelFontSize: 8,
          labelFill: EDGE_COLOR[e.edge_type] || '#aaa',
        },
      }))

      const g = new G6.Graph({
        container: containerRef.current!,
        width,
        height,
        data: { nodes, edges },
        autoFit: 'view',
        animation: {
          duration: 420,
          easing: 'ease-cubic',
        },
        theme: 'dark',
        behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element', 'click-select'],
        node: {
          type: 'circle',
        },
        edge: {
          type: 'quadratic',
        },
      })

      graphRef.current = g
      g.render().catch(() => {})
    })

    return () => {
      if (settleTimerRef.current) {
        window.clearTimeout(settleTimerRef.current)
        settleTimerRef.current = null
      }
      if (graphRef.current) {
        graphRef.current.destroy()
        graphRef.current = null
      }
    }
  }, [graphData, visibleContext, focusSkillId, graphMode])

  useEffect(() => {
    if (graphMode === 'live' && graphRef.current) {
      graphRef.current.destroy()
      graphRef.current = null
    }
    if (graphMode === 'static') {
      setHoverNodeId(null)
      setLivePaused(false)
    }
  }, [graphMode])

  useEffect(() => {
    if (graphMode !== 'live') return
    const graph = getLiveGraphControls(liveGraphRef)
    if (!graph) return
    const linkForce = graph.d3Force('link') as { distance?: (value: number | ((link: LiveLink) => number)) => unknown; strength?: (value: number | ((link: LiveLink) => number)) => unknown } | undefined
    linkForce?.distance?.((link: LiveLink) => link.isFocusEdge ? 72 : 112)
    linkForce?.strength?.((link: LiveLink) => link.isFocusEdge ? 0.34 : 0.12)
    const chargeForce = graph.d3Force('charge') as { strength?: (value: number) => unknown } | undefined
    chargeForce?.strength?.(-120)
    graph.d3ReheatSimulation()
    graph.zoomToFit(900, 80)
    setLivePaused(false)
  }, [graphMode, liveGraphData])

  const edgeTypes = graphData ? [...new Set(graphData.edges.map(e => e.edge_type))] : []
  const nodeTypes = graphData ? [...new Set(graphData.nodes.map(n => n.node_type || 'skill'))] : []
  const visibleNodeTypes = graphData
    ? [...new Set(graphData.nodes
      .filter(n => nodeTypeFilter.length === 0 || nodeTypeFilter.includes(n.node_type || 'skill'))
      .map(n => n.node_type || 'skill'))]
    : []
  const liveNeighborIds = useMemo(() => {
    const ids = new Set<string>()
    if (!hoverNodeId) return ids
    ids.add(hoverNodeId)
    liveGraphData.links.forEach(link => {
      const sourceId = typeof link.source === 'object' ? String(link.source.id) : String(link.source)
      const targetId = typeof link.target === 'object' ? String(link.target.id) : String(link.target)
      if (sourceId === hoverNodeId) ids.add(targetId)
      if (targetId === hoverNodeId) ids.add(sourceId)
    })
    return ids
  }, [hoverNodeId, liveGraphData.links])

  const drawLiveNode = (node: LiveNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const x = node.x || 0
    const y = node.y || 0
    const isHoverContext = !hoverNodeId || liveNeighborIds.has(node.id)
    const alpha = node.opacity * (isHoverContext ? 1 : 0.14)
    const radius = Math.max(4, node.size / 4.2)
    const label = truncateLabel(node.label, 28)
    const fontSize = Math.max(7.5, Math.min(13, 11 / globalScale))
    const labelWidth = ctx.measureText(label).width + 12 / globalScale
    const labelHeight = 18 / globalScale

    ctx.save()
    ctx.globalAlpha = alpha
    const glow = ctx.createRadialGradient(x, y, radius * 0.2, x, y, radius * 4.4)
    glow.addColorStop(0, node.color)
    glow.addColorStop(0.42, `${node.color}55`)
    glow.addColorStop(1, 'rgba(0,0,0,0)')
    ctx.fillStyle = glow
    ctx.beginPath()
    ctx.arc(x, y, radius * (node.isFocused ? 5.2 : 4.0), 0, Math.PI * 2)
    ctx.fill()

    ctx.fillStyle = node.color
    ctx.strokeStyle = node.isFocused ? '#fff4d6' : 'rgba(255,255,255,0.42)'
    ctx.lineWidth = node.isFocused ? 2.4 / globalScale : 1 / globalScale
    ctx.beginPath()
    ctx.arc(x, y, radius * (node.isFocused ? 1.38 : 1), 0, Math.PI * 2)
    ctx.fill()
    ctx.stroke()

    ctx.fillStyle = '#06111f'
    ctx.font = `${Math.max(6, fontSize * 0.78)}px sans-serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(getNodeIcon(node.nodeType, node.skillType), x, y)

    if (globalScale > 0.52 || node.isFocused || node.nodeType === 'skill') {
      const labelY = y + radius + 12 / globalScale
      ctx.fillStyle = 'rgba(5, 13, 32, 0.72)'
      ctx.strokeStyle = 'rgba(125, 183, 255, 0.2)'
      ctx.lineWidth = 1 / globalScale
      ctx.beginPath()
      ctx.roundRect(x - labelWidth / 2, labelY - labelHeight / 2, labelWidth, labelHeight, 7 / globalScale)
      ctx.fill()
      ctx.stroke()
      ctx.fillStyle = node.isFocusContext ? '#e8f1ff' : 'rgba(232,241,255,0.78)'
      ctx.font = `${fontSize}px sans-serif`
      ctx.fillText(label, x, labelY)
    }
    ctx.restore()
  }

  const drawLiveLink = (link: LiveLink, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const source = link.source as LiveNode
    const target = link.target as LiveNode
    if (
      typeof source?.x !== 'number'
      || typeof source?.y !== 'number'
      || typeof target?.x !== 'number'
      || typeof target?.y !== 'number'
    ) return
    const sourceId = String(source.id)
    const targetId = String(target.id)
    const isHoverContext = !hoverNodeId || sourceId === hoverNodeId || targetId === hoverNodeId
    ctx.save()
    ctx.globalAlpha = (focusSkillId && !link.isFocusEdge ? 0.12 : 0.42) * (isHoverContext ? 1 : 0.12)
    ctx.strokeStyle = link.color
    ctx.lineWidth = Math.max(0.35, link.width / globalScale)
    ctx.shadowColor = link.color
    ctx.shadowBlur = link.isFocusEdge ? 8 : 3
    ctx.beginPath()
    ctx.moveTo(source.x, source.y)
    ctx.lineTo(target.x, target.y)
    ctx.stroke()
    ctx.restore()
  }

  return (
    <div style={{ padding: embedded ? 0 : 24, height: embedded ? 680 : 'calc(100vh - 120px)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontWeight: 800, letterSpacing: -0.4 }}>{embedded ? 'Skill Graph Nebula' : 'SkillOS Memory Nebula'}</h2>
          <div style={{ color: '#6b7280', fontSize: 13, marginTop: 4 }}>
            Orbital memory map. Drag, zoom, and focus a skill to reveal its nearest knowledge orbit.
          </div>
          {focusSkillId && (
            <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 4 }}>
              Focused on skill node: <Tag color="red">{focusTargetName || `${focusSkillId.slice(0, 8)}...`}</Tag>
            </div>
          )}
        </div>
        <Space>
          <Segmented
            value={graphMode}
            onChange={value => setGraphMode(value as 'static' | 'live')}
            options={[
              { label: 'Static Nebula', value: 'static' },
              { label: 'Live Nebula', value: 'live' },
            ]}
          />
          {graphMode === 'live' && (
            <Button
              icon={livePaused ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
              onClick={() => {
                const graph = getLiveGraphControls(liveGraphRef)
                if (!graph) return
                if (livePaused) {
                  graph.resumeAnimation()
                  graph.d3ReheatSimulation()
                } else {
                  graph.pauseAnimation()
                }
                setLivePaused(!livePaused)
              }}
            >
              {livePaused ? 'Resume Motion' : 'Freeze'}
            </Button>
          )}
          <Select
            mode="multiple"
            placeholder="Node types"
            style={{ minWidth: 220 }}
            allowClear
            onChange={setNodeTypeFilter}
            options={nodeTypes.map(t => ({ label: t.replace(/_/g, ' '), value: t }))}
          />
          <Select
            mode="multiple"
            placeholder="Relation types"
            style={{ minWidth: 200 }}
            allowClear
            onChange={setEdgeFilter}
            options={edgeTypes.map(t => ({ label: t.replace(/_/g, ' '), value: t }))}
          />
          <Button icon={<ReloadOutlined />} onClick={loadGraph}>Refresh</Button>
        </Space>
      </div>

      <Card
        style={{
          flex: 1,
          borderRadius: 24,
          overflow: 'hidden',
          padding: 0,
          border: '1px solid rgba(122, 164, 255, 0.24)',
          boxShadow: '0 24px 70px rgba(7, 13, 31, 0.18)',
          background: '#050917',
        }}
        styles={{ body: { padding: 0, height: '100%' } }}
      >
        {loading ? (
          <div style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            height: 500,
            color: '#dbeafe',
            background: 'radial-gradient(circle at 50% 45%, rgba(61,231,255,0.16), transparent 32%), #050917',
          }}>
            <Spin size="large" tip="Loading graph data..." />
          </div>
        ) : (
          <div
            style={{
              position: 'relative',
              height: '100%',
              minHeight: embedded ? 560 : 620,
              overflow: 'hidden',
              background: [
                'radial-gradient(circle at 18% 18%, rgba(61,231,255,0.18), transparent 28%)',
                'radial-gradient(circle at 78% 24%, rgba(185,140,255,0.16), transparent 30%)',
                'radial-gradient(circle at 52% 72%, rgba(255,197,110,0.11), transparent 34%)',
                'linear-gradient(135deg, #050917 0%, #071225 48%, #02040b 100%)',
              ].join(', '),
            }}
          >
            <div
              aria-hidden
              style={{
                position: 'absolute',
                inset: 0,
                opacity: 0.42,
                backgroundImage: [
                  'radial-gradient(rgba(255,255,255,0.85) 0.8px, transparent 0.8px)',
                  'radial-gradient(rgba(125,183,255,0.38) 1px, transparent 1px)',
                ].join(', '),
                backgroundPosition: '0 0, 18px 22px',
                backgroundSize: '38px 38px, 74px 74px',
                pointerEvents: 'none',
              }}
            />
            <div
              aria-hidden
              style={{
                position: 'absolute',
                inset: 0,
                background: 'radial-gradient(circle at center, transparent 0%, rgba(2,4,11,0.18) 58%, rgba(2,4,11,0.82) 100%)',
                pointerEvents: 'none',
                zIndex: 1,
              }}
            />
            <div
              ref={containerRef}
              style={{
                position: 'relative',
                zIndex: 2,
                width: '100%',
                height: '100%',
                minHeight: embedded ? 560 : 620,
              }}
            >
              {graphMode === 'live' && (
                <ForceGraph2D<LiveNode, LiveLink>
                  ref={liveGraphRef as never}
                  graphData={liveGraphData}
                  width={containerRef.current?.clientWidth || 960}
                  height={containerRef.current?.clientHeight || (embedded ? 560 : 620)}
                  backgroundColor="rgba(0,0,0,0)"
                  nodeId="id"
                  linkSource="source"
                  linkTarget="target"
                  nodeCanvasObject={drawLiveNode}
                  nodePointerAreaPaint={(node, color, ctx) => {
                    const radius = Math.max(10, node.size / 2.8)
                    ctx.fillStyle = color
                    ctx.beginPath()
                    ctx.arc(node.x || 0, node.y || 0, radius, 0, Math.PI * 2)
                    ctx.fill()
                  }}
                  linkCanvasObject={drawLiveLink}
                  linkCanvasObjectMode={() => 'replace'}
                  nodeLabel={node => `${node.label}<br/>${LAYER_LABEL[node.nodeType] || node.nodeType}`}
                  linkLabel={link => link.edgeType.replace(/_/g, ' ')}
                  nodeVal={node => Math.max(1, node.size / 18)}
                  nodeColor={node => node.color}
                  linkColor={link => link.color}
                  linkWidth={link => link.width}
                  linkDirectionalParticles={link => link.isFocusEdge ? 2 : 0}
                  linkDirectionalParticleWidth={link => link.isFocusEdge ? 2 : 0}
                  linkDirectionalParticleSpeed={0.004}
                  cooldownTicks={140}
                  cooldownTime={6000}
                  d3AlphaDecay={0.035}
                  d3VelocityDecay={0.42}
                  warmupTicks={18}
                  autoPauseRedraw={false}
                  minZoom={0.25}
                  maxZoom={5}
                  onNodeHover={node => setHoverNodeId(node?.id ? String(node.id) : null)}
                  onNodeClick={node => {
                    setHoverNodeId(node?.id ? String(node.id) : null)
                    getLiveGraphControls(liveGraphRef)?.zoomToFit(650, 90)
                  }}
                  onBackgroundClick={() => setHoverNodeId(null)}
                  onEngineStop={() => setLivePaused(true)}
                />
              )}
            </div>
            <div
              style={{
                position: 'absolute',
                left: 18,
                top: 18,
                zIndex: 3,
                padding: '12px 14px',
                borderRadius: 16,
                color: '#e8f1ff',
                backdropFilter: 'blur(16px)',
                background: 'linear-gradient(135deg, rgba(10, 22, 48, 0.82), rgba(7, 12, 28, 0.56))',
                border: '1px solid rgba(125, 183, 255, 0.24)',
                boxShadow: '0 12px 32px rgba(0,0,0,0.24)',
                maxWidth: 320,
              }}
            >
              <div style={{ fontSize: 12, color: '#9fb7da', textTransform: 'uppercase', letterSpacing: 1.4 }}>Live Graph Context</div>
              <div style={{ display: 'flex', gap: 18, marginTop: 8 }}>
                <div>
                  <div style={{ fontSize: 24, fontWeight: 850 }}>{renderStats.nodes}</div>
                  <div style={{ fontSize: 11, color: '#94a3b8' }}>nodes</div>
                </div>
                <div>
                  <div style={{ fontSize: 24, fontWeight: 850 }}>{renderStats.edges}</div>
                  <div style={{ fontSize: 11, color: '#94a3b8' }}>relations</div>
                </div>
                <div>
                  <div style={{ fontSize: 24, fontWeight: 850 }}>{visibleNodeTypes.length}</div>
                  <div style={{ fontSize: 11, color: '#94a3b8' }}>layers</div>
                </div>
              </div>
              {focusSkillId && (
                <div style={{ marginTop: 10, fontSize: 12, color: '#ffe8a3' }}>
                  Focus mode highlights direct neighbors and dims distant memory.
                </div>
              )}
            </div>
            <div
              style={{
                position: 'absolute',
                right: 18,
                bottom: 18,
                zIndex: 3,
                display: 'flex',
                flexWrap: 'wrap',
                justifyContent: 'flex-end',
                gap: 8,
                maxWidth: 520,
              }}
            >
              {visibleNodeTypes.slice(0, 12).map(type => (
                <span
                  key={type}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    color: '#dbeafe',
                    fontSize: 11,
                    padding: '5px 8px',
                    borderRadius: 999,
                    background: 'rgba(7, 14, 31, 0.62)',
                    border: '1px solid rgba(148, 163, 184, 0.18)',
                    backdropFilter: 'blur(12px)',
                  }}
                >
                  <i style={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    background: NODE_TYPE_COLOR[type] || NODE_TYPE_COLOR.skill,
                    boxShadow: `0 0 12px ${NODE_TYPE_COLOR[type] || NODE_TYPE_COLOR.skill}`,
                  }} />
                  {LAYER_LABEL[type] || type.replace(/_/g, ' ')}
                </span>
              ))}
            </div>
          </div>
        )}
      </Card>

      {graphData && (
        <div style={{ marginTop: 8, color: '#6b7280', fontSize: 12 }}>
          {renderStats.nodes} rendered nodes · {renderStats.edges} rendered relations
          {focusSkillId && ` · full graph has ${graphData.nodes.length} nodes / ${graphData.edges.length} relations`}
        </div>
      )}
    </div>
  )
}
