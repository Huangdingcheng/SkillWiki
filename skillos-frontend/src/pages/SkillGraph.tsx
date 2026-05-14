import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  InputNumber,
  Popover,
  Select,
  Segmented,
  Slider,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  AimOutlined,
  ExportOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SettingOutlined,
  ShareAltOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from '@ant-design/icons'
import { useLocation, useNavigate } from 'react-router-dom'
import { graphApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import type {
  GraphData,
  GraphViewData,
  GraphViewEdgeData,
  GraphViewMode,
  GraphViewNodeData,
} from '@/api/types'

const { Text, Paragraph } = Typography

const TYPE_COLOR: Record<string, string> = {
  atomic: '#1677ff',
  functional: '#722ed1',
  strategic: '#fa8c16',
}

const KIND_COLOR: Record<string, string> = {
  source: '#08979c',
  trajectory: '#08979c',
  skill: '#1677ff',
  execution: '#2f54eb',
  validation: '#52c41a',
  version: '#fa8c16',
}

const KIND_LABEL: Record<string, string> = {
  source: 'Source / Trajectory',
  trajectory: 'Trajectory',
  skill: 'Skill',
  execution: 'Execution',
  validation: 'Validation',
  version: 'Version',
}

const STATE_OPACITY: Record<string, number> = {
  S0: 0.4,
  S1: 0.5,
  S2: 0.6,
  S3: 0.75,
  S4: 1,
  S5: 0.85,
  S6: 0.3,
  S7: 0.2,
}

const STATE_LABEL: Record<string, string> = {
  S0: 'Raw',
  S1: 'Candidate',
  S2: 'Draft',
  S3: 'Verified',
  S4: 'Released',
  S5: 'Degraded',
  S6: 'Deprecated',
  S7: 'Archived',
}

const EDGE_COLOR: Record<string, string> = {
  depends_on: '#1677ff',
  composes_with: '#722ed1',
  similar_to: '#52c41a',
  evolved_from: '#fa8c16',
  conflicts_with: '#ff4d4f',
  replaces: '#eb2f96',
  specializes: '#13c2c2',
  generalizes: '#d4a106',
  derived_from: '#08979c',
  executed_as: '#2f54eb',
  validated_by: '#52c41a',
  versioned_as: '#fa8c16',
}

const GRAPH_VIEW_OPTIONS: { label: string; value: GraphViewMode }[] = [
  { label: 'Skill-only', value: 'skill_only' },
  { label: 'Provenance', value: 'provenance' },
  { label: 'Version impact', value: 'version_impact' },
]

const GRAPH_VIEW_COPY: Record<GraphViewMode, { subtitle: string; empty: string; reload: string }> = {
  skill_only: {
    subtitle: 'Skill dependency, composition, similarity, and evolution links.',
    empty: 'No Skill graph data is available.',
    reload: 'Reload Skill graph',
  },
  provenance: {
    subtitle: 'Typed Source, Skill, Execution, Validation, and Version evidence chain.',
    empty: 'No heterogeneous provenance graph data is available.',
    reload: 'Reload provenance graph',
  },
  version_impact: {
    subtitle: 'Meta-path projection with version, shared-source, and validation evidence.',
    empty: 'No projected version-impact graph data is available.',
    reload: 'Reload impact view',
  },
}

type GraphMode = 'full' | 'subgraph'

type GraphLayoutSettings = {
  repulsion: number
  attraction: number
  linkDistance: number
  nodeSpacing: number
}

type GraphCanvasSize = {
  width: number
  height: number
}

type GraphEvent = {
  target?: { id?: string }
}

type GraphInstance = {
  destroy: () => void
  draw: () => void | Promise<void>
  fitView: (options?: unknown, animation?: unknown) => void | Promise<void>
  getZoom: () => number
  on: (eventName: string, handler: (event: GraphEvent) => void) => void
  render: () => void | Promise<void>
  updateEdgeData: (edges: Array<{ id: string; style: { badgeText: string } }>) => void
  zoomBy: (ratio: number, animation?: unknown) => void | Promise<void>
}

type ForceEdgeDatum = {
  data?: { weight?: number }
  weight?: number
}

const GRAPH_LAYOUT_STORAGE_KEY = 'skillos.graph.layoutSettings.v1'
const EDGE_LABEL_ZOOM_THRESHOLD = 0.85
const DEFAULT_GRAPH_LAYOUT: GraphLayoutSettings = {
  repulsion: 180,
  attraction: 0.35,
  linkDistance: 150,
  nodeSpacing: 56,
}
const GRAPH_LAYOUT_PRESETS: Record<string, GraphLayoutSettings> = {
  compact: {
    repulsion: 100,
    attraction: 0.6,
    linkDistance: 100,
    nodeSpacing: 44,
  },
  balanced: DEFAULT_GRAPH_LAYOUT,
  open: {
    repulsion: 300,
    attraction: 0.2,
    linkDistance: 220,
    nodeSpacing: 76,
  },
}

function clampNumber(value: unknown, min: number, max: number, fallback: number) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return fallback
  return Math.min(max, Math.max(min, numeric))
}

