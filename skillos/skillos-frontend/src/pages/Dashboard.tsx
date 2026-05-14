import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Card, Col, Row, Statistic, Progress, Tag, Table, Badge, Spin, Timeline, Button, Empty, Divider, Space } from 'antd'
import {
  RocketOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  ThunderboltOutlined,
  ClearOutlined,
  WifiOutlined,
  StarOutlined,
  SyncOutlined,
  BulbOutlined,
} from '@ant-design/icons'
import { motion } from 'framer-motion'
import { statsApi, evolutionApi } from '@/api/client'
import type { EvolutionStats } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import { useAppStore } from '@/store/appStore'
import type { OverviewStats, HealthReport } from '@/api/types'

const stateColors: Record<string, string> = {
  S4: 'green', S2: 'blue', S3: 'cyan', S1: 'orange',
  S5: 'gold', S6: 'red', S7: 'default', S0: 'purple',
}
const stateLabels: Record<string, string> = {
  S0: 'Raw', S1: 'Candidate', S2: 'Draft', S3: 'Verified',
  S4: 'Released', S5: 'Degraded', S6: 'Deprecated', S7: 'Archived',
}

const healthColors: Record<string, string> = {
  healthy: '#52c41a',
  degraded: '#faad14',
  critical: '#ff4d4f',
  stale: '#d9d9d9',
  unknown: '#8c8c8c',
}

const REFRESH_EVENT_TYPES = new Set([
  'plan_completed',
  'health_degraded',
  'health_critical',
  'evolution_cycle_done',
])

function formatEventTime(timestamp: string) {
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleTimeString()
}

