import { useEffect, useState } from 'react'
import {
  Card, Tabs, Input, Button, Alert, Tag, Progress, Space,
  Row, Col, Typography, Divider, List, Badge, message, Steps, Select,
  Segmented,
} from 'antd'
import {
  CloudUploadOutlined, CodeOutlined, FileTextOutlined,
  ApiOutlined, PlayCircleOutlined, CheckCircleOutlined,
  FilterOutlined, CompressOutlined, FileSearchOutlined, DatabaseOutlined,
  LoadingOutlined, HistoryOutlined, ArrowRightOutlined,
} from '@ant-design/icons'
import { motion, AnimatePresence } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { hostInfoApi, ingestApi, skillsApi } from '@/api/client'
import type { IngestResponse } from '@/api/client'
import type { HostSurveyPreset, HostSurveyResponse, SkillVisibility } from '@/api/types'

const { TextArea } = Input
const { Text, Paragraph } = Typography

const SOURCE_TYPES = [
  {
    key: 'trajectory',
    label: 'Trajectory',
    icon: <PlayCircleOutlined />,
    color: '#1677ff',
    placeholder: 'Paste a fixed trajectory JSON or load the demo fixture.',
  },
  {
    key: 'document',
    label: 'Document',
    icon: <FileTextOutlined />,
    color: '#52c41a',
    placeholder: 'Paste a fixed document JSON or load the demo fixture.',
  },
  {
    key: 'api_doc',
    label: 'API Doc',
    icon: <ApiOutlined />,
    color: '#722ed1',
    placeholder: 'Paste a fixed API documentation JSON or load the demo fixture.',
  },
  {
    key: 'script',
    label: 'Script',
    icon: <CodeOutlined />,
    color: '#fa8c16',
    placeholder: 'Paste a fixed script JSON or load the demo fixture.',
  },
  {
    key: 'natural_language',
    label: 'Natural Workflow',
    icon: <FileSearchOutlined />,
    color: '#13c2c2',
    placeholder: 'Describe the reusable Skill very precisely: goal, variable inputs, expected outputs, detailed workflow steps, and validation criteria.',
  },
  {
    key: 'host_survey',
    label: 'Host Survey',
    icon: <DatabaseOutlined />,
    color: '#2f54eb',
    placeholder: 'Run safe read-only host information collection tasks.',
  },
]

const TYPE_COLOR: Record<string, string> = {
  atomic: 'blue', functional: 'purple', strategic: 'gold',
}

const PIPELINE_STAGES = [
  { title: 'Extractor', icon: <FilterOutlined />, desc: 'Extract fixed source actions' },
  { title: 'Generalizer', icon: <CompressOutlined />, desc: 'Split generic and scenario-specific capabilities' },
  { title: 'Normalizer', icon: <CompressOutlined />, desc: 'Normalize into experience units' },
  { title: 'Summarizer', icon: <FileSearchOutlined />, desc: 'Propose reusable Skills' },
  { title: 'Indexer', icon: <DatabaseOutlined />, desc: 'Write Skills and graph nodes' },
]

