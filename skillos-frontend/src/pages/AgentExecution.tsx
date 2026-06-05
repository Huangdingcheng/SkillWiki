import { useState } from 'react'
import {
  Card, Input, Button, Tag, Alert, Spin, Divider,
  Typography, Space, Statistic, Row, Col, Progress, Tooltip, Collapse, Timeline,
} from 'antd'
import {
  PlayCircleOutlined, ThunderboltOutlined, CheckCircleOutlined,
  CloseCircleOutlined, SearchOutlined, DatabaseOutlined,
  BranchesOutlined, NodeIndexOutlined, QuestionCircleOutlined, PictureOutlined,
} from '@ant-design/icons'
import { motion, AnimatePresence } from 'framer-motion'
import { executionApi } from '@/api/client'
import type { ExecutionResult, ExecutionStepResult, RetrievedSkill } from '@/api/types'

const { TextArea } = Input
const { Text } = Typography

const STATUS_COLOR: Record<string, string> = {
  success: '#52c41a',
  failed: '#ff4d4f',
  running: '#1677ff',
  pending: '#d9d9d9',
  skipped: '#faad14',
  waiting_for_user: '#faad14',
}

const SKILL_TYPE_COLOR: Record<string, string> = {
  atomic: '#1677ff', functional: '#722ed1', strategic: '#faad14',
}

const NODE_TYPE_COLOR: Record<string, string> = {
  host_information: 'geekblue',
  document: 'green',
  trajectory: 'red',
  task: 'orange',
  tool: 'cyan',
  api_doc: 'purple',
  script: 'gold',
  agent: 'lime',
}

const TRACE_LABEL: Record<string, string> = {
  interpret_without_graph_or_skills: 'Task-only interpretation',
  read_graph_context: 'Graph / host information retrieval',
  decompose_task_layers: 'Three-layer decomposition',
  retrieve_and_judge_skill_candidates: 'Skill retrieval and grounded judgment',
  predict_expected_outcome: 'Expected outcome prediction',
  build_execution_plan: 'Execution plan generation',
  bind_step_inputs: 'Runtime input binding',
  configure_observation_loop: 'Observation loop configuration',
  observe_runtime_steps: 'Runtime step observations',
  browser_observe_decide_act_loop: 'Browser observe-decide-act loop',
  validate_expected_outcome: 'Outcome validation',
  retry_after_mismatch: 'Mismatch repair retry',
  request_user_assistance: 'Human-in-the-loop pause',
  execute_on_host_runtime: 'Host runtime execution',
  reflect_and_update_skill_memory: 'Execution learning',
}

const DEFAULT_EXECUTION_CONTEXT = {
  username: 'demo@example.com',
  password: 'pass123',
  form_data: {
    email: 'demo@example.com',
    password: 'pass123',
  },
}