export default function Dashboard() {
  const [stats, setStats] = useState<OverviewStats | null>(null)
  const [reports, setReports] = useState<HealthReport[]>([])
  const [evoStats, setEvoStats] = useState<EvolutionStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)
  const { wsEvents, clearWsEvents } = useAppStore()

  const loadDashboardData = useCallback(async (initial = false) => {
    if (initial) setLoading(true)
    else setRefreshing(true)

    try {
      const [s, h, evo] = await Promise.all([
        statsApi.overview(),
        evolutionApi.systemHealth().catch(() => null),
        statsApi.evolutionStats().catch(() => null),
      ])

      setStats(s)
      setReports(h?.skill_reports.slice(0, 10) ?? [])
      setEvoStats(evo)
      setError(null)
      setLastUpdated(new Date().toLocaleTimeString())
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, 'Dashboard 数据加载失败'))
    } finally {
      if (initial) setLoading(false)
      else setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void loadDashboardData(true)
    const timer = window.setInterval(() => {
      void loadDashboardData(false)
    }, 15_000)
    return () => window.clearInterval(timer)
  }, [loadDashboardData])

  const refreshSignal = useMemo(() => {
    const event = wsEvents.find(e => REFRESH_EVENT_TYPES.has(e.type))
    return event ? `${event.type}:${event.timestamp}` : ''
  }, [wsEvents])

  useEffect(() => {
    if (refreshSignal) void loadDashboardData(false)
  }, [loadDashboardData, refreshSignal])

  const feedEvents = useMemo(
    () => wsEvents.filter(e => e.type !== 'pong' && e.type !== 'connected').slice(0, 30),
    [wsEvents],
  )

  if (loading && !stats) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>
  if (!stats) {
    return (
      <div style={{ padding: 24 }}>
        <Alert
          type="error"
          showIcon
          title="Dashboard 数据加载失败"
          description={error || '请检查后端服务是否可用'}
          action={<Button size="small" onClick={() => loadDashboardData(true)}>重试</Button>}
        />
      </div>
    )
  }

  const healthyPct = stats.total_skills > 0
    ? Math.round((stats.by_state['S4'] || 0) / stats.total_skills * 100)
    : 0

  const cardVariants = {
    hidden: { opacity: 0, y: 20 },
    visible: (i: number) => ({ opacity: 1, y: 0, transition: { delay: i * 0.08 } }),
  }

  return (
    <div style={{ padding: '24px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
        <motion.h2
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          style={{ margin: 0, fontSize: 22, fontWeight: 700 }}
        >
          SkillOS Dashboard
        </motion.h2>
        <Space>
          <span style={{ color: '#999', fontSize: 12 }}>
            最后更新：{lastUpdated || '尚未刷新'}
          </span>
          <Button
            size="small"
            icon={<SyncOutlined spin={refreshing} />}
            loading={refreshing}
            onClick={() => loadDashboardData(false)}
          >
            刷新
          </Button>
        </Space>
      </div>

      {error && (
        <Alert
          type="warning"
          showIcon
          closable
          title="最近一次刷新失败"
          description={error}
          style={{ marginBottom: 16 }}
          onClose={() => setError(null)}
        />
      )}

      {/* 核心指标 */}
      <Row gutter={[16, 16]}>
        {[
          { title: 'Total Skills', value: stats.total_skills, icon: <RocketOutlined />, color: '#1677ff', i: 0 },
          { title: 'Released', value: stats.by_state['S4'] || 0, icon: <CheckCircleOutlined />, color: '#52c41a', i: 1 },
          { title: 'Degraded', value: stats.by_state['S5'] || 0, icon: <WarningOutlined />, color: '#faad14', i: 2 },
          { title: 'Total Executions', value: stats.total_executions, icon: <ThunderboltOutlined />, color: '#722ed1', i: 3 },
        ].map(({ title, value, icon, color, i }) => (
          <Col xs={24} sm={12} lg={6} key={title}>
            <motion.div custom={i} initial="hidden" animate="visible" variants={cardVariants}>
              <Card variant="borderless" style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
                <Statistic
                  title={title}
                  value={value}
                  prefix={<span style={{ color }}>{icon}</span>}
                  styles={{ content: { color, fontWeight: 700 } }}
                />
              </Card>
            </motion.div>
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        {/* 类型分布 */}
        <Col xs={24} md={8}>
          <motion.div custom={4} initial="hidden" animate="visible" variants={cardVariants}>
            <Card title="Skill Types" variant="borderless" style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
              {Object.entries(stats.by_type).map(([type, count]) => (
                <div key={type} style={{ marginBottom: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <Tag color={type === 'atomic' ? 'blue' : type === 'functional' ? 'purple' : 'gold'}>
                      {type.toUpperCase()}
                    </Tag>
                    <span style={{ fontWeight: 600 }}>{count}</span>
                  </div>
                  <Progress
                    percent={stats.total_skills > 0 ? Math.round(count / stats.total_skills * 100) : 0}
                    strokeColor={type === 'atomic' ? '#1677ff' : type === 'functional' ? '#722ed1' : '#faad14'}
                    showInfo={false}
                    size="small"
                  />
                </div>
              ))}
            </Card>
          </motion.div>
        </Col>

        {/* 状态分布 */}
        <Col xs={24} md={8}>
          <motion.div custom={5} initial="hidden" animate="visible" variants={cardVariants}>
            <Card title="State Distribution" variant="borderless" style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
              {Object.entries(stats.by_state).map(([state, count]) => (
                <div key={state} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                  <Tag color={stateColors[state] || 'default'}>{stateLabels[state] || state}</Tag>
                  <Badge count={count} style={{ backgroundColor: '#1677ff' }} />
                </div>
              ))}
            </Card>
          </motion.div>
        </Col>

        {/* 健康度 */}
        <Col xs={24} md={8}>
          <motion.div custom={6} initial="hidden" animate="visible" variants={cardVariants}>
            <Card title="System Health" variant="borderless" style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
              <div style={{ textAlign: 'center', marginBottom: 16 }}>
                <Progress
                  type="circle"
                  percent={healthyPct}
                  strokeColor={{ '0%': '#52c41a', '100%': '#1677ff' }}
                  format={p => <span style={{ fontWeight: 700 }}>{p}%<br /><small>Released</small></span>}
                />
              </div>
              <Statistic
                title="Avg Success Rate"
                value={(stats.avg_success_rate * 100).toFixed(1)}
                suffix="%"
                styles={{ content: { color: stats.avg_success_rate > 0.8 ? '#52c41a' : '#faad14' } }}
              />
            </Card>
          </motion.div>
        </Col>
      </Row>

      {/* 健康报告表格 */}
      <motion.div custom={7} initial="hidden" animate="visible" variants={cardVariants} style={{ marginTop: 16 }}>
        <Card title="Skill Health Overview" variant="borderless" style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
          <Table
            dataSource={reports}
            rowKey="skill_id"
            size="small"
            pagination={false}
            columns={[
              {
                title: 'Skill',
                dataIndex: 'skill_name',
                render: (name: string) => <strong>{name}</strong>,
              },
              {
                title: 'Status',
                dataIndex: 'status',
                render: (s: string) => (
                  <Badge color={healthColors[s] || '#8c8c8c'} text={s} />
                ),
              },
              {
                title: 'Success Rate',
                dataIndex: 'success_rate',
                render: (r: number) => (
                  <Progress
                    percent={Math.round(r * 100)}
                    size="small"
                    strokeColor={r > 0.8 ? '#52c41a' : r > 0.5 ? '#faad14' : '#ff4d4f'}
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
            ]}
          />
        </Card>
      </motion.div>

      {/* 演化指标面板 */}
      {evoStats && (
        <motion.div custom={8} initial="hidden" animate="visible" variants={cardVariants} style={{ marginTop: 16 }}>
          <Card
            title={<span><SyncOutlined style={{ color: '#1677ff', marginRight: 6 }} />Self-Evolution Metrics</span>}
            variant="borderless"
            style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          >
            <Row gutter={[16, 16]}>
              <Col xs={12} sm={6}>
                <div style={{ textAlign: 'center' }}>
                  <Statistic
                    title="Auto-Generated Skills"
                    value={evoStats.auto_generated}
                    prefix={<BulbOutlined style={{ color: '#faad14' }} />}
                    styles={{ content: { color: '#faad14', fontWeight: 700 } }}
                  />
                </div>
              </Col>
              <Col xs={12} sm={6}>
                <div style={{ textAlign: 'center' }}>
                  <Statistic
                    title="Avg Reuse Rate"
                    value={evoStats.avg_reuse_rate.toFixed(1)}
                    suffix="x"
                    prefix={<StarOutlined style={{ color: '#722ed1' }} />}
                    styles={{ content: { color: '#722ed1', fontWeight: 700 } }}
                  />
                </div>
              </Col>
              <Col xs={12} sm={6}>
                <div style={{ textAlign: 'center' }}>
                  <Statistic
                    title="Version Improved"
                    value={evoStats.version_improved_count}
                    prefix={<RocketOutlined style={{ color: '#52c41a' }} />}
                    styles={{ content: { color: '#52c41a', fontWeight: 700 } }}
                  />
                </div>
              </Col>
              <Col xs={12} sm={6}>
                <div style={{ textAlign: 'center' }}>
                  <Statistic
                    title="Avg Success Rate"
                    value={(evoStats.avg_success_rate * 100).toFixed(1)}
                    suffix="%"
                    prefix={<CheckCircleOutlined style={{ color: '#1677ff' }} />}
                    styles={{ content: { color: '#1677ff', fontWeight: 700 } }}
                  />
                </div>
              </Col>
            </Row>
            <Divider style={{ margin: '16px 0 12px' }} />
            <div style={{ marginBottom: 8 }}>
              <Tag color="blue">Skills by Category</Tag>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {Object.entries(evoStats.skills_by_category).map(([cat, count]) => (
                <Tag key={cat} color={cat === 'atomic' ? 'blue' : cat === 'functional' ? 'purple' : cat === 'strategic' ? 'gold' : 'default'}>
                  {cat}: {count}
                </Tag>
              ))}
            </div>
            {evoStats.recent_activity.length > 0 && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <div style={{ marginBottom: 8 }}>
                  <Tag color="green">Recent Activity</Tag>
                </div>
                <div style={{ maxHeight: 160, overflowY: 'auto' }}>
                  {evoStats.recent_activity.map((a, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', borderBottom: i < evoStats.recent_activity.length - 1 ? '1px solid #f0f0f0' : 'none' }}>
                      <Badge color={a.event === 'updated' ? 'blue' : 'green'} />
                      <span style={{ fontSize: 12, flex: 1 }}><strong>{a.name}</strong> — {a.event}</span>
                      <Tag style={{ fontSize: 10 }}>{a.state}</Tag>
                    </div>
                  ))}
                </div>
              </>
            )}
          </Card>
        </motion.div>
      )}

      {/* Agent 动态 Feed */}
      <motion.div custom={8} initial="hidden" animate="visible" variants={cardVariants} style={{ marginTop: 16 }}>
        <Card
          title={<span><WifiOutlined style={{ color: '#52c41a', marginRight: 6 }} />Agent 实时动态</span>}
          variant="borderless"
          style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          extra={
            <Button size="small" icon={<ClearOutlined />} onClick={clearWsEvents}>
              清空
            </Button>
          }
        >
          {feedEvents.length === 0 ? (
            <Empty description="暂无事件，执行 Agent 任务后将在此显示实时动态" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          ) : (
            <div style={{ maxHeight: 320, overflowY: 'auto' }}>
              <Timeline
                items={feedEvents
                  .map(e => {
                    const isError = e.type.includes('error') || e.type.includes('fail')
                    const isSuccess = e.type.includes('success') || e.type.includes('complete') || e.type.includes('release')
                    return {
                      color: isError ? 'red' : isSuccess ? 'green' : 'blue',
                      children: (
                        <div>
                          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                            <Tag color={isError ? 'red' : isSuccess ? 'green' : 'blue'} style={{ fontSize: 11 }}>
                              {e.type}
                            </Tag>
                            <span style={{ fontSize: 11, color: '#999' }}>{formatEventTime(e.timestamp)}</span>
                          </div>
                          {e.payload !== null && e.payload !== undefined && typeof e.payload === 'object' && (
                            <div style={{ fontSize: 11, color: '#666', marginTop: 2 }}>
                              {JSON.stringify(e.payload as Record<string, unknown>).slice(0, 120)}
                            </div>
                          )}
                        </div>
                      ),
                    }
                  })}
              />
            </div>
          )}
        </Card>
      </motion.div>
    </div>
  )
}
