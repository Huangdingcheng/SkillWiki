import { useCallback, useEffect, useState } from 'react'
import {
  Card, Input, Button, Tag, Alert, Spin, Divider,
  Typography, Space, Badge, Statistic, Row, Col, Progress, Tooltip, Table, Empty,
} from 'antd'
import {
  PlayCircleOutlined, ThunderboltOutlined, CheckCircleOutlined,
  CloseCircleOutlined, SearchOutlined, DatabaseOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { motion, AnimatePresence } from 'framer-motion'
import { executionApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import type { ExecutionHistoryItem, ExecutionResult, ExecutionStepResult, RetrievedSkill } from '@/api/types'

const { TextArea } = Input
const { Text } = Typography

const STATUS_COLOR: Record<string, string> = {
  success: '#52c41a',
  failed: '#ff4d4f',
  running: '#1677ff',
  pending: '#d9d9d9',
  skipped: '#faad14',
}

const SKILL_TYPE_COLOR: Record<string, string> = {
  atomic: '#1677ff', functional: '#722ed1', strategic: '#faad14',
}

function formatHistoryTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function StepCard({ step, index }: { step: ExecutionStepResult; index: number }) {
  const [expanded, setExpanded] = useState(false)
  const stepResult = step.result ?? {}
  const latency = step.latency_ms ?? 0
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
              : step.status === 'failed'
                ? <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
                : <ThunderboltOutlined style={{ color: STATUS_COLOR[step.status] || '#d9d9d9' }} />}
            <Text strong>{step.skill_name}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>{step.skill_id.slice(0, 8)}...</Text>
          </Space>
          <Space>
            <Tag color={STATUS_COLOR[step.status]}>{step.status}</Tag>
            <Text type="secondary">{latency.toFixed(0)}ms</Text>
          </Space>
        </div>
        {expanded && (
          <div style={{ marginTop: 8 }}>
            {step.error && <Alert type="error" message={step.error} style={{ marginBottom: 8 }} />}
            {Object.keys(stepResult).length > 0 && (
              <pre style={{ background: '#f5f5f5', padding: 8, borderRadius: 4, fontSize: 11, overflow: 'auto' }}>
                {JSON.stringify(stepResult, null, 2)}
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
  const [history, setHistory] = useState<ExecutionHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true)
    setHistoryError(null)
    try {
      setHistory(await executionApi.history())
    } catch (e: unknown) {
      setHistoryError(getApiErrorMessage(e, '执行历史加载失败'))
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadHistory()
  }, [loadHistory])

  const handleExecute = async () => {
    if (!goal.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await executionApi.executePlan(goal)
      setResult(res)
      void loadHistory()
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, '执行失败'))
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

      <Card
        title={<span><DatabaseOutlined style={{ color: '#1677ff', marginRight: 6 }} />最近执行历史</span>}
        bordered={false}
        size="small"
        style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
        extra={
          <Button size="small" icon={<ReloadOutlined />} loading={historyLoading} onClick={loadHistory}>
            刷新
          </Button>
        }
      >
        {historyError && (
          <Alert type="warning" showIcon message={historyError} style={{ marginBottom: 12 }} />
        )}
        {!historyLoading && history.length === 0 ? (
          <Empty description="暂无执行历史，完成一次任务后会自动显示在这里" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <Table
            rowKey="execution_id"
            dataSource={history}
            loading={historyLoading}
            size="small"
            pagination={{ pageSize: 5, size: 'small' }}
            columns={[
              {
                title: 'Goal',
                dataIndex: 'goal',
                ellipsis: true,
                render: (goalText: string) => <Text style={{ maxWidth: 240 }}>{goalText}</Text>,
              },
              {
                title: 'Status',
                dataIndex: 'status',
                width: 90,
                render: (status: string) => <Tag color={STATUS_COLOR[status] || 'default'}>{status}</Tag>,
              },
              {
                title: 'Steps',
                width: 90,
                render: (_: unknown, item: ExecutionHistoryItem) => `${item.success_count}/${item.step_count}`,
              },
              {
                title: 'Skills',
                dataIndex: 'retrieved_skill_count',
                width: 80,
              },
              {
                title: 'Latency',
                dataIndex: 'total_latency_ms',
                width: 90,
                render: (ms: number) => `${ms.toFixed(0)}ms`,
              },
              {
                title: 'Created',
                dataIndex: 'created_at',
                width: 170,
                render: formatHistoryTime,
              },
            ]}
          />
        )}
      </Card>

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
          {/* 检索到的 Skill */}
            {retrieved.length > 0 && (
              <Card
                title={<span><SearchOutlined style={{ color: '#1677ff', marginRight: 6 }} />检索到的 Skill ({retrieved.length})</span>}
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
                    valueStyle={{ color: result.status === 'success' ? '#52c41a' : result.status === 'failed' ? '#ff4d4f' : '#faad14' }}
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
              title={<><ThunderboltOutlined /> 执行步骤</>}
              bordered={false}
              style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
            >
              {result.steps.length === 0 ? (
                <Alert type="warning" message="未找到可执行的 Skill，请尝试更具体的任务描述" />
              ) : (
                result.steps.map((step, i) => <StepCard key={step.step_id} step={step} index={i} />)
              )}
            </Card>

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
