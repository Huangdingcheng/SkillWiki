import { useState } from 'react'
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
  Space,
  Spin,
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
import { useNavigate } from 'react-router-dom'
import { executionApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import type { ExecutionResult, RetrievedSkill } from '@/api/types'

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

type Phase =
  | 'idle'
  | 'searching'
  | 'planning'
  | 'executing'
  | 'recording'
  | 'learning'
  | 'done'

const PHASE_STEPS = [
  { title: 'Skill Retrieval', description: 'Recall relevant capabilities from the Skill Wiki', icon: <SearchOutlined /> },
  { title: 'Plan Generation', description: 'Decompose the goal into executable steps', icon: <BulbOutlined /> },
  { title: 'Execution', description: 'Invoke Skills according to the plan and collect results', icon: <ThunderboltOutlined /> },
  { title: 'Experience Recording', description: 'Write the execution trace into the experience layer', icon: <DatabaseOutlined /> },
  { title: 'Evolution Learning', description: 'Update quality signals and prepare for reuse', icon: <StarOutlined /> },
]

const phaseIndex: Record<Phase, number> = {
  idle: -1,
  searching: 0,
  planning: 1,
  executing: 2,
  recording: 3,
  learning: 4,
  done: 4,
}

function sleep(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function getStatusColor(status: string) {
  if (status === 'success') return 'green'
  if (status === 'failed' || status === 'error') return 'red'
  if (status === 'partial') return 'gold'
  return 'blue'
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
  const [phase, setPhase] = useState<Phase>('idle')
  const [result, setResult] = useState<ExecutionResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [visibleSteps, setVisibleSteps] = useState(0)

  const runDemo = async () => {
    const goal = task.trim()
    if (!goal) return

    setError(null)
    setResult(null)
    setVisibleSteps(0)

    try {
      setPhase('searching')
      await sleep(450)
      setPhase('planning')
      await sleep(400)
      setPhase('executing')

      const res = await executionApi.executePlan(goal)
      setResult(res)

      for (let index = 1; index <= res.steps.length; index += 1) {
        await sleep(260)
        setVisibleSteps(index)
      }

      setPhase('recording')
      await sleep(550)
      setPhase('learning')
      await sleep(450)
      setPhase('done')
    } catch (err) {
      setError(getApiErrorMessage(err, 'Self-evolution demo failed'))
      setPhase('idle')
    }
  }

  const reset = () => {
    setPhase('idle')
    setResult(null)
    setError(null)
    setVisibleSteps(0)
    setTask('')
  }

  const goToWiki = (skillId: string) => {
    navigate(`/wiki?skill_id=${encodeURIComponent(skillId)}`)
  }

  const goToGraph = (skillId: string) => {
    navigate(`/graph?skill_id=${encodeURIComponent(skillId)}`)
  }

  const isRunning = phase !== 'idle' && phase !== 'done'
  const retrieved = result?.retrieved_skills || []
  const successCount = result?.steps.filter(step => step.status === 'success').length || 0
  const stepCount = result?.steps.length || 0
  const currentStep = phaseIndex[phase]
  const experienceRecorded = result?.experience_recorded === true

  return (
    <div style={{ padding: 24, maxWidth: 1120, margin: '0 auto' }}>
      <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }}>
        <div style={{ marginBottom: 24 }}>
          <h2 style={{ fontWeight: 800, fontSize: 22, marginBottom: 4 }}>
            Self-Evolution Loop Demo
          </h2>
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            Enter a task goal and watch SkillOS retrieve Skills, generate a plan, execute it, record experience, and update evolution signals.
          </Paragraph>
        </div>
      </motion.div>

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
            {phase === 'done' && (
              <Button icon={<ReloadOutlined />} onClick={reset}>
                Reset
              </Button>
            )}
            <Button
              type="primary"
              icon={isRunning ? <LoadingOutlined /> : <RocketOutlined />}
              onClick={runDemo}
              loading={isRunning}
              disabled={!task.trim() || phase === 'done'}
              size="large"
            >
              {phase === 'done' ? 'Completed' : 'Start Evolution Loop'}
            </Button>
          </Space>
        </div>
      </Card>

      {phase !== 'idle' && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} style={{ marginBottom: 16 }}>
          <Card variant="borderless" style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
            <Steps
              current={currentStep}
              status={phase === 'done' ? 'finish' : 'process'}
              size="small"
              items={PHASE_STEPS.map((step, index) => ({
                title: step.title,
                description: step.description,
                icon: currentStep > index
                  ? <CheckCircleFilled style={{ color: '#52c41a' }} />
                  : currentStep === index
                    ? <LoadingOutlined style={{ color: '#1677ff' }} />
                    : <ClockCircleOutlined style={{ color: '#d9d9d9' }} />,
              }))}
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
                      result.steps.slice(0, visibleSteps).map((step, index) => (
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
                    {visibleSteps < result.steps.length && (
                      <div style={{ textAlign: 'center', padding: 8 }}>
                        <Spin size="small" />
                      </div>
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
                        <Tag>Experience recording pending</Tag>
                      )}
                    </Space>
                  </Card>
                </motion.div>

                <AnimatePresence>
                  {(phase === 'recording' || phase === 'learning' || phase === 'done') && (
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
                              children: <Text style={{ fontSize: 12 }}>Execution trace summarized: {result.steps.length} steps</Text>,
                            },
                            {
                              color: experienceRecorded ? 'green' : 'gray',
                              children: <Text style={{ fontSize: 12 }}>{experienceRecorded ? 'Experience unit written to the Experience Store' : 'Backend has not confirmed experience recording yet'}</Text>,
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
                  {(phase === 'learning' || phase === 'done') && (
                    <motion.div initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: 0.1 }}>
                      <Card
                        title={<span><StarOutlined style={{ color: '#faad14', marginRight: 6 }} />Evolution Learning</span>}
                        variant="borderless"
                        size="small"
                        style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 12, borderLeft: '3px solid #faad14' }}
                      >
                        {retrieved.length > 0 ? (
                          <div style={{ marginBottom: 8 }}>
                            {retrieved.slice(0, 3).map(skill => (
                              <div key={skill.skill_id} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                                <ArrowRightOutlined style={{ color: '#52c41a', fontSize: 10 }} />
                                <Text style={{ fontSize: 12 }}>Reuse signals updated for <strong>{skill.name}</strong></Text>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <Text type="secondary" style={{ fontSize: 12 }}>No reusable Skill was retrieved this time. Future experience retention can fill this capability gap.</Text>
                        )}
                        <Divider style={{ margin: '8px 0' }} />
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          This execution result can be used for health assessment, version evolution, and future reuse on similar tasks.
                        </Text>
                      </Card>
                    </motion.div>
                  )}
                </AnimatePresence>

                {result.suggested_skill && (
                  <SuggestedSkill suggested={result.suggested_skill} />
                )}

                <AnimatePresence>
                  {phase === 'done' && (
                    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
                      <Alert
                        type="success"
                        icon={<CheckCircleFilled />}
                        title="Self-Evolution Loop Completed"
                        description={(
                          <Space orientation="vertical" size={8}>
                            <Text style={{ fontSize: 12 }}>
                              SkillOS completed the full loop for this task: retrieval, planning, execution, recording, and learning.
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

      {phase === 'idle' && !result && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }}>
          <Card variant="borderless" style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', textAlign: 'center', padding: '32px 0' }}>
            <Empty
              image={<RocketOutlined style={{ fontSize: 48, color: '#1677ff' }} />}
              description={(
                <div>
                  <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 8 }}>Self-Evolution Loop</div>
                  <Paragraph type="secondary" style={{ maxWidth: 520, margin: '0 auto', fontSize: 13 }}>
                    Through the loop of retrieving Skills, executing tasks, recording experience, and feeding evolution signals back, SkillOS turns each task result into reusable capability evidence.
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