function findTrace(result: ExecutionResult | null, action: string) {
  return result?.agent_trace?.find(trace => trace.action === action)
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function traceStatusColor(status: string) {
  if (['success', 'completed', 'created', 'reused'].includes(status)) return 'green'
  if (['partial', 'empty', 'skipped', 'mismatch', 'waiting_for_user'].includes(status)) return 'gold'
  if (['failed', 'error'].includes(status)) return 'red'
  return 'blue'
}

function JsonBlock({ value, maxHeight = 280 }: { value: unknown; maxHeight?: number }) {
  return (
    <pre style={{
      background: '#0f172a',
      color: '#dbeafe',
      padding: 12,
      borderRadius: 8,
      fontSize: 11,
      overflow: 'auto',
      maxHeight,
      margin: 0,
    }}>
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function getTrace(result: ExecutionResult | null, action: string) {
  return result?.agent_trace?.find(trace => trace.action === action)
}

function getHostInformationUsed(result: ExecutionResult | null) {
  const all: Record<string, Record<string, unknown>> = {}
  for (const trace of result?.agent_trace || []) {
    const details = asRecord(trace.details)
    for (const item of asArray(details.host_information_used)) {
      const node = asRecord(item)
      const id = String(node.id || node.name || Math.random())
      all[id] = node
    }
    for (const item of asArray(details.graph_context)) {
      const node = asRecord(item)
      if (node.node_type === 'host_information') {
        const id = String(node.id || node.name || Math.random())
        all[id] = node
      }
    }
  }
  return Object.values(all)
}

function getBrowserLoopTrace(result: ExecutionResult | null) {
  return result?.agent_trace?.find(trace =>
    trace.action === 'desktop_resume_observe_decide_act'
    || trace.action === 'browser_resume_observe_decide_act'
    || trace.action === 'browser_observe_decide_act_loop'
  ) || null
}

function compactValue(value: unknown, max = 90) {
  if (value === undefined || value === null || value === '') return ''
  if (typeof value === 'object') return JSON.stringify(value).slice(0, max)
  return String(value).slice(0, max)
}

function BrowserLoopPanel({ result }: { result: ExecutionResult | null }) {
  const browserTrace = getBrowserLoopTrace(result)
  const details = asRecord(browserTrace?.details)
  const finalState = asRecord(result?.final_state)
  const controller = asRecord(details.controller || finalState.controller)
  const observations = asArray(details.observations || finalState.observations)
  const actions = asArray(details.actions || finalState.actions)
  if (!browserTrace && observations.length === 0 && actions.length === 0) return null

  return (
    <Card
      title={<span><NodeIndexOutlined style={{ color: '#1677ff', marginRight: 6 }} />Browser Observation Loop</span>}
      bordered={false}
      size="small"
      style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
    >
      <Alert
        type={details.requires_visual_controller ? 'warning' : 'success'}
        showIcon
        message={details.requires_visual_controller ? 'Visual/DOM controller required' : 'Browser loop completed'}
        description={String(details.blocking_reason || details.message || 'The agent exposed browser observations and actions for this execution.')}
        style={{ marginBottom: 12 }}
      />
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={24} md={6}><Statistic title="Rounds" value={Number(details.rounds || actions.length || 0)} /></Col>
        <Col xs={24} md={6}><Statistic title="Max Rounds" value={Number(details.max_rounds || 0)} /></Col>
        <Col xs={24} md={6}><Statistic title="Observations" value={observations.length} /></Col>
        <Col xs={24} md={6}><Statistic title="Actions" value={actions.length} /></Col>
      </Row>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Space wrap>
          <Tag color="blue">query: {compactValue(details.query || finalState.query, 80)}</Tag>
          <Tag color="geekblue">entry: {compactValue(details.entry_url || finalState.entry_url, 120)}</Tag>
          <Tag color={details.success ? 'green' : 'gold'}>{details.success ? 'success' : 'not finished'}</Tag>
          {Object.keys(controller).length > 0 && (
            <Tag color={controller.dom_supported ? 'green' : 'volcano'}>
              controller: {String(controller.name || 'browser_controller')} · DOM {controller.dom_supported ? 'on' : 'off'}
            </Tag>
          )}
        </Space>
        <Row gutter={[12, 12]}>
          <Col xs={24} md={12}>
            <Card size="small" title="Action Rounds" style={{ height: '100%' }}>
              <Timeline
                items={actions.map((item, index) => {
                  const action = asRecord(item)
                  return {
                    color: action.status === 'success' ? 'green' : action.status === 'blocked' ? 'red' : 'blue',
                    children: (
                      <div>
                        <Space wrap>
                          <Tag color={action.status === 'blocked' ? 'red' : 'blue'}>round {String(action.round ?? index)}</Tag>
                          <Text strong>{String(action.action || 'action')}</Text>
                        </Space>
                        <div><Text type="secondary">{String(action.reason || '')}</Text></div>
                        <Text code style={{ fontSize: 11 }}>{compactValue(action.target, 140)}</Text>
                      </div>
                    ),
                  }
                })}
              />
            </Card>
          </Col>
          <Col xs={24} md={12}>
            <Card size="small" title="Observation Evidence" style={{ height: '100%' }}>
              <Collapse
                size="small"
                items={observations.map((item, index) => {
                  const obs = asRecord(item)
                  return {
                    key: `browser-loop-${index}`,
                    label: `round ${String(obs.round ?? index)} · ${String(obs.observation_type || obs.type || 'observation')}`,
                    children: <JsonBlock value={obs} maxHeight={220} />,
                  }
                })}
              />
            </Card>
          </Col>
        </Row>
      </Space>
    </Card>
  )
}

function collectScreenshotObservations(result: ExecutionResult | null) {
  const screenshots: Record<string, unknown>[] = []
  for (const step of result?.steps || []) {
    for (const packet of step.observations || []) {
      const packetRecord = asRecord(packet)
      for (const obs of asArray(packetRecord.observations)) {
        const obsRecord = asRecord(obs)
        const evidence = asRecord(obsRecord.evidence)
        if (obsRecord.type === 'screenshot' || evidence.path) {
          screenshots.push({
            step_id: step.step_id,
            skill_name: step.skill_name,
            phase: packetRecord.phase,
            status: obsRecord.status,
            ...evidence,
          })
        }
      }
    }
    const output = asRecord(step.outputs)
    for (const obs of asArray(output.observations)) {
      const obsRecord = asRecord(obs)
      const evidence = asRecord(obsRecord.evidence)
      if (obsRecord.observation_type === 'screenshot' || evidence.path) {
        screenshots.push({
          step_id: step.step_id,
          skill_name: step.skill_name,
          phase: 'browser_round',
          status: 'success',
          ...evidence,
        })
      }
    }
  }
  return screenshots
}

function AssistancePanel({
  result,
  onResume,
  loading = false,
}: {
  result: ExecutionResult | null
  onResume: (guidance: string) => void
  loading?: boolean
}) {
  const [guidance, setGuidance] = useState('')
  const request = asRecord(result?.assistance_request)
  if (Object.keys(request).length === 0) return null
  const needed = asArray(request.needed_information)
  const accepted = asArray(request.accepted_inputs)
  const currentObservations = asArray(request.current_observations)
  const screenshots = currentObservations.length > 0 ? currentObservations : collectScreenshotObservations(result)

  return (
    <Card
      title={<span><QuestionCircleOutlined style={{ color: '#faad14', marginRight: 6 }} />Agent Paused For Guidance</span>}
      bordered={false}
      size="small"
      style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16, borderLeft: '4px solid #faad14' }}
    >
      <Alert
        type="warning"
        showIcon
        message={String(request.summary || 'The agent paused before the next action.')}
        description={String(request.reason || 'More perception guidance is needed.')}
        style={{ marginBottom: 12 }}
      />
      <Row gutter={[12, 12]}>
        <Col xs={24} md={12}>
          <Card size="small" title="What The Agent Needs" style={{ height: '100%' }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              {needed.map((item, index) => (
                <Text key={`need-${index}`}>• {String(item)}</Text>
              ))}
              <Space wrap>
                {accepted.map(item => <Tag key={String(item)} color="gold">{String(item)}</Tag>)}
              </Space>
            </Space>
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card size="small" title={<span><PictureOutlined /> Current Visual Evidence</span>} style={{ height: '100%' }}>
            {screenshots.length === 0 ? (
              <Text type="secondary">No screenshot evidence was captured or permission was unavailable.</Text>
            ) : (
              <Space direction="vertical" style={{ width: '100%' }}>
                {screenshots.slice(-4).map((item, index) => {
                  const obs = asRecord(item)
                  return (
                    <div key={`screenshot-${index}`} style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: 8 }}>
                      <Space wrap>
                        <Tag color={String(obs.status) === 'success' ? 'magenta' : 'volcano'}>{String(obs.status || 'unknown')}</Tag>
                        <Text strong>{String(obs.skill_name || obs.step_id || 'screen')}</Text>
                      </Space>
                      <div><Text code style={{ fontSize: 11 }}>{String(obs.path || 'no path')}</Text></div>
                    </div>
                  )
                })}
              </Space>
            )}
          </Card>
        </Col>
      </Row>
      <Divider style={{ margin: '12px 0' }} />
      <TextArea
        value={guidance}
        onChange={e => setGuidance(e.target.value)}
        placeholder={String(request.resume_instruction || 'Tell the agent what to click/type next, or describe the visible target.')}
        rows={3}
        style={{ marginBottom: 8 }}
      />
      <Button
        type="primary"
        disabled={!guidance.trim()}
        loading={loading}
        onClick={() => onResume(guidance)}
      >
        Resume With Guidance
      </Button>
    </Card>
  )
}

