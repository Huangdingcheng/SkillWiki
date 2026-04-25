import { useEffect, useState } from 'react'
import {
  Card, Row, Col, Button, Table, Tag, Progress, Alert,
  Statistic, Badge, Spin, message, Divider,
} from 'antd'
import { SyncOutlined, MedicineBoxOutlined, DeleteOutlined } from '@ant-design/icons'
import { motion } from 'framer-motion'
import { evolutionApi } from '@/api/client'
import type { SystemHealth, HealthReport } from '@/api/types'

const STATUS_COLOR: Record<string, string> = {
  healthy: '#52c41a',
  degraded: '#faad14',
  critical: '#ff4d4f',
  stale: '#d9d9d9',
  unknown: '#8c8c8c',
}

export default function Evolution() {
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [loading, setLoading] = useState(true)
  const [cycling, setCycling] = useState(false)
  const [repairing, setRepairing] = useState<string | null>(null)
  const [cycleResult, setCycleResult] = useState<Record<string, unknown> | null>(null)

  const loadHealth = () => {
    setLoading(true)
    evolutionApi.systemHealth()
      .then(setHealth)
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadHealth() }, [])

  const runCycle = async () => {
    setCycling(true)
    try {
      const result = await evolutionApi.runCycle()
      setCycleResult(result)
      message.success(`演化周期完成：修复 ${(result as { repaired: unknown[] }).repaired?.length || 0} 个，废弃 ${(result as { deprecated: unknown[] }).deprecated?.length || 0} 个`)
      loadHealth()
    } catch {
      message.error('演化周期失败')
    } finally {
      setCycling(false)
    }
  }

  const repairSkill = async (id: string) => {
    setRepairing(id)
    try {
      const result = await evolutionApi.repair(id)
      const r = result as { success: boolean; root_cause: string }
      if (r.success) {
        message.success(`修复成功：${r.root_cause}`)
      } else {
        message.warning('修复建议废弃该 Skill')
      }
      loadHealth()
    } catch {
      message.error('修复失败')
    } finally {
      setRepairing(null)
    }
  }

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>

  const needsAttention = health?.skill_reports.filter(r =>
    r.status === 'degraded' || r.status === 'critical'
  ) || []

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h2 style={{ margin: 0, fontWeight: 700 }}>Feedback & Evolution</h2>
        <Button
          type="primary"
          icon={<SyncOutlined spin={cycling} />}
          onClick={runCycle}
          loading={cycling}
          size="large"
        >
          运行演化周期
        </Button>
      </div>

      {cycleResult && (
        <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} style={{ marginBottom: 16 }}>
          <Alert
            type="success"
            message="演化周期完成"
            description={
              <pre style={{ margin: 0, fontSize: 12 }}>{JSON.stringify(cycleResult, null, 2)}</pre>
            }
            closable
            onClose={() => setCycleResult(null)}
          />
        </motion.div>
      )}

      {/* 系统健康概览 */}
      {health && (
        <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
          {[
            { label: 'Total', value: health.total_skills, color: '#1677ff' },
            { label: 'Healthy', value: health.healthy_count, color: '#52c41a' },
            { label: 'Degraded', value: health.degraded_count, color: '#faad14' },
            { label: 'Critical', value: health.critical_count, color: '#ff4d4f' },
            { label: 'Stale', value: health.stale_count, color: '#8c8c8c' },
          ].map(({ label, value, color }, i) => (
            <Col xs={12} sm={8} md={4} key={label}>
              <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.08 }}>
                <Card bordered={false} style={{ borderRadius: 12, textAlign: 'center', boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
                  <Statistic value={value} valueStyle={{ color, fontWeight: 700 }} />
                  <div style={{ color: '#666', fontSize: 12 }}>{label}</div>
                </Card>
              </motion.div>
            </Col>
          ))}
          <Col xs={12} sm={8} md={4}>
            <Card bordered={false} style={{ borderRadius: 12, textAlign: 'center', boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
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
      )}

      {/* 需要关注的 Skill */}
      {needsAttention.length > 0 && (
        <Card
          title={<><MedicineBoxOutlined /> 需要关注的 Skill ({needsAttention.length})</>}
          bordered={false}
          style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
        >
          <Table
            dataSource={needsAttention}
            rowKey="skill_id"
            size="small"
            pagination={false}
            columns={[
              {
                title: 'Skill',
                dataIndex: 'skill_name',
                render: (n: string) => <strong>{n}</strong>,
              },
              {
                title: '状态',
                dataIndex: 'status',
                render: (s: string) => <Badge color={STATUS_COLOR[s]} text={s} />,
              },
              {
                title: '成功率',
                dataIndex: 'success_rate',
                render: (r: number) => (
                  <Progress
                    percent={Math.round(r * 100)}
                    size="small"
                    strokeColor={r > 0.7 ? '#52c41a' : r > 0.4 ? '#faad14' : '#ff4d4f'}
                    style={{ width: 100 }}
                  />
                ),
              },
              {
                title: '问题',
                dataIndex: 'issues',
                render: (issues: string[]) => issues.map((i, idx) => <Tag key={idx} color="red">{i}</Tag>),
              },
              {
                title: '建议',
                dataIndex: 'recommendations',
                render: (recs: string[]) => recs.map((r, idx) => <Tag key={idx} color="blue">{r}</Tag>),
              },
              {
                title: '操作',
                render: (_: unknown, r: HealthReport) => (
                  <Button
                    size="small"
                    type="primary"
                    icon={<MedicineBoxOutlined />}
                    loading={repairing === r.skill_id}
                    onClick={() => repairSkill(r.skill_id)}
                  >
                    修复
                  </Button>
                ),
              },
            ]}
          />
        </Card>
      )}

      {/* 全部健康报告 */}
      <Card
        title="全部 Skill 健康报告"
        bordered={false}
        extra={<Button size="small" onClick={loadHealth}>刷新</Button>}
        style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
      >
        <Table
          dataSource={health?.skill_reports || []}
          rowKey="skill_id"
          size="small"
          pagination={{ pageSize: 10 }}
          columns={[
            { title: 'Skill', dataIndex: 'skill_name', render: (n: string) => <strong>{n}</strong> },
            {
              title: '状态',
              dataIndex: 'status',
              render: (s: string) => <Badge color={STATUS_COLOR[s] || '#8c8c8c'} text={s} />,
            },
            {
              title: '成功率',
              dataIndex: 'success_rate',
              render: (r: number) => `${(r * 100).toFixed(1)}%`,
            },
            { title: '执行次数', dataIndex: 'usage_count' },
            {
              title: '平均延迟',
              dataIndex: 'avg_latency_ms',
              render: (ms: number) => `${ms.toFixed(0)}ms`,
            },
          ]}
        />
      </Card>
    </div>
  )
}