const DEMO_FIXTURES: Record<string, string> = {
  trajectory: JSON.stringify({
    source_id: 'trajectory:checkout_walkthrough',
    source_type: 'trajectory',
    title: 'Checkout Walkthrough Trajectory',
    description: 'A curated desktop/browser trajectory that mixes generic file and URL opening with a checkout flow.',
    content: 'Open abc.json from Downloads to read task data, open https://shop.example.com/checkout, add item to cart, fill shipping form, submit checkout.',
    skills: [
      {
        name: 'complete_checkout_flow',
        type: 'functional',
        version: '1.0.0',
        description: 'Complete an e-commerce checkout flow from cart to confirmation.',
        tags: ['web', 'checkout', 'trajectory'],
        actions: ['open abc.json file', 'open https://shop.example.com/checkout', 'fill shipping form', 'submit checkout', 'verify confirmation page'],
        tools: ['Browser Driver'],
        tests: ['checkout happy path', 'missing address validation'],
        confidence: 0.94,
      },
    ],
  }, null, 2),
  document: JSON.stringify({
    source_id: 'document:form_validation_spec',
    source_type: 'document',
    title: 'Form Validation Specification',
    description: 'Static requirements for validating and submitting web forms.',
    content: 'Required fields must be checked before submission. Invalid inputs should produce field-level errors.',
    skills: [
      {
        name: 'validate_required_form_fields',
        type: 'atomic',
        version: '1.0.0',
        description: 'Validate required fields before a form is submitted.',
        tags: ['form', 'validation', 'document'],
        actions: ['inspect required field schema', 'check each required value', 'return validation errors'],
        tests: ['all required fields present', 'missing required field'],
        confidence: 0.93,
      },
    ],
  }, null, 2),
  api_doc: JSON.stringify({
    source_id: 'api_doc:auth_service_v1',
    source_type: 'api_doc',
    title: 'Auth Service API v1',
    description: 'Static API documentation for login and profile retrieval.',
    content: 'POST /login accepts username and password. GET /profile returns the authenticated profile.',
    skills: [
      {
        name: 'authenticate_and_fetch_profile',
        type: 'functional',
        version: '1.0.0',
        description: 'Authenticate a user and fetch the profile with the returned session token.',
        tags: ['api', 'auth', 'profile'],
        actions: ['send login request', 'extract session token', 'call profile endpoint', 'validate profile response'],
        api_endpoints: ['POST /login', 'GET /profile'],
        tools: ['HTTP Client'],
        tests: ['valid credentials profile fetch', 'invalid credentials rejection'],
        confidence: 0.96,
        interface: {
          input_schema: {
            type: 'object',
            properties: {
              username: { type: 'string' },
              password: { type: 'string' },
            },
            required: ['username', 'password'],
          },
          output_schema: {
            type: 'object',
            properties: {
              profile: { type: 'object' },
              authenticated: { type: 'boolean' },
            },
          },
        },
      },
    ],
  }, null, 2),
  script: JSON.stringify({
    source_id: 'script:browser_login_helper',
    source_type: 'script',
    title: 'Browser Login Helper Script',
    description: 'Static Playwright-style helper used to log into a web application and open Chrome settings.',
    content: 'async def login(page, username, password): open Chrome settings, navigate to https://app.example.com/login, fill fields and click submit.',
    skills: [
      {
        name: 'browser_login_flow',
        type: 'functional',
        version: '1.0.0',
        description: 'Log into a browser application using username and password fields.',
        tags: ['browser', 'login', 'script'],
        actions: ['navigate to login page', 'fill username', 'fill password', 'click submit', 'wait for dashboard'],
        tools: ['Browser Driver'],
        tests: ['successful browser login', 'login timeout'],
        confidence: 0.95,
        implementation: {
          prompt_template: 'Use browser automation to fill credentials, submit the login form, and verify dashboard access.',
        },
      },
    ],
  }, null, 2),
  natural_language: JSON.stringify({
    title: 'Parameterized Website Opening Workflow',
    description: 'A natural-language workflow Skill where the agent decides the target URL from the user task.',
    workflow: [
      'Read the user task and identify the target organization, product, website, or URL.',
      'If a concrete URL is provided, normalize it to a full URL.',
      'If only a target name is provided, let the agent infer or search for the most likely official website URL.',
      'Open Google Chrome with the resolved URL.',
      'Validate that the opened URL matches the user task instead of reusing examples from this import.',
    ].join('\n'),
    parameters: [
      { name: 'url', type: 'string', description: 'Target URL generated or verified by the execution agent', required: false },
      { name: 'goal', type: 'string', description: 'Original user task used to resolve the URL', required: false },
    ],
    outputs: [
      { name: 'launched', type: 'boolean', description: 'Whether Chrome accepted the open request' },
      { name: 'url', type: 'string', description: 'The URL opened by the runtime' },
    ],
    tool_calls: ['host.open_url_in_chrome'],
    test_cases: [
      {
        name: 'open arbitrary official website',
        input_data: { goal: 'Open HITWH official website', url: 'https://www.hitwh.edu.cn/' },
        expected_output: { launched: true },
      },
    ],
  }, null, 2),
}

const HISTORY_KEY = 'skillos.import.history.v1'

