import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Badge, Button, Card, Col, Descriptions, Divider, Empty, Input, Popconfirm, Row,
  Select, Space, Steps, Table, Tag, Timeline, Typography, message, Collapse, Segmented,
} from 'antd'
import {
  AuditOutlined, BranchesOutlined, ExperimentOutlined, MergeCellsOutlined, PlusOutlined,
  RocketOutlined, SaveOutlined, StopOutlined, SyncOutlined,
} from '@ant-design/icons'
import { motion } from 'framer-motion'
import { lifecycleApi, skillsApi } from '@/api/client'
import type { SkillFull, SkillReviewResult, SkillState, SkillSummary, SkillVisibility } from '@/api/types'

const { Text, Paragraph } = Typography
const { TextArea } = Input

const STATE_COLOR: Record<string, string> = {
  S0: 'purple', S1: 'orange', S2: 'blue', S3: 'cyan',
  S4: 'green', S5: 'gold', S6: 'red', S7: 'default',
}

const STATE_LABEL: Record<string, string> = {
  S0: 'Raw', S1: 'Candidate', S2: 'Draft', S3: 'Verified',
  S4: 'Released', S5: 'Degraded', S6: 'Deprecated', S7: 'Archived',
}

const LIFECYCLE = ['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'] as const

const TRANSITIONS: Record<string, SkillState[]> = {
  S0: ['S1'],
  S1: ['S2'],
  S2: ['S3', 'S1'],
  S3: ['S4', 'S2'],
  S4: ['S5', 'S6'],
  S5: ['S4', 'S6'],
  S6: ['S7'],
  S7: [],
}

function semverCompare(a: string, b: string) {
  const pa = a.split('.').map(Number)
  const pb = b.split('.').map(Number)
  for (let i = 0; i < 3; i++) {
    if ((pa[i] || 0) !== (pb[i] || 0)) return (pb[i] || 0) - (pa[i] || 0)
  }
  return 0
}

function splitTags(value: string) {
  return value.split(',').map(tag => tag.trim()).filter(Boolean)
}

function splitLines(value: string) {
  return value.split(/\n|,/).map(item => item.trim()).filter(Boolean)
}

function prettyJson(value: unknown, fallback: unknown) {
  try {
    return JSON.stringify(value ?? fallback, null, 2)
  } catch {
    return JSON.stringify(fallback, null, 2)
  }
}

function scorePercent(review: SkillReviewResult) {
  const ratio = review.score_ratio ?? (review.overall_score > 1 ? review.overall_score / 10 : review.overall_score)
  return Math.round(Math.max(0, Math.min(1, ratio)) * 100)
}

