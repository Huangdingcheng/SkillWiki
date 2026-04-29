import { useEffect, useState } from 'react'
import {
  Card, Select, Table, Tag, Button, Space, Typography,
  Timeline, Badge, Descriptions, Drawer, Popconfirm, message, Row, Col, Collapse, Empty,
} from 'antd'
import {
  BranchesOutlined, TagOutlined, HistoryOutlined,
  PlusOutlined, RocketOutlined, DiffOutlined,
} from '@ant-design/icons'
import { motion } from 'framer-motion'
import { skillsApi, lifecycleApi } from '@/api/client'
import type { SkillSummary } from '@/api/types'

const { Text } = Typography

const STATE_COLOR: Record<string, string> = {
  S4: 'green', S3: 'cyan', S2: 'blue', S1: 'orange',
  S5: 'gold', S6: 'red', S7: 'default', S0: 'purple',
}
const STATE_LABEL: Record<string, string> = {
  S0: 'Raw', S1: 'Candidate', S2: 'Draft', S3: 'Verified',
  S4: 'Released', S5: 'Degraded', S6: 'Deprecated', S7: 'Archived',
}

interface DiffLine {
  field: string
  type: 'modified' | 'added' | 'removed'
  old_value: string
  new_value: string
  old_lines: string[]
  new_lines: string[]
}

interface DiffData {
  skill_id: string
  skill_name: string
  current_version: string
  history: {
    record_id: string
    from_version: string
    to_version: string
    change_type: string
    summary: string
    author: string
    created_at: string
    diff: DiffLine[]
    is_breaking: boolean
  }[]
}

function semverCompare(a: string, b: string) {
  const pa = a.split('.').map(Number)
  const pb = b.split('.').map(Number)
  for (let i = 0; i < 3; i++) {
    if ((pa[i] || 0) !== (pb[i] || 0)) return (pb[i] || 0) - (pa[i] || 0)
  }
  return 0
}

