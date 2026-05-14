import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import {
  Badge,
  Button,
  Descriptions,
  Drawer,
  Input,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { TableColumnsType } from 'antd'
import {
  CheckOutlined,
  EyeOutlined,
  HistoryOutlined,
  PlayCircleOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { motion } from 'framer-motion'
import { useLocation, useNavigate } from 'react-router-dom'
import { skillsApi, lifecycleApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import type { SkillFull, SkillParameter, SkillState, SkillSummary, SkillType } from '@/api/types'

const { Text, Paragraph } = Typography

const STATE_COLOR: Record<string, string> = {
  S0: 'purple',
  S1: 'orange',
  S2: 'blue',
  S3: 'cyan',
  S4: 'green',
  S5: 'gold',
  S6: 'red',
  S7: 'default',
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

const TYPE_COLOR: Record<string, string> = {
  atomic: 'blue',
  functional: 'purple',
  strategic: 'gold',
}

function schemaToParameters(schema: SkillFull['interface']['input_schema'] | SkillFull['interface']['output_schema']): SkillParameter[] {
  const properties = schema?.properties ?? {}
  const required = new Set(schema?.required ?? [])

  return Object.entries(properties).map(([name, field]) => ({
    name,
    type: field.type ?? 'unknown',
    description: field.description ?? '',
    required: required.has(name),
    default: field.default,
  }))
}

function getInterfaceParameters(skill: SkillFull, direction: 'input' | 'output'): SkillParameter[] {
  const skillInterface = skill.interface
  if (!skillInterface) return []
  if (direction === 'input') {
    return Array.isArray(skillInterface.inputs) ? skillInterface.inputs : schemaToParameters(skillInterface.input_schema)
  }
  return Array.isArray(skillInterface.outputs) ? skillInterface.outputs : schemaToParameters(skillInterface.output_schema)
}

function uniqueTags(tags?: string[]): string[] {
  return Array.from(new Set((tags ?? []).filter(Boolean)))
}

function formatDate(value?: string) {
  if (!value) return ''
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

function buildSkillTimeline(skill: SkillFull) {
  const items: {
    color: string
    dot?: ReactNode
    children: ReactNode
  }[] = []
  const provenance = skill.provenance
  const evaluation = skill.evaluation
  const metrics = skill.metrics

  if (provenance?.source_type || provenance?.source_ids?.length) {
    items.push({
      color: 'blue',
      children: (
        <div>
          <Text strong>Source imported</Text>
          <div style={{ fontSize: 12, color: '#666' }}>
            {provenance.source_type || 'unknown source'} {provenance.source_ids?.slice(0, 2).map(id => <Tag key={id}>{id}</Tag>)}
          </div>
        </div>
      ),
    })
  }

  items.push({
    color: 'orange',
    children: (
      <div>
        <Text strong>Candidate created</Text>
        <div style={{ fontSize: 12, color: '#666' }}>{formatDate(skill.created_at)}</div>
      </div>
    ),
  })

  if (evaluation?.verifier_specs?.length || evaluation?.validation_summary) {
    items.push({
      color: 'cyan',
      dot: <SafetyCertificateOutlined />,
      children: (
        <div>
          <Text strong>Audited / verifier attached</Text>
          <div style={{ fontSize: 12, color: '#666' }}>
            {evaluation.validation_summary || `${evaluation.verifier_specs.length} verifier spec(s)`}
          </div>
        </div>
      ),
    })
  }

  if (['S3', 'S4', 'S5', 'S6', 'S7'].includes(skill.state)) {
    items.push({
      color: skill.state === 'S4' ? 'green' : 'blue',
      children: (
        <div>
          <Text strong>{skill.state === 'S4' ? 'Released' : STATE_LABEL[skill.state]}</Text>
          <div style={{ fontSize: 12, color: '#666' }}>Current state: {STATE_LABEL[skill.state]}</div>
        </div>
      ),
    })
  }

  if (metrics.total_executions > 0) {
    items.push({
      color: 'purple',
      dot: <PlayCircleOutlined />,
      children: (
        <div>
          <Text strong>Executed</Text>
          <div style={{ fontSize: 12, color: '#666' }}>
            {metrics.total_executions} runs, {(metrics.success_rate * 100).toFixed(1)}% success
          </div>
        </div>
      ),
    })
  }

  if (metrics.failed_executions > 0 || skill.state === 'S5') {
    items.push({
      color: 'red',
      children: (
        <div>
          <Text strong>Failed / degraded evidence</Text>
          <div style={{ fontSize: 12, color: '#666' }}>
            {metrics.failed_executions} failed run(s)
          </div>
        </div>
      ),
    })
  } else if (metrics.successful_executions > 0 || evaluation?.validation_summary) {
    items.push({
      color: 'green',
      children: (
        <div>
          <Text strong>Validated evidence</Text>
          <div style={{ fontSize: 12, color: '#666' }}>
            {metrics.successful_executions} successful run(s)
          </div>
        </div>
      ),
    })
  }

  if (provenance?.parent_skill_ids?.length || skill.tags.includes('maintenance') || skill.tags.includes('repair')) {
    items.push({
      color: 'gold',
      children: (
        <div>
          <Text strong>Repaired / derived</Text>
          <div style={{ fontSize: 12, color: '#666' }}>
            {provenance?.parent_skill_ids?.map(id => <Tag key={id}>{id}</Tag>)}
          </div>
        </div>
      ),
    })
  }

  items.push({
    color: 'blue',
    dot: <HistoryOutlined />,
    children: (
      <div>
        <Text strong>Versioned</Text>
        <div style={{ fontSize: 12, color: '#666' }}>
          <Text code>v{skill.version}</Text> updated {formatDate(skill.updated_at)}
        </div>
      </div>
    ),
  })

  return items
}

export default function SkillWiki() {
  const location = useLocation()
  const navigate = useNavigate()
  const openedFromQuery = useRef<string | null>(null)
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [stateFilter, setStateFilter] = useState<SkillState | undefined>()
  const [typeFilter, setTypeFilter] = useState<SkillType | undefined>()
  const [selected, setSelected] = useState<SkillFull | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await skillsApi.list({ state: stateFilter, skill_type: typeFilter, limit: 200 })
      setSkills(data)
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Failed to load Skills'))
    } finally {
      setLoading(false)
    }
  }, [stateFilter, typeFilter])

  useEffect(() => {
    const timeoutId = window.setTimeout(() => { void load() }, 0)
    return () => window.clearTimeout(timeoutId)
  }, [load])

  const filtered = skills.filter(skill => {
    if (!search) return true
    return (
      skill.name.includes(search) ||
      skill.description.includes(search) ||
      skill.tags.some(tag => tag.includes(search))
    )
  })

  const openDetail = async (id: string) => {
    const full = await skillsApi.getFull(id)
    setSelected(full)
    setDrawerOpen(true)
  }

  useEffect(() => {
    const id = new URLSearchParams(location.search).get('skill_id')
    if (!id || openedFromQuery.current === id) return
    openedFromQuery.current = id
    openDetail(id).catch(err => message.error(getApiErrorMessage(err, 'Skill does not exist or is temporarily unavailable')))
  }, [location.search])

  const handleRelease = async (id: string) => {
    try {
      await lifecycleApi.release(id)
      message.success('Released')
      void load()
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Release failed'))
    }
  }

  const handleDeprecate = async (id: string) => {
    try {
      await lifecycleApi.deprecate(id, 'Manual deprecation')
      message.success('Deprecated')
      void load()
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Deprecation failed'))
    }
  }

  const columns: TableColumnsType<SkillSummary> = [
    {
      title: 'Name',
      dataIndex: 'name',
      render: (name: string, record) => (
        <Button type="link" onClick={() => openDetail(record.skill_id)} style={{ padding: 0, fontWeight: 600 }}>
          {name}
        </Button>
      ),
    },
    {
      title: 'Type',
      dataIndex: 'skill_type',
      render: (type: string) => <Tag color={TYPE_COLOR[type]}>{type.toUpperCase()}</Tag>,
    },
    {
      title: 'State',
      dataIndex: 'state',
      render: (state: string) => <Badge color={STATE_COLOR[state] || 'default'} text={STATE_LABEL[state] || state} />,
    },
    {
      title: 'Version',
      dataIndex: 'version',
      render: (version: string) => <Text code>{version}</Text>,
    },
    {
      title: 'Tags',
      dataIndex: 'tags',
      render: (tags: string[]) => uniqueTags(tags).slice(0, 3).map(tag => <Tag key={tag}>{tag}</Tag>),
    },
    {
      title: 'Success Rate',
      dataIndex: 'metrics',
      render: (metrics: SkillSummary['metrics']) => (
        metrics.total_executions >= 5
          ? <Progress percent={Math.round(metrics.success_rate * 100)} size="small" style={{ width: 80 }} />
          : <Text type="secondary">N/A</Text>
      ),
    },
    {
      title: 'Actions',
      render: (_, record) => (
        <Space>
          <Tooltip title="View details">
            <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(record.skill_id)} />
          </Tooltip>
          {record.state === 'S2' && (
            <Tooltip title="Release">
              <Button size="small" icon={<CheckOutlined />} type="primary" onClick={() => handleRelease(record.skill_id)} />
            </Tooltip>
          )}
          {record.state === 'S4' && (
            <Tooltip title="Deprecate">
              <Popconfirm title="Deprecate this Skill?" onConfirm={() => handleDeprecate(record.skill_id)}>
                <Button size="small" icon={<StopOutlined />} danger />
              </Popconfirm>
            </Tooltip>
          )}
        </Space>
      ),
    },
  ]

  const selectedTags = uniqueTags(selected?.tags)
  const selectedInputParams = selected ? getInterfaceParameters(selected, 'input') : []
  const selectedOutputParams = selected ? getInterfaceParameters(selected, 'output') : []
  const selectedPreconditions = selected?.interface?.preconditions ?? []
  const selectedPostconditions = selected?.interface?.postconditions ?? []
  const selectedSubSkillIds = selected?.implementation?.sub_skill_ids ?? []
  const selectedEvaluation = selected?.evaluation
  const selectedProvenance = selected?.provenance
  const selectedTimeline = selected ? buildSkillTimeline(selected) : []

  return (
    <div style={{ padding: 24 }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
          <Input
            placeholder="Search Skill name, description, or tags"
            prefix={<SearchOutlined />}
            value={search}
            onChange={event => setSearch(event.target.value)}
            style={{ width: 280 }}
            allowClear
          />
          <Select
            placeholder="Filter by state"
            allowClear
            style={{ width: 150 }}
            onChange={value => setStateFilter(value as SkillState)}
            options={[
              { label: 'Raw (S0)', value: 'S0' },
              { label: 'Candidate (S1)', value: 'S1' },
              { label: 'Draft (S2)', value: 'S2' },
              { label: 'Verified (S3)', value: 'S3' },
              { label: 'Released (S4)', value: 'S4' },
              { label: 'Degraded (S5)', value: 'S5' },
              { label: 'Deprecated (S6)', value: 'S6' },
              { label: 'Archived (S7)', value: 'S7' },
            ]}
          />
          <Select
            placeholder="Filter by type"
            allowClear
            style={{ width: 150 }}
            onChange={value => setTypeFilter(value as SkillType)}
            options={['atomic', 'functional', 'strategic'].map(type => ({ label: type, value: type }))}
          />
          <Button onClick={load}>Refresh</Button>
        </div>

        <Table
          dataSource={filtered}
          columns={columns}
          rowKey="skill_id"
          loading={loading}
          size="middle"
          pagination={{ pageSize: 15, showSizeChanger: true }}
          style={{ borderRadius: 8, overflow: 'hidden' }}
        />
      </motion.div>

      <Drawer
        title={selected?.name}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        size="large"
        extra={selected && (
          <Space>
            <Button size="small" onClick={() => navigate(`/graph?skill_id=${encodeURIComponent(selected.skill_id)}`)}>
              View Graph
            </Button>
            <Tag color={TYPE_COLOR[selected.skill_type]}>{selected.skill_type.toUpperCase()}</Tag>
            <Badge color={STATE_COLOR[selected.state]} text={STATE_LABEL[selected.state] || selected.state} />
          </Space>
        )}
      >
        {selected && (
          <Tabs
            items={[
              {
                key: 'info',
                label: 'Basic Info',
                children: (
                  <Descriptions column={1} bordered size="small">
                    <Descriptions.Item label="ID"><Text code copyable>{selected.skill_id}</Text></Descriptions.Item>
                    <Descriptions.Item label="Version"><Text code>{selected.version}</Text></Descriptions.Item>
                    <Descriptions.Item label="Description"><Paragraph>{selected.description}</Paragraph></Descriptions.Item>
                    <Descriptions.Item label="Tags">
                      {selectedTags.length > 0
                        ? selectedTags.map(tag => <Tag key={tag}>{tag}</Tag>)
                        : <Text type="secondary">No tags</Text>}
                    </Descriptions.Item>
                    <Descriptions.Item label="Granularity">{selected.granularity_level}</Descriptions.Item>
                    <Descriptions.Item label="Created At">{new Date(selected.created_at).toLocaleString()}</Descriptions.Item>
                    <Descriptions.Item label="Updated At">{new Date(selected.updated_at).toLocaleString()}</Descriptions.Item>
                  </Descriptions>
                ),
              },
              {
                key: 'interface',
                label: 'Interface',
                children: selected.interface && (
                  <div>
                    <h4>Input Parameters</h4>
                    {selectedInputParams.length > 0
                      ? selectedInputParams.map(param => (
                        <div key={param.name} style={{ marginBottom: 8 }}>
                          <Text code>{param.name}</Text>
                          <Tag style={{ marginLeft: 8 }}>{param.type}</Tag>
                          {param.required && <Tag color="red">Required</Tag>}
                          <div style={{ color: '#666', fontSize: 12 }}>{param.description}</div>
                        </div>
                      ))
                      : <Text type="secondary">No input parameters</Text>}
                    <h4 style={{ marginTop: 16 }}>Output Parameters</h4>
                    {selectedOutputParams.length > 0
                      ? selectedOutputParams.map(param => (
                        <div key={param.name} style={{ marginBottom: 8 }}>
                          <Text code>{param.name}</Text>
                          <Tag style={{ marginLeft: 8 }}>{param.type}</Tag>
                          <div style={{ color: '#666', fontSize: 12 }}>{param.description}</div>
                        </div>
                      ))
                      : <Text type="secondary">No output parameters</Text>}
                    {selectedPreconditions.length > 0 && (
                      <>
                        <h4 style={{ marginTop: 16 }}>Preconditions</h4>
                        {selectedPreconditions.map((condition, index) => <div key={index}>- {condition}</div>)}
                      </>
                    )}
                    {selectedPostconditions.length > 0 && (
                      <>
                        <h4 style={{ marginTop: 16 }}>Postconditions</h4>
                        {selectedPostconditions.map((condition, index) => <div key={index}>- {condition}</div>)}
                      </>
                    )}
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
                    {selectedSubSkillIds.length > 0 && (
                      <div style={{ marginTop: 8 }}>
                        <strong>Sub-Skills:</strong>
                        {selectedSubSkillIds.map(id => <Tag key={id}>{id}</Tag>)}
                      </div>
                    )}
                  </div>
                ),
              },
              {
                key: 'evidence',
                label: 'Evidence',
                children: (
                  <div>
                    <h4>Provenance</h4>
                    {selectedProvenance ? (
                      <Descriptions column={1} bordered size="small">
                        <Descriptions.Item label="Source type">{selectedProvenance.source_type}</Descriptions.Item>
                        <Descriptions.Item label="Source IDs">
                          {selectedProvenance.source_ids.length
                            ? selectedProvenance.source_ids.map(id => <Tag key={id}>{id}</Tag>)
                            : <Text type="secondary">None</Text>}
                        </Descriptions.Item>
                        <Descriptions.Item label="Parent Skills">
                          {selectedProvenance.parent_skill_ids.length
                            ? selectedProvenance.parent_skill_ids.map(id => <Tag key={id}>{id}</Tag>)
                            : <Text type="secondary">None</Text>}
                        </Descriptions.Item>
                        <Descriptions.Item label="Created by">{selectedProvenance.created_by_agent || 'unknown'}</Descriptions.Item>
                        <Descriptions.Item label="Context">
                          <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 8, overflow: 'auto', fontSize: 12 }}>
                            {JSON.stringify(selectedProvenance.creation_context, null, 2)}
                          </pre>
                        </Descriptions.Item>
                      </Descriptions>
                    ) : (
                      <Text type="secondary">No provenance recorded.</Text>
                    )}

                    <h4 style={{ marginTop: 16 }}>Evaluation</h4>
                    {selectedEvaluation ? (
                      <Descriptions column={1} bordered size="small">
                        <Descriptions.Item label="Verifier specs">
                          <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 8, overflow: 'auto', fontSize: 12 }}>
                            {JSON.stringify(selectedEvaluation.verifier_specs, null, 2)}
                          </pre>
                        </Descriptions.Item>
                        <Descriptions.Item label="Test cases">
                          {selectedEvaluation.test_case_refs.length
                            ? selectedEvaluation.test_case_refs.map(id => <Tag key={id}>{id}</Tag>)
                            : <Text type="secondary">None</Text>}
                        </Descriptions.Item>
                        <Descriptions.Item label="Benchmark tasks">
                          {selectedEvaluation.benchmark_task_ids.length
                            ? selectedEvaluation.benchmark_task_ids.map(id => <Tag key={id}>{id}</Tag>)
                            : <Text type="secondary">None</Text>}
                        </Descriptions.Item>
                        <Descriptions.Item label="Validation summary">
                          {selectedEvaluation.validation_summary || <Text type="secondary">None</Text>}
                        </Descriptions.Item>
                      </Descriptions>
                    ) : (
                      <Text type="secondary">No evaluation contract recorded.</Text>
                    )}
                  </div>
                ),
              },
              {
                key: 'timeline',
                label: 'Timeline',
                children: (
                  <Timeline items={selectedTimeline} />
                ),
              },
              {
                key: 'metrics',
                label: 'Metrics',
                children: (
                  <Descriptions column={1} bordered size="small">
                    <Descriptions.Item label="Total Executions">{selected.metrics.total_executions}</Descriptions.Item>
                    <Descriptions.Item label="Successful Executions">{selected.metrics.successful_executions}</Descriptions.Item>
                    <Descriptions.Item label="Failed Executions">{selected.metrics.failed_executions}</Descriptions.Item>
                    <Descriptions.Item label="Success Rate">
                      <Progress percent={Math.round(selected.metrics.success_rate * 100)} size="small" style={{ width: 120 }} />
                    </Descriptions.Item>
                    <Descriptions.Item label="Average Latency">{selected.metrics.avg_latency_ms.toFixed(0)}ms</Descriptions.Item>
                    <Descriptions.Item label="Usage Count">{selected.metrics.usage_count}</Descriptions.Item>
                  </Descriptions>
                ),
              },
            ]}
          />
        )}
      </Drawer>
    </div>
  )
}
