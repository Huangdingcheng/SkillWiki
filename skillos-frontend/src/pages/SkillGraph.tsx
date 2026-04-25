import { useEffect, useRef, useState } from 'react'
import { Card, Select, Spin, Tag, Button, Space } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { graphApi } from '@/api/client'
import type { GraphData } from '@/api/types'

const TYPE_COLOR: Record<string, string> = {
  atomic: '#1677ff',
  functional: '#722ed1',
  strategic: '#fa8c16',
}

const STATE_OPACITY: Record<string, number> = {
  S4: 1.0, S2: 0.55, S3: 0.7,
  S5: 0.8, S6: 0.3, S7: 0.2,
}

const EDGE_COLOR: Record<string, string> = {
  depends_on: '#1677ff', composes_with: '#722ed1', similar_to: '#52c41a',
  evolved_from: '#fa8c16', conflicts_with: '#ff4d4f', replaces: '#eb2f96',
  specializes: '#13c2c2', generalizes: '#fadb14',
}

export default function SkillGraph() {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<{ destroy: () => void } | null>(null)
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [edgeFilter, setEdgeFilter] = useState<string[]>([])

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

      const filteredEdges = edgeFilter.length > 0
        ? graphData.edges.filter(e => edgeFilter.includes(e.edge_type))
        : graphData.edges

      const nodes = graphData.nodes.map(n => ({
        id: n.id,
        data: {
          label: n.name,
          skillType: n.skill_type,
          state: n.state,
        },
        style: {
          fill: TYPE_COLOR[n.skill_type] || '#8c8c8c',
          fillOpacity: STATE_OPACITY[n.state] || 0.5,
          stroke: TYPE_COLOR[n.skill_type] || '#8c8c8c',
          lineWidth: n.state === 'released' ? 2 : 1,
          size: Math.max(24, Math.min(48, 24 + n.usage_count * 0.5)),
          labelText: n.name,
          labelFill: '#fff',
          labelFontSize: 10,
          labelFontWeight: 600,
          labelPlacement: 'center' as const,
        },
      }))

      const edges = filteredEdges.map(e => ({
        id: e.id,
        source: e.source,
        target: e.target,
        data: { edgeType: e.edge_type },
        style: {
          stroke: EDGE_COLOR[e.edge_type] || '#aaa',
          lineWidth: Math.max(1, e.weight * 2),
          endArrow: true,
          labelText: e.edge_type.replace(/_/g, ' '),
          labelFontSize: 9,
          labelFill: EDGE_COLOR[e.edge_type] || '#aaa',
        },
      }))

      const g = new G6.Graph({
        container: containerRef.current!,
        width: containerRef.current!.clientWidth || 800,
        height: containerRef.current!.clientHeight || 600,
        data: { nodes, edges },
        layout: {
          type: 'force',
          preventOverlap: true,
          nodeSize: 40,
          linkDistance: 120,
          nodeStrength: -80,
        },
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
  }, [graphData, edgeFilter])

  const edgeTypes = graphData ? [...new Set(graphData.edges.map(e => e.edge_type))] : []

  return (
    <div style={{ padding: 24, height: 'calc(100vh - 120px)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontWeight: 700 }}>Skill Knowledge Graph</h2>
        <Space>
          <Select
            mode="multiple"
            placeholder="过滤边类型"
            style={{ minWidth: 200 }}
            allowClear
            onChange={setEdgeFilter}
            options={edgeTypes.map(t => ({ label: t, value: t }))}
          />
          <Button icon={<ReloadOutlined />} onClick={loadGraph}>刷新</Button>
        </Space>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        {Object.entries(TYPE_COLOR).map(([type, color]) => (
          <Tag key={type} color={color} style={{ borderRadius: 12 }}>{type}</Tag>
        ))}
        <span style={{ color: '#999', fontSize: 12, alignSelf: 'center' }}>
          节点颜色 = 类型 | 大小 = 使用频率
        </span>
      </div>

      <Card
        style={{ flex: 1, borderRadius: 12, overflow: 'hidden', padding: 0 }}
        styles={{ body: { padding: 0, height: '100%' } }}
      >
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 500 }}>
            <Spin size="large" tip="加载图谱数据..." />
          </div>
        ) : (
          <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight: 500 }} />
        )}
      </Card>

      {graphData && (
        <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
          {graphData.nodes.length} 个节点 · {graphData.edges.length} 条边
        </div>
      )}
    </div>
  )
}
