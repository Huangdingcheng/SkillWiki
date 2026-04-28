import { useState, useRef } from 'react'
import {
  Card, Button, Input, Steps, Tag, Badge, Space, Typography,
  Row, Col, Progress, Alert, Divider, Timeline, Spin, Tooltip,
} from 'antd'
import {
  SearchOutlined, ThunderboltOutlined, DatabaseOutlined,
  BulbOutlined, RocketOutlined, CheckCircleFilled,
  LoadingOutlined, ClockCircleOutlined, StarOutlined,
  ArrowRightOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { motion, AnimatePresence } from 'framer-motion'
import { executionApi, ingestApi } from '@/api/client'
import type { ExecutionResult } from '@/api/types'

const { TextArea } = Input
const { Text, Paragraph } = Typography

const SKILL_TYPE_COLOR: Record<string, string> = {
  atomic: '#1677ff', functional: '#722ed1', strategic: '#faad14',
}

const DEMO_TASKS = [
  '在网页上找到登录按钮并点击，然后填写用户名和密码',
  '从执行轨迹中提取可复用的操作模式并生成 Skill',
  '审计一个 Skill 的安全性并生成测试用例',
  '将功能重复的两个 Skill 合并为一个统一版本',
]

type Phase =
  | 'idle'
  | 'searching'
  | 'planning'
  | 'executing'
  | 'recording'
  | 'learning'
  | 'done'

interface RetrievedSkill {
  skill_id: string
  name: string
  description: string
  skill_type: string
  score: number
  match_reason: string
}

const PHASE_STEPS = [
  { title: 'Skill 检索', icon: <SearchOutlined /> },
  { title: '计划生成', icon: <BulbOutlined /> },
  { title: '执行', icon: <ThunderboltOutlined /> },
  { title: '经验记录', icon: <DatabaseOutlined /> },
  { title: 'Skill 学习', icon: <StarOutlined /> },
]

const phaseIndex: Record<Phase, number> = {
  idle: -1, searching: 0, planning: 1, executing: 2, recording: 3, learning: 4, done: 4,
}

export default function SelfEvolutionDemo() {
  const [task, setTask] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [result, setResult] = useState<ExecutionResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [visibleSteps, setVisibleSteps] = useState<number>(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

  const runDemo = async () => {
    if (!task.trim()) return
    setError(null)
    setResult(null)
    setVisibleSteps(0)

    try {
      setPhase('searching')
      await sleep(600)
      setPhase('planning')
      await sleep(500)
      setPhase('executing')

      const res = await executionApi.executePlan(task)
      setResult(res)

      // 逐步显示执行步骤
      for (let i = 1; i <= res.steps.length; i++) {
        await sleep(300)
        setVisibleSteps(i)
      }

      setPhase('recording')
      await sleep(800)
      setPhase('learning')
      await sleep(600)
      setPhase('done')
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      setError(err?.response?.data?.detail || err?.message || '执行失败')
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

  const isRunning = phase !== 'idle' && phase !== 'done'
  const retrieved = result?.retrieved_skills || []
  const successCount = result?.steps.filter(s => s.status === 'success').length || 0

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }}>
        <div style={{ marginBottom: 24 }}>
          <h2 style={{ fontWeight: 800, fontSize: 22, marginBottom: 4 }}>
            Self-Evolution Loop Demo
          </h2>
          <p style={{ color: '#666', marginBottom: 0 }}>
            输入任务，观察 SkillOS 如何检索 Skill、生成计划、执行、记录经验并持续学习。
          </p>
        </div>
      </motion.div>

      {/* 任务输入 */}
      <Card bordered={false} style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}>
        <TextArea
          value={task}
          onChange={e => setTask(e.target.value)}
          placeholder="描述你的任务目标..."
          rows={2}
          disabled={isRunning}
          style={{ fontSize: 14, marginBottom: 10 }}
        />
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <Space wrap>
            <Text type="secondary" style={{ fontSize: 12 }}>示例：</Text>
            {DEMO_TASKS.map(t => (
              <Tag
                key={t}
                style={{ cursor: 'pointer', fontSize: 11 }}
                onClick={() => !isRunning && setTask(t)}
              >
                {t.slice(0, 20)}…
              </Tag>
            ))}
          </Space>
          <Space>
            {phase === 'done' && (
              <Button icon={<ReloadOutlined />} onClick={reset}>重置</Button>
            )}
            <Button
              type="primary"
              icon={isRunning ? <LoadingOutlined /> : <RocketOutlined />}
              onClick={runDemo}
              loading={isRunning}
              disabled={!task.trim() || phase === 'done'}
              size="large"
            >
              {phase === 'done' ? '已完成' : '启动演化循环'}
            </Button>
          </Space>
        </div>
      </Card>

      {/* 阶段进度条 */}
      {phase !== 'idle' && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} style={{ marginBottom: 16 }}>
          <Card bordered={false} style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
            <Steps
              current={phaseIndex[phase]}
              status={phase === 'done' ? 'finish' : 'process'}
              size="small"
              items={PHASE_STEPS.map((s, i) => ({
                title: s.title,
                icon: phaseIndex[phase] > i
                  ? <CheckCircleFilled style={{ color: '#52c41a' }} />
                  : phaseIndex[phase] === i
                    ? <LoadingOutlined style={{ color: '#1677ff' }} />
                    : <ClockCircleOutlined style={{ color: '#d9d9d9' }} />,
              }))}
            />
          </Card>
        </motion.div>
      )}

      {error && (
        <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} closable onClose={() => setError(null)} />
      )}

      <AnimatePresence>
        {result && (
          <motion.div key="result" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <Row gutter={[16, 16]}>
              {/* 左列：检索到的 Skill + 执行步骤 */}
              <Col xs={24} lg={14}>
                {/* 检索到的 Skill */}
                {retrieved.length > 0 && (
                  <motion.div initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.1 }}>
                    <Card
                      title={<span><SearchOutlined style={{ color: '#1677ff', marginRight: 6 }} />检索到的 Skill ({retrieved.length})</span>}
                      bordered={false}
                      size="small"
                      style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 12 }}
                    >
                      {retrieved.map((sk, i) => (
                        <motion.div
                          key={sk.skill_id}
                          initial={{ opacity: 0, x: -12 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ delay: i * 0.08 }}
                          style={{
                            display: 'flex', alignItems: 'center', gap: 10,
                            padding: '8px 0',
                            borderBottom: i < retrieved.length - 1 ? '1px solid #f0f0f0' : 'none',
                          }}
                        >
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                              <Text strong style={{ fontSize: 13 }}>{sk.name}</Text>
                              <Tag color={SKILL_TYPE_COLOR[sk.skill_type]} style={{ fontSize: 10, padding: '0 4px' }}>
                                {sk.skill_type}
                              </Tag>
                            </div>
                            <Text type="secondary" style={{ fontSize: 11 }}>{sk.match_reason}</Text>
                          </div>
                          <Tooltip title={`相关度 ${(sk.score * 100).toFixed(0)}%`}>
                            <Progress
                              type="circle"
                              percent={Math.round(sk.score * 100)}
                              width={36}
                              strokeColor={sk.score > 0.7 ? '#52c41a' : sk.score > 0.4 ? '#faad14' : '#ff4d4f'}
                              format={p => <span style={{ fontSize: 10 }}>{p}%</span>}
                            />
                          </Tooltip>
                        </motion.div>
                      ))}
                    </Card>
                  </motion.div>
                )}

                {/* 执行步骤 */}
                <motion.div initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.2 }}>
                  <Card
                    title={<span><ThunderboltOutlined style={{ color: '#722ed1', marginRight: 6 }} />执行步骤</span>}
                    bordered={false}
                    size="small"
                    style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
                  >
                    {result.steps.length === 0 ? (
                      <Alert type="warning" message="未找到可执行的 Skill，请尝试更具体的任务描述" />
                    ) : (
                      result.steps.slice(0, visibleSteps).map((step, i) => (
                        <motion.div
                          key={step.step_id}
                          initial={{ opacity: 0, y: 8 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ duration: 0.25 }}
                          style={{
                            display: 'flex', alignItems: 'center', gap: 10,
                            padding: '8px 0',
                            borderBottom: i < result.steps.length - 1 ? '1px solid #f0f0f0' : 'none',
                          }}
                        >
                          {step.status === 'success'
                            ? <CheckCircleFilled style={{ color: '#52c41a', fontSize: 16 }} />
                            : <ClockCircleOutlined style={{ color: '#ff4d4f', fontSize: 16 }} />}
                          <div style={{ flex: 1 }}>
                            <Text strong style={{ fontSize: 13 }}>{step.skill_name}</Text>
                            {step.error && (
                              <div style={{ fontSize: 11, color: '#ff4d4f' }}>{step.error}</div>
                            )}
                          </div>
                          <Text type="secondary" style={{ fontSize: 11 }}>{(step.latency_ms ?? 0).toFixed(0)}ms</Text>
                          <Tag color={step.status === 'success' ? 'green' : 'red'} style={{ fontSize: 10 }}>
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

              {/* 右列：演化结果 */}
              <Col xs={24} lg={10}>
                {/* 执行摘要 */}
                <motion.div initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.15 }}>
                  <Card
                    bordered={false}
                    size="small"
                    style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 12 }}
                  >
                    <Row gutter={8}>
                      {[
                        { label: '步骤', value: result.steps.length, color: '#1677ff' },
                        { label: '成功', value: successCount, color: '#52c41a' },
                        { label: '耗时', value: `${result.total_latency_ms.toFixed(0)}ms`, color: '#722ed1' },
                      ].map(({ label, value, color }) => (
                        <Col span={8} key={label} style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: 20, fontWeight: 700, color }}>{value}</div>
                          <Text type="secondary" style={{ fontSize: 11 }}>{label}</Text>
                        </Col>
                      ))}
                    </Row>
                  </Card>
                </motion.div>

                {/* 经验记录 */}
                <AnimatePresence>
                  {(phase === 'recording' || phase === 'learning' || phase === 'done') && (
                    <motion.div
                      initial={{ opacity: 0, scale: 0.95 }}
                      animate={{ opacity: 1, scale: 1 }}
                      transition={{ delay: 0.1 }}
                    >
                      <Card
                        title={<span><DatabaseOutlined style={{ color: '#52c41a', marginRight: 6 }} />经验已记录</span>}
                        bordered={false}
                        size="small"
                        style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 12, borderLeft: '3px solid #52c41a' }}
                      >
                        <Timeline
                          items={[
                            { color: 'green', children: <Text style={{ fontSize: 12 }}>执行轨迹已捕获（{result.steps.length} 个步骤）</Text> },
                            { color: 'green', children: <Text style={{ fontSize: 12 }}>状态变更已记录（{Object.keys(result.final_state).length} 个字段）</Text> },
                            { color: 'blue', children: <Text style={{ fontSize: 12 }}>经验单元已写入 Experience Store</Text> },
                          ]}
                        />
                      </Card>
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Skill 学习 */}
                <AnimatePresence>
                  {(phase === 'learning' || phase === 'done') && (
                    <motion.div
                      initial={{ opacity: 0, scale: 0.95 }}
                      animate={{ opacity: 1, scale: 1 }}
                      transition={{ delay: 0.2 }}
                    >
                      <Card
                        title={<span><StarOutlined style={{ color: '#faad14', marginRight: 6 }} />Skill 学习</span>}
                        bordered={false}
                        size="small"
                        style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 12, borderLeft: '3px solid #faad14' }}
                      >
                        <div style={{ marginBottom: 8 }}>
                          {retrieved.slice(0, 3).map(sk => (
                            <div key={sk.skill_id} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                              <ArrowRightOutlined style={{ color: '#52c41a', fontSize: 10 }} />
                              <Text style={{ fontSize: 12 }}><strong>{sk.name}</strong> 执行记录已更新</Text>
                            </div>
                          ))}
                        </div>
                        <Divider style={{ margin: '8px 0' }} />
                        <div style={{ fontSize: 11, color: '#666' }}>
                          本次执行将用于 Skill 质量评估与版本演化决策
                        </div>
                      </Card>
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* 完成 */}
                <AnimatePresence>
                  {phase === 'done' && (
                    <motion.div
                      initial={{ opacity: 0, y: 12 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: 0.3 }}
                    >
                      <Alert
                        type="success"
                        icon={<CheckCircleFilled />}
                        message="自演化循环完成"
                        description={
                          <div style={{ fontSize: 12 }}>
                            SkillOS 已完成本次任务的完整演化循环：检索 → 规划 → 执行 → 记录 → 学习。
                            下次遇到相似任务时，系统将更快、更准确地响应。
                          </div>
                        }
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

      {/* 空状态说明 */}
      {phase === 'idle' && !result && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.3 }}>
          <Card bordered={false} style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', textAlign: 'center', padding: '32px 0' }}>
            <div style={{ fontSize: 48, marginBottom: 12 }}>🔄</div>
            <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 8 }}>Self-Evolution Loop</div>
            <Paragraph type="secondary" style={{ maxWidth: 480, margin: '0 auto', fontSize: 13 }}>
              SkillOS 通过持续的"检索 → 执行 → 记录 → 学习"循环，让 Agent 在每次任务后自动积累经验、
              优化 Skill 质量，实现真正的自演化能力。
            </Paragraph>
            <div style={{ marginTop: 20, display: 'flex', justifyContent: 'center', gap: 8, flexWrap: 'wrap' }}>
              {PHASE_STEPS.map((s, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Tag color="blue" style={{ fontSize: 12 }}>{s.icon} {s.title}</Tag>
                  {i < PHASE_STEPS.length - 1 && <ArrowRightOutlined style={{ color: '#d9d9d9', fontSize: 10 }} />}
                </div>
              ))}
            </div>
          </Card>
        </motion.div>
      )}
    </div>
  )
}