function DiffView({ lines }: { lines: DiffLine[] }) {
  if (!lines.length) return <Empty description="无变更记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  return (
    <div style={{ fontFamily: 'monospace', fontSize: 12 }}>
      {lines.map((line, i) => (
        <div key={i} style={{ marginBottom: 12 }}>
          <div style={{ fontWeight: 600, color: '#1677ff', marginBottom: 4 }}>
            {line.field}
            {line.type === 'modified' && <Tag color="orange" style={{ marginLeft: 8 }}>modified</Tag>}
            {line.type === 'added' && <Tag color="green" style={{ marginLeft: 8 }}>added</Tag>}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            {line.old_lines.length > 0 && (
              <div style={{ flex: 1, background: '#fff1f0', borderRadius: 4, padding: '4px 8px', border: '1px solid #ffccc7' }}>
                {line.old_lines.map((l, j) => (
                  <div key={j} style={{ color: '#cf1322' }}>- {l}</div>
                ))}
              </div>
            )}
            {line.new_lines.length > 0 && (
              <div style={{ flex: 1, background: '#f6ffed', borderRadius: 4, padding: '4px 8px', border: '1px solid #b7eb8f' }}>
                {line.new_lines.map((l, j) => (
                  <div key={j} style={{ color: '#389e0d' }}>+ {l}</div>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

export default function VersionControl() {
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [versions, setVersions] = useState<SkillSummary[]>([])
  const [loadingVersions, setLoadingVersions] = useState(false)
  const [bumpLoading, setBumpLoading] = useState(false)
  const [drawerSkill, setDrawerSkill] = useState<SkillSummary | null>(null)
  const [diffData, setDiffData] = useState<DiffData | null>(null)
  const [loadingDiff, setLoadingDiff] = useState(false)

  useEffect(() => {
    skillsApi.list({ limit: 200 }).then(setSkills)
  }, [])

  const loadVersions = async (id: string) => {
    setLoadingVersions(true)
    try {
      const vs = await skillsApi.versions(id)
      setVersions(vs.sort((a, b) => semverCompare(a.version, b.version)))
    } finally {
      setLoadingVersions(false)
    }
  }

  const loadDiff = async (id: string) => {
    setLoadingDiff(true)
    try {
      const data = await lifecycleApi.getDiff(id)
      setDiffData(data as unknown as DiffData)
    } catch {
      message.error('获取 diff 失败')
    } finally {
      setLoadingDiff(false)
    }
  }

  const handleSelect = (id: string) => {
    setSelectedId(id)
    setDiffData(null)
    loadVersions(id)
  }

  const handleBump = async (bump: 'major' | 'minor' | 'patch') => {
    if (!selectedId) return
    setBumpLoading(true)
    try {
      const newSkill = await lifecycleApi.newVersion(selectedId, bump)
      message.success(`已创建新版本 v${newSkill.version}`)
      const allSkills = await skillsApi.list({ limit: 200 })
      setSkills(allSkills)
      loadVersions(selectedId)
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '创建失败')
    } finally {
      setBumpLoading(false)
    }
  }

  const handleRelease = async (id: string) => {
    try {
      await lifecycleApi.release(id)
      message.success('已发布')
      if (selectedId) loadVersions(selectedId)
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '发布失败')
    }
  }

  const selectedSkill = selectedId ? skills.find(s => s.skill_id === selectedId) : null

  const columns = [
    {
      title: 'Version',
      dataIndex: 'version',
      render: (v: string, r: SkillSummary) => (
        <Space>
          <TagOutlined style={{ color: '#1677ff' }} />
          <Text code style={{ cursor: 'pointer' }} onClick={() => setDrawerSkill(r)}>v{v}</Text>
          {r.skill_id === selectedId && <Tag color="blue">HEAD</Tag>}
        </Space>
      ),
    },
    {
      title: 'State',
      dataIndex: 'state',
      render: (s: string) => <Badge color={STATE_COLOR[s] || 'default'} text={STATE_LABEL[s] || s} />,
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      render: (t: string) => new Date(t).toLocaleString(),
    },
    {
      title: 'Success Rate',
      dataIndex: 'metrics',
      render: (m: SkillSummary['metrics']) =>
        m.total_executions >= 5
          ? `${(m.success_rate * 100).toFixed(1)}%`
          : <Text type="secondary">N/A</Text>,
    },
    {
      title: 'Actions',
      render: (_: unknown, r: SkillSummary) => (
        <Space>
          {r.state === 'S2' || r.state === 'S3' ? (
            <Popconfirm title="确认发布此版本？" onConfirm={() => handleRelease(r.skill_id)}>
              <Button size="small" type="primary" icon={<RocketOutlined />}>发布</Button>
            </Popconfirm>
          ) : null}
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h2 style={{ fontWeight: 700, marginBottom: 4 }}>版本管理</h2>
        <p style={{ color: '#666', marginBottom: 24 }}>
          Git 式 Skill 版本控制 — 查看版本历史、创建新版本、管理发布。
        </p>
      </motion.div>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={16}>
          <Card
            title={<span><BranchesOutlined /> 版本历史</span>}
            variant="borderless"
            style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
            extra={
              selectedId && (
                <Space>
                  <Button
                    size="small"
                    icon={<DiffOutlined />}
                    loading={loadingDiff}
                    onClick={() => loadDiff(selectedId)}
                  >
                    查看 Diff
                  </Button>
                  <Text type="secondary" style={{ fontSize: 12 }}>新版本：</Text>
                  {(['patch', 'minor', 'major'] as const).map(bump => (
                    <Button
                      key={bump}
                      size="small"
                      icon={<PlusOutlined />}
                      loading={bumpLoading}
                      onClick={() => handleBump(bump)}
                    >
                      {bump}
                    </Button>
                  ))}
                </Space>
              )
            }
          >
            <div style={{ marginBottom: 16 }}>
              <Select
                placeholder="选择 Skill 查看版本历史"
                style={{ width: '100%' }}
                onChange={handleSelect}
                showSearch
                filterOption={(input, opt) =>
                  (opt?.label as string)?.toLowerCase().includes(input.toLowerCase())
                }
                options={[...new Map(skills.map(s => [s.name, s])).values()].map(s => ({
                  label: `${s.name} (${STATE_LABEL[s.state] || s.state})`,
                  value: s.skill_id,
                }))}
              />
            </div>

            <Table
              dataSource={versions}
              columns={columns}
              rowKey="skill_id"
              loading={loadingVersions}
              size="small"
              pagination={false}
              locale={{ emptyText: selectedId ? '暂无版本记录' : '请先选择一个 Skill' }}
            />
          </Card>

          {diffData && (
            <Card
              title={<span><DiffOutlined /> 变更历史 — {diffData.skill_name}</span>}
              variant="borderless"
              style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginTop: 16 }}
            >
              {diffData.history.length === 0 ? (
                <Empty description="暂无变更记录" />
              ) : (
                <Collapse
                  items={diffData.history.map(h => ({
                    key: h.record_id,
                    label: (
                      <Space>
                        <Text code>{h.from_version} → {h.to_version}</Text>
                        <Tag color={h.is_breaking ? 'red' : 'blue'}>{h.change_type}</Tag>
                        {h.is_breaking && <Tag color="red">BREAKING</Tag>}
                        <Text type="secondary" style={{ fontSize: 11 }}>{h.summary}</Text>
                      </Space>
                    ),
                    children: (
                      <div>
                        <div style={{ marginBottom: 8, color: '#666', fontSize: 12 }}>
                          {h.author} · {new Date(h.created_at).toLocaleString()}
                        </div>
                        <DiffView lines={h.diff} />
                      </div>
                    ),
                  }))}
                />
              )}
            </Card>
          )}
        </Col>

        <Col xs={24} lg={8}>
          <Card
            title={<span><HistoryOutlined /> 版本时间线</span>}
            variant="borderless"
            style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          >
            {versions.length > 0 ? (
              <Timeline
                items={versions.map((v, i) => ({
                  color: STATE_COLOR[v.state] || 'blue',
                  children: (
                    <div>
                      <div style={{ fontWeight: i === 0 ? 700 : 400 }}>
                        <Text code>v{v.version}</Text>
                        <Badge
                          color={STATE_COLOR[v.state] || 'default'}
                          text={STATE_LABEL[v.state] || v.state}
                          style={{ marginLeft: 8 }}
                        />
                      </div>
                      <div style={{ fontSize: 11, color: '#999' }}>
                        {new Date(v.created_at).toLocaleString()}
                      </div>
                      {v.metrics.total_executions > 0 && (
                        <div style={{ fontSize: 11, color: '#666' }}>
                          {v.metrics.total_executions} 次执行，
                          成功率 {(v.metrics.success_rate * 100).toFixed(0)}%
                        </div>
                      )}
                    </div>
                  ),
                }))}
              />
            ) : (
              <div style={{ textAlign: 'center', color: '#999', padding: 40 }}>
                <BranchesOutlined style={{ fontSize: 32, marginBottom: 8 }} />
                <div>选择 Skill 查看版本时间线</div>
              </div>
            )}
          </Card>

          {selectedSkill && (
            <Card
              title="当前版本信息"
              variant="borderless"
              style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginTop: 16 }}
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="名称">{selectedSkill.name}</Descriptions.Item>
                <Descriptions.Item label="版本"><Text code>v{selectedSkill.version}</Text></Descriptions.Item>
                <Descriptions.Item label="状态">
                  <Badge color={STATE_COLOR[selectedSkill.state]} text={STATE_LABEL[selectedSkill.state]} />
                </Descriptions.Item>
                <Descriptions.Item label="类型">
                  <Tag>{selectedSkill.skill_type}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="执行次数">{selectedSkill.metrics.total_executions}</Descriptions.Item>
              </Descriptions>
            </Card>
          )}
        </Col>
      </Row>

      <Drawer
        title={drawerSkill ? `v${drawerSkill.version} 详情` : ''}
        open={!!drawerSkill}
        onClose={() => setDrawerSkill(null)}
        size="default"
      >
        {drawerSkill && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="Skill ID">
              <Text code copyable style={{ fontSize: 11 }}>{drawerSkill.skill_id}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="版本"><Text code>v{drawerSkill.version}</Text></Descriptions.Item>
            <Descriptions.Item label="状态">
              <Badge color={STATE_COLOR[drawerSkill.state]} text={STATE_LABEL[drawerSkill.state]} />
            </Descriptions.Item>
            <Descriptions.Item label="描述">{drawerSkill.description}</Descriptions.Item>
            <Descriptions.Item label="标签">
              {drawerSkill.tags.map(t => <Tag key={t}>{t}</Tag>)}
            </Descriptions.Item>
            <Descriptions.Item label="创建时间">{new Date(drawerSkill.created_at).toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="更新时间">{new Date(drawerSkill.updated_at).toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="执行次数">{drawerSkill.metrics.total_executions}</Descriptions.Item>
            <Descriptions.Item label="成功率">
              {drawerSkill.metrics.total_executions >= 5
                ? `${(drawerSkill.metrics.success_rate * 100).toFixed(1)}%`
                : 'N/A'}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </div>
  )
}
