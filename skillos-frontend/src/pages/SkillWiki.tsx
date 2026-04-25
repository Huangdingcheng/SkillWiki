import { useEffect, useState } from 'react'
import {
  Table, Tag, Input, Select, Button, Drawer, Descriptions, Badge,
  Space, Tooltip, Popconfirm, message, Tabs, Typography, Progress,
} from 'antd'
import {
  SearchOutlined, EyeOutlined,
  CheckOutlined, StopOutlined,
} from '@ant-design/icons'
import { motion } from 'framer-motion'
import { skillsApi, lifecycleApi } from '@/api/client'
import type { SkillFull, SkillState, SkillSummary, SkillType } from '@/api/types'

const { Text, Paragraph } = Typography

const STATE_COLOR: Record<string, string> = {
  S4: 'green', S2: 'blue', S3: 'cyan', S1: 'orange',
  S5: 'gold', S6: 'red', S7: 'default', S0: 'purple',
}
const STATE_LABEL: Record<string, string> = {
  S0: 'Raw', S1: 'Candidate', S2: 'Draft', S3: 'Verified',
  S4: 'Released', S5: 'Degraded', S6: 'Deprecated', S7: 'Archived',
}
const TYPE_COLOR: Record<string, string> = {
  atomic: 'blue', functional: 'purple', strategic: 'gold',
}

