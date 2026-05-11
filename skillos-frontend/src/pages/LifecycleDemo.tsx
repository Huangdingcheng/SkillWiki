import { useEffect, useState } from 'react'
import {
  Card, Steps, Tag, Button, Select, Alert, Divider,
  Timeline, Typography, Space, Badge, Row, Col, Tooltip, message,
} from 'antd'
import {
  ArrowRightOutlined, CheckCircleOutlined, ClockCircleOutlined,
  SyncOutlined, StopOutlined, ExperimentOutlined,
} from '@ant-design/icons'
import { motion, AnimatePresence } from 'framer-motion'
import { skillsApi, lifecycleApi } from '@/api/client'
import type { SkillSummary } from '@/api/types'

const { Text, Paragraph } = Typography

// SkillOS 8 状态机定义
const STATES = [
  { key: 'S0', label: 'Raw Experience', color: '#722ed1', icon: '🌱', desc: '原始经验，来自轨迹/文档/脚本' },
  { key: 'S1', label: 'Candidate',      color: '#1677ff', icon: '🔍', desc: 'LLM 识别出的 Skill 候选' },
  { key: 'S2', label: 'Draft',          color: '#13c2c2', icon: '✏️', desc: '形式化为结构化 Skill 草稿' },
  { key: 'S3', label: 'Verified',       color: '#52c41a', icon: '✅', desc: '通过静态检查和语义验证' },
  { key: 'S4', label: 'Released',       color: '#52c41a', icon: '🚀', desc: '正式发布，可被 Agent 调用' },
  { key: 'S5', label: 'Degraded',       color: '#faad14', icon: '⚠️', desc: '成功率下降，需要修复' },
  { key: 'S6', label: 'Deprecated',     color: '#ff4d4f', icon: '🗑️', desc: '已废弃，不再推荐使用' },
  { key: 'S7', label: 'Archived',       color: '#8c8c8c', icon: '📦', desc: '归档，历史记录保留' },
]

const TRANSITIONS: Record<string, string[]> = {
  S0: ['S1'],
  S1: ['S2'],
  S2: ['S3', 'S1'],
  S3: ['S4', 'S2'],
  S4: ['S5', 'S6'],
  S5: ['S4', 'S6'],
  S6: ['S7'],
  S7: [],
}

const TRANSITION_LABELS: Record<string, string> = {
  'S0→S1': '候选挖掘',
  'S1→S2': '形式化',
  'S2→S3': '验证通过',
  'S2→S1': '重新挖掘',
  'S3→S4': '发布',
  'S3→S2': '修订',
  'S4→S5': '性能退化',
  'S4→S6': '手动废弃',
  'S5→S4': '修复成功',
  'S5→S6': '无法修复',
  'S6→S7': '归档',
}

