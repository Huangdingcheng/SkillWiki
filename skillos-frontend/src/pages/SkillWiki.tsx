import { useEffect, useState } from 'react'
import {
  Table, Tag, Input, Select, Button, Drawer, Descriptions, Badge,
  Space, Tooltip, Popconfirm, message, Tabs, Typography, Progress, Card, Alert,
} from 'antd'
import {
  SearchOutlined, EyeOutlined,
  CheckOutlined, StopOutlined, ApartmentOutlined,
} from '@ant-design/icons'
import { motion } from 'framer-motion'
import { useSearchParams } from 'react-router-dom'
import { skillsApi, lifecycleApi, evolutionApi } from '@/api/client'
import type { HealthReport, SkillFull, SkillInterface, SkillParameter, SkillState, SkillSummary, SkillType, SkillVisibility } from '@/api/types'
import SkillGraph from '@/pages/SkillGraph'

const { Text, Paragraph } = Typography

const STATE_COLOR: Record<string, string> = {
  S4: 'green', S2: 'blue', S3: 'cyan', S1: 'orange',
  S5: 'gold', S6: 'red', S7: 'default', S0: 'purple',
}
const STATE_LABEL: Record<string, string> = {
  S0: 'Raw', S1: 'Candidate', S2: 'Draft', S3: 'Verified',
  S4: 'Released', S5: 'Degraded', S6: 'Deprecated', S7: 'Archived',
}
const TYPE_COLOR: Record<string, string> = {
  atomic: 'blue', functional: 'purple', strategic: 'gold',
}

function schemaToParameters(schema: Record<string, unknown> | undefined, direction: 'input' | 'output'): SkillParameter[] {
  const properties = schema?.properties && typeof schema.properties === 'object'
    ? schema.properties as Record<string, Record<string, unknown>>
    : {}
  const required = new Set(Array.isArray(schema?.required) ? schema.required.map(String) : [])
  return Object.entries(properties).map(([name, prop]) => ({
    name,
    type: String(prop?.type || 'unknown'),
    description: String(prop?.description || `${direction} field`),
    required: required.has(name),
  }))
}

function getInterfaceInputs(skillInterface: SkillInterface): SkillParameter[] {
  if (Array.isArray(skillInterface.inputs)) return skillInterface.inputs
  return schemaToParameters(skillInterface.input_schema, 'input')
}

function getInterfaceOutputs(skillInterface: SkillInterface): SkillParameter[] {
  if (Array.isArray(skillInterface.outputs)) return skillInterface.outputs
  return schemaToParameters(skillInterface.output_schema, 'output')
}

function SchemaBlock({ title, schema }: { title: string; schema?: Record<string, unknown> }) {
  if (!schema) return null
  return (
    <>
      <h4 style={{ marginTop: 16 }}>{title}</h4>
      <pre style={{ background: '#f7f9fc', padding: 12, borderRadius: 8, overflow: 'auto', fontSize: 12 }}>
        {JSON.stringify(schema, null, 2)}
      </pre>
    </>
  )
}

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