export default function SkillWiki() {
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [stateFilter, setStateFilter] = useState<SkillState | undefined>()
  const [typeFilter, setTypeFilter] = useState<SkillType | undefined>()
  const [selected, setSelected] = useState<SkillFull | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)

  const load = () => {
    setLoading(true)
    skillsApi.list({ state: stateFilter, skill_type: typeFilter, limit: 200 })
      .then(setSkills)
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [stateFilter, typeFilter])

  const filtered = skills.filter(s =>
    !search || s.name.includes(search) || s.description.includes(search) || s.tags.some(t => t.includes(search))
  )

  const openDetail = async (id: string) => {
    const full = await skillsApi.getFull(id)
    setSelected(full)
    setDrawerOpen(true)
  }

  const handleRelease = async (id: string) => {
    try {
      await lifecycleApi.release(id)
      message.success('已发布')
      load()
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '操作失败')
    }
  }

  const handleDeprecate = async (id: string) => {
    try {
      await lifecycleApi.deprecate(id, '手动废弃')
      message.success('已废弃')
      load()
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '操作失败')
    }
  }

  const columns = [
    {
      title: 'Name',
      dataIndex: 'name',
      render: (name: string, r: SkillSummary) => (
        <Button type="link" onClick={() => openDetail(r.skill_id)} style={{ padding: 0, fontWeight: 600 }}>
          {name}
        </Button>
      ),
    },
    {
      title: 'Type',
      dataIndex: 'skill_type',
      render: (t: string) => <Tag color={TYPE_COLOR[t]}>{t.toUpperCase()}</Tag>,
    },
    {
      title: 'State',
      dataIndex: 'state',
      render: (s: string) => <Badge color={STATE_COLOR[s] || 'default'} text={STATE_LABEL[s] || s} />,
    },
    {
      title: 'Version',
      dataIndex: 'version',
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: 'Tags',
      dataIndex: 'tags',
      render: (tags: string[]) => tags.slice(0, 3).map(t => <Tag key={t}>{t}</Tag>),
    },
    {
      title: 'Success Rate',
      dataIndex: 'metrics',
      render: (m: SkillSummary['metrics']) => (
        m.total_executions >= 5
          ? <Progress percent={Math.round(m.success_rate * 100)} size="small" style={{ width: 80 }} />
          : <Text type="secondary">N/A</Text>
      ),
    },
    {
      title: 'Actions',
      render: (_: unknown, r: SkillSummary) => (
        <Space>
          <Tooltip title="查看详情">
            <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(r.skill_id)} />
          </Tooltip>
          {r.state === 'S2' && (
            <Tooltip title="发布">
              <Button size="small" icon={<CheckOutlined />} type="primary" onClick={() => handleRelease(r.skill_id)} />
            </Tooltip>
          )}
          {r.state === 'S4' && (
            <Tooltip title="废弃">
              <Popconfirm title="确认废弃？" onConfirm={() => handleDeprecate(r.skill_id)}>
                <Button size="small" icon={<StopOutlined />} danger />
              </Popconfirm>
            </Tooltip>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
          <Input
            placeholder="搜索 Skill 名称/描述/标签"
            prefix={<SearchOutlined />}
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ width: 280 }}
            allowClear
          />
          <Select
            placeholder="状态筛选"
            allowClear
            style={{ width: 140 }}
            onChange={v => setStateFilter(v as SkillState)}
            options={[
              { label: 'Raw (S0)',        value: 'S0' },
              { label: 'Candidate (S1)', value: 'S1' },
              { label: 'Draft (S2)',      value: 'S2' },
              { label: 'Verified (S3)',   value: 'S3' },
              { label: 'Released (S4)',   value: 'S4' },
              { label: 'Degraded (S5)',   value: 'S5' },
              { label: 'Deprecated (S6)', value: 'S6' },
              { label: 'Archived (S7)',   value: 'S7' },
            ]}
          />
          <Select
            placeholder="类型筛选"
            allowClear
            style={{ width: 140 }}
            onChange={v => setTypeFilter(v as SkillType)}
            options={['atomic', 'functional', 'strategic'].map(t => ({ label: t, value: t }))}
          />
          <Button onClick={load}>刷新</Button>
        </div>

        <Table
          dataSource={filtered}
          columns={columns}
          rowKey="skill_id"
          loading={loading}
          size="middle"
          pagination={{ pageSize: 15, showSizeChanger: true }}
          style={{ borderRadius: 12, overflow: 'hidden' }}
        />
      </motion.div>

      <Drawer
        title={selected?.name}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={600}
        extra={
          <Space>
            <Tag color={TYPE_COLOR[selected?.skill_type || '']}>{selected?.skill_type?.toUpperCase()}</Tag>
            <Badge color={STATE_COLOR[selected?.state || '']} text={STATE_LABEL[selected?.state || ''] || selected?.state} />
          </Space>
        }
      >
        {selected && (
          <Tabs
            items={[
              {
                key: 'info',
                label: '基本信息',
                children: (
                  <Descriptions column={1} bordered size="small">
                    <Descriptions.Item label="ID"><Text code copyable>{selected.skill_id}</Text></Descriptions.Item>
                    <Descriptions.Item label="版本"><Text code>{selected.version}</Text></Descriptions.Item>
                    <Descriptions.Item label="描述"><Paragraph>{selected.description}</Paragraph></Descriptions.Item>
                    <Descriptions.Item label="标签">{selected.tags.map(t => <Tag key={t}>{t}</Tag>)}</Descriptions.Item>
                    <Descriptions.Item label="粒度级别">{selected.granularity_level}</Descriptions.Item>
                    <Descriptions.Item label="创建时间">{new Date(selected.created_at).toLocaleString()}</Descriptions.Item>
                    <Descriptions.Item label="更新时间">{new Date(selected.updated_at).toLocaleString()}</Descriptions.Item>
                  </Descriptions>
                ),
              },
              {
                key: 'interface',
                label: '接口',
                children: selected.interface && (
                  <div>
                    <h4>输入参数</h4>
                    {selected.interface.inputs.map(p => (
                      <div key={p.name} style={{ marginBottom: 8 }}>
                        <Text code>{p.name}</Text>
                        <Tag style={{ marginLeft: 8 }}>{p.type}</Tag>
                        {p.required && <Tag color="red">必填</Tag>}
                        <div style={{ color: '#666', fontSize: 12 }}>{p.description}</div>
                      </div>
                    ))}
                    <h4 style={{ marginTop: 16 }}>输出参数</h4>
                    {selected.interface.outputs.map(p => (
                      <div key={p.name} style={{ marginBottom: 8 }}>
                        <Text code>{p.name}</Text>
                        <Tag style={{ marginLeft: 8 }}>{p.type}</Tag>
                        <div style={{ color: '#666', fontSize: 12 }}>{p.description}</div>
                      </div>
                    ))}
                    {selected.interface.preconditions.length > 0 && (
                      <>
                        <h4 style={{ marginTop: 16 }}>前置条件</h4>
                        {selected.interface.preconditions.map((c, i) => <div key={i}>• {c}</div>)}
                      </>
                    )}
                  </div>
                ),
              },
              {
                key: 'impl',
                label: '实现',
                children: selected.implementation && (
                  <div>
                    <Tag color="blue">{selected.implementation.language}</Tag>
                    {selected.implementation.code && (
                      <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 8, marginTop: 8, overflow: 'auto', fontSize: 12 }}>
                        {selected.implementation.code}
                      </pre>
                    )}
                    {selected.implementation.prompt_template && (
                      <div style={{ background: '#f0f7ff', padding: 12, borderRadius: 8, marginTop: 8 }}>
                        {selected.implementation.prompt_template}
                      </div>
                    )}
                    {selected.implementation.sub_skill_ids.length > 0 && (
                      <div style={{ marginTop: 8 }}>
                        <strong>子 Skill：</strong>
                        {selected.implementation.sub_skill_ids.map(id => <Tag key={id}>{id}</Tag>)}
                      </div>
                    )}
                  </div>
                ),
              },
              {
                key: 'metrics',
                label: '指标',
                children: (
                  <Descriptions column={1} bordered size="small">
                    <Descriptions.Item label="总执行次数">{selected.metrics.total_executions}</Descriptions.Item>
                    <Descriptions.Item label="成功次数">{selected.metrics.successful_executions}</Descriptions.Item>
                    <Descriptions.Item label="失败次数">{selected.metrics.failed_executions}</Descriptions.Item>
                    <Descriptions.Item label="成功率">
                      <Progress percent={Math.round(selected.metrics.success_rate * 100)} size="small" style={{ width: 120 }} />
                    </Descriptions.Item>
                    <Descriptions.Item label="平均延迟">{selected.metrics.avg_latency_ms.toFixed(0)}ms</Descriptions.Item>
                    <Descriptions.Item label="使用次数">{selected.metrics.usage_count}</Descriptions.Item>
                  </Descriptions>
                ),
              },
            ]}
          />
        )}
      </Drawer>
    </div>
  )
}
