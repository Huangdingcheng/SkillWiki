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
import type { GraphData, GraphEdgeData, GraphNodeData } from '@/api/types'

const { Text, Paragraph } = Typography

const TYPE_COLOR: Record<string, string> = {
  atomic: '#1677ff',
  functional: '#722ed1',
  strategic: '#fa8c16',
}

const STATE_OPACITY: Record<string, number> = {
  S4: 1,
  S2: 0.55,
  S3: 0.7,
  S5: 0.8,
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
  targetType?: string
}

type GraphInstance = {
  destroy: () => void
  fitView: (options?: unknown, animation?: unknown) => void | Promise<void>
  on: (eventName: string, handler: (event: GraphEvent) => void) => void
  render: () => void | Promise<void>
  zoomBy: (ratio: number, animation?: unknown) => void | Promise<void>
}

const GRAPH_LAYOUT_STORAGE_KEY = 'skillos.graph.layoutSettings.v1'
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
    // Ignore storage failures; the graph should still remain usable.
  }
}

function formatPercent(value: number) {
  if (!Number.isFinite(value)) return 'N/A'
  return `${Math.round(value * 100)}%`
}

function nodeColor(node?: GraphNodeData) {
  if (!node) return '#8c8c8c'
  return TYPE_COLOR[node.skill_type] || '#8c8c8c'
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

function calculateInitialNodePositions(
  nodes: GraphNodeData[],
  edges: GraphEdgeData[],
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

  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [subgraphLoading, setSubgraphLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [edgeFilter, setEdgeFilter] = useState<string[]>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [mode, setMode] = useState<GraphMode>('full')
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

  const centerNode = useMemo(
    () => graphData?.nodes.find(node => node.id === centerSkillId) || null,
    [graphData, centerSkillId],
  )

  const edgeTypes = useMemo(
    () => (graphData ? [...new Set(graphData.edges.map(edge => edge.edge_type))] : []),
    [graphData],
  )

  const loadFullGraph = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await graphApi.full(300)
      setGraphData(data)
      setMode('full')
      setCenterSkillId(null)
      setSelectedNodeId(null)
    } catch (err) {
      setError(getApiErrorMessage(err, '加载完整图谱失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadSubgraph = useCallback(async (skillId: string, nextDepth = depth) => {
    if (!skillId) return
    setSubgraphLoading(true)
    setError(null)
    try {
      const data = await graphApi.subgraph(skillId, nextDepth)
      setGraphData(data)
      setMode('subgraph')
      setCenterSkillId(skillId)
      setSelectedNodeId(skillId)
    } catch (err) {
      setError(getApiErrorMessage(err, '加载子图失败，已保留当前视图'))
    } finally {
      setSubgraphLoading(false)
      setLoading(false)
    }
  }, [depth])

  useEffect(() => {
    const querySkillId = new URLSearchParams(location.search).get('skill_id')
    if (querySkillId && openedFromQuery.current !== querySkillId) {
      openedFromQuery.current = querySkillId
      void loadSubgraph(querySkillId, depth)
      return
    }
    if (!querySkillId && !graphData) {
      void loadFullGraph()
    }
  }, [depth, graphData, loadFullGraph, loadSubgraph, location.search])

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
        linkDistance: layoutSettings.linkDistance,
        nodeStrength: -layoutSettings.repulsion,
        edgeStrength: layoutSettings.attraction,
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
          states: selected ? ['selected'] : [],
          data: {
            label: node.name,
            skillType: node.skill_type,
            state: node.state,
          },
          style: {
            x: position?.x,
            y: position?.y,
            fill: color,
            fillOpacity: STATE_OPACITY[node.state] || 0.65,
            stroke: centered ? '#111827' : color,
            strokeOpacity: 1,
            lineWidth: selected || centered ? 3 : 1,
            size: Math.max(28, Math.min(56, 28 + node.usage_count * 0.5)),
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

      const edges = filteredEdges.map(edge => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        data: { edgeType: edge.edge_type },
        style: {
          stroke: EDGE_COLOR[edge.edge_type] || '#8c8c8c',
          strokeOpacity: 0.85,
          lineWidth: Math.max(1, edge.weight * 2),
          endArrow: true,
          labelText: edge.edge_type.replace(/_/g, ' '),
          labelFontSize: 9,
          labelFill: EDGE_COLOR[edge.edge_type] || '#8c8c8c',
        },
      }))

      const g = new G6.Graph({
        container: containerRef.current,
        width: graphWidth,
        height: graphHeight,
        data: { nodes, edges },
        autoFit: 'view',
        padding: [48, 48, 72, 48],
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
        node: {
          type: 'circle',
        },
        edge: {
          type: 'line',
        },
      }) as GraphInstance

      g.on('node:click', (event) => {
        const id = event.target?.id
        if (id) setSelectedNodeId(id)
      })

      g.on('canvas:click', () => {
        setSelectedNodeId(null)
      })

      graphRef.current = g
      void Promise.resolve(g.render()).then(() => {
        if (!disposed) void g.fitView({ when: 'always' }, { duration: 160 })
      })
    })

    return () => {
      disposed = true
      if (graphRef.current) {
        graphRef.current.destroy()
        graphRef.current = null
      }
    }
  }, [canvasSize, centerSkillId, edgeFilter, graphData, layoutSettings, selectedNodeId])

  const returnToFullGraph = () => {
    openedFromQuery.current = null
    navigate('/graph', { replace: true })
    void loadFullGraph()
  }

  const openWiki = () => {
    if (!selectedNode) return
    navigate(`/wiki?skill_id=${encodeURIComponent(selectedNode.id)}`)
  }

  const hasNodes = Boolean(graphData?.nodes.length)
  const selectedNodeTags = uniqueTags(selectedNode?.tags)
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
        <Text strong>布局设置</Text>
        <Space.Compact block>
          <Button size="small" onClick={() => applyLayoutPreset('compact')}>紧凑</Button>
          <Button size="small" onClick={() => applyLayoutPreset('balanced')}>均衡</Button>
          <Button size="small" onClick={() => applyLayoutPreset('open')}>开阔</Button>
        </Space.Compact>
        {renderLayoutSlider('排斥力度', 'repulsion', 40, 400)}
        {renderLayoutSlider('吸引力度', 'attraction', 0.05, 1, 0.05)}
        {renderLayoutSlider('连接距离', 'linkDistance', 60, 280)}
        {renderLayoutSlider('节点间距', 'nodeSpacing', 36, 96)}
        <Space style={{ justifyContent: 'flex-end', width: '100%' }}>
          <Button size="small" onClick={resetLayoutSettings}>恢复默认</Button>
          <Button size="small" type="primary" onClick={applyLayoutSettings}>应用布局</Button>
        </Space>
      </Space>
    </div>
  )

  return (
    <div style={{ padding: 24, minHeight: 'calc(100vh - 120px)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0, fontWeight: 700 }}>Skill Knowledge Graph</h2>
          <Text type="secondary">
            点击节点查看摘要，展开关联子图，或跳转到 Wiki 详情。
          </Text>
        </div>
        <Space wrap>
          <Select
            mode="multiple"
            placeholder="过滤边类型"
            style={{ minWidth: 220 }}
            allowClear
            value={edgeFilter}
            onChange={setEdgeFilter}
            options={edgeTypes.map(type => ({ label: type, value: type }))}
          />
          <Button icon={<ReloadOutlined />} onClick={loadFullGraph} loading={loading}>
            刷新全图
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
          title={`当前子图中心：${centerNode.name}`}
          description={`depth=${depth}，共 ${graphData?.nodes.length || 0} 个节点、${graphData?.edges.length || 0} 条边。`}
          action={(
            <Button size="small" icon={<RollbackOutlined />} onClick={returnToFullGraph}>
              返回全图
            </Button>
          )}
          style={{ marginBottom: 12 }}
        />
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 320px', gap: 16, alignItems: 'stretch' }}>
        <Card
          style={{ minHeight: 560, borderRadius: 8, overflow: 'hidden' }}
          styles={{ body: { padding: 0, height: 560 } }}
        >
          {loading && !graphData ? (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
              <Spin size="large" description="加载图谱数据..." />
            </div>
          ) : !hasNodes ? (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', padding: 24 }}>
              <Empty
                description="暂无可展示的 Skill 图谱数据"
              >
                <Button icon={<ReloadOutlined />} onClick={loadFullGraph}>
                  重新加载
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
                  <Button size="small" icon={<SettingOutlined />} aria-label="布局设置" />
                </Popover>
                <Tooltip title="放大图谱">
                  <Button size="small" icon={<ZoomInOutlined />} aria-label="放大图谱" onClick={() => zoomGraph(1.2)} />
                </Tooltip>
                <Tooltip title="缩小图谱">
                  <Button size="small" icon={<ZoomOutOutlined />} aria-label="缩小图谱" onClick={() => zoomGraph(0.8)} />
                </Tooltip>
                <Tooltip title="适配视图">
                  <Button size="small" icon={<AimOutlined />} aria-label="适配视图" onClick={fitGraph} />
                </Tooltip>
              </Space.Compact>
            </div>
          )}
        </Card>

        <Card
          title="节点详情"
          style={{ borderRadius: 8 }}
          styles={{ body: { paddingTop: 12 } }}
          extra={selectedNode ? <Tag color={nodeColor(selectedNode)}>{selectedNode.skill_type}</Tag> : null}
        >
          {selectedNode ? (
            <Space orientation="vertical" size={14} style={{ width: '100%' }}>
              <div>
                <Text strong style={{ fontSize: 16 }}>{selectedNode.name}</Text>
                <Paragraph copyable={{ text: selectedNode.id }} style={{ margin: '6px 0 0' }}>
                  <Text code>{selectedNode.id}</Text>
                </Paragraph>
              </div>

              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="状态">
                  <Tag>{STATE_LABEL[selectedNode.state] || selectedNode.state}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="版本">
                  <Text code>{selectedNode.version}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="成功率">
                  {formatPercent(selectedNode.success_rate)}
                </Descriptions.Item>
                <Descriptions.Item label="使用次数">
                  {selectedNode.usage_count}
                </Descriptions.Item>
                <Descriptions.Item label="粒度级别">
                  {selectedNode.granularity_level}
                </Descriptions.Item>
              </Descriptions>

              <div>
                <Text type="secondary">标签</Text>
                <div style={{ marginTop: 6 }}>
                  {selectedNodeTags.length > 0
                    ? selectedNodeTags.map(tag => <Tag key={tag}>{tag}</Tag>)
                    : <Text type="secondary">暂无标签</Text>}
                </div>
              </div>

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
                  onClick={() => loadSubgraph(selectedNode.id, depth)}
                  style={{ flex: 1 }}
                >
                  展开关联
                </Button>
              </Space.Compact>

              <Button block type="primary" icon={<ExportOutlined />} onClick={openWiki}>
                在 Wiki 中查看
              </Button>
            </Space>
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="点击图中的节点后，这里会显示 Skill 摘要和操作入口。"
            />
          )}
        </Card>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginTop: 10, color: '#8c8c8c', fontSize: 12, flexWrap: 'wrap' }}>
        <Space wrap>
          {Object.entries(TYPE_COLOR).map(([type, color]) => (
            <Tag key={type} color={color} style={{ borderRadius: 8 }}>{type}</Tag>
          ))}
          <span>节点颜色 = 类型，节点大小 = 使用频率，透明度 = 生命周期状态。</span>
        </Space>
        {graphData && (
          <span>
            {graphData.nodes.length} 个节点 / {graphData.edges.length} 条边
          </span>
        )}
      </div>
    </div>
  )
}
