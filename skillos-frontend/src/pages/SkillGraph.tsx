import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  InputNumber,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd'
import {
  ExportOutlined,
  ReloadOutlined,
  RollbackOutlined,
  ShareAltOutlined,
} from '@ant-design/icons'
import { useLocation, useNavigate } from 'react-router-dom'
import { graphApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import type { GraphData, GraphNodeData } from '@/api/types'

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

type GraphEvent = {
  target?: { id?: string }
  targetType?: string
}

type GraphInstance = {
  destroy: () => void
  on: (eventName: string, handler: (event: GraphEvent) => void) => void
  render: () => void | Promise<void>
}

function formatPercent(value: number) {
  if (!Number.isFinite(value)) return 'N/A'
  return `${Math.round(value * 100)}%`
}

function nodeColor(node?: GraphNodeData) {
  if (!node) return '#8c8c8c'
  return TYPE_COLOR[node.skill_type] || '#8c8c8c'
}

export default function SkillGraph() {
  const navigate = useNavigate()
  const location = useLocation()
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

      const nodes = graphData.nodes.map(node => {
        const color = nodeColor(node)
        const selected = node.id === selectedNodeId
        const centered = node.id === centerSkillId
        return {
          id: node.id,
          states: selected ? ['selected'] : [],
          data: {
            label: node.name,
            skillType: node.skill_type,
            state: node.state,
          },
          style: {
            fill: color,
            fillOpacity: STATE_OPACITY[node.state] || 0.65,
            stroke: centered ? '#111827' : color,
            strokeOpacity: 1,
            lineWidth: selected || centered ? 3 : 1,
            size: Math.max(28, Math.min(56, 28 + node.usage_count * 0.5)),
            labelText: node.name,
            labelFill: '#fff',
            labelFontSize: 10,
            labelFontWeight: selected || centered ? 700 : 600,
            labelPlacement: 'center' as const,
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
        width: containerRef.current.clientWidth || 800,
        height: containerRef.current.clientHeight || 600,
        data: { nodes, edges },
        layout: {
          type: 'force',
          preventOverlap: true,
          nodeSize: 44,
          linkDistance: 120,
          nodeStrength: -80,
        },
        behaviors: [
          'drag-canvas',
          'zoom-canvas',
          'drag-element',
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

      void g.render()
      graphRef.current = g
    })

    return () => {
      disposed = true
      if (graphRef.current) {
        graphRef.current.destroy()
        graphRef.current = null
      }
    }
  }, [centerSkillId, edgeFilter, graphData, selectedNodeId])

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
          message={error}
          style={{ marginBottom: 12 }}
        />
      )}

      {mode === 'subgraph' && centerNode && (
        <Alert
          type="info"
          showIcon
          message={`当前子图中心：${centerNode.name}`}
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
              <Spin size="large" tip="加载图谱数据..." />
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
            <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight: 560 }} />
          )}
        </Card>

        <Card
          title="节点详情"
          style={{ borderRadius: 8 }}
          styles={{ body: { paddingTop: 12 } }}
          extra={selectedNode ? <Tag color={nodeColor(selectedNode)}>{selectedNode.skill_type}</Tag> : null}
        >
          {selectedNode ? (
            <Space direction="vertical" size={14} style={{ width: '100%' }}>
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
                  {selectedNode.tags.length > 0
                    ? selectedNode.tags.map(tag => <Tag key={tag}>{tag}</Tag>)
                    : <Text type="secondary">暂无标签</Text>}
                </div>
              </div>

              <Space.Compact style={{ width: '100%' }}>
                <InputNumber
                  min={1}
                  max={5}
                  value={depth}
                  onChange={value => setDepth(value || 2)}
                  style={{ width: 92 }}
                  addonBefore="Depth"
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