function observationColor(type: string) {
  if (type === 'filesystem') return 'gold'
  if (type === 'terminal') return 'green'
  if (type === 'browser') return 'blue'
  if (type === 'application') return 'purple'
  if (type === 'screenshot') return 'magenta'
  if (type === 'runtime') return 'default'
  return 'cyan'
}

function getExecutionContextForGoal(goalText: string) {
  const normalized = goalText.toLowerCase()
  const needsFormFixture = normalized.includes('login')
    || normalized.includes('form')
    || goalText.includes('登录')
    || goalText.includes('表单')
  return needsFormFixture ? DEFAULT_EXECUTION_CONTEXT : {}
}

function mergeResumeResult(previous: ExecutionResult, resumed: ExecutionResult): ExecutionResult {
  const existingStepIds = new Set(resumed.steps.map(step => step.step_id))
  const mergedSteps = [
    ...resumed.steps,
    ...previous.steps.filter(step => !existingStepIds.has(step.step_id)),
  ]
  return {
    ...previous,
    ...resumed,
    steps: mergedSteps,
    total_latency_ms: previous.total_latency_ms + resumed.total_latency_ms,
    retrieved_skills: previous.retrieved_skills,
    agent_trace: [
      ...(resumed.agent_trace || []),
      ...(previous.agent_trace || []),
    ],
    experience_recorded: previous.experience_recorded || resumed.experience_recorded,
  }
}