function normalizeLayoutSettings(value: Partial<GraphLayoutSettings> = {}): GraphLayoutSettings {
  return {
    repulsion: clampNumber(value.repulsion, 40, 400, DEFAULT_GRAPH_LAYOUT.repulsion),
    attraction: clampNumber(value.attraction, 0.05, 1, DEFAULT_GRAPH_LAYOUT.attraction),
    linkDistance: clampNumber(value.linkDistance, 60, 280, DEFAULT_GRAPH_LAYOUT.linkDistance),
    nodeSpacing: clampNumber(value.nodeSpacing, 36, 96, DEFAULT_GRAPH_LAYOUT.nodeSpacing),
  }
}

function loadLayoutSettings(): GraphLayoutSettings {
  if (typeof window === 'undefined') return DEFAULT_GRAPH_LAYOUT
  try {
    const stored = window.localStorage.getItem(GRAPH_LAYOUT_STORAGE_KEY)
    return stored ? normalizeLayoutSettings(JSON.parse(stored)) : DEFAULT_GRAPH_LAYOUT
  } catch {
    return DEFAULT_GRAPH_LAYOUT
  }
}

function saveLayoutSettings(settings: GraphLayoutSettings) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(GRAPH_LAYOUT_STORAGE_KEY, JSON.stringify(settings))
  } catch {
    // Layout persistence is optional.
  }
}

function formatPercent(value?: number | null) {
  if (!Number.isFinite(Number(value))) return 'N/A'
  return `${Math.round(Number(value) * 100)}%`
}

function formatEdgeType(edgeType: string) {
  return edgeType.replace(/_/g, ' ')
}

function formatKind(kind?: string) {
  if (!kind) return 'Skill'
  return KIND_LABEL[kind] || kind
}

function nodeColor(node?: GraphViewNodeData) {
  if (!node) return '#8c8c8c'
  if (node.kind && KIND_COLOR[node.kind]) return KIND_COLOR[node.kind]
  return TYPE_COLOR[String(node.skill_type || '')] || '#8c8c8c'
}

function uniqueTags(tags?: string[]): string[] {
  return Array.from(new Set((tags ?? []).filter(Boolean)))
}

function stableRandom(seed: string) {
  let hash = 2166136261
  for (let index = 0; index < seed.length; index += 1) {
    hash ^= seed.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0) / 4294967295
}

function edgeWeight(edge?: ForceEdgeDatum) {
  return clampNumber(edge?.data?.weight ?? edge?.weight, 0, 1, 1)
}

function weightedLinkDistance(edge: ForceEdgeDatum | undefined, baseDistance: number) {
  const weight = edgeWeight(edge)
  return Math.round(baseDistance * (1.4 - weight * 0.7))
}

function weightedEdgeStrength(edge: ForceEdgeDatum | undefined, baseAttraction: number) {
  const weight = edgeWeight(edge)
  return baseAttraction * (0.45 + weight * 0.75) * 80
}

function formatEdgeLabel(edge: GraphViewEdgeData) {
  const score = edge.confidence ?? edge.weight
  return `${formatEdgeType(edge.edge_type)} / ${score.toFixed(2)}`
}

function shouldShowEdgeLabels(zoom: number) {
  return zoom >= EDGE_LABEL_ZOOM_THRESHOLD
}

function formatMetadataValue(value: unknown) {
  if (value === null || value === undefined || value === '') return 'N/A'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number' || typeof value === 'string') return String(value)
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function metadataEntries(metadata?: Record<string, unknown>) {
  return Object.entries(metadata ?? {}).filter(([, value]) => value !== undefined && value !== null && value !== '')
}

function graphDataToView(data: GraphData): GraphViewData {
  return {
    view: 'skill_only',
    source_endpoint: '/api/v1/graph/subgraph',
    nodes: data.nodes.map(node => ({
      id: node.id,
      name: node.name,
      kind: 'skill',
      description: node.description || '',
      skill_type: node.skill_type,
      state: node.state,
      tags: node.tags,
      version: node.version,
      granularity_level: node.granularity_level,
      success_rate: node.success_rate,
      usage_count: node.usage_count,
      metadata: node.metadata || {},
    })),
    edges: data.edges.map(edge => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      edge_type: edge.edge_type,
      weight: edge.weight,
      confidence: edge.confidence ?? null,
      metadata: edge.metadata || {},
    })),
    stats: data.stats,
    metadata: {},
    validation_evidence: {},
  }
}