function StateMachineViz({ currentState }: { currentState: string }) {
  const mainFlow = ['S0', 'S1', 'S2', 'S3', 'S4']
  const sideFlow = ['S5', 'S6', 'S7']

  const getStepStatus = (key: string) => {
    const idx = mainFlow.indexOf(key)
    const curIdx = mainFlow.indexOf(currentState)
    if (key === currentState) return 'process'
    if (idx < curIdx) return 'finish'
    return 'wait'
  }

  return (
    <div>
      {/* 主流程 */}
      <Steps
        current={mainFlow.indexOf(currentState)}
        items={mainFlow.map(key => {
          const s = STATES.find(x => x.key === key)!
          return {
            title: <span style={{ fontSize: 12 }}>{s.icon} {s.label}</span>,
            status: getStepStatus(key) as 'process' | 'finish' | 'wait',
            description: <span style={{ fontSize: 11, color: '#999' }}>{s.key}</span>,
          }
        })}
        style={{ marginBottom: 16 }}
      />

      {/* 侧流程（退化/废弃/归档） */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
        <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>异常路径：</Text>
        {sideFlow.map(key => {
          const s = STATES.find(x => x.key === key)!
          const isActive = currentState === key
          return (
            <Tag
              key={key}
              color={isActive ? s.color : undefined}
              style={{
                borderColor: s.color,
                color: isActive ? '#fff' : s.color,
                fontWeight: isActive ? 700 : 400,
              }}
            >
              {s.icon} {s.label}
            </Tag>
          )
        })}
      </div>
    </div>
  )
}

export default function LifecycleDemo() {
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<SkillSummary | null>(null)
  const [history, setHistory] = useState<{ state: string; label: string; time: string }[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    skillsApi.list({ limit: 100 }).then(setSkills)
  }, [])

  useEffect(() => {
    if (!selectedId) return
    const s = skills.find(x => x.skill_id === selectedId) || null
    setSelected(s)
    if (s) {
      setHistory([{ state: s.state, label: STATES.find(x => x.key === s.state)?.label || s.state, time: new Date().toLocaleTimeString() }])
    }
  }, [selectedId, skills])

  const doTransition = async (targetState: string) => {
    if (!selected) return
    setLoading(true)
    try {
      let updated: SkillSummary
      if (targetState === 'S4') {
        updated = await lifecycleApi.release(selected.skill_id)
      } else if (targetState === 'S6') {
        updated = await lifecycleApi.deprecate(selected.skill_id, '演示废弃')
      } else {
        updated = await lifecycleApi.transition(selected.skill_id, targetState as SkillSummary['state'])
      }
      setSelected(updated)
      setSkills(prev => prev.map(s => s.skill_id === updated.skill_id ? updated : s))
      const stateInfo = STATES.find(x => x.key === updated.state)
      setHistory(prev => [
        { state: updated.state, label: stateInfo?.label || updated.state, time: new Date().toLocaleTimeString() },
        ...prev,
      ])
      message.success(`状态已转换为 ${stateInfo?.label}`)
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '转换失败')
    } finally {
      setLoading(false)
    }
  }

  const currentStateInfo = selected ? STATES.find(x => x.key === selected.state) : null
  const availableTransitions = selected ? (TRANSITIONS[selected.state] || []) : []

  return (
    <div style={{ padding: 24 }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h2 style={{ fontWeight: 700, marginBottom: 4 }}>Skill Lifecycle Demo</h2>
        <p style={{ color: '#666', marginBottom: 24 }}>
          选择一个 Skill，交互式演示其在 SkillOS 8 状态机中的完整生命周期流转。
        </p>
      </motion.div>

      <Row gutter={[16, 16]}>
        {/* 左侧：状态机可视化 + 操作 */}
        <Col xs={24} lg={14}>
          <Card
            title="状态机可视化"
            variant="borderless"
            style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
          >
            <div style={{ marginBottom: 16 }}>
              <Select
                placeholder="选择一个 Skill"
                style={{ width: '100%' }}
                onChange={setSelectedId}
                options={skills.map(s => ({
                  label: `${s.name} (${STATES.find(x => x.key === s.state)?.label || s.state})`,
                  value: s.skill_id,
                }))}
                showSearch
                filterOption={(input, opt) =>
                  (opt?.label as string)?.toLowerCase().includes(input.toLowerCase())
                }
              />
            </div>

            {selected && (
              <motion.div key={selected.skill_id} initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                <StateMachineViz currentState={selected.state} />

                <Divider />

                {/* 当前状态详情 */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                  <div style={{
                    width: 48, height: 48, borderRadius: '50%',
                    background: currentStateInfo?.color,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 24,
                  }}>
                    {currentStateInfo?.icon}
                  </div>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 16 }}>{currentStateInfo?.label}</div>
                    <div style={{ color: '#666', fontSize: 13 }}>{currentStateInfo?.desc}</div>
                  </div>
                </div>

                {/* 可用转换 */}
                <div>
                  <Text type="secondary" style={{ fontSize: 12, marginBottom: 8, display: 'block' }}>
                    可执行的状态转换：
                  </Text>
                  <Space wrap>
                    {availableTransitions.length === 0 ? (
                      <Text type="secondary">终态，无可用转换</Text>
                    ) : (
                      availableTransitions.map(target => {
                        const targetInfo = STATES.find(x => x.key === target)!
                        const label = TRANSITION_LABELS[`${selected.state}→${target}`] || target
                        return (
                          <motion.div key={target} whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
                            <Button
                              type="primary"
                              style={{ background: targetInfo.color, borderColor: targetInfo.color }}
                              icon={<ArrowRightOutlined />}
                              onClick={() => doTransition(target)}
                              loading={loading}
                            >
                              {targetInfo.icon} {label} → {targetInfo.label}
                            </Button>
                          </motion.div>
                        )
                      })
                    )}
                  </Space>
                </div>
              </motion.div>
            )}

            {!selected && (
              <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
                <ExperimentOutlined style={{ fontSize: 48, marginBottom: 12 }} />
                <div>请先选择一个 Skill 开始演示</div>
              </div>
            )}
          </Card>

          {/* 状态说明卡片 */}
          <Card
            title="SkillOS 状态机说明"
            variant="borderless"
            style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          >
            <Row gutter={[8, 8]}>
              {STATES.map(s => (
                <Col xs={12} sm={8} key={s.key}>
                  <div style={{
                    padding: '8px 12px',
                    borderRadius: 8,
                    border: `1px solid ${s.color}22`,
                    background: `${s.color}08`,
                  }}>
                    <div style={{ fontWeight: 600, color: s.color, fontSize: 13 }}>
                      {s.icon} {s.key}: {s.label}
                    </div>
                    <div style={{ fontSize: 11, color: '#666', marginTop: 2 }}>{s.desc}</div>
                  </div>
                </Col>
              ))}
            </Row>
          </Card>
        </Col>

        {/* 右侧：转换历史 + Skill 信息 */}
        <Col xs={24} lg={10}>
          {selected && (
            <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }}>
              <Card
                title={`${selected.name} 信息`}
                variant="borderless"
                style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
              >
                <Space orientation="vertical" style={{ width: '100%' }}>
                  <div>
                    <Text type="secondary">类型：</Text>
                    <Tag color={selected.skill_type === 'atomic' ? 'blue' : selected.skill_type === 'functional' ? 'purple' : 'gold'}>
                      {selected.skill_type.toUpperCase()}
                    </Tag>
                  </div>
                  <div>
                    <Text type="secondary">版本：</Text>
                    <Text code>{selected.version}</Text>
                  </div>
                  <div>
                    <Text type="secondary">描述：</Text>
                    <Paragraph style={{ margin: 0 }}>{selected.description}</Paragraph>
                  </div>
                  <div>
                    <Text type="secondary">标签：</Text>
                    {selected.tags.map(t => <Tag key={t}>{t}</Tag>)}
                  </div>
                  <div>
                    <Text type="secondary">执行次数：</Text>
                    <Text strong>{selected.metrics.total_executions}</Text>
                    <Text type="secondary" style={{ marginLeft: 8 }}>
                      成功率 {(selected.metrics.success_rate * 100).toFixed(1)}%
                    </Text>
                  </div>
                </Space>
              </Card>

              <Card
                title="状态转换历史"
                variant="borderless"
                style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
              >
                <AnimatePresence>
                  <Timeline
                    items={history.map((h, i) => {
                      const info = STATES.find(x => x.key === h.state)
                      return {
                        color: info?.color || '#1677ff',
                        dot: i === 0 ? <SyncOutlined spin style={{ color: info?.color }} /> : undefined,
                        children: (
                          <motion.div
                            key={`${h.state}-${h.time}`}
                            initial={{ opacity: 0, x: -10 }}
                            animate={{ opacity: 1, x: 0 }}
                          >
                            <div style={{ fontWeight: i === 0 ? 700 : 400 }}>
                              {info?.icon} {h.label}
                            </div>
                            <div style={{ fontSize: 11, color: '#999' }}>{h.time}</div>
                          </motion.div>
                        ),
                      }
                    })}
                  />
                </AnimatePresence>
              </Card>
            </motion.div>
          )}
        </Col>
      </Row>
    </div>
  )
}