export default function SkillManage() {
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [visibility, setVisibility] = useState<SkillVisibility | 'all'>('user')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedFull, setSelectedFull] = useState<SkillFull | null>(null)
  const [versions, setVersions] = useState<SkillSummary[]>([])
  const [review, setReview] = useState<SkillReviewResult | null>(null)
  const [descriptionDraft, setDescriptionDraft] = useState('')
  const [tagsDraft, setTagsDraft] = useState('')
  const [inputSchemaDraft, setInputSchemaDraft] = useState('{}')
  const [outputSchemaDraft, setOutputSchemaDraft] = useState('{}')
  const [preconditionsDraft, setPreconditionsDraft] = useState('')
  const [postconditionsDraft, setPostconditionsDraft] = useState('')
  const [sideEffectsDraft, setSideEffectsDraft] = useState('')
  const [implementationLanguage, setImplementationLanguage] = useState('python')
  const [promptDraft, setPromptDraft] = useState('')
  const [codeDraft, setCodeDraft] = useState('')
  const [toolCallsDraft, setToolCallsDraft] = useState('')
  const [subSkillIdsDraft, setSubSkillIdsDraft] = useState('')
  const [testCasesDraft, setTestCasesDraft] = useState('[]')
  const [mergeStrategy, setMergeStrategy] = useState<'agent_generalize' | 'field_union'>('agent_generalize')
  const [mergeSourceIds, setMergeSourceIds] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [versionLoading, setVersionLoading] = useState(false)

  const selected = useMemo(
    () => selectedId ? skills.find(skill => skill.skill_id === selectedId) || null : null,
    [selectedId, skills],
  )

  const mergeOptions = useMemo(
    () => skills
      .filter(skill => skill.skill_id !== selectedId)
      .map(skill => ({
        label: `${skill.name} · ${skill.skill_type} · v${skill.version}`,
        value: skill.skill_id,
      })),
    [skills, selectedId],
  )

  const visibleVersions = versions.length > 0 ? versions : selected ? [selected] : []
  const availableTransitions = selected ? TRANSITIONS[selected.state] || [] : []

  const loadSkills = async (preferredId?: string) => {
    const all = await skillsApi.list({ limit: 500, visibility })
    setSkills(all)
    if (preferredId) {
      setSelectedId(preferredId)
    } else if (all.length > 0 && (!selectedId || !all.some(skill => skill.skill_id === selectedId))) {
      setSelectedId(all[0].skill_id)
    } else if (all.length === 0) {
      setSelectedId(null)
    }
  }

  const loadSelectedDetails = async (id: string) => {
    setVersionLoading(true)
    try {
      const [full, history] = await Promise.all([
        skillsApi.getFull(id),
        skillsApi.versions(id),
      ])
      setSelectedFull(full)
      setDescriptionDraft(full.description || '')
      setTagsDraft((full.tags || []).join(', '))
      setInputSchemaDraft(prettyJson(full.interface?.input_schema, { type: 'object', properties: {}, required: [] }))
      setOutputSchemaDraft(prettyJson(full.interface?.output_schema, { type: 'object', properties: {} }))
      setPreconditionsDraft((full.interface?.preconditions || []).join('\n'))
      setPostconditionsDraft((full.interface?.postconditions || []).join('\n'))
      setSideEffectsDraft((full.interface?.side_effects || []).join('\n'))
      setImplementationLanguage(full.implementation?.language || 'python')
      setPromptDraft(full.implementation?.prompt_template || '')
      setCodeDraft(full.implementation?.code || '')
      setToolCallsDraft((full.implementation?.tool_calls || []).join('\n'))
      setSubSkillIdsDraft((full.implementation?.sub_skill_ids || []).join('\n'))
      setTestCasesDraft(prettyJson(full.test_cases || [], []))
      setVersions(history.sort((a, b) => semverCompare(a.version, b.version)))
      setReview(null)
      setMergeSourceIds([])
      setMergeStrategy('agent_generalize')
    } finally {
      setVersionLoading(false)
    }
  }

  useEffect(() => { loadSkills() }, [visibility])

  useEffect(() => {
    if (selectedId) loadSelectedDetails(selectedId)
  }, [selectedId])

  const refreshSelected = async (updated?: SkillSummary) => {
    await loadSkills(updated?.skill_id)
    if (updated?.skill_id) await loadSelectedDetails(updated.skill_id)
    else if (selectedId) await loadSelectedDetails(selectedId)
  }

  const transitionTo = async (target: SkillState) => {
    if (!selected) return
    setLoading(true)
    try {
      const updated = target === 'S4'
        ? await lifecycleApi.release(selected.skill_id)
        : target === 'S6'
          ? await lifecycleApi.deprecate(selected.skill_id, 'Deprecated from Skill Manage')
          : await lifecycleApi.transition(selected.skill_id, target, 'Transitioned from Skill Manage')
      message.success(`Lifecycle moved to ${STATE_LABEL[updated.state] || updated.state}`)
      await refreshSelected(updated)
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Lifecycle transition failed')
    } finally {
      setLoading(false)
    }
  }

  const buildEditablePayload = () => {
    let inputSchema: Record<string, unknown>
    let outputSchema: Record<string, unknown>
    let testCases: unknown[]
    try {
      inputSchema = JSON.parse(inputSchemaDraft || '{}') as Record<string, unknown>
    } catch {
      message.error('Input Schema must be valid JSON.')
      return null
    }
    try {
      outputSchema = JSON.parse(outputSchemaDraft || '{}') as Record<string, unknown>
    } catch {
      message.error('Output Schema must be valid JSON.')
      return null
    }
    try {
      const parsed = JSON.parse(testCasesDraft || '[]') as unknown
      if (!Array.isArray(parsed)) {
        message.error('Test Cases must be a JSON array.')
        return null
      }
      testCases = parsed
    } catch {
      message.error('Test Cases must be valid JSON.')
      return null
    }

    const implementation = {
      language: implementationLanguage || 'python',
      code: codeDraft.trim() || undefined,
      prompt_template: promptDraft.trim() || undefined,
      tool_calls: splitLines(toolCallsDraft),
      sub_skill_ids: splitLines(subSkillIdsDraft),
    }
    if (!implementation.code && !implementation.prompt_template && implementation.sub_skill_ids.length === 0) {
      message.error('Implementation needs code, prompt_template, or sub_skill_ids.')
      return null
    }

    return {
      description: descriptionDraft,
      tags: splitTags(tagsDraft),
      interface: {
        input_schema: inputSchema,
        output_schema: outputSchema,
        preconditions: splitLines(preconditionsDraft),
        postconditions: splitLines(postconditionsDraft),
        side_effects: splitLines(sideEffectsDraft),
      },
      implementation,
      test_cases: testCases,
      domain: selectedFull?.domain || 'general',
      granularity_level: selectedFull?.granularity_level,
    }
  }

  const createVersion = async (bump: 'patch' | 'minor' | 'major') => {
    if (!selectedFull) return
    const payload = buildEditablePayload()
    if (!payload) return
    setLoading(true)
    try {
      const updated = await lifecycleApi.newVersion(selectedFull.skill_id, {
        bump,
        ...payload,
      })
      message.success(`Created ${bump} version v${updated.version}`)
      await refreshSelected(updated)
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Version update failed')
    } finally {
      setLoading(false)
    }
  }

  const mergeUpdate = async () => {
    if (!selected || mergeSourceIds.length === 0) return
    const payload = mergeStrategy === 'field_union' ? buildEditablePayload() : null
    if (mergeStrategy === 'field_union' && !payload) return
    setLoading(true)
    try {
      const result = await lifecycleApi.mergeUpdate(selected.skill_id, {
        source_skill_ids: mergeSourceIds,
        bump: 'minor',
        merge_strategy: mergeStrategy,
        ...(payload || {}),
      })
      message.success(`Merged ${result.merged_skills.length} Skill(s) into v${result.updated_skill.version}`)
      await refreshSelected(result.updated_skill)
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Merge update failed')
    } finally {
      setLoading(false)
    }
  }

  const runReview = async (autoApply: boolean) => {
    if (!selected) return
    setLoading(true)
    try {
      const result = await lifecycleApi.review(selected.skill_id, autoApply)
      setReview(result)
      if (result.updated_skill) {
        message.warning(`Review downgraded lifecycle: ${result.lifecycle_action}`)
        await refreshSelected(result.updated_skill)
      } else {
        message.success(`Review finished: score ${scorePercent(result)}%`)
      }
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Review failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ padding: 24 }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h2 style={{ fontWeight: 700, marginBottom: 4 }}>Skill Manage</h2>
        <p style={{ color: '#666', marginBottom: 24 }}>
          Manage lifecycle states, Git-style versions, review downgrades, and workflow merge updates.
        </p>
      </motion.div>

      <Card bordered={false} style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Segmented
            value={visibility}
            onChange={value => setVisibility(value as SkillVisibility | 'all')}
            options={[
              { label: 'User Skills', value: 'user' },
              { label: 'Kernel Skills', value: 'kernel' },
              { label: 'All', value: 'all' },
            ]}
          />
          <Select
            showSearch
            placeholder="Select a Skill"
            style={{ width: '100%' }}
            value={selectedId || undefined}
            onChange={setSelectedId}
            filterOption={(input, option) => String(option?.label || '').toLowerCase().includes(input.toLowerCase())}
            options={skills.map(skill => ({
              label: `${skill.name} · ${skill.visibility} · ${skill.skill_type} · v${skill.version} · ${STATE_LABEL[skill.state] || skill.state}`,
              value: skill.skill_id,
            }))}
          />
        </Space>
      </Card>

      {!selected ? (
        <Empty description="Select a Skill to manage lifecycle, versions, review, and merge updates." />
      ) : (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={14}>
            <Card
              title={<span><ExperimentOutlined /> Lifecycle State Machine</span>}
              bordered={false}
              style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
            >
              <Steps
                current={Math.max(0, LIFECYCLE.indexOf(selected.state))}
                items={LIFECYCLE.map(state => ({ title: STATE_LABEL[state], description: state }))}
                size="small"
                style={{ marginBottom: 20 }}
              />

              <Descriptions column={1} bordered size="small" style={{ marginBottom: 16 }}>
                <Descriptions.Item label="Skill">{selected.name}</Descriptions.Item>
                <Descriptions.Item label="Current State">
                  <Badge color={STATE_COLOR[selected.state]} text={`${STATE_LABEL[selected.state]} (${selected.state})`} />
                </Descriptions.Item>
                <Descriptions.Item label="Type">
                  <Tag color={selected.skill_type === 'strategic' ? 'orange' : selected.skill_type === 'functional' ? 'purple' : 'blue'}>
                    {selected.skill_type}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="Description">
                  <Paragraph style={{ margin: 0 }}>{selected.description}</Paragraph>
                </Descriptions.Item>
              </Descriptions>

              <Space wrap>
                {availableTransitions.length === 0 ? (
                  <Text type="secondary">No available lifecycle transition.</Text>
                ) : availableTransitions.map(target => (
                  target === 'S6' ? (
                    <Popconfirm key={target} title="Deprecate this Skill?" onConfirm={() => transitionTo(target)}>
                      <Button danger icon={<StopOutlined />} loading={loading}>Move to {STATE_LABEL[target]}</Button>
                    </Popconfirm>
                  ) : (
                    <Button
                      key={target}
                      type={target === 'S4' ? 'primary' : 'default'}
                      icon={target === 'S4' ? <RocketOutlined /> : <SyncOutlined />}
                      loading={loading}
                      onClick={() => transitionTo(target)}
                    >
                      Move to {STATE_LABEL[target]}
                    </Button>
                  )
                ))}
              </Space>
            </Card>

            <Card
              title={<span><SaveOutlined /> Update / Merge Knowledge</span>}
              bordered={false}
              style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginTop: 16 }}
            >
              <Alert
                type="info"
                showIcon
                message="Updates create a new version instead of overwriting the current Skill."
                style={{ marginBottom: 16 }}
              />
              <Text strong>Description</Text>
              <TextArea
                value={descriptionDraft}
                onChange={event => setDescriptionDraft(event.target.value)}
                autoSize={{ minRows: 4, maxRows: 8 }}
                style={{ marginTop: 8, marginBottom: 12 }}
              />
              <Text strong>Tags</Text>
              <Input
                value={tagsDraft}
                onChange={event => setTagsDraft(event.target.value)}
                placeholder="comma,separated,tags"
                style={{ marginTop: 8, marginBottom: 12 }}
              />
              <Collapse
                size="small"
                style={{ marginBottom: 12 }}
                items={[
                  {
                    key: 'interface',
                    label: 'Interface: inputs, outputs, conditions',
                    children: (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <Text strong>Input Schema JSON</Text>
                        <TextArea
                          value={inputSchemaDraft}
                          onChange={event => setInputSchemaDraft(event.target.value)}
                          autoSize={{ minRows: 5, maxRows: 12 }}
                          style={{ fontFamily: 'monospace', fontSize: 12 }}
                        />
                        <Text strong>Output Schema JSON</Text>
                        <TextArea
                          value={outputSchemaDraft}
                          onChange={event => setOutputSchemaDraft(event.target.value)}
                          autoSize={{ minRows: 5, maxRows: 12 }}
                          style={{ fontFamily: 'monospace', fontSize: 12 }}
                        />
                        <Text strong>Preconditions</Text>
                        <TextArea value={preconditionsDraft} onChange={event => setPreconditionsDraft(event.target.value)} autoSize={{ minRows: 2, maxRows: 5 }} />
                        <Text strong>Postconditions</Text>
                        <TextArea value={postconditionsDraft} onChange={event => setPostconditionsDraft(event.target.value)} autoSize={{ minRows: 2, maxRows: 5 }} />
                        <Text strong>Side Effects</Text>
                        <TextArea value={sideEffectsDraft} onChange={event => setSideEffectsDraft(event.target.value)} autoSize={{ minRows: 2, maxRows: 5 }} />
                      </Space>
                    ),
                  },
                  {
                    key: 'implementation',
                    label: 'Implementation: prompt, code, tool calls',
                    children: (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <Text strong>Language</Text>
                        <Input value={implementationLanguage} onChange={event => setImplementationLanguage(event.target.value)} placeholder="python / natural_language" />
                        <Text strong>Prompt Template</Text>
                        <TextArea value={promptDraft} onChange={event => setPromptDraft(event.target.value)} autoSize={{ minRows: 4, maxRows: 10 }} />
                        <Text strong>Code</Text>
                        <TextArea
                          value={codeDraft}
                          onChange={event => setCodeDraft(event.target.value)}
                          autoSize={{ minRows: 4, maxRows: 14 }}
                          style={{ fontFamily: 'monospace', fontSize: 12 }}
                        />
                        <Text strong>Tool Calls</Text>
                        <TextArea value={toolCallsDraft} onChange={event => setToolCallsDraft(event.target.value)} placeholder="host.open_url_in_chrome" autoSize={{ minRows: 2, maxRows: 5 }} />
                        <Text strong>Sub Skill IDs</Text>
                        <TextArea value={subSkillIdsDraft} onChange={event => setSubSkillIdsDraft(event.target.value)} autoSize={{ minRows: 2, maxRows: 5 }} />
                      </Space>
                    ),
                  },
                  {
                    key: 'tests',
                    label: 'Validation test cases',
                    children: (
                      <TextArea
                        value={testCasesDraft}
                        onChange={event => setTestCasesDraft(event.target.value)}
                        autoSize={{ minRows: 6, maxRows: 16 }}
                        style={{ fontFamily: 'monospace', fontSize: 12 }}
                      />
                    ),
                  },
                ]}
              />
              <Space wrap>
                {(['patch', 'minor', 'major'] as const).map(bump => (
                  <Button key={bump} icon={<PlusOutlined />} loading={loading} onClick={() => createVersion(bump)}>
                    Save {bump} version
                  </Button>
                ))}
              </Space>

              <Divider />
              <Text strong>Merge selected Skills into a new version</Text>
              <Alert
                type="warning"
                showIcon
                message="Agent-generalized merge parameterizes common workflows. For example, two website-opening Skills become one URL-input Skill instead of a hard-coded mix of two websites."
                style={{ marginTop: 8, marginBottom: 8 }}
              />
              <Select
                value={mergeStrategy}
                onChange={setMergeStrategy}
                style={{ width: '100%', marginBottom: 8 }}
                options={[
                  { value: 'agent_generalize', label: 'Agent-generalize common workflow' },
                  { value: 'field_union', label: 'Raw field union' },
                ]}
              />
              <Select
                mode="multiple"
                allowClear
                style={{ width: '100%', marginTop: 8, marginBottom: 12 }}
                placeholder="Select source Skills to merge"
                options={mergeOptions}
                value={mergeSourceIds}
                onChange={setMergeSourceIds}
                maxTagCount="responsive"
              />
              <Button
                type="primary"
                icon={<MergeCellsOutlined />}
                disabled={mergeSourceIds.length === 0}
                loading={loading}
                onClick={mergeUpdate}
              >
                Merge into new minor version
              </Button>
            </Card>
          </Col>

          <Col xs={24} lg={10}>
            <Card
              title={<span><AuditOutlined /> Review</span>}
              bordered={false}
              style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
            >
              <Space wrap style={{ marginBottom: 12 }}>
                <Button icon={<AuditOutlined />} loading={loading} onClick={() => runReview(false)}>
                  Review only
                </Button>
                <Popconfirm title="Review and downgrade this Skill if it is unqualified?" onConfirm={() => runReview(true)}>
                  <Button danger icon={<StopOutlined />} loading={loading}>
                    Review + downgrade
                  </Button>
                </Popconfirm>
              </Space>

              {review ? (
                <Alert
                  type={review.is_approved ? 'success' : 'warning'}
                  showIcon
                  message={`${review.status} · score ${scorePercent(review)}%`}
                  description={
                    <div>
                      <Paragraph style={{ marginBottom: 8 }}>{review.summary}</Paragraph>
                      {review.lifecycle_action !== 'none' && <Tag color="gold">{review.lifecycle_action}</Tag>}
                      {review.comments.slice(0, 4).map(comment => (
                        <div key={`${comment.field}-${comment.message}`} style={{ marginTop: 6 }}>
                          <Text strong>{comment.field}</Text>
                          <Text type="secondary"> · {comment.severity}</Text>
                          <div>{comment.message}</div>
                        </div>
                      ))}
                    </div>
                  }
                />
              ) : (
                <Text type="secondary">Run review to evaluate schema, implementation, safety, and lifecycle quality.</Text>
              )}
            </Card>

            <Card
              title={<span><BranchesOutlined /> Version History</span>}
              bordered={false}
              style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
            >
              <Table
                dataSource={visibleVersions}
                rowKey="skill_id"
                loading={versionLoading}
                size="small"
                pagination={false}
                columns={[
                  {
                    title: 'Version',
                    dataIndex: 'version',
                    render: (version: string, row: SkillSummary) => (
                      <Space>
                        <Text code>v{version}</Text>
                        {row.skill_id === selected.skill_id && <Tag color="blue">HEAD</Tag>}
                      </Space>
                    ),
                  },
                  {
                    title: 'State',
                    dataIndex: 'state',
                    render: (state: string) => <Badge color={STATE_COLOR[state]} text={STATE_LABEL[state] || state} />,
                  },
                  {
                    title: 'Updated',
                    dataIndex: 'updated_at',
                    render: (value: string) => new Date(value).toLocaleString(),
                  },
                ]}
              />

              <Timeline
                style={{ marginTop: 20 }}
                items={visibleVersions.map(version => ({
                  color: STATE_COLOR[version.state] || 'blue',
                  children: (
                    <div>
                      <Text code>v{version.version}</Text>
                      <Tag style={{ marginLeft: 8 }} color={STATE_COLOR[version.state]}>{STATE_LABEL[version.state] || version.state}</Tag>
                      <div style={{ fontSize: 11, color: '#8c8c8c' }}>{new Date(version.created_at).toLocaleString()}</div>
                    </div>
                  ),
                }))}
              />
            </Card>
          </Col>
        </Row>
      )}
    </div>
  )
}
