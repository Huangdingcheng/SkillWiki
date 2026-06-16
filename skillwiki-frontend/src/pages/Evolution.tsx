import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Empty,
  message,
  Progress,
  Popconfirm,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'
import {
  BranchesOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  MedicineBoxOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import { motion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { evolutionApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import type {
  HealthReport,
  MaintenanceProposal,
  MaintenanceProposalListResponse,
  SystemHealth,
} from '@/api/types'

const { Text } = Typography

const STATUS_COLOR: Record<string, string> = {
  healthy: '#52c41a',
  degraded: '#faad14',
  critical: '#ff4d4f',
  stale: '#d9d9d9',
  unknown: '#8c8c8c',
}

const STATUS_LABEL: Record<string, string> = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  critical: 'Critical',
  stale: 'Stale',
  unknown: 'Unknown',
}

type EvolutionCycleResult = {
  cycle_id?: string
  tasks_total?: number
  tasks_completed?: number
  tasks_failed?: number
  repaired?: string[]
  deprecated?: string[]
  merged?: unknown[]
  split?: unknown[]
  errors?: string[]
  maintenance_proposals?: MaintenanceProposal[]
}

type RepairResult = {
  success?: boolean
  root_cause?: string
  should_deprecate?: boolean
}

function countOf(value: unknown[] | undefined) {
  return Array.isArray(value) ? value.length : 0
}

function proposalColor(action?: string) {
  if (action === 'repair') return 'orange'
  if (action === 'review') return 'blue'
  if (action === 'deprecate') return 'red'
  return 'default'
}

function proposalStatusColor(status?: string) {
  if (status === 'pending') return 'gold'
  if (status === 'accepted') return 'green'
  if (status === 'rejected') return 'red'
  if (status === 'superseded') return 'default'
  return 'blue'
}

function joinPreview(values?: string[], fallback = 'None') {
  if (!values || values.length === 0) return fallback
  return values.slice(0, 2).join('; ')
}

export default function Evolution() {
  const navigate = useNavigate()
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [cycling, setCycling] = useState(false)
  const [repairing, setRepairing] = useState<string | null>(null)
  const [cycleResult, setCycleResult] = useState<EvolutionCycleResult | null>(null)
  const [proposalQueue, setProposalQueue] = useState<MaintenanceProposalListResponse | null>(null)
  const [queueLoading, setQueueLoading] = useState(false)
  const [proposalActionId, setProposalActionId] = useState<string | null>(null)

  const loadHealth = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await evolutionApi.systemHealth()
      setHealth(data)
    } catch (err) {
      setError(getApiErrorMessage(err, 'Failed to load health report'))
    } finally {
      setLoading(false)
    }
  }

  const loadProposalQueue = async () => {
    setQueueLoading(true)
    try {
      const data = await evolutionApi.proposals()
      setProposalQueue(data)
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Failed to load maintenance proposals'))
    } finally {
      setQueueLoading(false)
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadHealth()
      void loadProposalQueue()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [])

  const runCycle = async () => {
    setCycling(true)
    try {
      const result = await evolutionApi.runCycle() as EvolutionCycleResult
      setCycleResult(result)
      message.success(`Evolution cycle completed; maintenance proposals queued: ${countOf(result.maintenance_proposals)}`)
      await loadHealth()
      await loadProposalQueue()
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Evolution cycle failed'))
    } finally {
      setCycling(false)
    }
  }

  const repairSkill = async (id: string) => {
    setRepairing(id)
    try {
      const result = await evolutionApi.repair(id) as RepairResult
      if (result.success) {
        message.success(`Review candidate generated: ${result.root_cause || 'maintenance evidence captured'}`)
      } else if (result.should_deprecate) {
        message.warning('Governed review suggested: consider deprecating or replacing this Skill')
      } else {
        message.warning('No repair candidate generated; check the health recommendations')
      }
      await loadHealth()
      await loadProposalQueue()
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Failed to generate maintenance candidate'))
    } finally {
      setRepairing(null)
    }
  }

  const openVersionControl = (proposal: MaintenanceProposal) => {
    const params = new URLSearchParams({
      skill_id: proposal.skill_id,
      proposal_id: proposal.proposal_id,
    })
    navigate(`/versions?${params.toString()}`)
  }

  const acceptProposal = async (proposal: MaintenanceProposal) => {
    setProposalActionId(proposal.proposal_id)
    try {
      const accepted = await evolutionApi.acceptProposal(proposal.proposal_id)
      message.success('Proposal accepted for governed review')
      await loadProposalQueue()
      openVersionControl(accepted)
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Failed to accept proposal'))
    } finally {
      setProposalActionId(null)
    }
  }

  const rejectProposal = async (proposal: MaintenanceProposal) => {
    setProposalActionId(proposal.proposal_id)
    try {
      await evolutionApi.rejectProposal(proposal.proposal_id)
      message.success('Proposal rejected')
      await loadProposalQueue()
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Failed to reject proposal'))
    } finally {
      setProposalActionId(null)
    }
  }

  const needsAttention = useMemo(
    () => health?.skill_reports.filter(report => report.status === 'degraded' || report.status === 'critical') || [],
    [health],
  )

  const columns: TableColumnsType<HealthReport> = [
    {
      title: 'Skill',
      dataIndex: 'skill_name',
      width: 220,
      render: (name: string) => <Text strong className="skillwiki-identifier-cell">{name}</Text>,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      render: (status: string) => <Badge color={STATUS_COLOR[status] || STATUS_COLOR.unknown} text={STATUS_LABEL[status] || status} />,
    },
    {
      title: 'Success Rate',
      dataIndex: 'success_rate',
      render: (rate: number) => (
        <Progress
          percent={Math.round(rate * 100)}
          size="small"
          strokeColor={rate > 0.7 ? '#52c41a' : rate > 0.4 ? '#faad14' : '#ff4d4f'}
          style={{ width: 110 }}
        />
      ),
    },
    {
      title: 'Executions',
      dataIndex: 'usage_count',
    },
    {
      title: 'Avg Latency',
      dataIndex: 'avg_latency_ms',
      render: (ms: number) => `${ms.toFixed(0)}ms`,
    },
    {
      title: 'Issues',
      dataIndex: 'issues',
      render: (issues: string[]) => (
        issues.length > 0
          ? issues.map((issue, index) => <Tag key={`${issue}-${index}`} color="red">{issue}</Tag>)
          : <Text type="secondary">None</Text>
      ),
    },
    {
      title: 'Recommendations',
      dataIndex: 'recommendations',
      render: (recommendations: string[]) => (
        recommendations.length > 0
          ? recommendations.map((recommendation, index) => <Tag key={`${recommendation}-${index}`} color="blue">{recommendation}</Tag>)
          : <Text type="secondary">None</Text>
      ),
    },
    {
      title: 'Proposal',
      dataIndex: 'maintenance_proposal',
      render: (proposal?: MaintenanceProposal | null) => (
        proposal ? (
          <Space direction="vertical" size={2}>
            <Space size={4} wrap>
              <Tag color={proposalColor(proposal.recommended_action)}>{proposal.recommended_action}</Tag>
              <Tag color={proposal.requires_human_review ? 'purple' : 'default'}>
                {proposal.requires_human_review ? 'human review' : 'auto'}
              </Tag>
            </Space>
            <Text type="secondary">{Math.round(proposal.confidence * 100)}% confidence</Text>
            {proposal.patch_hint ? <Text>{proposal.patch_hint}</Text> : null}
          </Space>
        ) : <Text type="secondary">None</Text>
      ),
    },
  ]

  const attentionColumns: TableColumnsType<HealthReport> = [
    ...columns,
    {
      title: 'Actions',
      render: (_, record) => (
        <Button
          size="small"
          type="primary"
          icon={<MedicineBoxOutlined />}
          loading={repairing === record.skill_id}
          onClick={() => repairSkill(record.skill_id)}
        >
          Review candidate
        </Button>
      ),
    },
  ]

  const proposalColumns: TableColumnsType<MaintenanceProposal> = [
    {
      title: 'Proposal',
      dataIndex: 'proposal_id',
      width: 260,
      render: (id: string, record) => (
        <Space direction="vertical" size={2} className="skillwiki-proposal-id-cell">
          <Text code copyable style={{ fontSize: 11 }}>{id.slice(0, 8)}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{record.skill_id}</Text>
        </Space>
      ),
    },
    {
      title: 'Action',
      dataIndex: 'recommended_action',
      width: 150,
      render: (action: string, record) => (
        <Space direction="vertical" size={2}>
          <Space size={4} wrap>
            <Tag color={proposalColor(action)}>{action}</Tag>
            <Tag color={proposalStatusColor(record.status)}>{record.status}</Tag>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {Math.round(record.confidence * 100)}% confidence
          </Text>
        </Space>
      ),
    },
    {
      title: 'Evidence',
      dataIndex: 'evidence',
      width: 360,
      render: (_: string[], record) => (
        <Space direction="vertical" size={4}>
          <Text>{record.root_cause || joinPreview(record.evidence)}</Text>
          {record.patch_hint ? <Text type="secondary">{record.patch_hint}</Text> : null}
          {record.validation_plan?.length ? (
            <Tag color="blue">Validation: {joinPreview(record.validation_plan)}</Tag>
          ) : null}
        </Space>
      ),
    },
    {
      title: 'Governance',
      dataIndex: 'next_action',
      width: 260,
      render: (_, record) => (
        <Space direction="vertical" size={6}>
          {record.status === 'accepted' || record.next_action ? (
            <>
              <Tag color="purple">B review required</Tag>
              <Text type="secondary" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>
                {record.next_action?.endpoint || `/api/v1/lifecycle/${record.skill_id}/propose-maintenance-change`}
              </Text>
            </>
          ) : (
            <Text type="secondary">Waiting for human decision</Text>
          )}
        </Space>
      ),
    },
    {
      title: 'Actions',
      width: 180,
      render: (_, record) => (
        <Space wrap className="skillwiki-table-actions">
          {record.status === 'pending' ? (
            <>
              <Button
                size="small"
                type="primary"
                icon={<CheckCircleOutlined />}
                loading={proposalActionId === record.proposal_id}
                onClick={() => acceptProposal(record)}
              >
                Accept
              </Button>
              <Popconfirm
                title="Reject this proposal?"
                onConfirm={() => rejectProposal(record)}
              >
                <Button
                  size="small"
                  danger
                  icon={<CloseCircleOutlined />}
                  loading={proposalActionId === record.proposal_id}
                >
                  Reject
                </Button>
              </Popconfirm>
            </>
          ) : record.status === 'accepted' ? (
            <Button
              size="small"
              icon={<BranchesOutlined />}
              onClick={() => openVersionControl(record)}
            >
              Version
            </Button>
          ) : (
            <Text type="secondary">No review action</Text>
          )}
        </Space>
      ),
    },
  ]

  if (loading && !health) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" description="Loading health report..." />
      </div>
    )
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, marginBottom: 24, flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0, fontWeight: 700 }}>Feedback & Evolution</h2>
          <Text type="secondary">Review Skill health, run evolution cycles, and create controlled maintenance proposals for unhealthy Skills.</Text>
        </div>
        <Space>
          <Button onClick={loadHealth} loading={loading}>
            Refresh
          </Button>
          <Button onClick={loadProposalQueue} loading={queueLoading}>
            Refresh proposals
          </Button>
          <Button
            type="primary"
            icon={<SyncOutlined spin={cycling} />}
            onClick={runCycle}
            loading={cycling}
            size="large"
          >
            Run Evolution Cycle
          </Button>
        </Space>
      </div>

      {error && (
        <Alert
          type="error"
          showIcon
          closable
          title={error}
          onClose={() => setError(null)}
          style={{ marginBottom: 16 }}
        />
      )}

      {cycleResult && (
        <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} style={{ marginBottom: 16 }}>
          <Alert
            type={countOf(cycleResult.errors) > 0 ? 'warning' : 'success'}
            title="Evolution Cycle Completed"
            description={(
              <Space wrap size={[8, 8]}>
                <Tag color="blue">Tasks {cycleResult.tasks_completed || 0}/{cycleResult.tasks_total || 0}</Tag>
                <Tag color="green">Repair candidates {countOf(cycleResult.repaired)}</Tag>
                <Tag color="red">Deprecated {countOf(cycleResult.deprecated)}</Tag>
                <Tag color="purple">Merged {countOf(cycleResult.merged)}</Tag>
                <Tag color="gold">Split {countOf(cycleResult.split)}</Tag>
                <Tag color="orange">Proposal {countOf(cycleResult.maintenance_proposals)}</Tag>
                <Tag color={cycleResult.tasks_failed ? 'red' : 'default'}>Failed {cycleResult.tasks_failed || 0}</Tag>
              </Space>
            )}
            closable
            onClose={() => setCycleResult(null)}
          />
        </motion.div>
      )}

      {health ? (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            {[
              { label: 'Total', value: health.total_skills, color: '#1677ff' },
              { label: 'Healthy', value: health.healthy_count, color: '#52c41a' },
              { label: 'Degraded', value: health.degraded_count, color: '#faad14' },
              { label: 'Critical', value: health.critical_count, color: '#ff4d4f' },
              { label: 'Stale', value: health.stale_count, color: '#8c8c8c' },
            ].map(({ label, value, color }, index) => (
              <Col xs={12} sm={8} md={4} key={label}>
                <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.06 }}>
                  <Card variant="borderless" style={{ borderRadius: 8, textAlign: 'center', boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
                    <Statistic value={value} styles={{ content: { color, fontWeight: 700 } }} />
                    <div style={{ color: '#666', fontSize: 12 }}>{label}</div>
                  </Card>
                </motion.div>
              </Col>
            ))}
            <Col xs={12} sm={8} md={4}>
              <Card variant="borderless" style={{ borderRadius: 8, textAlign: 'center', boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
                <Progress
                  type="circle"
                  percent={Math.round(health.health_ratio * 100)}
                  size={60}
                  strokeColor="#52c41a"
                />
                <div style={{ color: '#666', fontSize: 12, marginTop: 4 }}>Health Ratio</div>
              </Card>
            </Col>
          </Row>

          <Card
            title={(
              <Space>
                <BranchesOutlined />
                <span>Maintenance Proposal Queue</span>
                <Tag color="gold">{proposalQueue?.pending_count || 0} pending</Tag>
              </Space>
            )}
            variant="borderless"
            style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
          >
            {proposalQueue && proposalQueue.proposals.length > 0 ? (
              <Table
                dataSource={proposalQueue.proposals}
                rowKey="proposal_id"
                size="small"
                loading={queueLoading}
                pagination={{ pageSize: 5 }}
                columns={proposalColumns}
                className="skillwiki-table-contained"
                scroll={{ x: 1210 }}
              />
            ) : (
              <Empty description="No maintenance proposals in the current service lifecycle" />
            )}
          </Card>

          <Card
            title={<><MedicineBoxOutlined /> Skills Needing Attention ({needsAttention.length})</>}
            variant="borderless"
            style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
          >
            {needsAttention.length > 0 ? (
              <Table
                dataSource={needsAttention}
                rowKey="skill_id"
                size="small"
                pagination={false}
                columns={attentionColumns}
                className="skillwiki-table-contained"
                scroll={{ x: 1180 }}
              />
            ) : (
              <Empty description="No degraded or critical Skills right now." />
            )}
          </Card>

          <Card
            title="All Skill Health Reports"
            variant="borderless"
            style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          >
            {health.skill_reports.length > 0 ? (
              <Table
                dataSource={health.skill_reports}
                rowKey="skill_id"
                size="small"
                pagination={{ pageSize: 10 }}
                columns={columns}
                className="skillwiki-table-contained"
                scroll={{ x: 980 }}
              />
            ) : (
              <Empty description="No health report data yet. Release or execute more Skills to generate health assessments." />
            )}
          </Card>
        </>
      ) : (
        <Card variant="borderless" style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
          <Empty description="System health report cannot be loaded right now.">
            <Button type="primary" onClick={loadHealth}>Retry</Button>
          </Empty>
        </Card>
      )}
    </div>
  )
}