function calculateInitialNodePositions(
  nodes: GraphViewNodeData[],
  edges: GraphViewEdgeData[],
  canvasSize: GraphCanvasSize,
  settings: GraphLayoutSettings,
) {
  const positions = new Map<string, { x: number; y: number }>()
  if (nodes.length === 0) return positions

  const width = Math.max(320, canvasSize.width || 800)
  const height = Math.max(360, canvasSize.height || 560)
  const padding = Math.max(48, settings.nodeSpacing + 18)
  const spreadRatio = 0.72 + ((settings.repulsion - 40) / 360) * 0.28
  const contentWidth = Math.max(120, (width - padding * 2) * spreadRatio)
  const contentHeight = Math.max(120, (height - padding * 2) * spreadRatio)
  const offsetX = (width - contentWidth) / 2
  const offsetY = (height - contentHeight) / 2
  const ratio = contentWidth / contentHeight
  const columns = Math.max(1, Math.ceil(Math.sqrt(nodes.length * ratio)))
  const rows = Math.max(1, Math.ceil(nodes.length / columns))
  const cellWidth = contentWidth / columns
  const cellHeight = contentHeight / rows
  const connected = new Set<string>()

  edges.forEach(edge => {
    connected.add(edge.source)
    connected.add(edge.target)
  })

  const orderedNodes = [...nodes].sort((a, b) => {
    const connectionDelta = Number(connected.has(b.id)) - Number(connected.has(a.id))
    if (connectionDelta !== 0) return connectionDelta
    return stableRandom(a.id) - stableRandom(b.id)
  })

  orderedNodes.forEach((node, index) => {
    const column = index % columns
    const row = Math.floor(index / columns)
    const jitterScale = connected.has(node.id) ? 0.28 : 0.42
    const jitterX = (stableRandom(`${node.id}:x`) - 0.5) * cellWidth * jitterScale
    const jitterY = (stableRandom(`${node.id}:y`) - 0.5) * cellHeight * jitterScale
    const x = offsetX + cellWidth * (column + 0.5) + jitterX
    const y = offsetY + cellHeight * (row + 0.5) + jitterY

    positions.set(node.id, {
      x: Math.min(width - padding, Math.max(padding, x)),
      y: Math.min(height - padding, Math.max(padding, y)),
    })
  })

  return positions
}