interface ImportHistoryItem {
  id: string
  mode: 'parse' | 'create'
  sourceType: string
  sourceTitle: string
  summary: string
  skillIds: string[]
  skillNames: string[]
  skillVisibility: Record<string, SkillVisibility>
  graphNodesCreated: number
  graphEdgesCreated: number
  success: boolean
  errorCount: number
  createdAt: string
}

function getSourceSummary(content: string, fallbackType: string) {
  try {
    const parsed = JSON.parse(content) as Record<string, unknown>
    return {
      title: String(parsed.title || parsed.source_id || fallbackType),
      summary: String(parsed.description || parsed.content || 'Fixed demo source imported into SkillOS.'),
    }
  } catch {
    return {
      title: fallbackType,
      summary: content.slice(0, 160) || 'Fixed demo source imported into SkillOS.',
    }
  }
}

function loadHistory(): ImportHistoryItem[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    const items = raw ? JSON.parse(raw) as ImportHistoryItem[] : []
    return items.map(item => ({ ...item, skillVisibility: item.skillVisibility || {} }))
  } catch {
    return []
  }
}

function capabilityLabel(metadata: Record<string, unknown>) {
  const scope = String(metadata.capability_scope || '')
  const kind = String(metadata.capability_kind || '')
  if (!scope && !kind) return ''
  return [scope, kind].filter(Boolean).join(' · ')
}

