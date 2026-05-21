import { useEffect, useState } from 'react'
import {
  Alert, Badge, Button, Card, Col, Input, List, message, Progress, Row, Space, Statistic, Table, Tag, Typography,
  Segmented,
} from 'antd'
import { BarChartOutlined, PlayCircleOutlined, ReloadOutlined, ToolOutlined } from '@ant-design/icons'
import { motion } from 'framer-motion'
import { evolutionApi, executionApi, skillsApi } from '@/api/client'
import type { ExecutionResult, HealthReport, SkillVisibility, SystemHealth } from '@/api/types'

const { Text, Paragraph } = Typography
const { TextArea } = Input

const STATUS_COLOR: Record<string, string> = {
  healthy: '#52c41a',
  degraded: '#faad14',
  critical: '#ff4d4f',
  stale: '#8c8c8c',
  unknown: '#bfbfbf',
}

export default function Evaluation() {
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [visibility, setVisibility] = useState<SkillVisibility | 'all'>('user')
  const [visibilityCounts, setVisibilityCounts] = useState({ user: 0, kernel: 0 })
  const [evalGoal, setEvalGoal] = useState('Open Chrome and navigate to the URL resolved from the user task.')
  const [runtimeResult, setRuntimeResult] = useState<ExecutionResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [runningEval, setRunningEval] = useState(false)
  const [improving, setImproving] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const [nextHealth, userSkills, kernelSkills] = await Promise.all([
        evolutionApi.systemHealth({ visibility }),
        skillsApi.list({ limit: 1000, visibility: 'user' }),
        skillsApi.list({ limit: 1000, visibility: 'kernel' }),
      ])
      setHealth(nextHealth)
      setVisibilityCounts({ user: userSkills.length, kernel: kernelSkills.length })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [visibility])

  const improve = async (skillId: string) => {
    setImproving(skillId)
    try {
      const result = await evolutionApi.improve(skillId) as { action?: string; reason?: string }
      message.success(`${result.action || 'evaluated'}: ${result.reason || 'Agent completed evaluation improvement.'}`)
      await load()
    } catch (error) {
      message.error('Agent improvement failed')
    } finally {
      setImproving(null)
    }
  }

  const runRuntimeEvaluation = async () => {
    if (!evalGoal.trim()) {
      message.warning('Please enter a real evaluation task.')
      return
    }
    setRunningEval(true)
    setRuntimeResult(null)
    try {
      const result = await executionApi.executePlan(evalGoal, { evaluation_mode: true })
      setRuntimeResult(result)
      message.success(`Runtime evaluation finished: ${result.status}`)
      await load()
    } catch {
      message.error('Runtime evaluation failed')
    } finally {
      setRunningEval(false)
    }
  }

  const reports = health?.skill_reports || []

  return (
    <div style={{ padding: 24 }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
          <div>
            <h2 style={{ fontWeight: 700, marginBottom: 4 }}>Evaluation</h2>
            <p style={{ color: '#666', margin: 0 }}>
              Skill health, execution quality, and readiness signals collected from the repository and runtime records.
            </p>
          </div>
          <Space wrap>
            <Segmented
              value={visibility}
              onChange={value => setVisibility(value as SkillVisibility | 'all')}
              options={[
                { label: 'User Skills', value: 'user' },
                { label: 'Kernel Skills', value: 'kernel' },
                { label: 'All', value: 'all' },
              ]}
            />
            <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>Refresh</Button>
          </Space>
        </div>
      </motion.div>

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {[
          { label: 'Total Skills', value: health?.total_skills || 0, color: '#1677ff' },
          { label: 'Healthy', value: health?.healthy_count || 0, color: '#52c41a' },
          { label: 'Degraded', value: health?.degraded_count || 0, color: '#faad14' },
          { label: 'Critical', value: health?.critical_count || 0, color: '#ff4d4f' },
          { label: 'Stale', value: health?.stale_count || 0, color: '#8c8c8c' },
        ].map(item => (
          <Col xs={12} md={4} key={item.label}>
            <Card bordered={false} style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
              <Statistic title={item.label} value={item.value} valueStyle={{ color: item.color, fontWeight: 700 }} />
            </Card>
          </Col>
        ))}
        <Col xs={12} md={4}>
          <Card bordered={false} style={{ borderRadius: 12, textAlign: 'center', boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
            <Progress type="circle" percent={Math.round((health?.health_ratio || 0) * 100)} width={62} strokeColor="#52c41a" />
            <div style={{ color: '#666', fontSize: 12, marginTop: 6 }}>Health Ratio</div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={10}>
          <Card bordered={false} style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
            <Statistic title="User-mode Skills" value={visibilityCounts.user} valueStyle={{ color: '#1677ff', fontWeight: 700 }} />
            <Text type="secondary">Visible product capabilities shown to users.</Text>
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card bordered={false} style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
            <Statistic title="Kernel-mode Skills" value={visibilityCounts.kernel} valueStyle={{ color: '#722ed1', fontWeight: 700 }} />
            <Text type="secondary">Internal governance tools for merge, update, review, and graph maintenance.</Text>
          </Card>
        </Col>
      </Row>

      <Card
        title={<span><PlayCircleOutlined style={{ color: '#13c2c2', marginRight: 8 }} />Real Runtime Evaluation</span>}
        bordered={false}
        style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
      >
        <Alert
          type="info"
          showIcon
          message="This runs the same execution pipeline as the Execution page, then feeds the resulting metrics back into Skill health."
          style={{ marginBottom: 12 }}
        />
        <TextArea
          value={evalGoal}
          onChange={event => setEvalGoal(event.target.value)}
          rows={3}
          placeholder="Describe a real task to evaluate, e.g. Open HITWH official website in Chrome."
          style={{ marginBottom: 12 }}
        />
        <Button type="primary" icon={<PlayCircleOutlined />} loading={runningEval} onClick={runRuntimeEvaluation}>
          Run Real Evaluation Task
        </Button>

        {runtimeResult && (
          <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
            <Col xs={24} md={8}>
              <Card size="small">
                <Statistic title="Runtime Status" value={runtimeResult.status} />
                <Text type="secondary">{Math.round(runtimeResult.total_latency_ms)}ms</Text>
              </Card>
            </Col>
            <Col xs={24} md={16}>
              <Card size="small" title="Retrieved / Used Knowledge">
                <Space wrap>
                  {runtimeResult.retrieved_skills.map(skill => (
                    <Tag key={skill.skill_id} color={skill.score > 0.7 ? 'green' : 'blue'}>
                      {skill.name} · {Math.round(skill.score * 100)}%
                    </Tag>
                  ))}
                </Space>
              </Card>
            </Col>
            <Col span={24}>
              <List
                size="small"
                header={<Text strong>Execution Trace</Text>}
                dataSource={runtimeResult.agent_trace || []}
                renderItem={step => (
                  <List.Item>
                    <Space wrap>
                      <Tag color={step.status === 'success' || step.status === 'completed' ? 'green' : 'blue'}>{step.status}</Tag>
                      <Text strong>{step.agent}</Text>
                      <Text type="secondary">{step.action.replace(/_/g, ' ')}</Text>
                    </Space>
                  </List.Item>
                )}
              />
              {runtimeResult.steps.map(step => (
                <Paragraph key={step.step_id} style={{ marginTop: 8, marginBottom: 0 }}>
                  <Text strong>{step.skill_name}</Text>
                  <Text type="secondary"> · {step.status} · {Math.round(step.latency_ms)}ms</Text>
                </Paragraph>
              ))}
            </Col>
          </Row>
        )}
      </Card>

      <Card
        title={<span><BarChartOutlined style={{ color: '#1677ff', marginRight: 8 }} />Skill Evaluation Reports</span>}
        bordered={false}
        style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
      >
        <Table
          dataSource={reports}
          rowKey="skill_id"
          loading={loading}
          size="middle"
          pagination={{ pageSize: 12 }}
          columns={[
            {
              title: 'Skill',
              dataIndex: 'skill_name',
              render: (value: string, row: HealthReport) => (
                <div>
                  <Text strong>{value}</Text>
                  <div style={{ fontSize: 11, color: '#8c8c8c' }}>{row.skill_id.slice(0, 8)}...</div>
                </div>
              ),
            },
            {
              title: 'Status',
              dataIndex: 'status',
              render: (status: string) => <Badge color={STATUS_COLOR[status] || '#bfbfbf'} text={status} />,
            },
            {
              title: 'Success Rate',
              dataIndex: 'success_rate',
              render: (value: number) => (
                <Progress
                  percent={Math.round(value * 100)}
                  size="small"
                  style={{ width: 120 }}
                  strokeColor={value > 0.75 ? '#52c41a' : value > 0.45 ? '#faad14' : '#ff4d4f'}
                />
              ),
            },
            { title: 'Usage', dataIndex: 'usage_count' },
            {
              title: 'Avg Latency',
              dataIndex: 'avg_latency_ms',
              render: (value: number) => `${value.toFixed(0)}ms`,
            },
            {
              title: 'Issues',
              dataIndex: 'issues',
              render: (issues: string[]) => issues.length
                ? issues.map(issue => <Tag key={issue} color="red">{issue}</Tag>)
                : <Tag color="green">clear</Tag>,
            },
            {
              title: 'Recommendations',
              dataIndex: 'recommendations',
              render: (items: string[]) => items.length
                ? items.map(item => <Tag key={item} color="blue">{item}</Tag>)
                : <Text type="secondary">None</Text>,
            },
            {
              title: 'Agent Action',
              render: (_: unknown, row: HealthReport) => (
                <Space>
                  <Button
                    size="small"
                    icon={<ToolOutlined />}
                    loading={improving === row.skill_id}
                    onClick={() => improve(row.skill_id)}
                  >
                    Evaluate & Improve
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </div>
  )
}