function StepCard({ step, index }: { step: ExecutionStepResult; index: number }) {
  const [expanded, setExpanded] = useState(false)
  const observations = step.observations || []
  const judgment = step.step_judgment || {}
  return (
    <motion.div
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.1 }}
    >
      <Card
        size="small"
        style={{
          marginBottom: 8,
          borderLeft: `4px solid ${STATUS_COLOR[step.status] || '#d9d9d9'}`,
          borderRadius: 8,
        }}
        onClick={() => setExpanded(!expanded)}
        hoverable
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space>
            {step.status === 'success'
              ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
              : <CloseCircleOutlined style={{ color: '#ff4d4f' }} />}
            <Text strong>{step.skill_name}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>{step.skill_id.slice(0, 8)}...</Text>
          </Space>
          <Space>
            <Tag color={STATUS_COLOR[step.status]}>{step.status}</Tag>
            {observations.length > 0 && <Tag color="cyan">obs {observations.length}</Tag>}
            <Text type="secondary">{step.latency_ms.toFixed(0)}ms</Text>
          </Space>
        </div>
        {expanded && (
          <div style={{ marginTop: 8 }}>
            {step.error && <Alert type="error" message={step.error} style={{ marginBottom: 8 }} />}
            {Object.keys(judgment).length > 0 && (
              <Alert
                type={judgment.next_action === 'repair' ? 'warning' : 'success'}
                showIcon
                message={`Step judgment: ${String(judgment.next_action || 'continue')}`}
                description={String(judgment.reason || '')}
                style={{ marginBottom: 8 }}
              />
            )}
            {observations.length > 0 && (
              <Collapse
                size="small"
                style={{ marginBottom: 8 }}
                items={observations.map((packet, packetIndex) => {
                  const packetRecord = asRecord(packet)
                  const packetObservations = asArray(packetRecord.observations)
                  return {
                    key: `${step.step_id}-obs-${packetIndex}`,
                    label: `${String(packetRecord.phase || 'observation')} observations (${packetObservations.length})`,
                    children: (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        {packetObservations.map((item, obsIndex) => {
                          const obs = asRecord(item)
                          return (
                            <Card key={`${step.step_id}-${packetIndex}-${obsIndex}`} size="small" style={{ background: '#fafafa' }}>
                              <Space wrap>
                                <Tag color={observationColor(String(obs.type))}>{String(obs.type || 'observation')}</Tag>
                                <Text strong>{String(obs.source || 'ObservationProvider')}</Text>
                                <Text type="secondary">{compactValue(obs.target, 120)}</Text>
                              </Space>
                              <JsonBlock value={obs.evidence || obs} maxHeight={180} />
                            </Card>
                          )
                        })}
                      </Space>
                    ),
                  }
                })}
              />
            )}
            {Object.keys(step.outputs).length > 0 && (
              <pre style={{ background: '#f5f5f5', padding: 8, borderRadius: 4, fontSize: 11, overflow: 'auto' }}>
                {JSON.stringify(step.outputs, null, 2)}
              </pre>
            )}
          </div>
        )}
      </Card>
    </motion.div>
  )
}

