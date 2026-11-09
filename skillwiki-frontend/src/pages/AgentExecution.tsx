import { useCallback, useEffect, useState } from 'react'
import {
  Card, Input, Button, Tag, Alert, Spin,
  Typography, Space, Statistic, Row, Col, Progress, Tooltip, Table, Empty,
} from 'antd'
import {
  PlayCircleOutlined, ThunderboltOutlined, CheckCircleOutlined,
  CloseCircleOutlined, SearchOutlined, DatabaseOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { motion, AnimatePresence } from 'framer-motion'
import { useSearchParams } from 'react-router-dom'
import { executionApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import { useSkillContext } from '@/hooks/useSkillContext'
import type { ExecutionHistoryItem, ExecutionResult, ExecutionStepResult, RetrievedSkill, RetrievalTrace } from '@/api/types'

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

function TraceChecks({ trace, verifierPassed, verifierSummary }: {
  trace?: RetrievalTrace
  verifierPassed?: boolean | null
  verifierSummary?: Record<string, unknown> | null
}) {
  const passed = verifierPassed ?? trace?.execution?.verifier_passed
  const summary = verifierSummary ?? trace?.execution?.verifier_summary
  const checks = trace?.governance || {}
  return (
    <div style={{ marginTop: 12 }}>
      <Space wrap>
        {trace?.trace_id && <Text code copyable>{trace.trace_id}</Text>}
        {trace?.execution?.status && <Tag>Execution: {trace.execution.status}</Tag>}
        <Tag color={passed === true ? 'green' : passed === false ? 'red' : 'default'}>
          {passed === true ? 'Verifier passed' : passed === false ? 'Verifier failed' : 'Verification not confirmed'}
        </Tag>
      </Space>
      <div style={{ marginTop: 8 }}>
        <Text strong>Saved source / environment checks</Text>
        {Object.keys(checks).length === 0 ? (
          <div><Text type="secondary">No source or environment checks recorded in this trace.</Text></div>
        ) : (
          <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 280, overflow: 'auto' }}>{JSON.stringify(checks, null, 2)}</pre>
        )}
      </div>
      {summary && (
        <details>
          <summary>Verifier evidence</summary>
          <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap', maxHeight: 280, overflow: 'auto' }}>{JSON.stringify(summary, null, 2)}</pre>
        </details>
      )}
    </div>
  )
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
            {step.error && <Alert type="error" title={step.error} style={{ marginBottom: 8 }} />}
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
  const { skillId } = useSkillContext()
  const [searchParams] = useSearchParams()
  const [goal, setGoal] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ExecutionResult | null>(null)
  const [savedTrace, setSavedTrace] = useState<RetrievalTrace | null>(null)
  const [traceLoading, setTraceLoading] = useState(false)
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
      setHistoryError(getApiErrorMessage(e, 'Failed to load execution history'))
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  useEffect(() => {
    const timeoutId = window.setTimeout(() => { void loadHistory() }, 0)
    return () => window.clearTimeout(timeoutId)
  }, [loadHistory])

  const handleExecute = async () => {
    if (!goal.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await executionApi.executePlan(goal, {}, skillId ? [skillId] : undefined)
      setResult(res)
      setSavedTrace(null)
      void loadHistory()
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, 'Execution failed'))
    } finally {
      setLoading(false)
    }
  }

  const handleLoadTrace = useCallback(async (executionId: string) => {
    setTraceLoading(true)
    setError(null)
    try {
      setSavedTrace(await executionApi.retrievalTrace(executionId))
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, 'Retrieval evidence could not be loaded'))
    } finally {
      setTraceLoading(false)
    }
  }, [])

  useEffect(() => {
    const executionId = searchParams.get('execution_id')
    if (!executionId) return
    const timer = window.setTimeout(() => { void handleLoadTrace(executionId) }, 0)
    return () => window.clearTimeout(timer)
  }, [searchParams, handleLoadTrace])

  const successCount = result?.steps.filter(s => s.status === 'success').length || 0
  const retrieved: RetrievedSkill[] = result?.retrieved_skills || []

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: '0 auto' }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h2 style={{ fontWeight: 700, marginBottom: 4 }}>Agent Execution</h2>
        {skillId && <Alert type="info" showIcon title={`本次检索限定技能：${skillId}；仍需通过发布状态、来源和环境检查。`} style={{ marginBottom: 12 }} />}
        <p style={{ color: '#666', marginBottom: 24 }}>
          Enter a task goal. SkillWiki will retrieve relevant Skills, build an execution plan, and run it.
        </p>

        <Card variant="borderless" style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}>
          <TextArea
            value={goal}
            onChange={e => setGoal(e.target.value)}
            placeholder="Describe your task goal, for example: find the login button on a page, click it, then fill in the username and password..."
            rows={3}
            style={{ marginBottom: 12, fontSize: 14 }}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              SkillWiki will decompose the task, retrieve Skills, and generate an execution plan.
            </Text>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              onClick={handleExecute}
              loading={loading}
              disabled={!goal.trim()}
              size="large"
            >
              Execute
            </Button>
          </div>
        </Card>

        {/* Example tasks */}
        <div style={{ marginBottom: 24 }}>
          <Text type="secondary" style={{ fontSize: 12, marginRight: 8 }}>Examples:</Text>
          {[
            'Click the submit button on the page',
            'Fill and submit the login form',
            'Type a keyword into the search box and search',
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
        title={<span><DatabaseOutlined style={{ color: '#1677ff', marginRight: 6 }} />Recent Execution History</span>}
        variant="borderless"
        size="small"
        style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
        extra={
          <Button size="small" icon={<ReloadOutlined />} loading={historyLoading} onClick={loadHistory}>
            Refresh
          </Button>
        }
      >
        {historyError && (
          <Alert type="warning" showIcon title={historyError} style={{ marginBottom: 12 }} />
        )}
        {!historyLoading && history.length === 0 ? (
          <Empty description="No execution history yet. Completed tasks will appear here automatically." image={Empty.PRESENTED_IMAGE_SIMPLE} />
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
              {
                title: 'Evidence',
                width: 100,
                render: (_: unknown, item: ExecutionHistoryItem) => (
                  <Button type="link" size="small" loading={traceLoading} onClick={() => void handleLoadTrace(item.execution_id)}>
                    Load trace
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>

      {savedTrace && (
        <Card title="Saved Retrieval Evidence" size="small" style={{ marginBottom: 16 }}>
          {savedTrace.blocked.length > 0 && <Alert type="warning" showIcon title="Retrieval blocked or degraded" description={savedTrace.blocked.join('; ')} style={{ marginBottom: 12 }} />}
          <Space wrap>
            <Tag color="blue">{savedTrace.adapter}</Tag>
            {savedTrace.source_commit && <Tag>Fabric {savedTrace.source_commit.slice(0, 12)}</Tag>}
            {savedTrace.embedding_model && <Tag>{savedTrace.embedding_model}</Tag>}
            {savedTrace.embedding_mode && <Tag>{savedTrace.embedding_mode}</Tag>}
          </Space>
          <div style={{ display: 'grid', gap: 6, marginTop: 12 }}>
            {savedTrace.candidates.map((candidate, index) => (
              <Text key={`${String(candidate.skill_id)}-${index}`} type="secondary" style={{ fontSize: 12 }}>
                {String(candidate.name || candidate.skill_id)} · v{String(candidate.version || 'unknown')} · {candidate.is_seed ? 'direct retrieval' : `graph-expanded (depth ${String(candidate.graph_depth ?? 0)})`}
              </Text>
            ))}
          </div>
          <TraceChecks trace={savedTrace} />
          <details>
            <summary>Saved plan and execution output</summary>
            <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 360, overflow: 'auto' }}>{JSON.stringify({ plan: savedTrace.plan, execution: savedTrace.execution }, null, 2)}</pre>
          </details>
        </Card>
      )}

      {loading && (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin size="large" description="Planning and executing..." />
        </div>
      )}

      {error && (
        <Alert type="error" title="Execution Failed" description={error} showIcon style={{ marginBottom: 16 }} />
      )}

      <AnimatePresence>
        {result && (
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
          {/* Retrieved Skills */}
            {retrieved.length > 0 && (
              <Card
                title={<span><SearchOutlined style={{ color: '#1677ff', marginRight: 6 }} />Retrieved Skills ({retrieved.length})</span>}
                variant="borderless"
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

            {result.retrieval_trace && (
              <Card
                title="Retrieval Evidence"
                variant="borderless"
                size="small"
                style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
              >
                {result.retrieval_trace.blocked.length > 0 && (
                  <Alert type="warning" showIcon title="Retrieval blocked or degraded" description={result.retrieval_trace.blocked.join('; ')} style={{ marginBottom: 12 }} />
                )}
                <Space wrap size={[8, 8]} style={{ marginBottom: 12 }}>
                  <Tag color="blue">{result.retrieval_trace.adapter}</Tag>
                  {result.retrieval_trace.source_commit && <Tag>Fabric {result.retrieval_trace.source_commit.slice(0, 12)}</Tag>}
                  {result.retrieval_trace.embedding_model && <Tag>{result.retrieval_trace.embedding_model}</Tag>}
                  {result.retrieval_trace.embedding_mode && <Tag>{result.retrieval_trace.embedding_mode}</Tag>}
                </Space>
                {retrieved.length > 0 && (
                  <div style={{ display: 'grid', gap: 6 }}>
                    {retrieved.map(skill => (
                      <Text key={skill.skill_id} type="secondary" style={{ fontSize: 12 }}>
                        {skill.name} · v{skill.version || 'unknown'} · {skill.is_seed ? 'direct retrieval' : `graph-expanded (depth ${skill.graph_depth ?? 0})`} · {skill.match_reason}
                      </Text>
                    ))}
                  </div>
                )}
              </Card>
            )}

            {/* Execution summary */}
            <Card
              variant="borderless"
              style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
            >
              <Row gutter={16}>
                <Col span={6}>
                  <Statistic
                    title="Status"
                    value={result.status}
                    styles={{ content: { color: result.status === 'success' ? '#52c41a' : result.status === 'failed' ? '#ff4d4f' : '#faad14' } }}
                  />
                </Col>
                <Col span={6}>
                  <Statistic title="Total Steps" value={result.steps.length} />
                </Col>
                <Col span={6}>
                  <Statistic title="Successful" value={successCount} styles={{ content: { color: '#52c41a' } }} />
                </Col>
                <Col span={6}>
                  <Statistic
                    title="Total Latency"
                    value={result.total_latency_ms.toFixed(0)}
                    suffix="ms"
                    styles={{ content: { color: '#1677ff' } }}
                  />
                </Col>
              </Row>
              <TraceChecks trace={result.retrieval_trace} verifierPassed={result.verifier_passed} verifierSummary={result.verifier_summary} />
            </Card>

            {/* Execution steps */}
            <Card
              title={<><ThunderboltOutlined /> Execution Steps</>}
              variant="borderless"
              style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
            >
              {result.steps.length === 0 ? (
                <Alert type="warning" title="No executable Skill was found. Try a more specific task description." />
              ) : (
                result.steps.map((step, i) => <StepCard key={step.step_id} step={step} index={i} />)
              )}
            </Card>

            {/* Final state */}
            {Object.keys(result.final_state).length > 0 && (
              <Card
                title="Final State"
                variant="borderless"
                style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
              >
                <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 8, fontSize: 12, overflow: 'auto' }}>
                  {JSON.stringify(result.final_state, null, 2)}
                </pre>
              </Card>
            )}
            {/* Experience recording feedback */}
            {result.experience_recorded && (
              <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}>
                <Card
                  variant="borderless"
                  size="small"
                  style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', borderLeft: '3px solid #52c41a' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <DatabaseOutlined style={{ color: '#52c41a', fontSize: 16 }} />
                    <Text style={{ fontSize: 13 }}>
                      <strong>Experience recorded</strong> - this execution trace was written to the Experience Store for Skill quality assessment and evolution decisions.
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