export default function KnowledgeImport() {
  const [activeTab, setActiveTab] = useState('trajectory')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [pipelineStage, setPipelineStage] = useState(-1)
  const [result, setResult] = useState<IngestResponse | null>(null)
  const [mode, setMode] = useState<'parse' | 'create'>('parse')
  const [importHistory, setImportHistory] = useState<ImportHistoryItem[]>(() => loadHistory())
  const [historyVisibility, setHistoryVisibility] = useState<SkillVisibility | 'all'>('user')
  const [hostPresets, setHostPresets] = useState<HostSurveyPreset[]>([])
  const [selectedHostTaskIds, setSelectedHostTaskIds] = useState<string[]>([])
  const [hostSurveyLoading, setHostSurveyLoading] = useState(false)
  const [hostSurvey, setHostSurvey] = useState<HostSurveyResponse | null>(null)
  const navigate = useNavigate()

  const currentSource = SOURCE_TYPES.find(s => s.key === activeTab)!

  const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

  useEffect(() => {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(importHistory.slice(0, 20)))
  }, [importHistory])

  useEffect(() => {
    hostInfoApi.presets()
      .then(setHostPresets)
      .catch(() => undefined)
  }, [])

  const rememberImport = async (submitMode: 'parse' | 'create', res: IngestResponse) => {
    const source = getSourceSummary(content, activeTab)
    const skillVisibility: Record<string, SkillVisibility> = {}
    await Promise.all((res.created_skill_ids || []).map(async skillId => {
      try {
        const skill = await skillsApi.get(skillId)
        skillVisibility[skillId] = skill.visibility
      } catch {
        skillVisibility[skillId] = 'user'
      }
    }))
    const item: ImportHistoryItem = {
      id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      mode: submitMode,
      sourceType: res.source_type || activeTab,
      sourceTitle: source.title,
      summary: source.summary,
      skillIds: res.created_skill_ids || [],
      skillNames: res.units.map(unit => unit.proposed_skill_name || 'Unnamed Skill'),
      skillVisibility,
      graphNodesCreated: res.graph_nodes_created,
      graphEdgesCreated: res.graph_edges_created,
      success: res.success,
      errorCount: res.errors.length,
      createdAt: new Date().toISOString(),
    }
    setImportHistory(prev => [item, ...prev].slice(0, 20))
  }

  const handleSubmit = async (submitMode: 'parse' | 'create') => {
    if (!content.trim()) {
      message.warning('Please provide a fixed demo input first.')
      return
    }
    setMode(submitMode)
    setLoading(true)
    setResult(null)
    setPipelineStage(0)

    try {
      // Animate pipeline stage progress while the backend processes fixed input.
      const stagePromise = (async () => {
        for (let i = 0; i < PIPELINE_STAGES.length; i++) {
          setPipelineStage(i)
          await sleep(400)
        }
      })()

      const apiPromise = submitMode === 'create'
        ? ingestApi.parseAndCreate(activeTab, content)
        : ingestApi.parse(activeTab, content)

      const [res] = await Promise.all([apiPromise, stagePromise])
      setPipelineStage(PIPELINE_STAGES.length)
      setResult(res)
      await rememberImport(submitMode, res)
      if (res.success) {
        message.success(`Pipeline completed: ${res.unit_count} experience unit(s).`)
      } else {
        message.warning('Pipeline finished with warnings.')
      }
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Pipeline failed.')
      setPipelineStage(-1)
    } finally {
      setLoading(false)
    }
  }

  const runHostSurvey = async () => {
    setHostSurveyLoading(true)
    setHostSurvey(null)
    try {
      const res = await hostInfoApi.survey({
        task_ids: selectedHostTaskIds.length ? selectedHostTaskIds : undefined,
        use_llm: true,
        persist: true,
        max_output_chars: 4000,
      })
      setHostSurvey(res)
      if (res.success) {
        message.success(`Host survey completed: ${res.created_nodes} node(s), ${res.created_edges} edge(s).`)
      } else {
        message.warning('Host survey finished with partial failures.')
      }
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Host survey failed.')
    } finally {
      setHostSurveyLoading(false)
    }
  }

  const filteredImportHistory = importHistory.filter(item => {
    if (historyVisibility === 'all') return true
    if (item.skillIds.length === 0) return historyVisibility === 'kernel' && item.sourceType === 'host_survey'
    return item.skillIds.some(skillId => (item.skillVisibility?.[skillId] || 'user') === historyVisibility)
  })

  return (
    <div style={{ padding: 24 }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h2 style={{ fontWeight: 700, marginBottom: 4 }}>Knowledge Import Pipeline</h2>
        <p style={{ color: '#666', marginBottom: 24 }}>
          Use fixed research/demo sources to extract Skills and write source, tool, test, version, and Skill nodes into the graph.
        </p>
      </motion.div>

      <Row gutter={[16, 16]}>
        <Col span={24}>
          <Card
            bordered={false}
            style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          >
            <Alert
              type="info"
              showIcon
              message="Natural Workflow is for human-described GUI/process knowledge. Describe variable inputs explicitly so the imported Skill stays generic instead of memorizing one example."
              style={{ marginBottom: 12 }}
            />
            <Tabs
              activeKey={activeTab}
              onChange={k => { setActiveTab(k); setContent(''); setResult(null) }}
              items={SOURCE_TYPES.map(s => ({
                key: s.key,
                label: <span>{s.icon} {s.label}</span>,
                children: null,
              }))}
            />

            <div style={{ marginBottom: 12 }}>
              <Tag color={currentSource.color} style={{ marginBottom: 8 }}>
                {currentSource.icon} {currentSource.label}
              </Tag>
            </div>

            {activeTab === 'host_survey' ? (
              <div>
                <Alert
                  type="warning"
                  showIcon
                  message="Host Survey is kernel-mode knowledge collection. The LLM may propose commands, but only read-only allowlisted commands are executed."
                  style={{ marginBottom: 12 }}
                />
                <Select
                  mode="multiple"
                  allowClear
                  placeholder="Select host survey tasks, or leave empty to run all presets"
                  style={{ width: '100%', marginBottom: 12 }}
                  value={selectedHostTaskIds}
                  onChange={setSelectedHostTaskIds}
                  options={hostPresets.map(preset => ({
                    value: preset.task_id,
                    label: `${preset.name} · ${preset.task_id}`,
                  }))}
                />
                <Space wrap>
                  <Button
                    type="primary"
                    icon={<DatabaseOutlined />}
                    loading={hostSurveyLoading}
                    onClick={runHostSurvey}
                  >
                    Plan + Run Safe Host Survey
                  </Button>
                  <Button onClick={() => setSelectedHostTaskIds([])}>Select All Presets</Button>
                </Space>
                <List
                  style={{ marginTop: 16 }}
                  dataSource={hostPresets}
                  renderItem={preset => (
                    <List.Item style={{ padding: '8px 0' }}>
                      <List.Item.Meta
                        title={<Space wrap><Text strong>{preset.name}</Text><Tag>{preset.task_id}</Tag></Space>}
                        description={
                          <div>
                            <Paragraph style={{ marginBottom: 4 }}>{preset.description}</Paragraph>
                            <Space wrap size={[4, 4]}>
                              {preset.labels.map(label => <Tag key={label} color="blue">{label}</Tag>)}
                              <Text code>{preset.fallback_command.join(' ')}</Text>
                            </Space>
                          </div>
                        }
                      />
                    </List.Item>
                  )}
                />
              </div>
            ) : (
              <>
                <TextArea
                  value={content}
                  onChange={e => setContent(e.target.value)}
                  placeholder={currentSource.placeholder}
                  rows={14}
                  style={{ fontFamily: 'monospace', fontSize: 13 }}
                />

                <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <Button
                    onClick={() => setContent(DEMO_FIXTURES[activeTab])}
                  >
                    Load Demo Fixture
                  </Button>
                  <Button
                    type="primary"
                    icon={<CloudUploadOutlined />}
                    loading={loading}
                    onClick={() => handleSubmit('parse')}
                  >
                    Preview Pipeline
                  </Button>
                  <Button
                    type="primary"
                    icon={<CheckCircleOutlined />}
                    loading={loading}
                    style={{ background: '#52c41a', borderColor: '#52c41a' }}
                    onClick={() => handleSubmit('create')}
                  >
                    Create Skill + Graph
                  </Button>
                  <div style={{ fontSize: 12, color: '#8c8c8c', width: '100%' }}>
                    Preview only parses. Create writes Skill and heterogeneous graph nodes.
                  </div>
                </div>

                {(loading || pipelineStage >= 0) && (
                  <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} style={{ marginTop: 16 }}>
                    <Divider style={{ margin: '12px 0' }} />
                    <div style={{ marginBottom: 8 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>Experience Processing Pipeline</Text>
                    </div>
                    <Steps
                      size="small"
                      current={pipelineStage}
                      status={pipelineStage >= PIPELINE_STAGES.length ? 'finish' : 'process'}
                      items={PIPELINE_STAGES.map((s, i) => ({
                        title: s.title,
                        description: s.desc,
                        icon: pipelineStage > i
                          ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                          : pipelineStage === i && loading
                            ? <LoadingOutlined style={{ color: '#1677ff' }} />
                            : s.icon,
                      }))}
                    />
                  </motion.div>
                )}
              </>
            )}
          </Card>
        </Col>

        {hostSurvey && (
          <Col span={24}>
            <Card
              title="Host Survey Result"
              bordered={false}
              style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
              extra={<Badge status={hostSurvey.success ? 'success' : 'warning'} text={`${hostSurvey.created_nodes} nodes · ${hostSurvey.created_edges} edges`} />}
            >
              <List
                dataSource={hostSurvey.commands}
                renderItem={command => (
                  <List.Item style={{ alignItems: 'flex-start' }}>
                    <List.Item.Meta
                      title={
                        <Space wrap>
                          <Tag color={command.status === 'success' ? 'green' : 'red'}>{command.status}</Tag>
                          <Text strong>{command.name}</Text>
                          <Tag color={command.command_source === 'llm' ? 'purple' : 'blue'}>{command.command_source}</Tag>
                          {command.node_id && <Tag color="geekblue">{command.node_id}</Tag>}
                        </Space>
                      }
                      description={
                        <div>
                          <Paragraph style={{ marginBottom: 6 }}>{command.summary}</Paragraph>
                          <Text code>{command.command.join(' ')}</Text>
                          {command.error && <Alert type="warning" showIcon message={command.error} style={{ marginTop: 8 }} />}
                        </div>
                      }
                    />
                  </List.Item>
                )}
              />
            </Card>
          </Col>
        )}

        <Col span={24}>
          <Card
            title={<span><HistoryOutlined style={{ color: '#1677ff', marginRight: 6 }} />Import History</span>}
            bordered={false}
            style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
            extra={(
              <Space wrap>
                <Segmented
                  size="small"
                  value={historyVisibility}
                  onChange={value => setHistoryVisibility(value as SkillVisibility | 'all')}
                  options={[
                    { label: 'User', value: 'user' },
                    { label: 'Kernel', value: 'kernel' },
                    { label: 'All', value: 'all' },
                  ]}
                />
                {importHistory.length > 0 && <Button size="small" onClick={() => setImportHistory([])}>Clear</Button>}
              </Space>
            )}
          >
            {filteredImportHistory.length === 0 ? (
              <Text type="secondary">No import history yet. Run “Create Skill + Graph” to connect source artifacts with generated Skills.</Text>
            ) : (
              <List
                dataSource={filteredImportHistory}
                renderItem={item => (
                  <List.Item
                    style={{ alignItems: 'flex-start' }}
                    actions={item.skillIds.map((skillId, index) => (
                      <Button
                        key={skillId}
                        size="small"
                        type={index === 0 ? 'primary' : 'default'}
                        icon={<ArrowRightOutlined />}
                        onClick={() => navigate(`/wiki?skill=${skillId}`)}
                      >
                        Open {item.skillNames[index] || 'Skill'}
                      </Button>
                    ))}
                  >
                    <List.Item.Meta
                      title={
                        <Space wrap>
                          <Text strong>{item.sourceTitle}</Text>
                          <Tag color={SOURCE_TYPES.find(s => s.key === item.sourceType)?.color}>{item.sourceType}</Tag>
                          <Tag color={item.mode === 'create' ? 'green' : 'blue'}>{item.mode === 'create' ? 'created graph context' : 'previewed'}</Tag>
                          <Badge status={item.success ? 'success' : 'warning'} text={item.success ? 'Success' : `${item.errorCount} warning(s)`} />
                        </Space>
                      }
                      description={
                        <div>
                          <Paragraph style={{ marginBottom: 4, color: '#666' }}>{item.summary}</Paragraph>
                          <Space wrap size={[4, 4]}>
                            {item.skillNames.map((name, index) => (
                              <Tag key={`${item.id}-${name}-${index}`} color={item.skillIds[index] ? 'purple' : 'default'}>
                                {name}{item.skillIds[index] ? ` · ${item.skillVisibility?.[item.skillIds[index]] || 'user'}` : ''}
                              </Tag>
                            ))}
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              {item.graphNodesCreated} nodes · {item.graphEdgesCreated} relations · {new Date(item.createdAt).toLocaleString()}
                            </Text>
                          </Space>
                        </div>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>

        <Col span={24}>
          <AnimatePresence mode="wait">
            {result && (
              <motion.div
                key="result"
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 20 }}
              >
                <Card
                  title="Pipeline Result"
                  bordered={false}
                  style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
                  extra={
                    <Badge
                      status={result.success ? 'success' : 'error'}
                      text={result.success ? 'Success' : 'Failed'}
                    />
                  }
                >
                  <Row gutter={[8, 8]} style={{ marginBottom: 12 }}>
                    <Col span={8}>
                      <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 24, fontWeight: 700, color: '#1677ff' }}>{result.unit_count}</div>
                        <Text type="secondary" style={{ fontSize: 12 }}>Experience Units</Text>
                      </div>
                    </Col>
                    <Col span={8}>
                      <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 24, fontWeight: 700, color: '#722ed1' }}>{result.token_usage}</div>
                        <Text type="secondary" style={{ fontSize: 12 }}>Token Usage</Text>
                      </div>
                    </Col>
                    <Col span={8}>
                      <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 24, fontWeight: 700, color: '#ff4d4f' }}>{result.errors.length}</div>
                        <Text type="secondary" style={{ fontSize: 12 }}>Errors</Text>
                      </div>
                    </Col>
                  </Row>

                  {result.errors.length > 0 && (
                    <Alert
                      type="warning"
                      message={result.errors.join('; ')}
                      style={{ marginBottom: 12 }}
                    />
                  )}
                </Card>

                {mode === 'create' && (
                  <Card
                    title="Agent-Orchestrated Management"
                    bordered={false}
                    style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
                  >
                    <Space wrap>
                      <Tag color="blue">{result.created_skill_ids.length} Skill node(s)</Tag>
                      <Tag color="green">{result.graph_nodes_created} graph node writes</Tag>
                      <Tag color="purple">{result.graph_edges_created} graph relation writes</Tag>
                    </Space>
                    {result.agent_trace.length > 0 && (
                      <List
                        style={{ marginTop: 12 }}
                        dataSource={result.agent_trace}
                        renderItem={step => (
                          <List.Item style={{ padding: '8px 0' }}>
                            <Space wrap>
                              <Tag color={step.status === 'success' || step.status === 'passed' ? 'green' : step.status === 'warning' ? 'gold' : 'blue'}>
                                {step.status}
                              </Tag>
                              <Text strong>{step.agent}</Text>
                              <Text type="secondary">{step.action.replace(/_/g, ' ')}</Text>
                            </Space>
                          </List.Item>
                        )}
                      />
                    )}
                  </Card>
                )}

                {result.units.length > 0 && (
                  <Card
                    title={`Experience Units (${result.units.length})`}
                    bordered={false}
                    style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
                  >
                    <List
                      dataSource={result.units}
                      renderItem={(unit, i) => (
                        <motion.div
                          key={unit.unit_id}
                          initial={{ opacity: 0, y: 8 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ delay: i * 0.05 }}
                        >
                          <List.Item style={{ flexDirection: 'column', alignItems: 'flex-start', padding: '12px 0' }}>
                            <div style={{ display: 'flex', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
                              {unit.proposed_skill_name && (
                                <Text strong>{unit.proposed_skill_name}</Text>
                              )}
                              {unit.proposed_type && (
                                <Tag color={TYPE_COLOR[unit.proposed_type] || 'default'}>
                                  {unit.proposed_type}
                                </Tag>
                              )}
                              {capabilityLabel(unit.metadata) && (
                                <Tag color={String(unit.metadata.capability_scope) === 'generic' ? 'blue' : 'magenta'}>
                                  {capabilityLabel(unit.metadata)}
                                </Tag>
                              )}
                              <Progress
                                percent={Math.round(unit.confidence * 100)}
                                size="small"
                                style={{ width: 80 }}
                                strokeColor={unit.confidence > 0.7 ? '#52c41a' : '#faad14'}
                              />
                            </div>
                            {unit.proposed_description && (
                              <Paragraph style={{ margin: 0, fontSize: 12, color: '#666' }}>
                                {unit.proposed_description}
                              </Paragraph>
                            )}
                            {Boolean(unit.metadata.target) && (
                              <Text type="secondary" style={{ fontSize: 11 }}>
                                Scenario target: {String(unit.metadata.target)}
                              </Text>
                            )}
                            {unit.extracted_actions.length > 0 && (
                              <div style={{ marginTop: 4 }}>
                                {unit.extracted_actions.slice(0, 3).map((a, j) => (
                                  <Tag key={j} style={{ fontSize: 11, marginBottom: 2 }}>{a}</Tag>
                                ))}
                                {unit.extracted_actions.length > 3 && (
                                  <Text type="secondary" style={{ fontSize: 11 }}>
                                    +{unit.extracted_actions.length - 3} more
                                  </Text>
                                )}
                              </div>
                            )}
                          </List.Item>
                          {i < result.units.length - 1 && <Divider style={{ margin: '4px 0' }} />}
                        </motion.div>
                      )}
                    />
                  </Card>
                )}
              </motion.div>
            )}

            {!result && !loading && (
              <motion.div
                key="empty"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
              >
                <Card
                  bordered={false}
                  style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', textAlign: 'center', padding: 40 }}
                >
                  <CloudUploadOutlined style={{ fontSize: 48, color: '#d9d9d9', marginBottom: 12 }} />
                  <div style={{ color: '#999' }}>Load a fixed fixture or paste static demo JSON, then run the pipeline.</div>
                  <div style={{ marginTop: 16 }}>
                    <Space wrap>
                      {SOURCE_TYPES.map(s => (
                        <Tag
                          key={s.key}
                          color={activeTab === s.key ? s.color : undefined}
                          style={{ cursor: 'pointer' }}
                          onClick={() => setActiveTab(s.key)}
                        >
                          {s.icon} {s.label}
                        </Tag>
                      ))}
                    </Space>
                  </div>
                </Card>
              </motion.div>
            )}
          </AnimatePresence>
        </Col>
      </Row>
    </div>
  )
}