export default function SkillGraph() {
  const navigate = useNavigate()
  const location = useLocation()
  const graphShellRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<GraphInstance | null>(null)
  const openedFromQuery = useRef<string | null>(null)

  const [viewMode, setViewMode] = useState<GraphViewMode>('skill_only')
  const [mode, setMode] = useState<GraphMode>('full')
  const [graphData, setGraphData] = useState<GraphViewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [subgraphLoading, setSubgraphLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [edgeFilter, setEdgeFilter] = useState<string[]>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [centerSkillId, setCenterSkillId] = useState<string | null>(null)
  const [depth, setDepth] = useState(2)
  const [layoutSettings, setLayoutSettings] = useState<GraphLayoutSettings>(loadLayoutSettings)
  const [layoutDraft, setLayoutDraft] = useState<GraphLayoutSettings>(loadLayoutSettings)
  const [layoutPanelOpen, setLayoutPanelOpen] = useState(false)
  const [canvasSize, setCanvasSize] = useState<GraphCanvasSize>({ width: 800, height: 560 })

  const selectedNode = useMemo(
    () => graphData?.nodes.find(node => node.id === selectedNodeId) || null,
    [graphData, selectedNodeId],
  )

  const selectedEdge = useMemo(
    () => graphData?.edges.find(edge => edge.id === selectedEdgeId) || null,
    [graphData, selectedEdgeId],
  )

  const centerNode = useMemo(
    () => graphData?.nodes.find(node => node.id === centerSkillId) || null,
    [graphData, centerSkillId],
  )

  const nodeById = useMemo(() => {
    const nodes = new Map<string, GraphViewNodeData>()
    graphData?.nodes.forEach(node => nodes.set(node.id, node))
    return nodes
  }, [graphData])

  const edgeTypes = useMemo(
    () => (graphData ? [...new Set(graphData.edges.map(edge => edge.edge_type))] : []),
    [graphData],
  )

  const nodeLegend = useMemo(() => {
    if (!graphData) return []
    const values = viewMode === 'skill_only'
      ? Object.keys(TYPE_COLOR)
      : [...new Set(graphData.nodes.map(node => node.kind))]
    return values.map(value => ({
      value,
      label: viewMode === 'skill_only' ? value : formatKind(value),
      color: viewMode === 'skill_only' ? TYPE_COLOR[value] : KIND_COLOR[value] || '#8c8c8c',
    }))
  }, [graphData, viewMode])

  const resetSelection = useCallback(() => {
    setSelectedNodeId(null)
    setSelectedEdgeId(null)
    setCenterSkillId(null)
    setEdgeFilter([])
  }, [])

  const loadGraphForView = useCallback(async (nextView: GraphViewMode) => {
    setLoading(true)
    setError(null)
    resetSelection()
    try {
      const data = await graphApi.view(nextView, 300)
      setGraphData(data)
      setMode('full')
    } catch (err) {
      setError(getApiErrorMessage(err, 'Failed to load graph data'))
    } finally {
      setLoading(false)
    }
  }, [resetSelection])

  const loadSubgraph = useCallback(async (skillId: string, nextDepth = depth) => {
    if (!skillId) return
    setSubgraphLoading(true)
    setError(null)
    setViewMode('skill_only')
    try {
      const data = await graphApi.subgraph(skillId, nextDepth)
      setGraphData(graphDataToView(data))
      setMode('subgraph')
      setCenterSkillId(skillId)
      setSelectedNodeId(skillId)
      setSelectedEdgeId(null)
      setEdgeFilter([])
    } catch (err) {
      setError(getApiErrorMessage(err, 'Failed to load subgraph; current view is preserved'))
    } finally {
      setSubgraphLoading(false)
      setLoading(false)
    }
  }, [depth])

  useEffect(() => {
    if (viewMode !== 'skill_only') return
    const querySkillId = new URLSearchParams(location.search).get('skill_id')
    const timeoutId = window.setTimeout(() => {
      if (querySkillId && openedFromQuery.current !== querySkillId) {
        openedFromQuery.current = querySkillId
        void loadSubgraph(querySkillId, depth)
        return
      }
      if (!querySkillId && !graphData) {
        void loadGraphForView('skill_only')
      }
    }, 0)
    return () => window.clearTimeout(timeoutId)
  }, [depth, graphData, loadGraphForView, loadSubgraph, location.search, viewMode])

  useEffect(() => {
    if (!graphData?.nodes.length || !graphShellRef.current) return

    const target = graphShellRef.current
    const updateSize = () => {
      const next = {
        width: Math.max(320, Math.round(target.clientWidth || 800)),
        height: Math.max(360, Math.round(target.clientHeight || 560)),
      }

      setCanvasSize(previous => {
        if (Math.abs(previous.width - next.width) < 8 && Math.abs(previous.height - next.height) < 8) {
          return previous
        }
        return next
      })
    }

    updateSize()

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateSize)
      return () => window.removeEventListener('resize', updateSize)
    }

    const observer = new ResizeObserver(updateSize)
    observer.observe(target)
    return () => observer.disconnect()
  }, [graphData?.nodes.length])

  useEffect(() => {
    if (!graphData || !containerRef.current || graphData.nodes.length === 0) return

    let disposed = false

    import('@antv/g6').then((G6) => {
      if (disposed || !containerRef.current) return

      if (graphRef.current) {
        graphRef.current.destroy()
        graphRef.current = null
      }

      const filteredEdges = edgeFilter.length > 0
        ? graphData.edges.filter(edge => edgeFilter.includes(edge.edge_type))
        : graphData.edges
      const graphWidth = canvasSize.width || containerRef.current.clientWidth || 800
      const graphHeight = canvasSize.height || containerRef.current.clientHeight || 560
      const forceLayout = {
        type: 'force',
        preventOverlap: true,
        nodeSize: layoutSettings.nodeSpacing,
        linkDistance: (edge?: ForceEdgeDatum) => weightedLinkDistance(edge, layoutSettings.linkDistance),
        nodeStrength: layoutSettings.repulsion,
        edgeStrength: (edge?: ForceEdgeDatum) => weightedEdgeStrength(edge, layoutSettings.attraction),
      }
      const initialPositions = calculateInitialNodePositions(
        graphData.nodes,
        filteredEdges,
        { width: graphWidth, height: graphHeight },
        layoutSettings,
      )

      const nodes = graphData.nodes.map(node => {
        const color = nodeColor(node)
        const selected = node.id === selectedNodeId
        const centered = node.id === centerSkillId
        const position = initialPositions.get(node.id)
        return {
          id: node.id,
          data: {
            label: node.name,
            nodeKind: node.kind,
            state: node.state,
          },
          style: {
            x: position?.x,
            y: position?.y,
            fill: color,
            fillOpacity: node.kind === 'skill' ? (STATE_OPACITY[String(node.state || '')] || 0.65) : 0.92,
            stroke: selected || centered ? '#111827' : color,
            strokeOpacity: 1,
            lineWidth: selected || centered ? 3 : 1,
            size: Math.max(28, Math.min(58, 30 + Number(node.usage_count || 0) * 0.5)),
            labelText: node.name,
            labelFill: '#111827',
            labelFontSize: 8,
            labelFontWeight: selected || centered ? 700 : 600,
            labelMaxWidth: '260%',
            labelPlacement: 'bottom' as const,
            labelOffsetY: 5,
            labelStroke: '#fff',
            labelLineWidth: 3,
            labelWordWrap: true,
            cursor: 'pointer',
          },
        }
      })

      const edges = filteredEdges.map(edge => {
        const selected = edge.id === selectedEdgeId
        const color = EDGE_COLOR[edge.edge_type] || '#8c8c8c'
        return {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          data: { edgeType: edge.edge_type, weight: edge.weight },
          style: {
            stroke: color,
            strokeOpacity: selected ? 1 : 0.82,
            lineWidth: selected ? Math.max(2.5, edge.weight * 3) : Math.max(1, edge.weight * 2),
            endArrow: true,
            labelText: formatEdgeLabel(edge),
            labelFontSize: 9,
            labelFill: color,
            label: false,
            labelStroke: '#fff',
            labelLineWidth: 3,
            labelPlacement: 'center' as const,
            labelOffsetX: 0,
            labelOffsetY: -18,
            badgeText: formatEdgeLabel(edge),
            badgeFontSize: 9,
            badgeFill: color,
            badgeBackgroundFill: '#fff',
            badgeBackgroundOpacity: 0.9,
            badgeBackgroundRadius: 4,
            badgePadding: [1, 4, 1, 4],
            badgePlacement: 'suffix' as const,
            badgeOffsetX: 0,
            badgeOffsetY: -18,
            cursor: 'pointer',
          },
        }
      })

      const g = new G6.Graph({
        container: containerRef.current,
        width: graphWidth,
        height: graphHeight,
        data: { nodes, edges },
        autoFit: 'view',
        padding: [48, 48, 72, 48],
        zoomRange: [0.25, 1.25],
        ...(filteredEdges.length > 0 ? { layout: forceLayout } : {}),
        behaviors: [
          'drag-canvas',
          'zoom-canvas',
          'drag-element',
          {
            type: 'auto-adapt-label',
            padding: 8,
            throttle: 64,
          },
          {
            type: 'click-select',
            degree: 0,
          },
        ],
        node: { type: 'circle' },
        edge: { type: 'line' },
      }) as GraphInstance

      let graphReady = false
      let edgeLabelsVisible: boolean | null = null
      const syncEdgeLabelVisibility = () => {
        if (disposed || !graphReady || filteredEdges.length === 0) return
        const visible = shouldShowEdgeLabels(g.getZoom())
        if (edgeLabelsVisible === visible) return

        edgeLabelsVisible = visible
        g.updateEdgeData(filteredEdges.map(edge => ({
          id: edge.id,
          style: { badgeText: visible ? formatEdgeLabel(edge) : '' },
        })))
        void Promise.resolve(g.draw())
      }

      g.on('node:click', event => {
        const id = event.target?.id
        if (id) {
          setSelectedNodeId(id)
          setSelectedEdgeId(null)
        }
      })

      g.on('edge:click', event => {
        const id = event.target?.id
        if (id) {
          setSelectedEdgeId(id)
          setSelectedNodeId(null)
        }
      })

      g.on('canvas:click', () => {
        setSelectedNodeId(null)
        setSelectedEdgeId(null)
      })

      graphRef.current = g
      void Promise.resolve(g.render()).then(() => {
        if (!disposed) {
          graphReady = true
          syncEdgeLabelVisibility()
          void Promise.resolve(g.fitView({ when: 'always' }, { duration: 160 })).then(() => {
            syncEdgeLabelVisibility()
            g.on('aftertransform', syncEdgeLabelVisibility)
          })
        }
      })
    })

    return () => {
      disposed = true
      if (graphRef.current) {
        graphRef.current.destroy()
        graphRef.current = null
      }
    }
  }, [canvasSize, centerSkillId, edgeFilter, graphData, layoutSettings, selectedEdgeId, selectedNodeId])

  const handleViewChange = (nextView: GraphViewMode) => {
    setViewMode(nextView)
    openedFromQuery.current = null
    navigate('/graph', { replace: true })
    void loadGraphForView(nextView)
  }

  const reloadCurrentView = () => {
    if (viewMode === 'skill_only' && mode === 'subgraph' && centerSkillId) {
      void loadSubgraph(centerSkillId, depth)
      return
    }
    void loadGraphForView(viewMode)
  }

  const returnToFullGraph = () => {
    openedFromQuery.current = null
    navigate('/graph', { replace: true })
    setViewMode('skill_only')
    void loadGraphForView('skill_only')
  }

  const openWiki = () => {
    if (!selectedNode || selectedNode.kind !== 'skill') return
    navigate(`/wiki?skill_id=${encodeURIComponent(selectedNode.id)}`)
  }

  const expandSelectedSkill = () => {
    if (!selectedNode || selectedNode.kind !== 'skill') return
    void loadSubgraph(selectedNode.id, depth)
  }

  const hasNodes = Boolean(graphData?.nodes.length)
  const selectedNodeTags = uniqueTags(selectedNode?.tags)
  const selectedNodeMetadata = metadataEntries(selectedNode?.metadata)
  const selectedEdgeMetadata = metadataEntries(selectedEdge?.metadata)
  const viewCopy = GRAPH_VIEW_COPY[viewMode]
  const detailTitle = selectedEdge ? 'Edge Details' : 'Node Details'

  const zoomGraph = (ratio: number) => {
    void graphRef.current?.zoomBy(ratio, { duration: 180 })
  }

  const fitGraph = () => {
    void graphRef.current?.fitView({ when: 'always' }, { duration: 180 })
  }

  const updateLayoutDraft = (field: keyof GraphLayoutSettings, value: number) => {
    setLayoutDraft(previous => normalizeLayoutSettings({ ...previous, [field]: value }))
  }

  const applyLayoutSettings = () => {
    const next = normalizeLayoutSettings(layoutDraft)
    setLayoutSettings(next)
    setLayoutDraft(next)
    saveLayoutSettings(next)
    setLayoutPanelOpen(false)
  }

  const resetLayoutSettings = () => {
    const next = DEFAULT_GRAPH_LAYOUT
    setLayoutSettings(next)
    setLayoutDraft(next)
    saveLayoutSettings(next)
  }

  const applyLayoutPreset = (preset: keyof typeof GRAPH_LAYOUT_PRESETS) => {
    setLayoutDraft(normalizeLayoutSettings(GRAPH_LAYOUT_PRESETS[preset]))
  }

  const handleLayoutPanelOpenChange = (open: boolean) => {
    setLayoutPanelOpen(open)
    if (open) setLayoutDraft(layoutSettings)
  }

  const renderLayoutSlider = (
    label: string,
    field: keyof GraphLayoutSettings,
    min: number,
    max: number,
    step = 1,
  ) => {
    const value = layoutDraft[field]
    const displayValue = field === 'attraction' ? value.toFixed(2) : Math.round(value)

    return (
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
          <Text>{label}</Text>
          <Text code>{displayValue}</Text>
        </div>
        <Slider
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={next => updateLayoutDraft(field, Number(next))}
        />
      </div>
    )
  }

  const layoutSettingsContent = (
    <div style={{ width: 292 }}>
      <Space orientation="vertical" size={12} style={{ width: '100%' }}>
        <Text strong>Layout Settings</Text>
        <Space.Compact block>
          <Button size="small" onClick={() => applyLayoutPreset('compact')}>Compact</Button>
          <Button size="small" onClick={() => applyLayoutPreset('balanced')}>Balanced</Button>
          <Button size="small" onClick={() => applyLayoutPreset('open')}>Open</Button>
        </Space.Compact>
        {renderLayoutSlider('Repulsion', 'repulsion', 40, 400)}
        {renderLayoutSlider('Attraction', 'attraction', 0.05, 1, 0.05)}
        {renderLayoutSlider('Link distance', 'linkDistance', 60, 280)}
        {renderLayoutSlider('Node spacing', 'nodeSpacing', 36, 96)}
        <Space style={{ justifyContent: 'flex-end', width: '100%' }}>
          <Button size="small" onClick={resetLayoutSettings}>Reset</Button>
          <Button size="small" type="primary" onClick={applyLayoutSettings}>Apply</Button>
        </Space>
      </Space>
    </div>
  )

  return (
    <div style={{ padding: 24, minHeight: 'calc(100vh - 120px)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0, fontWeight: 700 }}>Skill Knowledge Graph</h2>
          <Text type="secondary">{viewCopy.subtitle}</Text>
        </div>
        <Space wrap>
          <Segmented
            options={GRAPH_VIEW_OPTIONS}
            value={viewMode}
            onChange={value => handleViewChange(value as GraphViewMode)}
          />
          <Select
            mode="multiple"
            placeholder="Filter edge types"
            style={{ minWidth: 220 }}
            allowClear
            value={edgeFilter}
            onChange={setEdgeFilter}
            options={edgeTypes.map(type => ({ label: formatEdgeType(type), value: type }))}
          />
          <Button icon={<ReloadOutlined />} onClick={reloadCurrentView} loading={loading}>
            {viewCopy.reload}
          </Button>
        </Space>
      </div>

      {error && (
        <Alert
          type="error"
          showIcon
          closable
          onClose={() => setError(null)}
          title={error}
          style={{ marginBottom: 12 }}
        />
      )}

      {mode === 'subgraph' && centerNode && (
        <Alert
          type="info"
          showIcon
          title={`Subgraph center: ${centerNode.name}`}
          description={`depth=${depth}, ${graphData?.nodes.length || 0} nodes, ${graphData?.edges.length || 0} edges.`}
          action={(
            <Button size="small" icon={<RollbackOutlined />} onClick={returnToFullGraph}>
              Back to full graph
            </Button>
          )}
          style={{ marginBottom: 12 }}
        />
      )}

      {viewMode === 'version_impact' && Boolean(graphData?.metadata?.meta_paths) && (
        <Alert
          type="info"
          showIcon
          title="Projection meta-paths"
          description={formatMetadataValue(graphData?.metadata?.meta_paths)}
          style={{ marginBottom: 12 }}
        />
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', gap: 16, alignItems: 'stretch' }}>
        <Card
          style={{ minHeight: 560, borderRadius: 8, overflow: 'hidden', minWidth: 0 }}
          styles={{ body: { padding: 0, height: 560 } }}
        >
          {loading && !graphData ? (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
              <Spin size="large" tip="Loading graph data..." />
            </div>
          ) : !hasNodes ? (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', padding: 24 }}>
              <Empty description={viewCopy.empty}>
                <Button icon={<ReloadOutlined />} onClick={reloadCurrentView}>
                  Reload
                </Button>
              </Empty>
            </div>
          ) : (
            <div ref={graphShellRef} style={{ position: 'relative', width: '100%', height: '100%', minHeight: 560 }}>
              <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight: 560 }} />
              <Space.Compact style={{ position: 'absolute', right: 12, top: 12, zIndex: 2, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
                <Popover
                  content={layoutSettingsContent}
                  trigger="click"
                  placement="bottomRight"
                  open={layoutPanelOpen}
                  onOpenChange={handleLayoutPanelOpenChange}
                >
                  <Button size="small" icon={<SettingOutlined />} aria-label="Layout settings" />
                </Popover>
                <Tooltip title="Zoom in">
                  <Button size="small" icon={<ZoomInOutlined />} aria-label="Zoom in" onClick={() => zoomGraph(1.2)} />
                </Tooltip>
                <Tooltip title="Zoom out">
                  <Button size="small" icon={<ZoomOutOutlined />} aria-label="Zoom out" onClick={() => zoomGraph(0.8)} />
                </Tooltip>
                <Tooltip title="Fit view">
                  <Button size="small" icon={<AimOutlined />} aria-label="Fit view" onClick={fitGraph} />
                </Tooltip>
              </Space.Compact>
            </div>
          )}
        </Card>

        <Card
          title={detailTitle}
          style={{ borderRadius: 8, minWidth: 0 }}
          styles={{ body: { paddingTop: 12 } }}
          extra={selectedNode ? <Tag color={nodeColor(selectedNode)}>{formatKind(selectedNode.kind)}</Tag> : null}
        >
          {selectedEdge ? (
            <Space orientation="vertical" size={14} style={{ width: '100%' }}>
              <div>
                <Text strong style={{ fontSize: 16 }}>{formatEdgeType(selectedEdge.edge_type)}</Text>
                <Paragraph copyable={{ text: selectedEdge.id }} style={{ margin: '6px 0 0' }}>
                  <Text code>{selectedEdge.id}</Text>
                </Paragraph>
              </div>
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="Source">
                  {nodeById.get(selectedEdge.source)?.name || selectedEdge.source}
                </Descriptions.Item>
                <Descriptions.Item label="Target">
                  {nodeById.get(selectedEdge.target)?.name || selectedEdge.target}
                </Descriptions.Item>
                <Descriptions.Item label="Weight">
                  {selectedEdge.weight.toFixed(2)}
                </Descriptions.Item>
                {selectedEdge.confidence !== undefined && selectedEdge.confidence !== null && (
                  <Descriptions.Item label="Confidence">
                    {selectedEdge.confidence.toFixed(2)}
                  </Descriptions.Item>
                )}
              </Descriptions>
              {selectedEdgeMetadata.length > 0 && (
                <Descriptions column={1} size="small" bordered title="Evidence">
                  {selectedEdgeMetadata.slice(0, 8).map(([key, value]) => (
                    <Descriptions.Item key={key} label={key}>
                      <Text style={{ whiteSpace: 'pre-wrap' }}>{formatMetadataValue(value)}</Text>
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              )}
            </Space>
          ) : selectedNode ? (
            <Space orientation="vertical" size={14} style={{ width: '100%' }}>
              <div>
                <Text strong style={{ fontSize: 16 }}>{selectedNode.name}</Text>
                <Paragraph copyable={{ text: selectedNode.id }} style={{ margin: '6px 0 0' }}>
                  <Text code>{selectedNode.id}</Text>
                </Paragraph>
                {selectedNode.description && (
                  <Paragraph style={{ marginBottom: 0 }}>{selectedNode.description}</Paragraph>
                )}
              </div>

              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="Kind">
                  <Tag color={nodeColor(selectedNode)}>{formatKind(selectedNode.kind)}</Tag>
                </Descriptions.Item>
                {selectedNode.state && (
                  <Descriptions.Item label="State">
                    <Tag>{STATE_LABEL[selectedNode.state] || selectedNode.state}</Tag>
                  </Descriptions.Item>
                )}
                {selectedNode.version && (
                  <Descriptions.Item label="Version">
                    <Text code>{selectedNode.version}</Text>
                  </Descriptions.Item>
                )}
                {selectedNode.success_rate !== undefined && selectedNode.success_rate !== null && (
                  <Descriptions.Item label="Success rate">
                    {formatPercent(selectedNode.success_rate)}
                  </Descriptions.Item>
                )}
                {selectedNode.usage_count !== undefined && selectedNode.usage_count !== null && (
                  <Descriptions.Item label="Usage count">
                    {selectedNode.usage_count}
                  </Descriptions.Item>
                )}
                {selectedNode.granularity_level !== undefined && selectedNode.granularity_level !== null && (
                  <Descriptions.Item label="Granularity">
                    {selectedNode.granularity_level}
                  </Descriptions.Item>
                )}
              </Descriptions>

              {selectedNodeTags.length > 0 && (
                <div>
                  <Text type="secondary">Tags</Text>
                  <div style={{ marginTop: 6 }}>
                    {selectedNodeTags.map(tag => <Tag key={tag}>{tag}</Tag>)}
                  </div>
                </div>
              )}

              {selectedNodeMetadata.length > 0 && (
                <Descriptions column={1} size="small" bordered title="Metadata">
                  {selectedNodeMetadata.slice(0, 8).map(([key, value]) => (
                    <Descriptions.Item key={key} label={key}>
                      <Text style={{ whiteSpace: 'pre-wrap' }}>{formatMetadataValue(value)}</Text>
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              )}

              {selectedNode.kind === 'skill' && (
                <>
                  <Space.Compact style={{ width: '100%' }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', padding: '0 10px', border: '1px solid #d9d9d9', borderRight: 0, borderRadius: '6px 0 0 6px', color: '#666', background: '#fafafa' }}>
                      Depth
                    </span>
                    <InputNumber
                      min={1}
                      max={5}
                      value={depth}
                      onChange={value => setDepth(value || 2)}
                      style={{ width: 92 }}
                    />
                    <Button
                      icon={<ShareAltOutlined />}
                      loading={subgraphLoading}
                      onClick={expandSelectedSkill}
                      style={{ flex: 1 }}
                    >
                      Expand related
                    </Button>
                  </Space.Compact>
                  <Button block type="primary" icon={<ExportOutlined />} onClick={openWiki}>
                    Open in Wiki
                  </Button>
                </>
              )}
            </Space>
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="Click a node or edge to inspect its evidence."
            />
          )}
        </Card>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginTop: 10, color: '#8c8c8c', fontSize: 12, flexWrap: 'wrap' }}>
        <Space wrap>
          {nodeLegend.map(item => (
            <Tag key={item.value} color={item.color} style={{ borderRadius: 8 }}>{item.label}</Tag>
          ))}
          <span>
            {viewMode === 'skill_only'
              ? 'Node color = skill type; opacity = lifecycle state.'
              : 'Node color = provenance kind; edges preserve typed evidence links.'}
          </span>
        </Space>
        {graphData && (
          <span>
            {graphData.nodes.length} nodes / {graphData.edges.length} edges
          </span>
        )}
      </div>
    </div>
  )
}
