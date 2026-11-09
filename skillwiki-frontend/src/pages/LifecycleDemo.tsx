import { useEffect, useState } from 'react'
import {
  Alert, Card, Steps, Tag, Button, Select, Divider,
  Timeline, Typography, Space, Row, Col, message,
} from 'antd'
import {
  ArrowRightOutlined,
  SyncOutlined, ExperimentOutlined,
} from '@ant-design/icons'
import { motion, AnimatePresence } from 'framer-motion'
import { Link } from 'react-router-dom'
import { skillsApi, lifecycleApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import { skillContextPath, useSkillContext } from '@/hooks/useSkillContext'
import type { SkillSummary } from '@/api/types'

const { Text, Paragraph } = Typography

// SkillWiki 8-state machine definition
const STATES = [
  { key: 'S0', label: 'Raw Experience', color: '#722ed1', icon: '🌱', desc: 'Raw experience from trajectories, documents, or scripts' },
  { key: 'S1', label: 'Candidate',      color: '#1677ff', icon: '🔍', desc: 'Imported or generated Skill candidate awaiting review' },
  { key: 'S2', label: 'Draft',          color: '#13c2c2', icon: '✏️', desc: 'Structured Skill draft' },
  { key: 'S3', label: 'Verified',       color: '#52c41a', icon: '✅', desc: 'Backend governance state; inspect evidence for task-run verification' },
  { key: 'S4', label: 'Released',       color: '#52c41a', icon: '🚀', desc: 'Released and callable by agents' },
  { key: 'S5', label: 'Degraded',       color: '#faad14', icon: '⚠️', desc: 'Success rate dropped and repair is needed' },
  { key: 'S6', label: 'Deprecated',     color: '#ff4d4f', icon: '🗑️', desc: 'Deprecated and no longer recommended' },
  { key: 'S7', label: 'Archived',       color: '#8c8c8c', icon: '📦', desc: 'Archived with history preserved' },
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
  'S0→S1': 'Candidate Mining',
  'S1→S2': 'Formalize',
  'S2→S3': 'Verify',
  'S2→S1': 'Remine',
  'S3→S4': 'Release',
  'S3→S2': 'Revise',
  'S4→S5': 'Performance Degraded',
  'S4→S6': 'Manual Deprecation',
  'S5→S4': 'Repair Succeeded',
  'S5→S6': 'Repair Failed',
  'S6→S7': 'Archive',
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
      {/* Main flow */}
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

      {/* Side flow: degraded, deprecated, archived */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
        <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>Exception Path:</Text>
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
  const { skillId: selectedId, setSkillId: setSelectedId } = useSkillContext()
  const [selected, setSelected] = useState<SkillSummary | null>(null)
  const [history, setHistory] = useState<{ state: string; label: string; time: string }[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    skillsApi.list({ limit: 100 }).then(items => setSkills(previous => [...new Map([...items, ...previous].map(skill => [skill.skill_id, skill])).values()])).catch(err => {
      setError(getApiErrorMessage(err, 'Load Skills failed'))
    })
  }, [])

  useEffect(() => {
    if (!selectedId) return
    let cancelled = false
    skillsApi.get(selectedId).then(skill => {
      if (cancelled) return
      setSelected(skill)
      setError(null)
      setSkills(previous => previous.some(item => item.skill_id === skill.skill_id) ? previous : [skill, ...previous])
      setHistory([{ state: skill.state, label: `Current state: ${STATES.find(x => x.key === skill.state)?.label || skill.state}`, time: `Updated ${new Date(skill.updated_at).toLocaleString()}` }])
    }).catch(err => {
      if (cancelled) return
      setSelected(null)
      setHistory([])
      setError(getApiErrorMessage(err, 'Skill does not exist or is temporarily unavailable'))
    })
    return () => { cancelled = true }
  }, [selectedId])

  const doTransition = async (targetState: string) => {
    if (!selected || selected.skill_id !== selectedId) return
    setLoading(true)
    setError(null)
    try {
      let updated: SkillSummary
      if (targetState === 'S4') {
        updated = await lifecycleApi.release(selected.skill_id)
      } else if (targetState === 'S6') {
        updated = await lifecycleApi.deprecate(selected.skill_id, 'Demo deprecation')
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
      message.success(`State changed to ${stateInfo?.label}`)
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, 'Transition failed'))
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
          Select a Skill and interactively walk through its full lifecycle in the SkillWiki 8-state machine.
        </p>
      </motion.div>
      <Alert type="info" showIcon style={{ marginBottom: 16 }} message="Governance state is separate from task execution evidence" description="Static review and lifecycle transitions do not prove a real task ran or passed its verifier. Inspect the Skill evidence and verification records separately." />
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}

      <Row gutter={[16, 16]}>
        {/* Left: state machine visualization and actions */}
        <Col xs={24} lg={14}>
          <Card
            title="State Machine Visualization"
            variant="borderless"
            style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
          >
            <div style={{ marginBottom: 16 }}>
              <Select
                placeholder="Select a Skill"
                style={{ width: '100%' }}
                value={selectedId ?? undefined}
                disabled={loading}
                onChange={id => { setSelected(null); setHistory([]); setError(null); setSelectedId(id) }}
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

            {selected && selected.skill_id === selectedId && (
              <motion.div key={selected.skill_id} initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                <StateMachineViz currentState={selected.state} />

                <Divider />

                {/* Current state details */}
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

                {/* Available transitions */}
                <div>
                  <Text type="secondary" style={{ fontSize: 12, marginBottom: 8, display: 'block' }}>
                    Available state transitions:
                  </Text>
                  <Space wrap>
                    {availableTransitions.length === 0 ? (
                      <Text type="secondary">Terminal state. No transitions available.</Text>
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

            {(!selected || selected.skill_id !== selectedId) && (
              <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
                <ExperimentOutlined style={{ fontSize: 48, marginBottom: 12 }} />
                <div>{selectedId ? 'Loading selected Skill or waiting for a valid API response.' : 'Select a Skill to start the demo.'}</div>
              </div>
            )}
          </Card>

          {/* State description cards */}
          <Card
            title="SkillWiki State Machine"
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

        {/* Right: transition history and Skill info */}
        <Col xs={24} lg={10}>
          {selected && selected.skill_id === selectedId && (
            <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }}>
              <Card
                title={`${selected.name} Info`}
                variant="borderless"
                style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
              >
                <Space orientation="vertical" style={{ width: '100%' }}>
                  <Text code copyable>{selected.skill_id}</Text>
                  <Space wrap><Link to={skillContextPath('/wiki', selectedId)}>Open in Wiki</Link><Link to={skillContextPath('/versions', selectedId)}>Version Governance</Link></Space>
                  <div>
                    <Text type="secondary">Type:</Text>
                    <Tag color={selected.skill_type === 'atomic' ? 'blue' : selected.skill_type === 'functional' ? 'purple' : 'gold'}>
                      {selected.skill_type.toUpperCase()}
                    </Tag>
                  </div>
                  <div>
                    <Text type="secondary">Version:</Text>
                    <Text code>{selected.version}</Text>
                  </div>
                  <div>
                    <Text type="secondary">Description:</Text>
                    <Paragraph style={{ margin: 0 }}>{selected.description}</Paragraph>
                  </div>
                  <div>
                    <Text type="secondary">Tags:</Text>
                    {selected.tags.map(t => <Tag key={t}>{t}</Tag>)}
                  </div>
                  <div>
                    <Text type="secondary">Executions:</Text>
                    <Text strong>{selected.metrics.total_executions}</Text>
                    <Text type="secondary" style={{ marginLeft: 8 }}>
                      Success rate {(selected.metrics.success_rate * 100).toFixed(1)}%
                    </Text>
                  </div>
                </Space>
              </Card>

              <Card
                title="Current state and transitions in this visit"
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