export default function AgentExecution() {
  const [goal, setGoal] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ExecutionResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const executeGoal = async (goalText: string) => {
    if (!goalText.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await executionApi.executePlan(goalText, getExecutionContextForGoal(goalText))
      setResult(res)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string; error?: string } }; message?: string }
      setError(err?.response?.data?.detail || err?.response?.data?.error || err?.message || '执行失败')
    } finally {
      setLoading(false)
    }
  }

  const handleExecute = async () => executeGoal(goal)

  const handleResumeWithGuidance = async (guidance: string) => {
    if (!result) return
    setLoading(true)
    setError(null)
    try {
      const resumed = await executionApi.resume({
        plan_id: result.plan_id,
        goal: result.goal || goal,
        guidance,
        final_state: result.final_state,
        assistance_request: result.assistance_request,
        context: getExecutionContextForGoal(result.goal || goal),
      })
      setResult(mergeResumeResult(result, resumed))
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string; error?: string } }; message?: string }
      setError(err?.response?.data?.detail || err?.response?.data?.error || err?.message || '继续执行失败')
    } finally {
      setLoading(false)
    }
  }

  const successCount = result?.steps.filter(s => s.status === 'success').length || 0
  const retrieved: RetrievedSkill[] = result?.retrieved_skills || []

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: '0 auto' }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h2 style={{ fontWeight: 700, marginBottom: 4 }}>Agent Execution</h2>
        <p style={{ color: '#666', marginBottom: 24 }}>
          输入任务目标，SkillOS 将自动检索相关 Skill、生成执行计划并运行。
        </p>

        <Card bordered={false} style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}>
          <TextArea
            value={goal}
            onChange={e => setGoal(e.target.value)}
            placeholder="描述你的任务目标，例如：在网页上找到登录按钮并点击，然后填写用户名和密码..."
            rows={3}
            style={{ marginBottom: 12, fontSize: 14 }}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              SkillOS 将自动分解任务、检索 Skill 并生成执行计划
            </Text>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              onClick={handleExecute}
              loading={loading}
              disabled={!goal.trim()}
              size="large"
            >
              执行
            </Button>
          </div>
        </Card>

        {/* 示例任务 */}
        <div style={{ marginBottom: 24 }}>
          <Text type="secondary" style={{ fontSize: 12, marginRight: 8 }}>示例：</Text>
          {[
            '点击页面上的提交按钮',
            '填写登录表单并提交',
            '在搜索框中输入关键词并搜索',
            'Please open the Chrome browser for me.',
            'Open Chrome and go to the GPT conversation page.',
            'Open GPT, ask today weather, and save the answer to Downloads.',
            'Open my Downloads folder in Finder.',
            '打开下载目录里的 abc.json 文件',
            '打开终端执行 top 命令查看不同进程的实时动态，持续 10 秒',
          ].map(ex => (
            <Tag
              key={ex}
              style={{ cursor: 'pointer', marginBottom: 4 }}
              onClick={() => setGoal(ex)}
            >
              {ex}
            </Tag>
          ))}
        </div>
      </motion.div>

      {loading && (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin size="large" tip="正在规划并执行..." />
        </div>
      )}

      {error && (
        <Alert type="error" message="执行失败" description={error} showIcon style={{ marginBottom: 16 }} />
      )}

      <AnimatePresence>
        {result && (
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
            <AssistancePanel result={result} onResume={handleResumeWithGuidance} loading={loading} />

            {/* 执行摘要 */}
            <Card
              bordered={false}
              style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
            >
              <Row gutter={16}>
                <Col span={6}>
                  <Statistic
                    title="状态"
                    value={result.status}
                    valueStyle={{ color: result.status === 'completed' ? '#52c41a' : '#faad14' }}
                  />
                </Col>
                <Col span={6}>
                  <Statistic title="总步骤" value={result.steps.length} />
                </Col>
                <Col span={6}>
                  <Statistic title="成功" value={successCount} valueStyle={{ color: '#52c41a' }} />
                </Col>
                <Col span={6}>
                  <Statistic
                    title="总耗时"
                    value={result.total_latency_ms.toFixed(0)}
                    suffix="ms"
                    valueStyle={{ color: '#1677ff' }}
                  />
                </Col>
              </Row>
            </Card>

            {/* 执行步骤 */}
            <Card
              title={<><ThunderboltOutlined /> Latest Execution Steps</>}
              bordered={false}
              style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
            >
              {result.steps.length === 0 ? (
                <Alert type="warning" message="未找到可执行的 Skill，请尝试更具体的任务描述" />
              ) : (
                result.steps.map((step, i) => <StepCard key={step.step_id} step={step} index={i} />)
              )}
            </Card>

            <BrowserLoopPanel result={result} />

            {result.agent_trace && result.agent_trace.length > 0 && (
              <Card
                title={<span><NodeIndexOutlined style={{ color: '#1677ff', marginRight: 6 }} />Full Agent Execution Trace</span>}
                bordered={false}
                size="small"
                style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
              >
                {(() => {
                  const retrieve = getTrace(result, 'retrieve_and_judge_skill_candidates')
                  const graphTrace = getTrace(result, 'read_graph_context')
                  const decompositionTrace = findTrace(result, 'decompose_task_layers')
                  const planTrace = findTrace(result, 'build_execution_plan')
                  const bindTrace = getTrace(result, 'bind_step_inputs')
                  const validationTrace = getTrace(result, 'validate_expected_outcome')
                  const details = asRecord(retrieve?.details)
                  const graphDetails = asRecord(graphTrace?.details)
                  const decompositionDetails = asRecord(decompositionTrace?.details)
                  const planDetails = asRecord(planTrace?.details)
                  const bindDetails = asRecord(bindTrace?.details)
                  const validationDetails = asRecord(validationTrace?.details)
                  const inferred = asRecord(details.inferred_context)
                  const graphContext = asArray(graphDetails.graph_context || details.graph_context).slice(0, 8)
                  const hostInfo = getHostInformationUsed(result)
                  const layers = asArray(decompositionDetails.layers)
                  const groundedDecision = asRecord(details.grounded_decision)
                  const rejected = asArray(groundedDecision.rejected_skills)
                  const candidates = asArray(details.candidate_skills)
                  return (
                    <div style={{ marginBottom: 12 }}>
                      <Alert
                        type="info"
                        showIcon
                        message="Execution is fully exposed here: task interpretation, graph evidence, host information, skill judgment, planning, input binding, runtime execution, validation, and learning."
                        style={{ marginBottom: 12 }}
                      />
                      {layers.length > 0 && (
                        <Card size="small" title="Three-Layer Agent Decomposition" style={{ marginBottom: 12 }}>
                          <Row gutter={[8, 8]}>
                            {layers.map((layer, index) => {
                              const item = asRecord(layer)
                              const matched = asArray(item.matched_skills)
                              return (
                                <Col xs={24} md={8} key={`${String(item.layer)}-${index}`}>
                                  <div style={{
                                    border: '1px solid #edf0f5',
                                    borderRadius: 10,
                                    padding: 10,
                                    height: '100%',
                                    background: index === 0 ? '#fff7e6' : index === 1 ? '#f9f0ff' : '#e6f4ff',
                                  }}>
                                    <Tag color={index === 0 ? 'gold' : index === 1 ? 'purple' : 'blue'}>
                                      {String(item.layer)} · {String(item.expected_skill_type)}
                                    </Tag>
                                    <div style={{ fontWeight: 700, marginTop: 6 }}>{String(item.intent)}</div>
                                    <Text type="secondary" style={{ fontSize: 12 }}>{String(item.description)}</Text>
                                    <div style={{ marginTop: 8 }}>
                                      {matched.length === 0 ? (
                                        <Tag>agent fallback</Tag>
                                      ) : matched.slice(0, 3).map(skill => (
                                        <Tag key={String(skill)} color="cyan">{String(skill)}</Tag>
                                      ))}
                                    </div>
                                  </div>
                                </Col>
                              )
                            })}
                          </Row>
                        </Card>
                      )}
                      <Row gutter={[12, 12]}>
                        <Col xs={24} md={8}>
                          <Card size="small" title="Inferred Context" style={{ height: '100%' }}>
                            {Object.keys(inferred).length === 0 ? (
                              <Text type="secondary">No inferred parameters.</Text>
                            ) : (
                              <Space wrap>
                                {Object.entries(inferred).map(([key, value]) => (
                                  <Tag key={key} color="blue">{key}: {String(value).slice(0, 60)}</Tag>
                                ))}
                              </Space>
                            )}
                          </Card>
                        </Col>
                        <Col xs={24} md={8}>
                          <Card size="small" title="Graph Evidence" style={{ height: '100%' }}>
                            <Space direction="vertical" size={4} style={{ width: '100%' }}>
                              {graphContext.length === 0 && <Text type="secondary">No graph evidence.</Text>}
                              {graphContext.map((node, index) => {
                                const item = asRecord(node)
                                return (
                                  <div key={`${String(item.id)}-${index}`}>
                                    <Tag color={NODE_TYPE_COLOR[String(item.node_type)] || 'cyan'}>{String(item.node_type || 'node')}</Tag>
                                    <Text style={{ fontSize: 12 }}>{String(item.name || item.id)}</Text>
                                  </div>
                                )
                              })}
                            </Space>
                          </Card>
                        </Col>
                        <Col xs={24} md={8}>
                          <Card size="small" title="Execution Plan" style={{ height: '100%' }}>
                            <Space direction="vertical" size={4}>
                              <Text type="secondary">step count: {String(planDetails.step_count || result.steps.length)}</Text>
                              {result.steps.map(step => (
                                <Tag key={step.step_id} color="purple">{step.skill_name}</Tag>
                              ))}
                            </Space>
                          </Card>
                        </Col>
                      </Row>
                      <Card
                        size="small"
                        title={<span><DatabaseOutlined style={{ color: '#2f54eb', marginRight: 6 }} />Host Information Used</span>}
                        style={{ marginTop: 12 }}
                      >
                        {hostInfo.length === 0 ? (
                          <Text type="secondary">No host information nodes were used for this execution.</Text>
                        ) : (
                          <Row gutter={[8, 8]}>
                            {hostInfo.map((node, index) => (
                              <Col xs={24} md={12} key={`${String(node.id)}-${index}`}>
                                <div style={{
                                  border: '1px solid #d6e4ff',
                                  background: 'linear-gradient(135deg, #f0f5ff, #e6fffb)',
                                  borderRadius: 10,
                                  padding: 10,
                                }}>
                                  <Space wrap>
                                    <Tag color="geekblue">host_information</Tag>
                                    <Text strong>{String(node.name || node.id)}</Text>
                                  </Space>
                                  <div style={{ marginTop: 6 }}>
                                    <Text type="secondary" style={{ fontSize: 12 }}>
                                      {String(node.description || '').slice(0, 180)}
                                    </Text>
                                  </div>
                                  <div style={{ marginTop: 6 }}>
                                    {asArray(node.labels).slice(0, 5).map(label => <Tag key={String(label)}>{String(label)}</Tag>)}
                                  </div>
                                  {Boolean(node.command) && (
                                    <Text code style={{ fontSize: 11 }}>{compactValue(node.command, 120)}</Text>
                                  )}
                                </div>
                              </Col>
                            ))}
                          </Row>
                        )}
                      </Card>

                      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
                        <Col xs={24} md={12}>
                          <Card size="small" title={<span><SearchOutlined /> Skill Candidate Judgment</span>} style={{ height: '100%' }}>
                            <Space direction="vertical" style={{ width: '100%' }}>
                              <Text type="secondary">action: {String(groundedDecision.skill_action || 'unknown')}</Text>
                              <Text type="secondary">coverage: {compactValue(asRecord(groundedDecision.coverage).coverage_score || 'n/a')}</Text>
                              <div>
                                <Text strong>Selected</Text>
                                <div style={{ marginTop: 4 }}>
                                  {asArray(details.selected).length === 0
                                    ? <Tag>none</Tag>
                                    : asArray(details.selected).map(name => <Tag key={String(name)} color="green">{String(name)}</Tag>)}
                                </div>
                              </div>
                              <div>
                                <Text strong>Rejected</Text>
                                <div style={{ marginTop: 4 }}>
                                  {rejected.length === 0
                                    ? <Tag>none</Tag>
                                    : rejected.slice(0, 6).map((item, i) => {
                                      const reject = asRecord(item)
                                      return <Tooltip key={`${String(reject.name)}-${i}`} title={String(reject.reason || '')}><Tag color="red">{String(reject.name || item)}</Tag></Tooltip>
                                    })}
                                </div>
                              </div>
                              <Collapse
                                size="small"
                                items={[{
                                  key: 'candidates',
                                  label: `All candidate skills seen by agent (${candidates.length})`,
                                  children: <JsonBlock value={candidates} />,
                                }]}
                              />
                            </Space>
                          </Card>
                        </Col>
                        <Col xs={24} md={12}>
                          <Card size="small" title={<span><BranchesOutlined /> Plan, Inputs, Validation</span>} style={{ height: '100%' }}>
                            <Space direction="vertical" style={{ width: '100%' }}>
                              <Text type="secondary">step count: {String(planDetails.step_count || result.steps.length)}</Text>
                              <Text type="secondary">validation: {String(validationTrace?.status || 'not available')}</Text>
                              <Space wrap>
                                {result.steps.map(step => <Tag key={step.step_id} color="purple">{step.skill_name}</Tag>)}
                              </Space>
                              <Collapse
                                size="small"
                                items={[
                                  { key: 'inputs', label: 'Bound runtime inputs', children: <JsonBlock value={bindDetails.steps || []} /> },
                                  { key: 'validation', label: 'Expected-vs-actual validation', children: <JsonBlock value={validationDetails} /> },
                                ]}
                              />
                            </Space>
                          </Card>
                        </Col>
                      </Row>
                    </div>
                  )
                })()}
                <Divider style={{ margin: '12px 0' }} />
                <Timeline
                  items={result.agent_trace.map((trace, index) => ({
                    color: traceStatusColor(trace.status),
                    children: (
                      <div>
                        <Space wrap>
                          <Tag color={traceStatusColor(trace.status)}>{trace.status}</Tag>
                          <Text strong>{TRACE_LABEL[trace.action] || trace.action.replace(/_/g, ' ')}</Text>
                          <Text type="secondary">{trace.agent}</Text>
                        </Space>
                        <div style={{ marginTop: 6 }}>
                          <Collapse
                            size="small"
                            ghost
                            items={[{
                              key: `${trace.action}-${index}`,
                              label: 'Show raw trace details',
                              children: <JsonBlock value={trace.details} maxHeight={360} />,
                            }]}
                          />
                        </div>
                      </div>
                    ),
                  }))}
                />
              </Card>
            )}

            {/* 检索到的 Skill */}
            {retrieved.length > 0 && (
              <Card
                title={<span><SearchOutlined style={{ color: '#1677ff', marginRight: 6 }} />Retrieved Skills ({retrieved.length})</span>}
                bordered={false}
                size="small"
                style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
              >
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {retrieved.map(sk => (
                    <Tooltip key={sk.skill_id} title={sk.match_reason}>
                      <div style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        padding: '4px 10px', borderRadius: 20,
                        background: '#f5f7fa', border: '1px solid #e8e8e8',
                        cursor: 'default',
                      }}>
                        <Tag color={SKILL_TYPE_COLOR[sk.skill_type]} style={{ margin: 0, fontSize: 10, padding: '0 4px' }}>
                          {sk.skill_type}
                        </Tag>
                        <Text style={{ fontSize: 12 }}>{sk.name}</Text>
                        <Progress
                          type="circle"
                          percent={Math.round(sk.score * 100)}
                          width={24}
                          strokeColor={sk.score > 0.6 ? '#52c41a' : '#faad14'}
                          format={p => <span style={{ fontSize: 8 }}>{p}</span>}
                        />
                      </div>
                    </Tooltip>
                  ))}
                </div>
              </Card>
            )}

            {/* 最终状态 */}
            {Object.keys(result.final_state).length > 0 && (
              <Card
                title="最终状态"
                bordered={false}
                style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
              >
                <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 8, fontSize: 12, overflow: 'auto' }}>
                  {JSON.stringify(result.final_state, null, 2)}
                </pre>
              </Card>
            )}
            {/* 经验记录反馈 */}
            {result.experience_recorded && (
              <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}>
                <Card
                  bordered={false}
                  size="small"
                  style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', borderLeft: '3px solid #52c41a' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <DatabaseOutlined style={{ color: '#52c41a', fontSize: 16 }} />
                    <Text style={{ fontSize: 13 }}>
                      <strong>经验已记录</strong> — 本次执行轨迹已写入 Experience Store，将用于 Skill 质量评估与演化决策。
                    </Text>
                  </div>
                </Card>
              </motion.div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
