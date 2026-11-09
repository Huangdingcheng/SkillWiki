import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Empty,
  Input,
  Progress,
  Row,
  Select,
  Space,
  Steps,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd'
import {
  ApartmentOutlined,
  ArrowRightOutlined,
  BranchesOutlined,
  BulbOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  DatabaseOutlined,
  HistoryOutlined,
  LoadingOutlined,
  ReloadOutlined,
  RocketOutlined,
  SearchOutlined,
  StarOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { motion, AnimatePresence } from 'framer-motion'
import { Link, useNavigate } from 'react-router-dom'
import { evolutionApi, executionApi } from '@/api/client'
import { useSkillContext } from '@/hooks/useSkillContext'
import { getApiErrorMessage } from '@/api/errors'
import { executionEvidence } from '@/utils/executionEvidence'
import type { ComponentRun, EvolutionComponent, ExecutionResult, RetrievedSkill } from '@/api/types'

const { TextArea } = Input
const { Text, Paragraph } = Typography

const SKILL_TYPE_COLOR: Record<string, string> = {
  atomic: '#1677ff',
  functional: '#722ed1',
  strategic: '#faad14',
}

const DEMO_TASKS = [
  'Complete a login flow on a web page and record reusable execution steps',
  'Extract a reusable operation pattern from an execution trace and promote it into a Skill',
  'Inspect why a Skill failed and propose a repair',
  'Compose existing Skills to complete a new automation task',
]

const PHASE_STEPS = [
  { title: 'Skill Retrieval', description: 'Recall relevant capabilities from the Skill Wiki', icon: <SearchOutlined /> },
  { title: 'Plan Generation', description: 'Decompose the goal into executable steps', icon: <BulbOutlined /> },
  { title: 'Execution', description: 'Invoke Skills according to the plan and collect results', icon: <ThunderboltOutlined /> },
  { title: 'Experience Recording', description: 'Write the execution trace into the experience layer', icon: <DatabaseOutlined /> },
  { title: 'Evolution Learning', description: 'Update quality signals and prepare for reuse', icon: <StarOutlined /> },
]

function getStatusColor(status: string) {
  if (status === 'success') return 'green'
  if (status === 'failed' || status === 'error') return 'red'
  if (status === 'partial') return 'gold'
  if (status === 'blocked') return 'gold'
  return 'blue'
}

function ComponentRunsPanel() {
  const { skillId, setSkillId } = useSkillContext()
  const [runs, setRuns] = useState<ComponentRun[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string>()
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState<EvolutionComponent | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const generation = useRef(0)

  useEffect(() => {
    const request = ++generation.current
    const timeoutId = window.setTimeout(() => {
      setRuns([])
      setError(null)
      if (!skillId) {
        setLoading(false)
        return
      }
      setLoading(true)
      void evolutionApi.componentRuns(skillId).then(response => {
        if (generation.current !== request) return
        setRuns(response.runs)
        setSelectedRunId(previous => response.runs.some(run => run.run_id === previous) ? previous : response.runs[0]?.run_id)
      }).catch(err => {
        if (generation.current === request) setError(getApiErrorMessage(err, 'Could not load component runs'))
      }).finally(() => {
        if (generation.current === request) setLoading(false)
      })
    }, 0)
    return () => {
      window.clearTimeout(timeoutId)
      generation.current = request + 1
    }
  }, [skillId, reload])

  const runComponent = async (component: EvolutionComponent, refresh = false) => {
    if (!skillId || running) return
    const request = generation.current
    setRunning(component)
    setError(null)
    try {
      const run = await evolutionApi.runComponent(component, skillId, refresh)
      if (generation.current !== request) return
      setRuns(previous => [run, ...previous.filter(item => item.run_id !== run.run_id)])
      setSelectedRunId(run.run_id)
    } catch (err) {
      if (generation.current === request) setError(getApiErrorMessage(err, 'Component request failed'))
    } finally {
      setRunning(null)
    }
  }

  const selected = runs.find(run => run.run_id === selectedRunId)
  const sourceRefreshRequired = runs.find(run => run.component === 'arex')?.result.refresh_required === true
  return (
    <Card title="Skill Component Runs" size="small" style={{ marginBottom: 16 }}
      extra={<Button size="small" icon={<ReloadOutlined />} disabled={!skillId || !!running} loading={loading} onClick={() => setReload(value => value + 1)}>Reload saved runs</Button>}>
      <Space orientation="vertical" style={{ width: '100%' }} size={12}>
        <Text type="secondary">Select a Skill, then explicitly run a component. Saved results are loaded on page refresh. Candidate creation does not confirm verification or release.</Text>
        <Input.Search key={skillId || 'empty'} defaultValue={skillId || ''} aria-label="Component Skill ID" placeholder="Enter Skill ID" enterButton="Load Skill" disabled={!!running} onSearch={value => setSkillId(value.trim() || null)} />
        <Text>Selected Skill: <Text code>{skillId || 'None'}</Text></Text>
        <Space wrap>
          {(['gepa', 'skillopt', 'arex', 'skillguard'] as const).map(component => (
            <Button key={component} disabled={!skillId || loading || !!running} loading={running === component} onClick={() => void runComponent(component)}>
              Run {component === 'skillguard' ? 'SkillGuard' : component === 'skillopt' ? 'SkillOpt' : component.toUpperCase()}
            </Button>
          ))}
        </Space>
        <Space orientation="vertical" size={4}>
          <Button disabled={!skillId || loading || !!running || !sourceRefreshRequired} onClick={() => void runComponent('arex', true)}>
            生成来源刷新草稿
          </Button>
          <Text type="secondary">会使用模型。请先运行 AREX 来源检查，检测到需要刷新的来源变化后才能生成草稿；草稿仍需审核和验证。</Text>
        </Space>
        {running && <Alert type="info" showIcon title="Waiting for component response" description="The backend has not confirmed a completed run yet." />}
        {error && <Alert type="error" showIcon title={error} />}
        {runs.length > 0 ? (
          <Select aria-label="Saved component run" style={{ width: '100%' }} value={selectedRunId} onChange={setSelectedRunId}
            options={runs.map(run => ({ value: run.run_id, label: `${run.component} · ${run.status} · ${run.created_at} · ${run.run_id}` }))} />
        ) : !loading && <Text type="secondary">No saved component runs for this Skill.</Text>}
        {selected && (
          <div>
            <Space wrap>
              <Tag color={getStatusColor(selected.status)}>{selected.status}</Tag>
              <Text code>{selected.run_id}</Text>
              <Text>Parent v{selected.parent_version}</Text>
            </Space>
            <Paragraph style={{ marginTop: 8, overflowWrap: 'anywhere' }}>Parent hash: <Text code copyable>{selected.parent_hash}</Text></Paragraph>
            {selected.error && <Alert type="error" showIcon title={selected.error} style={{ marginBottom: 8 }} />}
            {selected.candidate && (
              <Space wrap style={{ marginBottom: 8 }}>
                <Tag>Candidate v{selected.candidate.version}</Tag>
                <Tag>Validation: {selected.candidate.validation_status}</Tag>
                <Link to={`/wiki?skill_id=${encodeURIComponent(selected.candidate.draft_skill_id)}`}>Open draft Skill</Link>
                <Link to={`/versions?skill_id=${encodeURIComponent(selected.candidate.draft_skill_id)}&compare_to=${encodeURIComponent(selected.skill_id)}`}>View candidate version / parent diff</Link>
              </Space>
            )}
            <details open>
              <summary>Full saved run, result and candidate diff</summary>
              <pre style={{ maxHeight: 400, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 12 }}>{JSON.stringify(selected, null, 2)}</pre>
            </details>
          </div>
        )}
      </Space>
    </Card>
  )
}

function SuggestedSkill({ suggested }: { suggested: Record<string, unknown> }) {
  const name = String(suggested.name || suggested.skill_name || 'Unnamed Skill')
  const description = String(suggested.description || suggested.summary || 'The backend suggested a new Skill candidate to retain.')
  const type = String(suggested.skill_type || suggested.type || 'candidate')

  return (
    <Card
      title={<span><BulbOutlined style={{ color: '#faad14', marginRight: 6 }} />Suggested Skill to Retain</span>}
      size="small"
      style={{ borderRadius: 8, marginTop: 12, borderLeft: '3px solid #faad14' }}
    >
      <Space orientation="vertical" size={6} style={{ width: '100%' }}>
        <Space>
          <Text strong>{name}</Text>
          <Tag color="gold">{type}</Tag>
        </Space>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {description}
        </Paragraph>
      </Space>
    </Card>
  )
}

export default function SelfEvolutionDemo() {
  const navigate = useNavigate()
  const [task, setTask] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const [result, setResult] = useState<ExecutionResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const runDemo = async () => {
    const goal = task.trim()
    if (!goal) return

    setError(null)
    setResult(null)
    setIsRunning(true)

    try {
      const res = await executionApi.executePlan(goal)
      setResult(res)
    } catch (err) {
      setError(getApiErrorMessage(err, 'Self-evolution demo failed'))
    } finally {
      setIsRunning(false)
    }
  }

  const reset = () => {
    setResult(null)
    setError(null)
    setTask('')
  }

  const goToWiki = (skillId: string) => {
    navigate(`/wiki?skill_id=${encodeURIComponent(skillId)}`)
  }

  const goToGraph = (skillId: string) => {
    navigate(`/graph?skill_id=${encodeURIComponent(skillId)}`)
  }

  const retrieved = result?.retrieved_skills || []
  const successCount = result?.steps.filter(step => step.status === 'success').length || 0
  const stepCount = result?.steps.length || 0
  const evidence = result ? executionEvidence(result) : null
  const experienceRecorded = evidence?.experienceRecorded === true

  return (
    <div style={{ padding: 24, maxWidth: 1120, margin: '0 auto' }}>
      <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }}>
        <div style={{ marginBottom: 24 }}>
          <h2 style={{ fontWeight: 800, fontSize: 22, marginBottom: 4 }}>
            Self-Evolution Loop Demo
          </h2>
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            Submit a task and inspect the backend response. Execution, experience recording, and verification are reported separately; learning is not inferred.
          </Paragraph>
        </div>
      </motion.div>

      <ComponentRunsPanel />

      <Card variant="borderless" style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}>
        <TextArea
          value={task}
          onChange={event => setTask(event.target.value)}
          placeholder="Describe your task goal, for example: extract a reusable Skill from an execution trace"
          rows={2}
          disabled={isRunning}
          style={{ fontSize: 14, marginBottom: 10 }}
        />
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <Space wrap>
            <Text type="secondary" style={{ fontSize: 12 }}>Examples:</Text>
            {DEMO_TASKS.map(example => (
              <Tag
                key={example}
                style={{ cursor: isRunning ? 'not-allowed' : 'pointer', fontSize: 12 }}
                onClick={() => !isRunning && setTask(example)}
              >
                {example}
              </Tag>
            ))}
          </Space>
          <Space>
            {(result || error) && !isRunning && (
              <Button icon={<ReloadOutlined />} onClick={reset}>
                Reset
              </Button>
            )}
            <Button
              type="primary"
              icon={isRunning ? <LoadingOutlined /> : <RocketOutlined />}
              onClick={runDemo}
              loading={isRunning}
              disabled={!task.trim() || isRunning}
              size="large"
            >
              {isRunning ? 'Waiting for backend' : 'Run task'}
            </Button>
          </Space>
        </div>
      </Card>

      {isRunning && <Alert type="info" showIcon icon={<LoadingOutlined />} title="Request in progress" description="Waiting for the execution API. No retrieval, planning, recording, or learning phase has been confirmed yet." style={{ marginBottom: 16 }} />}
      {evidence && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} style={{ marginBottom: 16 }}>
          <Card variant="borderless" style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
            <Steps
              size="small"
              items={evidence.steps}
            />
          </Card>
        </motion.div>
      )}

      {error && (
        <Alert
          type="error"
          title={error}
          showIcon
          style={{ marginBottom: 16 }}
          closable
          onClose={() => setError(null)}
        />
      )}

      <AnimatePresence>
        {result && (
          <motion.div key="result" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <Row gutter={[16, 16]}>
              <Col xs={24} lg={14}>
                {retrieved.length > 0 && (
                  <motion.div initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.1 }}>
                    <Card
                      title={<span><SearchOutlined style={{ color: '#1677ff', marginRight: 6 }} />Retrieved Skills ({retrieved.length})</span>}
                      variant="borderless"
                      size="small"
                      style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 12 }}
                    >
                      {retrieved.map((skill: RetrievedSkill, index) => (
                        <motion.div
                          key={skill.skill_id}
                          initial={{ opacity: 0, x: -12 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ delay: index * 0.06 }}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 10,
                            padding: '10px 0',
                            borderBottom: index < retrieved.length - 1 ? '1px solid #f0f0f0' : 'none',
                          }}
                        >
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4, flexWrap: 'wrap' }}>
                              <Text strong style={{ fontSize: 13 }}>{skill.name}</Text>
                              <Tag color={SKILL_TYPE_COLOR[skill.skill_type]} style={{ fontSize: 11 }}>
                                {skill.skill_type}
                              </Tag>
                            </div>
                            <Text type="secondary" style={{ fontSize: 12 }}>{skill.match_reason || skill.description}</Text>
                            <div style={{ marginTop: 6 }}>
                              <Space size={6}>
                                <Button size="small" icon={<ApartmentOutlined />} onClick={() => goToWiki(skill.skill_id)}>
                                  Wiki
                                </Button>
                                <Button size="small" icon={<BranchesOutlined />} onClick={() => goToGraph(skill.skill_id)}>
                                  Graph
                                </Button>
                              </Space>
                            </div>
                          </div>
                          <Tooltip title={`Relevance ${Math.round(skill.score * 100)}%`}>
                            <Progress
                              type="circle"
                              percent={Math.round(skill.score * 100)}
                              width={40}
                              strokeColor={skill.score > 0.7 ? '#52c41a' : skill.score > 0.4 ? '#faad14' : '#ff4d4f'}
                              format={percent => <span style={{ fontSize: 10 }}>{percent}%</span>}
                            />
                          </Tooltip>
                        </motion.div>
                      ))}
                    </Card>
                  </motion.div>
                )}

                <motion.div initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.2 }}>
                  <Card
                    title={<span><ThunderboltOutlined style={{ color: '#722ed1', marginRight: 6 }} />Execution Steps</span>}
                    variant="borderless"
                    size="small"
                    style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
                  >
                    {result.steps.length === 0 ? (
                      <Alert type="warning" showIcon title="No executable Skill was found. Try a more specific task description." />
                    ) : (
                      result.steps.map((step, index) => (
                        <motion.div
                          key={step.step_id}
                          initial={{ opacity: 0, y: 8 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ duration: 0.25 }}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 10,
                            padding: '10px 0',
                            borderBottom: index < result.steps.length - 1 ? '1px solid #f0f0f0' : 'none',
                          }}
                        >
                          {step.status === 'success'
                            ? <CheckCircleFilled style={{ color: '#52c41a', fontSize: 16 }} />
                            : <ClockCircleOutlined style={{ color: '#ff4d4f', fontSize: 16 }} />}
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <Text strong style={{ fontSize: 13 }}>{step.skill_name}</Text>
                            <div style={{ color: '#8c8c8c', fontSize: 11 }}>
                              Step {step.step_index + 1} / Skill ID: {step.skill_id}
                            </div>
                            {step.error && (
                              <div style={{ fontSize: 12, color: '#ff4d4f', marginTop: 2 }}>{step.error}</div>
                            )}
                          </div>
                          <Text type="secondary" style={{ fontSize: 11 }}>{(step.latency_ms ?? 0).toFixed(0)}ms</Text>
                          <Tag color={getStatusColor(step.status)} style={{ fontSize: 11 }}>
                            {step.status}
                          </Tag>
                        </motion.div>
                      ))
                    )}
                  </Card>
                </motion.div>
              </Col>

              <Col xs={24} lg={10}>
                <motion.div initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.15 }}>
                  <Card
                    variant="borderless"
                    size="small"
                    style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 12 }}
                  >
                    <Row gutter={8}>
                      {[
                        { label: 'Steps', value: stepCount, color: '#1677ff' },
                        { label: 'Succeeded', value: successCount, color: '#52c41a' },
                        { label: 'Latency', value: `${result.total_latency_ms.toFixed(0)}ms`, color: '#722ed1' },
                      ].map(({ label, value, color }) => (
                        <Col span={8} key={label} style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: 20, fontWeight: 700, color }}>{value}</div>
                          <Text type="secondary" style={{ fontSize: 11 }}>{label}</Text>
                        </Col>
                      ))}
                    </Row>
                    <Divider style={{ margin: '12px 0' }} />
                    <Space wrap>
                      <Tag color={getStatusColor(result.status)}>Execution status: {result.status}</Tag>
                      {experienceRecorded ? (
                        <Tag color="green">Experience recorded</Tag>
                      ) : (
                        <Tag>Experience recording not confirmed</Tag>
                      )}
                      <Tag color={evidence?.verificationColor}>{evidence?.verificationLabel}</Tag>
                    </Space>
                  </Card>
                </motion.div>

                <AnimatePresence>
                  {result && (
                    <motion.div initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }}>
                      <Card
                        title={<span><DatabaseOutlined style={{ color: '#52c41a', marginRight: 6 }} />Experience Recording</span>}
                        variant="borderless"
                        size="small"
                        style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 12, borderLeft: '3px solid #52c41a' }}
                      >
                        <Timeline
                          items={[
                            {
                              color: 'green',
                              children: <Text style={{ fontSize: 12 }}>Backend returned {result.steps.length} execution step records</Text>,
                            },
                            {
                              color: experienceRecorded ? 'green' : 'gray',
                              children: <Text style={{ fontSize: 12 }}>{experienceRecorded ? `Backend confirmed experience recording${result.experience_unit?.unit_id ? `: ${result.experience_unit.unit_id}` : ''}` : 'Backend has not confirmed experience recording'}</Text>,
                            },
                            {
                              color: 'blue',
                              children: <Text style={{ fontSize: 12 }}>Final state fields: {Object.keys(result.final_state || {}).length}</Text>,
                            },
                          ]}
                        />
                      </Card>
                    </motion.div>
                  )}
                </AnimatePresence>

                <AnimatePresence>
                  {result && (
                    <motion.div initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: 0.1 }}>
                      <Card
                        title={<span><StarOutlined style={{ color: '#faad14', marginRight: 6 }} />Evolution Evidence</span>}
                        variant="borderless"
                        size="small"
                        style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 12, borderLeft: '3px solid #faad14' }}
                      >
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          This API response does not confirm learning or reuse-signal updates. A suggested Skill is a candidate, not a verified or released capability.
                        </Text>
                        {result.verifier_summary && <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap', overflow: 'auto' }}>{JSON.stringify(result.verifier_summary, null, 2)}</pre>}
                      </Card>
                    </motion.div>
                  )}
                </AnimatePresence>

                {result.suggested_skill && (
                  <SuggestedSkill suggested={result.suggested_skill} />
                )}

                <AnimatePresence>
                  {evidence && (
                    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
                      <Alert
                        type={evidence.alertType}
                        title={`Backend result: ${result.status}`}
                        description={(
                          <Space orientation="vertical" size={8}>
                            <Text style={{ fontSize: 12 }}>
                              {evidence.verificationLabel}. Experience recording is {experienceRecorded ? 'confirmed' : 'not confirmed'}. This response does not establish a completed self-evolution loop.
                            </Text>
                            <Button size="small" icon={<HistoryOutlined />} onClick={() => navigate('/execution')}>
                              View Execution History
                            </Button>
                          </Space>
                        )}
                        showIcon
                      />
                    </motion.div>
                  )}
                </AnimatePresence>
              </Col>
            </Row>
          </motion.div>
        )}
      </AnimatePresence>

      {!isRunning && !result && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }}>
          <Card variant="borderless" style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', textAlign: 'center', padding: '32px 0' }}>
            <Empty
              image={<RocketOutlined style={{ fontSize: 48, color: '#1677ff' }} />}
              description={(
                <div>
                  <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 8 }}>Self-Evolution Workflow</div>
                  <Paragraph type="secondary" style={{ maxWidth: 520, margin: '0 auto', fontSize: 13 }}>
                    The stages below describe the intended workflow. A stage is only confirmed when the backend provides its evidence; static review does not establish task execution or learning.
                  </Paragraph>
                </div>
              )}
            />
            <div style={{ marginTop: 20, display: 'flex', justifyContent: 'center', gap: 8, flexWrap: 'wrap' }}>
              {PHASE_STEPS.map((step, index) => (
                <div key={step.title} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Tag color="blue" style={{ fontSize: 12 }}>{step.icon} {step.title}</Tag>
                  {index < PHASE_STEPS.length - 1 && <ArrowRightOutlined style={{ color: '#d9d9d9', fontSize: 10 }} />}
                </div>
              ))}
            </div>
          </Card>
        </motion.div>
      )}
    </div>
  )
}
