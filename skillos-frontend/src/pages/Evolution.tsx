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
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'
import { MedicineBoxOutlined, SyncOutlined } from '@ant-design/icons'
import { motion } from 'framer-motion'
import { evolutionApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import type { HealthReport, SystemHealth } from '@/api/types'

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
}

type RepairResult = {
  success?: boolean
  root_cause?: string
  should_deprecate?: boolean
}

function countOf(value: unknown[] | undefined) {
  return Array.isArray(value) ? value.length : 0
}

export default function Evolution() {
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [cycling, setCycling] = useState(false)
  const [repairing, setRepairing] = useState<string | null>(null)
  const [cycleResult, setCycleResult] = useState<EvolutionCycleResult | null>(null)

  const loadHealth = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await evolutionApi.systemHealth()
      setHealth(data)
    } catch (err) {
      setError(getApiErrorMessage(err, '加载健康报告失败'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void loadHealth() }, [])

  const runCycle = async () => {
    setCycling(true)
    try {
      const result = await evolutionApi.runCycle() as EvolutionCycleResult
      setCycleResult(result)
      message.success(`演化周期完成：修复 ${countOf(result.repaired)} 个，废弃 ${countOf(result.deprecated)} 个`)
      await loadHealth()
    } catch (err) {
      message.error(getApiErrorMessage(err, '演化周期执行失败'))
    } finally {
      setCycling(false)
    }
  }

  const repairSkill = async (id: string) => {
    setRepairing(id)
    try {
      const result = await evolutionApi.repair(id) as RepairResult
      if (result.success) {
        message.success(`修复成功：${result.root_cause || '已生成修复结果'}`)
      } else if (result.should_deprecate) {
        message.warning('修复建议：该 Skill 可考虑废弃或替换')
      } else {
        message.warning('修复未完成，请查看健康报告建议')
      }
      await loadHealth()
    } catch (err) {
      message.error(getApiErrorMessage(err, '修复 Skill 失败'))
    } finally {
      setRepairing(null)
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
      render: (name: string) => <Text strong>{name}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      render: (status: string) => <Badge color={STATUS_COLOR[status] || STATUS_COLOR.unknown} text={STATUS_LABEL[status] || status} />,
    },
    {
      title: '成功率',
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
      title: '执行次数',
      dataIndex: 'usage_count',
    },
    {
      title: '平均延迟',
      dataIndex: 'avg_latency_ms',
      render: (ms: number) => `${ms.toFixed(0)}ms`,
    },
    {
      title: '问题',
      dataIndex: 'issues',
      render: (issues: string[]) => (
        issues.length > 0
          ? issues.map((issue, index) => <Tag key={`${issue}-${index}`} color="red">{issue}</Tag>)
          : <Text type="secondary">无</Text>
      ),
    },
    {
      title: '建议',
      dataIndex: 'recommendations',
      render: (recommendations: string[]) => (
        recommendations.length > 0
          ? recommendations.map((recommendation, index) => <Tag key={`${recommendation}-${index}`} color="blue">{recommendation}</Tag>)
          : <Text type="secondary">无</Text>
      ),
    },
  ]

  const attentionColumns: TableColumnsType<HealthReport> = [
    ...columns,
    {
      title: '操作',
      render: (_, record) => (
        <Button
          size="small"
          type="primary"
          icon={<MedicineBoxOutlined />}
          loading={repairing === record.skill_id}
          onClick={() => repairSkill(record.skill_id)}
        >
          修复
        </Button>
      ),
    },
  ]

  if (loading && !health) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" tip="加载健康报告..." />
      </div>
    )
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, marginBottom: 24, flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0, fontWeight: 700 }}>Feedback & Evolution</h2>
          <Text type="secondary">查看 Skill 健康状态，触发演化周期，并对异常 Skill 发起修复。</Text>
        </div>
        <Space>
          <Button onClick={loadHealth} loading={loading}>
            刷新
          </Button>
          <Button
            type="primary"
            icon={<SyncOutlined spin={cycling} />}
            onClick={runCycle}
            loading={cycling}
            size="large"
          >
            运行演化周期
          </Button>
        </Space>
      </div>

      {error && (
        <Alert
          type="error"
          showIcon
          closable
          message={error}
          onClose={() => setError(null)}
          style={{ marginBottom: 16 }}
        />
      )}

      {cycleResult && (
        <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} style={{ marginBottom: 16 }}>
          <Alert
            type={countOf(cycleResult.errors) > 0 ? 'warning' : 'success'}
            message="演化周期完成"
            description={(
              <Space wrap size={[8, 8]}>
                <Tag color="blue">任务 {cycleResult.tasks_completed || 0}/{cycleResult.tasks_total || 0}</Tag>
                <Tag color="green">修复 {countOf(cycleResult.repaired)}</Tag>
                <Tag color="red">废弃 {countOf(cycleResult.deprecated)}</Tag>
                <Tag color="purple">合并 {countOf(cycleResult.merged)}</Tag>
                <Tag color="gold">拆分 {countOf(cycleResult.split)}</Tag>
                <Tag color={cycleResult.tasks_failed ? 'red' : 'default'}>失败 {cycleResult.tasks_failed || 0}</Tag>
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
                  <Card bordered={false} style={{ borderRadius: 8, textAlign: 'center', boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
                    <Statistic value={value} valueStyle={{ color, fontWeight: 700 }} />
                    <div style={{ color: '#666', fontSize: 12 }}>{label}</div>
                  </Card>
                </motion.div>
              </Col>
            ))}
            <Col xs={12} sm={8} md={4}>
              <Card bordered={false} style={{ borderRadius: 8, textAlign: 'center', boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
                <Progress
                  type="circle"
                  percent={Math.round(health.health_ratio * 100)}
                  width={60}
                  strokeColor="#52c41a"
                />
                <div style={{ color: '#666', fontSize: 12, marginTop: 4 }}>Health Ratio</div>
              </Card>
            </Col>
          </Row>

          <Card
            title={<><MedicineBoxOutlined /> 需要关注的 Skill ({needsAttention.length})</>}
            bordered={false}
            style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
          >
            {needsAttention.length > 0 ? (
              <Table
                dataSource={needsAttention}
                rowKey="skill_id"
                size="small"
                pagination={false}
                columns={attentionColumns}
              />
            ) : (
              <Empty description="当前没有 degraded 或 critical 的 Skill" />
            )}
          </Card>

          <Card
            title="全部 Skill 健康报告"
            bordered={false}
            style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          >
            {health.skill_reports.length > 0 ? (
              <Table
                dataSource={health.skill_reports}
                rowKey="skill_id"
                size="small"
                pagination={{ pageSize: 10 }}
                columns={columns}
              />
            ) : (
              <Empty description="暂无健康报告数据。释放或执行更多 Skill 后，这里会出现健康评估结果。" />
            )}
          </Card>
        </>
      ) : (
        <Card bordered={false} style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
          <Empty description="暂时无法加载系统健康报告">
            <Button type="primary" onClick={loadHealth}>重试</Button>
          </Empty>
        </Card>
      )}
    </div>
  )
}