export default function SkillWiki() {
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [stateFilter, setStateFilter] = useState<SkillState | undefined>()
  const [typeFilter, setTypeFilter] = useState<SkillType | undefined>()
  const [visibilityFilter, setVisibilityFilter] = useState<SkillVisibility | 'all'>('user')
  const [selected, setSelected] = useState<SkillFull | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const [focusGraphSkillId, setFocusGraphSkillId] = useState<string | null>(null)
  const [selectedHealth, setSelectedHealth] = useState<HealthReport | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setLoadError(null)
    try {
      let data: SkillSummary[] = []
      for (let attempt = 0; attempt < 3; attempt += 1) {
        data = await skillsApi.list({ state: stateFilter, skill_type: typeFilter, visibility: visibilityFilter, limit: 500 })
        if (data.length > 0 || attempt === 2) break
        await sleep(500 * (attempt + 1))
      }
      setSkills(Array.isArray(data) ? data : [])
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } }; message?: string })?.response?.data?.detail
        || (e as { message?: string })?.message
        || 'Failed to load Skill list'
      setLoadError(detail)
      setSkills([])
      message.error(detail)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [stateFilter, typeFilter, visibilityFilter])

  const filtered = skills.filter(s => {
    const q = search.trim().toLowerCase()
    return !q
      || s.name.toLowerCase().includes(q)
      || s.description.toLowerCase().includes(q)
      || s.tags.some(t => t.toLowerCase().includes(q))
  })

  const openDetail = async (id: string, updateUrl = true) => {
    try {
      const full = await skillsApi.getFull(id)
      setSelected(full)
      setDrawerOpen(true)
      if (updateUrl) setSearchParams({ skill: id })
      evolutionApi.skillHealth(id).then(setSelectedHealth).catch(() => setSelectedHealth(null))
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Failed to load Skill details')
    }
  }

  useEffect(() => {
    const skillId = searchParams.get('skill')
    if (skillId) openDetail(skillId, false)
  }, [searchParams])

  const closeDetail = () => {
    setDrawerOpen(false)
    setSelected(null)
    setSelectedHealth(null)
    setSearchParams({})
  }

  const focusInGraph = (id: string) => {
    setFocusGraphSkillId(id)
    setTimeout(() => {
      document.getElementById('skill-wiki-graph')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 50)
  }

  const handleRelease = async (id: string) => {
    try {
      await lifecycleApi.release(id)
      message.success('已发布')
      load()
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '操作失败')
    }
  }

  const handleDeprecate = async (id: string) => {
    try {
      await lifecycleApi.deprecate(id, '手动废弃')
      message.success('已废弃')
      load()
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '操作失败')
    }
  }

  const columns = [
    {
      title: 'Name',
      dataIndex: 'name',
      render: (name: string, r: SkillSummary) => (
        <Button type="link" onClick={() => openDetail(r.skill_id)} style={{ padding: 0, fontWeight: 600 }}>
          {name}
        </Button>
      ),
    },
    {
      title: 'Type',
      dataIndex: 'skill_type',
      render: (t: string) => <Tag color={TYPE_COLOR[t]}>{t.toUpperCase()}</Tag>,
    },
    {
      title: 'State',
      dataIndex: 'state',
      render: (s: string) => <Badge color={STATE_COLOR[s] || 'default'} text={STATE_LABEL[s] || s} />,
    },
    {
      title: 'Scope',
      dataIndex: 'visibility',
      render: (v: string) => <Tag color={v === 'kernel' ? 'volcano' : 'green'}>{v === 'kernel' ? 'KERNEL' : 'USER'}</Tag>,
    },
    {
      title: 'Version',
      dataIndex: 'version',
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: 'Tags',
      dataIndex: 'tags',
      render: (tags: string[]) => tags.slice(0, 3).map(t => <Tag key={t}>{t}</Tag>),
    },
    {
      title: 'Success Rate',
      dataIndex: 'metrics',
      render: (m: SkillSummary['metrics']) => (
        m.total_executions >= 5
          ? <Progress percent={Math.round(m.success_rate * 100)} size="small" style={{ width: 80 }} />
          : <Text type="secondary">N/A</Text>
      ),
    },
    {
      title: 'Actions',
      render: (_: unknown, r: SkillSummary) => (
        <Space>
          <Tooltip title="查看详情">
            <Button size="small" aria-label={`Open ${r.name}`} icon={<EyeOutlined />} onClick={() => openDetail(r.skill_id)} />
          </Tooltip>
          {r.state === 'S2' && (
            <Tooltip title="发布">
              <Button size="small" aria-label={`Release ${r.name}`} icon={<CheckOutlined />} type="primary" onClick={() => handleRelease(r.skill_id)} />
            </Tooltip>
          )}
          {r.state === 'S4' && (
            <Tooltip title="废弃">
              <Popconfirm title="确认废弃？" onConfirm={() => handleDeprecate(r.skill_id)}>
                <Button size="small" aria-label={`Deprecate ${r.name}`} icon={<StopOutlined />} danger />
              </Popconfirm>
            </Tooltip>
          )}
          <Tooltip title="Show in graph">
            <Button size="small" aria-label={`Show ${r.name} in graph`} icon={<ApartmentOutlined />} onClick={() => focusInGraph(r.skill_id)} />
          </Tooltip>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
          <Input
            placeholder="Search Skill name / description / tags"
            prefix={<SearchOutlined />}
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ width: 280 }}
            allowClear
          />
          <Select
            placeholder="State"
            allowClear
            style={{ width: 140 }}
            onChange={v => setStateFilter(v as SkillState)}
            options={[
              { label: 'Raw (S0)',        value: 'S0' },
              { label: 'Candidate (S1)', value: 'S1' },
              { label: 'Draft (S2)',      value: 'S2' },
              { label: 'Verified (S3)',   value: 'S3' },
              { label: 'Released (S4)',   value: 'S4' },
              { label: 'Degraded (S5)',   value: 'S5' },
              { label: 'Deprecated (S6)', value: 'S6' },
              { label: 'Archived (S7)',   value: 'S7' },
            ]}
          />
          <Select
            placeholder="Type"
            allowClear
            style={{ width: 140 }}
            onChange={v => setTypeFilter(v as SkillType)}
            options={['atomic', 'functional', 'strategic'].map(t => ({ label: t, value: t }))}
          />
          <Select
            value={visibilityFilter}
            style={{ width: 160 }}
            onChange={v => setVisibilityFilter(v as SkillVisibility | 'all')}
            options={[
              { label: 'User Skills', value: 'user' },
              { label: 'Kernel Skills', value: 'kernel' },
              { label: 'All Skills', value: 'all' },
            ]}
          />
          <Button onClick={load}>Refresh</Button>
          <Text type="secondary" style={{ alignSelf: 'center' }}>
            {filtered.length} shown / {skills.length} loaded
          </Text>
        </div>

        {loadError && (
          <Alert
            type="error"
            showIcon
            message="Skill list failed to load"
            description={loadError}
            style={{ marginBottom: 12 }}
          />
        )}

        <Table
          dataSource={filtered}
          columns={columns}
          rowKey="skill_id"
          loading={loading}
          size="middle"
          pagination={{ pageSize: 15, showSizeChanger: true }}
          style={{ borderRadius: 12, overflow: 'hidden' }}
        />

        <Card
          id="skill-wiki-graph"
          title="Knowledge Graph"
          extra={focusGraphSkillId ? (
            <Space>
              <Tag color="red">Focused: {focusGraphSkillId.slice(0, 8)}...</Tag>
              <Button size="small" onClick={() => setFocusGraphSkillId(null)}>Clear Focus</Button>
            </Space>
          ) : <Text type="secondary">Click the graph icon on any Skill row to focus its graph context.</Text>}
          bordered={false}
          style={{ marginTop: 16, borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          styles={{ body: { padding: 16 } }}
        >
          <SkillGraph embedded focusSkillId={focusGraphSkillId} visibility={visibilityFilter} />
        </Card>
      </motion.div>

      <Drawer
        title={selected?.display_name || selected?.name}
        open={drawerOpen}
        onClose={closeDetail}
        width={600}
        extra={
          <Space>
            <Tag color={TYPE_COLOR[selected?.skill_type || '']}>{selected?.skill_type?.toUpperCase()}</Tag>
            <Badge color={STATE_COLOR[selected?.state || '']} text={STATE_LABEL[selected?.state || ''] || selected?.state} />
          </Space>
        }
      >
        {selected && (
          <Tabs
            items={[
              {
                key: 'info',
                label: 'Overview',
                children: (
                  <Descriptions column={1} bordered size="small">
                    <Descriptions.Item label="ID"><Text code copyable>{selected.skill_id}</Text></Descriptions.Item>
                    <Descriptions.Item label="Version"><Text code>{selected.version}</Text></Descriptions.Item>
                    <Descriptions.Item label="Scope">
                      <Tag color={selected.visibility === 'kernel' ? 'volcano' : 'green'}>
                        {selected.visibility === 'kernel' ? 'Kernel Skill' : 'User Skill'}
                      </Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="Description"><Paragraph>{selected.description}</Paragraph></Descriptions.Item>
                    <Descriptions.Item label="Tags">{selected.tags.map(t => <Tag key={t}>{t}</Tag>)}</Descriptions.Item>
                    <Descriptions.Item label="Domain">{selected.domain || 'general'}</Descriptions.Item>
                    <Descriptions.Item label="Granularity">{selected.granularity_level}</Descriptions.Item>
                    <Descriptions.Item label="Created">{new Date(selected.created_at).toLocaleString()}</Descriptions.Item>
                    <Descriptions.Item label="Updated">{new Date(selected.updated_at).toLocaleString()}</Descriptions.Item>
                  </Descriptions>
                ),
              },
              {
                key: 'interface',
                label: 'Interface',
                children: selected.interface && (
                  <div>
                    <h4>Inputs</h4>
                    {getInterfaceInputs(selected.interface).length > 0
                      ? getInterfaceInputs(selected.interface).map(p => (
                        <div key={p.name} style={{ marginBottom: 8 }}>
                          <Text code>{p.name}</Text>
                          <Tag style={{ marginLeft: 8 }}>{p.type}</Tag>
                          {p.required && <Tag color="red">required</Tag>}
                          <div style={{ color: '#666', fontSize: 12 }}>{p.description}</div>
                        </div>
                      ))
                      : <Text type="secondary">No structured input fields.</Text>}
                    <h4 style={{ marginTop: 16 }}>Outputs</h4>
                    {getInterfaceOutputs(selected.interface).length > 0
                      ? getInterfaceOutputs(selected.interface).map(p => (
                        <div key={p.name} style={{ marginBottom: 8 }}>
                          <Text code>{p.name}</Text>
                          <Tag style={{ marginLeft: 8 }}>{p.type}</Tag>
                          <div style={{ color: '#666', fontSize: 12 }}>{p.description}</div>
                        </div>
                      ))
                      : <Text type="secondary">No structured output fields.</Text>}
                    {selected.interface.preconditions.length > 0 && (
                      <>
                        <h4 style={{ marginTop: 16 }}>Preconditions</h4>
                        {selected.interface.preconditions.map((c, i) => <div key={i}>• {c}</div>)}
                      </>
                    )}
                    {selected.interface.postconditions.length > 0 && (
                      <>
                        <h4 style={{ marginTop: 16 }}>Postconditions</h4>
                        {selected.interface.postconditions.map((c, i) => <div key={i}>• {c}</div>)}
                      </>
                    )}
                    <SchemaBlock title="Input Schema" schema={selected.interface.input_schema} />
                    <SchemaBlock title="Output Schema" schema={selected.interface.output_schema} />
                  </div>
                ),
              },
              {
                key: 'impl',
                label: 'Implementation',
                children: selected.implementation && (
                  <div>
                    <Tag color="blue">{selected.implementation.language}</Tag>
                    {selected.implementation.code && (
                      <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 8, marginTop: 8, overflow: 'auto', fontSize: 12 }}>
                        {selected.implementation.code}
                      </pre>
                    )}
                    {selected.implementation.prompt_template && (
                      <div style={{ background: '#f0f7ff', padding: 12, borderRadius: 8, marginTop: 8 }}>
                        {selected.implementation.prompt_template}
                      </div>
                    )}
                    {selected.implementation.sub_skill_ids.length > 0 && (
                      <div style={{ marginTop: 8 }}>
                        <strong>Sub Skills: </strong>
                        {selected.implementation.sub_skill_ids.map(id => <Tag key={id}>{id}</Tag>)}
                      </div>
                    )}
                  </div>
                ),
              },
              {
                key: 'metrics',
                label: 'Metrics',
                children: (
                  <Descriptions column={1} bordered size="small">
                    <Descriptions.Item label="Total Executions">{selected.metrics.total_executions}</Descriptions.Item>
                    <Descriptions.Item label="Successful">{selected.metrics.successful_executions}</Descriptions.Item>
                    <Descriptions.Item label="Failed">{selected.metrics.failed_executions}</Descriptions.Item>
                    <Descriptions.Item label="Success Rate">
                      <Progress percent={Math.round(selected.metrics.success_rate * 100)} size="small" style={{ width: 120 }} />
                    </Descriptions.Item>
                    <Descriptions.Item label="Average Latency">{selected.metrics.avg_latency_ms.toFixed(0)}ms</Descriptions.Item>
                    <Descriptions.Item label="Usage Count">{selected.metrics.usage_count}</Descriptions.Item>
                  </Descriptions>
                ),
              },
              {
                key: 'evaluation',
                label: 'Evaluation',
                children: (
                  <div>
                    {selectedHealth ? (
                      <Descriptions column={1} bordered size="small">
                        <Descriptions.Item label="Health Status">
                          <Badge status={selectedHealth.status === 'healthy' ? 'success' : selectedHealth.status === 'degraded' ? 'warning' : 'error'} text={selectedHealth.status} />
                        </Descriptions.Item>
                        <Descriptions.Item label="Success Rate">
                          <Progress percent={Math.round(selectedHealth.success_rate * 100)} size="small" style={{ width: 160 }} />
                        </Descriptions.Item>
                        <Descriptions.Item label="Usage Count">{selectedHealth.usage_count}</Descriptions.Item>
                        <Descriptions.Item label="Average Latency">{selectedHealth.avg_latency_ms.toFixed(0)}ms</Descriptions.Item>
                        <Descriptions.Item label="Issues">
                          {selectedHealth.issues.length > 0
                            ? selectedHealth.issues.map(issue => <Tag key={issue} color="red">{issue}</Tag>)
                            : <Tag color="green">No active issues</Tag>}
                        </Descriptions.Item>
                        <Descriptions.Item label="Recommendations">
                          {selectedHealth.recommendations.length > 0
                            ? selectedHealth.recommendations.map(item => <Tag key={item} color="blue">{item}</Tag>)
                            : <Text type="secondary">No recommendations.</Text>}
                        </Descriptions.Item>
                      </Descriptions>
                    ) : (
                      <Alert type="info" message="No evaluation report is available for this Skill yet." />
                    )}
                  </div>
                ),
              },
              {
                key: 'provenance',
                label: 'Provenance',
                children: (
                  <div>
                    <Descriptions column={1} bordered size="small">
                      <Descriptions.Item label="Source">
                        <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{JSON.stringify(selected.provenance || {}, null, 2)}</pre>
                      </Descriptions.Item>
                      <Descriptions.Item label="Tools">
                        {(selected.tool_refs || []).length > 0 ? selected.tool_refs?.map((item, index) => <Tag key={index}>{String(item)}</Tag>) : <Text type="secondary">None</Text>}
                      </Descriptions.Item>
                      <Descriptions.Item label="Documents">
                        {(selected.doc_refs || []).length > 0 ? selected.doc_refs?.map((item, index) => <Tag key={index}>{String(item)}</Tag>) : <Text type="secondary">None</Text>}
                      </Descriptions.Item>
                      <Descriptions.Item label="Trajectories">
                        {(selected.trajectory_refs || []).length > 0 ? selected.trajectory_refs?.map((item, index) => <Tag key={index}>{String(item)}</Tag>) : <Text type="secondary">None</Text>}
                      </Descriptions.Item>
                    </Descriptions>
                  </div>
                ),
              },
            ]}
          />
        )}
      </Drawer>
    </div>
  )
}
