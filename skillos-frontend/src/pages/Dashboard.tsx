import { useEffect, useState } from 'react'
import { Card, Col, Row, Statistic, Progress, Tag, Badge, Spin, Divider, Table, Segmented } from 'antd'
import {
  RocketOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  ThunderboltOutlined,
  StarOutlined,
  SyncOutlined,
  BulbOutlined,
} from '@ant-design/icons'
import { motion } from 'framer-motion'
import { statsApi, evolutionApi } from '@/api/client'
import type { EvolutionStats } from '@/api/client'
import type { HealthReport, OverviewStats, SkillVisibility } from '@/api/types'

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

export default function Dashboard() {
  const [stats, setStats] = useState<OverviewStats | null>(null)
  const [reports, setReports] = useState<HealthReport[]>([])
  const [healthVisibility, setHealthVisibility] = useState<SkillVisibility | 'all'>('user')
  const [evoStats, setEvoStats] = useState<EvolutionStats | null>(null)
  const [statsLoading, setStatsLoading] = useState(true)
  const [healthLoading, setHealthLoading] = useState(true)
  const [evoLoading, setEvoLoading] = useState(true)

  useEffect(() => {
    setStatsLoading(true)
    statsApi.overview()
      .then(setStats)
      .finally(() => setStatsLoading(false))
  }, [])

  useEffect(() => {
    setHealthLoading(true)
    evolutionApi.systemHealth({ visibility: healthVisibility })
      .then(h => setReports(h.skill_reports.slice(0, 6)))
      .finally(() => setHealthLoading(false))
  }, [healthVisibility])

  useEffect(() => {
    setEvoLoading(true)
    statsApi.evolutionStats()
      .then(setEvoStats)
      .catch(() => setEvoStats(null))
      .finally(() => setEvoLoading(false))
  }, [])

  const emptyStats: OverviewStats = {
    total_skills: 0,
    by_state: {},
    by_type: {},
    total_executions: 0,
    avg_success_rate: 1,
    graph_stats: {},
  }
  const displayStats = stats || emptyStats
  const healthyPct = displayStats.total_skills > 0
    ? Math.round((displayStats.by_state['S4'] || 0) / displayStats.total_skills * 100)
    : 0

  const cardVariants = {
    hidden: { opacity: 0, y: 20 },
    visible: (i: number) => ({ opacity: 1, y: 0, transition: { delay: i * 0.08 } }),
  }

  return (
    <div style={{ padding: '24px' }}>
      <motion.h2
        initial={{ opacity: 0, x: -20 }}
        animate={{ opacity: 1, x: 0 }}
        style={{ marginBottom: 24, fontSize: 22, fontWeight: 700 }}
      >
        SkillOS Dashboard
      </motion.h2>

      {/* 核心指标 */}
      <Row gutter={[16, 16]}>
        {[
          { title: 'Total Skills', value: displayStats.total_skills, icon: <RocketOutlined />, color: '#1677ff', i: 0 },
          { title: 'Released', value: displayStats.by_state['S4'] || 0, icon: <CheckCircleOutlined />, color: '#52c41a', i: 1 },
          { title: 'Degraded', value: displayStats.by_state['S5'] || 0, icon: <WarningOutlined />, color: '#faad14', i: 2 },
          { title: 'Total Executions', value: displayStats.total_executions, icon: <ThunderboltOutlined />, color: '#722ed1', i: 3 },
        ].map(({ title, value, icon, color, i }) => (
          <Col xs={24} sm={12} lg={6} key={title}>
            <motion.div custom={i} initial="hidden" animate="visible" variants={cardVariants}>
              <Card bordered={false} style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
                <Statistic
                  title={title}
                  value={value}
                  loading={statsLoading}
                  prefix={<span style={{ color }}>{icon}</span>}
                  valueStyle={{ color, fontWeight: 700 }}
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
            <Card title="Skill Types" bordered={false} style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
              <Spin spinning={statsLoading}>
              {Object.entries(displayStats.by_type).length === 0 && !statsLoading && (
                <div style={{ color: '#8c8c8c' }}>No type data yet.</div>
              )}
              {Object.entries(displayStats.by_type).map(([type, count]) => (
                <div key={type} style={{ marginBottom: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <Tag color={type === 'atomic' ? 'blue' : type === 'functional' ? 'purple' : 'gold'}>
                      {type.toUpperCase()}
                    </Tag>
                    <span style={{ fontWeight: 600 }}>{count}</span>
                  </div>
                  <Progress
                    percent={displayStats.total_skills ? Math.round(count / displayStats.total_skills * 100) : 0}
                    strokeColor={type === 'atomic' ? '#1677ff' : type === 'functional' ? '#722ed1' : '#faad14'}
                    showInfo={false}
                    size="small"
                  />
                </div>
              ))}
              </Spin>
            </Card>
          </motion.div>
        </Col>

        {/* 状态分布 */}
        <Col xs={24} md={8}>
          <motion.div custom={5} initial="hidden" animate="visible" variants={cardVariants}>
            <Card title="State Distribution" bordered={false} style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
              <Spin spinning={statsLoading}>
              {Object.entries(displayStats.by_state).length === 0 && !statsLoading && (
                <div style={{ color: '#8c8c8c' }}>No state data yet.</div>
              )}
              {Object.entries(displayStats.by_state).map(([state, count]) => (
                <div key={state} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                  <Tag color={stateColors[state] || 'default'}>{stateLabels[state] || state}</Tag>
                  <Badge count={count} style={{ backgroundColor: '#1677ff' }} />
                </div>
              ))}
              </Spin>
            </Card>
          </motion.div>
        </Col>

        {/* 健康度 */}
        <Col xs={24} md={8}>
          <motion.div custom={6} initial="hidden" animate="visible" variants={cardVariants}>
            <Card title="System Health" bordered={false} style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
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
                value={(displayStats.avg_success_rate * 100).toFixed(1)}
                suffix="%"
                loading={statsLoading}
                valueStyle={{ color: displayStats.avg_success_rate > 0.8 ? '#52c41a' : '#faad14' }}
              />
            </Card>
          </motion.div>
        </Col>
      </Row>

      <motion.div custom={7} initial="hidden" animate="visible" variants={cardVariants} style={{ marginTop: 16 }}>
        <Card
          title="Skill Health Preview"
          bordered={false}
          style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          extra={
            <Segmented
              size="small"
              value={healthVisibility}
              onChange={value => setHealthVisibility(value as SkillVisibility | 'all')}
              options={[
                { label: 'User', value: 'user' },
                { label: 'Kernel', value: 'kernel' },
                { label: 'All', value: 'all' },
              ]}
            />
          }
        >
          <Table
            dataSource={reports}
            loading={healthLoading}
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
                render: (s: string) => <Badge color={healthColors[s] || '#8c8c8c'} text={s} />,
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
              { title: 'Executions', dataIndex: 'usage_count' },
            ]}
          />
          <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 8 }}>
            Full review reports and real runtime evaluation live in the Evaluation page.
          </div>
        </Card>
      </motion.div>

      {/* 演化指标面板 */}
      <Spin spinning={evoLoading}>
      {evoStats && (
        <motion.div custom={8} initial="hidden" animate="visible" variants={cardVariants} style={{ marginTop: 16 }}>
          <Card
            title={<span><SyncOutlined style={{ color: '#1677ff', marginRight: 6 }} />Self-Evolution Metrics</span>}
            bordered={false}
            style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          >
            <Row gutter={[16, 16]}>
              <Col xs={12} sm={6}>
                <div style={{ textAlign: 'center' }}>
                  <Statistic
                    title="Auto-Generated Skills"
                    value={evoStats.auto_generated}
                    prefix={<BulbOutlined style={{ color: '#faad14' }} />}
                    valueStyle={{ color: '#faad14', fontWeight: 700 }}
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
                    valueStyle={{ color: '#722ed1', fontWeight: 700 }}
                  />
                </div>
              </Col>
              <Col xs={12} sm={6}>
                <div style={{ textAlign: 'center' }}>
                  <Statistic
                    title="Version Improved"
                    value={evoStats.version_improved_count}
                    prefix={<RocketOutlined style={{ color: '#52c41a' }} />}
                    valueStyle={{ color: '#52c41a', fontWeight: 700 }}
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
                    valueStyle={{ color: '#1677ff', fontWeight: 700 }}
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
      </Spin>

    </div>
  )
}
