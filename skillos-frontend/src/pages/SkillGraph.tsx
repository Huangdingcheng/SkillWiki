import { useEffect, useRef, useState } from 'react'
import { Card, Select, Spin, Tag, Button, Space } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { graphApi } from '@/api/client'
import type { GraphData } from '@/api/types'

const SKILL_TYPE_COLOR: Record<string, string> = {
  atomic: '#1677ff',
  functional: '#722ed1',
  strategic: '#fa8c16',
}

const NODE_TYPE_COLOR: Record<string, string> = {
  skill: '#2f80ed',
  task: '#f2994a',
  trajectory: '#eb5757',
  document: '#27ae60',
  api_doc: '#9b51e0',
  tool: '#56ccf2',
  script: '#f2c94c',
  test: '#6fcf97',
  version: '#bdbdbd',
  feedback: '#ff7a45',
  agent: '#219653',
  dataset: '#828282',
  host_information: '#00d4ff',
}

const STATE_OPACITY: Record<string, number> = {
  S4: 1.0, S2: 0.55, S3: 0.7,
  S5: 0.9, S6: 0.65, S7: 0.35,
}

const EDGE_COLOR: Record<string, string> = {
  depends_on: '#1677ff', composes_with: '#722ed1', similar_to: '#52c41a',
  evolved_from: '#fa8c16', conflicts_with: '#ff4d4f', replaces: '#eb2f96',
  specializes: '#13c2c2', generalizes: '#fadb14',
  derived_from: '#eb5757', belongs_to: '#8c8c8c', uses: '#13c2c2',
  requires: '#1677ff', verified_by: '#52c41a', evolves_from: '#fa8c16',
  produced_by: '#219653', triggered_by: '#f2994a', feeds_back_to: '#ff7a45',
  documents: '#27ae60', tests: '#6fcf97', version_of: '#bdbdbd',
}

const LEFT_COLUMN_TYPES = new Set(['task', 'trajectory', 'document', 'script', 'dataset'])
const RIGHT_COLUMN_TYPES = new Set(['api_doc', 'tool', 'test', 'version'])

function truncateLabel(value: string, max = 24) {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

function getColumnX(nodeType: string, width: number) {
  if (LEFT_COLUMN_TYPES.has(nodeType)) return width * 0.14
  if (nodeType === 'agent') return width * 0.24
  if (nodeType === 'host_information') return width * 0.68
  if (nodeType === 'feedback') return width * 0.58
  if (RIGHT_COLUMN_TYPES.has(nodeType)) return width * 0.82
  return width * 0.48
}

function getLayoutGroup(nodeType: string) {
  if (LEFT_COLUMN_TYPES.has(nodeType)) return 'left-source'
  if (RIGHT_COLUMN_TYPES.has(nodeType)) return 'right-artifact'
  if (nodeType === 'agent') return 'agent'
  if (nodeType === 'host_information') return 'host-memory'
  if (nodeType === 'feedback') return 'feedback'
  return 'skill'
}

interface SkillGraphProps {
  focusSkillId?: string | null
  embedded?: boolean
  visibility?: 'user' | 'kernel' | 'all'
}

export default function SkillGraph({ focusSkillId, embedded = false, visibility = embedded ? 'user' : 'all' }: SkillGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<{ destroy: () => void } | null>(null)
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [edgeFilter, setEdgeFilter] = useState<string[]>([])
  const [nodeTypeFilter, setNodeTypeFilter] = useState<string[]>([])
  const [renderStats, setRenderStats] = useState({ nodes: 0, edges: 0 })
  const [focusTargetName, setFocusTargetName] = useState<string | null>(null)

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

  useEffect(() => {
    if (!graphData || !containerRef.current) return

    import('@antv/g6').then((G6) => {
      if (graphRef.current) {
        graphRef.current.destroy()
        graphRef.current = null
      }

      const visibleNodeIds = new Set(
        (() => {
          const connectedIds = new Set<string>()
          graphData.edges.forEach(edge => {
            connectedIds.add(edge.source)
            connectedIds.add(edge.target)
          })
          return graphData.nodes.filter(node => {
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
        })()
          .map(n => n.id)
      )

      const filteredEdges = graphData.edges
        .filter(e => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target))
        .filter(e => edgeFilter.length === 0 || edgeFilter.includes(e.edge_type))

      const visibleNodes = graphData.nodes.filter(n => visibleNodeIds.has(n.id))
      setRenderStats({ nodes: visibleNodes.length, edges: filteredEdges.length })
      setFocusTargetName(graphData.nodes.find(n => n.id === focusSkillId)?.name || null)
      const width = containerRef.current!.clientWidth || 960
      const height = containerRef.current!.clientHeight || 600
      const groupSizes = visibleNodes.reduce<Record<string, number>>((acc, n) => {
        const type = getLayoutGroup(n.node_type || 'skill')
        acc[type] = (acc[type] || 0) + 1
        return acc
      }, {})
      const groupIndexes: Record<string, number> = {}

      const nodes = visibleNodes.map(n => {
        const nodeType = n.node_type || 'skill'
        const layoutGroup = getLayoutGroup(nodeType)
        const index = groupIndexes[layoutGroup] || 0
        groupIndexes[layoutGroup] = index + 1
        const groupSize = groupSizes[layoutGroup] || 1
        const yPadding = 72
        let x = getColumnX(nodeType, width)
        let y = yPadding + ((height - yPadding * 2) * (index + 1)) / (groupSize + 1)

        if (nodeType === 'skill') {
          const cols = Math.max(2, Math.ceil(Math.sqrt(groupSize)))
          const rows = Math.ceil(groupSize / cols)
          const col = index % cols
          const row = Math.floor(index / cols)
          const skillWidth = width * 0.38
          const skillHeight = height - yPadding * 2
          x = width * 0.32 + (cols === 1 ? skillWidth / 2 : (skillWidth * col) / (cols - 1))
          y = yPadding + (rows === 1 ? skillHeight / 2 : (skillHeight * row) / (rows - 1))
        }

        const color = nodeType === 'skill'
          ? SKILL_TYPE_COLOR[n.skill_type] || NODE_TYPE_COLOR.skill
          : NODE_TYPE_COLOR[nodeType] || '#8c8c8c'
        const isFocused = focusSkillId === n.id
        const label = n.version && nodeType === 'skill'
          ? `${n.name} v${n.version}`
          : n.name
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
            x,
            y,
            fill: color,
            fillOpacity: nodeType === 'skill' ? (STATE_OPACITY[n.state] || 0.75) : 0.88,
            stroke: isFocused ? '#ff4d4f' : color,
            lineWidth: isFocused ? 5 : nodeType === 'skill' && n.state === 'S4' ? 3 : 1,
            size: nodeType === 'skill'
              ? Math.max(isFocused ? 60 : 28, Math.min(isFocused ? 72 : 56, 30 + n.usage_count * 0.45))
              : Math.max(24, Math.min(42, 28 + (n.labels?.length || 0) * 2)),
            labelText: truncateLabel(label),
            labelFill: '#162033',
            labelFontSize: 9,
            labelFontWeight: 600,
            labelPlacement: 'bottom' as const,
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
          lineWidth: Math.max(1, e.weight * 2),
          endArrow: true,
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
        behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element', 'click-select'],
        node: {
          type: 'circle',
        },
        edge: {
          type: 'line',
        },
      })

      g.render()
      graphRef.current = g
    })

    return () => {
      if (graphRef.current) {
        graphRef.current.destroy()
        graphRef.current = null
      }
    }
  }, [graphData, edgeFilter, nodeTypeFilter, focusSkillId])

  const edgeTypes = graphData ? [...new Set(graphData.edges.map(e => e.edge_type))] : []
  const nodeTypes = graphData ? [...new Set(graphData.nodes.map(n => n.node_type || 'skill'))] : []

  return (
    <div style={{ padding: embedded ? 0 : 24, height: embedded ? 620 : 'calc(100vh - 120px)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontWeight: 700 }}>{embedded ? 'Skill Graph Context' : 'SkillOS Heterogeneous Graph'}</h2>
          {focusSkillId && (
            <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 4 }}>
              Focused on skill node: <Tag color="red">{focusTargetName || `${focusSkillId.slice(0, 8)}...`}</Tag>
            </div>
          )}
        </div>
        <Space>
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

      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        {Object.entries(NODE_TYPE_COLOR).map(([type, color]) => (
          <Tag key={type} color={color} style={{ borderRadius: 12 }}>{type.replace(/_/g, ' ')}</Tag>
        ))}
        <span style={{ color: '#999', fontSize: 12, alignSelf: 'center' }}>
          Node color = entity type | Skill size = usage | Relations show provenance, execution, and evolution links
        </span>
      </div>

      <Card
        style={{
          flex: 1,
          borderRadius: 16,
          overflow: 'hidden',
          padding: 0,
          background: 'radial-gradient(circle at 30% 25%, rgba(0,212,255,0.16), transparent 28%), radial-gradient(circle at 70% 55%, rgba(114,46,209,0.14), transparent 32%), #f8fbff',
        }}
        styles={{ body: { padding: 0, height: '100%' } }}
      >
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 500 }}>
            <Spin size="large" tip="Loading graph data..." />
          </div>
        ) : (
          <div
            ref={containerRef}
            style={{
              width: '100%',
              height: '100%',
              minHeight: 500,
              backgroundImage: 'radial-gradient(rgba(22,119,255,0.13) 1px, transparent 1px)',
              backgroundSize: '22px 22px',
            }}
          />
        )}
      </Card>

      {graphData && (
        <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
          {renderStats.nodes} rendered nodes · {renderStats.edges} rendered relations
          {focusSkillId && ` · full graph has ${graphData.nodes.length} nodes / ${graphData.edges.length} relations`}
        </div>
      )}
    </div>
  )
}
