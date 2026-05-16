import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  InputNumber,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import type { TableColumnsType } from 'antd'
import {
  CheckCircleOutlined,
  FileProtectOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import { useLocation } from 'react-router-dom'
import { harnessApi, skillsApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import type {
  HarnessKind,
  HarnessRunResult,
  HarnessVerifyLoopResponse,
  SkillSummary,
} from '@/api/types'

const { Paragraph, Text } = Typography

const STATE_COLOR: Record<string, string> = {
  S0: 'purple',
  S1: 'orange',
  S2: 'blue',
  S3: 'cyan',
  S4: 'green',
  S5: 'gold',
  S6: 'red',
  S7: 'default',
}

const STATUS_COLOR: Record<string, string> = {
  verified: 'green',
  passed: 'green',
  failed: 'red',
  rejected: 'red',
  needs_human_review: 'gold',
  timeout: 'volcano',
  harness_error: 'volcano',
  harness_unavailable: 'default',
}

const HARNESS_LABEL: Record<HarnessKind, string> = {
  local_skillos: 'Local SkillOS',
  codex_cli: 'Codex CLI',
  claude_code: 'Claude Code',
}

function scoreNumber(score: Record<string, unknown> | undefined, key: string) {
  const value = score?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function percent(score: number) {
  return Math.max(0, Math.min(100, Math.round(score * 100)))
}

function formatPercent(value: number) {
  return `${(value * 100).toFixed(1)}%`
}

function formatMs(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(value >= 10 ? 0 : 2)} ms` : '-'
}

function renderJson(value: unknown) {
  return (
    <pre
      style={{
        margin: 0,
        maxHeight: 180,
        overflow: 'auto',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        background: '#f6f8fa',
        border: '1px solid #edf0f3',
        borderRadius: 6,
        padding: 10,
        fontSize: 12,
      }}
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function statusTag(status?: string) {
  if (!status) return <Text type="secondary">-</Text>
  return <Tag color={STATUS_COLOR[status] || 'default'}>{status}</Tag>
}

function stateTag(state?: string) {
  if (!state) return <Text type="secondary">-</Text>
  return <Tag color={STATE_COLOR[state] || 'default'}>{state}</Tag>
}

function attemptTitle(result: HarnessRunResult) {
  const label = HARNESS_LABEL[result.harness] || result.harness
  return `${label} attempt ${result.attempt}`
}

function verifierDetails(result: HarnessRunResult) {
  const details = result.verifier_summary?.details
  if (!details || typeof details !== 'object' || !('results' in details)) return []
  const results = (details as { results?: unknown }).results
  return Array.isArray(results) ? results : []
}

function AttemptTimeline({ attempts }: { attempts: HarnessRunResult[] }) {
  if (!attempts.length) {
    return <Empty description="No harness attempts yet" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }

  return (
    <Timeline
      items={attempts.map(result => ({
        color: result.verifier_passed ? 'green' : 'red',
        dot: result.verifier_passed ? <CheckCircleOutlined /> : <ToolOutlined />,
        children: (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Space wrap>
              <Text strong>{attemptTitle(result)}</Text>
              {statusTag(result.status)}
              <Tag color={result.verifier_passed ? 'green' : 'red'}>
                verifier {result.verifier_passed ? 'passed' : 'failed'}
              </Tag>
              <Text type="secondary">{formatMs(result.latency_ms)}</Text>
            </Space>
            {result.failure_reason && (
              <Alert type="warning" showIcon message={result.failure_reason} style={{ borderRadius: 6 }} />
            )}
            <Row gutter={[12, 12]}>
              <Col xs={24} md={12}>
                <Text type="secondary">Input</Text>
                <div style={{ marginTop: 6 }}>{renderJson(result.input_data)}</div>
              </Col>
              <Col xs={24} md={12}>
                <Text type="secondary">Output</Text>
                <div style={{ marginTop: 6 }}>{renderJson(result.output)}</div>
              </Col>
            </Row>
            {verifierDetails(result).length > 0 && renderJson(verifierDetails(result))}
          </Space>
        ),
      }))}
    />
  )
}

function RepairList({ repairs }: { repairs: Record<string, unknown>[] }) {
  if (!repairs.length) {
    return <Empty description="No repair was needed yet" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {repairs.map((repair, index) => (
        <div
          key={`${String(repair.source || 'repair')}-${index}`}
          style={{
            border: '1px solid #edf0f3',
            borderRadius: 8,
            padding: 12,
            background: '#fbfcfd',
          }}
        >
          <Space wrap style={{ marginBottom: 8 }}>
            <Tag color={repair.success ? 'green' : 'red'}>{repair.success ? 'applied' : 'failed'}</Tag>
            <Tag>{String(repair.source || 'unknown')}</Tag>
            <Text type="secondary">attempt {String(repair.attempt ?? index + 1)}</Text>
          </Space>
          {renderJson(repair)}
        </div>
      ))}
    </Space>
  )
}

export default function HarnessVerification() {
  const location = useLocation()
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [harness, setHarness] = useState<HarnessKind>('local_skillos')
  const [maxAttempts, setMaxAttempts] = useState(3)
  const [timeoutS, setTimeoutS] = useState(120)
  const [allowRepair, setAllowRepair] = useState(true)
  const [promoteOnPass, setPromoteOnPass] = useState(true)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<HarnessVerifyLoopResponse | null>(null)
  const [recentLoops, setRecentLoops] = useState<HarnessVerifyLoopResponse[]>([])

  const querySkillId = useMemo(() => new URLSearchParams(location.search).get('skill_id'), [location.search])

  const draftSkills = useMemo(() => skills.filter(skill => skill.state === 'S2'), [skills])
  const selectedSkill = useMemo(
    () => skills.find(skill => skill.skill_id === selectedId) || null,
    [selectedId, skills],
  )

  const loadData = useCallback(async (preferredId?: string | null) => {
    setLoading(true)
    setError(null)
    try {
      const [skillList, loopList] = await Promise.all([
        skillsApi.list({ limit: 200 }),
        harnessApi.list(20).catch(() => ({ loops: [], total: 0 })),
      ])
      setSkills(skillList)
      setRecentLoops(loopList.loops || [])
      const preferred = preferredId || querySkillId || 'demo_draft_extract_email'
      const preferredExists = skillList.some(skill => skill.skill_id === preferred)
      const demoDraft = skillList.find(skill => skill.skill_id === 'demo_draft_extract_email' && skill.state === 'S2')
      const firstDraft = skillList.find(skill => skill.state === 'S2')
      setSelectedId(current => {
        if (preferredExists) return preferred
        if (current && skillList.some(skill => skill.skill_id === current)) return current
        return demoDraft?.skill_id || firstDraft?.skill_id || null
      })
    } catch (err) {
      setError(getApiErrorMessage(err, 'Load harness data failed'))
    } finally {
      setLoading(false)
    }
  }, [querySkillId])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadData()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [loadData])

  const runVerification = async () => {
    if (!selectedId) return
    setRunning(true)
    setError(null)
    try {
      const response = await harnessApi.runVerifyLoop(selectedId, {
        harness,
        max_attempts: maxAttempts,
        promote_on_pass: promoteOnPass,
        allow_repair: allowRepair,
        timeout_s: timeoutS,
      })
      setResult(response)
      setRecentLoops(prev => [response, ...prev.filter(loop => loop.loop_id !== response.loop_id)].slice(0, 20))
      message.success(`Harness loop ${response.status}`)
      await loadData(selectedId)
    } catch (err) {
      setError(getApiErrorMessage(err, 'Run verification loop failed'))
    } finally {
      setRunning(false)
    }
  }

  const latest = result || recentLoops[0] || null
  const overall = scoreNumber(latest?.score, 'overall')
  const passRate = scoreNumber(latest?.score, 'verifier_pass_rate')

  const loopColumns: TableColumnsType<HarnessVerifyLoopResponse> = [
    {
      title: 'Loop',
      dataIndex: 'loop_id',
      width: 190,
      render: value => <Text code>{String(value)}</Text>,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 150,
      render: statusTag,
    },
    {
      title: 'Score',
      dataIndex: 'score',
      width: 120,
      render: score => formatPercent(scoreNumber(score as Record<string, unknown>, 'overall')),
    },
    {
      title: 'Attempts',
      dataIndex: 'attempt_count',
      width: 110,
      render: value => <Text>{String(value ?? '-')}</Text>,
    },
    {
      title: 'Final State',
      dataIndex: 'final_state',
      width: 120,
      render: stateTag,
    },
    {
      title: 'Evidence',
      dataIndex: 'evidence_path',
      render: value => (
        <Paragraph
          copyable={typeof value === 'string' ? { text: value } : false}
          ellipsis={{ rows: 1, expandable: true, symbol: 'more' }}
          style={{ marginBottom: 0 }}
        >
          {String(value || '-')}
        </Paragraph>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 24 }}>
        <Space wrap align="center" style={{ marginBottom: 4 }}>
          <SafetyCertificateOutlined style={{ color: '#1677ff', fontSize: 22 }} />
          <h2 style={{ margin: 0, fontWeight: 700 }}>Harness Verification</h2>
        </Space>
        <Paragraph type="secondary" style={{ maxWidth: 920, marginBottom: 0 }}>
          Execute S2 Draft Skills in a controlled harness, verify deterministic postconditions, repair failures, and promote only passing Skills to S3.
        </Paragraph>
      </div>

      {error && (
        <Alert
          type="error"
          showIcon
          closable
          title="Harness request failed"
          description={error}
          style={{ marginBottom: 16 }}
          onClose={() => setError(null)}
        />
      )}

      <Spin spinning={loading && !running}>
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={9}>
            <Card title="Verification Control" variant="borderless" style={{ borderRadius: 8 }}>
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <div>
                  <Text strong>Draft Skill</Text>
                  <Select
                    showSearch
                    value={selectedId || undefined}
                    placeholder="Select an S2 Draft Skill"
                    optionFilterProp="label"
                    style={{ width: '100%', marginTop: 8 }}
                    onChange={setSelectedId}
                    options={draftSkills.map(skill => ({
                      value: skill.skill_id,
                      label: `${skill.name} · ${skill.version}`,
                    }))}
                    notFoundContent="No S2 Draft Skills available"
                  />
                </div>

                {selectedSkill ? (
                  <Alert
                    type={selectedSkill.state === 'S2' ? 'info' : 'success'}
                    showIcon
                    message={(
                      <Space wrap>
                        <Text strong>{selectedSkill.name}</Text>
                        {stateTag(selectedSkill.state)}
                        <Tag>{selectedSkill.version}</Tag>
                      </Space>
                    )}
                    description={(
                      <Space direction="vertical" size={6} style={{ width: '100%' }}>
                        <Text>{selectedSkill.description}</Text>
                        <Space wrap>
                          {(selectedSkill.evaluation.verifier_specs || []).slice(0, 3).map((spec, index) => (
                            <Tag key={`${selectedSkill.skill_id}-spec-${index}`} color="blue">
                              {String(spec.type || 'verifier')}
                            </Tag>
                          ))}
                        </Space>
                        {selectedSkill.evaluation.validation_summary && (
                          <Text type="secondary">{selectedSkill.evaluation.validation_summary}</Text>
                        )}
                      </Space>
                    )}
                  />
                ) : (
                  <Empty description="Select a Draft Skill to verify" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                )}

                <div>
                  <Text strong>Harness</Text>
                  <Segmented
                    block
                    value={harness}
                    onChange={value => setHarness(value as HarnessKind)}
                    style={{ marginTop: 8 }}
                    options={[
                      { label: 'Local', value: 'local_skillos' },
                      { label: 'Codex CLI', value: 'codex_cli' },
                      { label: 'Claude Code', value: 'claude_code', disabled: true },
                    ]}
                  />
                </div>

                <Row gutter={12}>
                  <Col span={12}>
                    <Text strong>Max Attempts</Text>
                    <InputNumber
                      min={1}
                      max={5}
                      value={maxAttempts}
                      onChange={value => setMaxAttempts(value || 3)}
                      style={{ width: '100%', marginTop: 8 }}
                    />
                  </Col>
                  <Col span={12}>
                    <Text strong>Timeout</Text>
                    <InputNumber
                      min={1}
                      max={600}
                      value={timeoutS}
                      addonAfter="s"
                      onChange={value => setTimeoutS(value || 120)}
                      style={{ width: '100%', marginTop: 8 }}
                    />
                  </Col>
                </Row>

                <Space direction="vertical" size={10}>
                  <Space>
                    <Switch checked={allowRepair} onChange={setAllowRepair} />
                    <Text>Allow repair and retry</Text>
                  </Space>
                  <Space>
                    <Switch checked={promoteOnPass} onChange={setPromoteOnPass} />
                    <Text>Promote to S3 on pass</Text>
                  </Space>
                </Space>

                <Space wrap>
                  <Button icon={<ReloadOutlined />} onClick={() => void loadData(selectedId)} disabled={running}>
                    Refresh
                  </Button>
                  <Button
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    loading={running}
                    disabled={!selectedSkill || selectedSkill.state !== 'S2'}
                    onClick={runVerification}
                  >
                    Run Verification Loop
                  </Button>
                </Space>
              </Space>
            </Card>
          </Col>

          <Col xs={24} lg={15}>
            <Row gutter={[16, 16]}>
              <Col xs={24} md={8}>
                <Card variant="borderless" style={{ borderRadius: 8 }}>
                  <Text type="secondary">Latest Status</Text>
                  <div style={{ marginTop: 12 }}>
                    {latest ? statusTag(latest.status) : <Tag>no run</Tag>}
                  </div>
                </Card>
              </Col>
              <Col xs={24} md={8}>
                <Card variant="borderless" style={{ borderRadius: 8 }}>
                  <Text type="secondary">Overall Score</Text>
                  <Progress percent={percent(overall)} size="small" style={{ marginTop: 14 }} />
                  <Text strong>{formatPercent(overall)}</Text>
                </Card>
              </Col>
              <Col xs={24} md={8}>
                <Card variant="borderless" style={{ borderRadius: 8 }}>
                  <Text type="secondary">Verifier Pass Rate</Text>
                  <Progress percent={percent(passRate)} size="small" status={passRate >= 1 ? 'success' : 'active'} style={{ marginTop: 14 }} />
                  <Text strong>{formatPercent(passRate)}</Text>
                </Card>
              </Col>
            </Row>

            <Card
              title="Latest Evidence"
              extra={latest ? <Text type="secondary">{latest.loop_id}</Text> : null}
              variant="borderless"
              style={{ borderRadius: 8, marginTop: 16 }}
            >
              {latest ? (
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <Descriptions size="small" column={{ xs: 1, md: 3 }} bordered>
                    <Descriptions.Item label="Skill">{latest.skill_id}</Descriptions.Item>
                    <Descriptions.Item label="Version">
                      {latest.initial_version}
                      {' -> '}
                      {latest.final_version}
                    </Descriptions.Item>
                    <Descriptions.Item label="Final State">{stateTag(latest.final_state)}</Descriptions.Item>
                    <Descriptions.Item label="Attempts">{latest.attempt_count}</Descriptions.Item>
                    <Descriptions.Item label="Promotion">{latest.promotion_allowed ? <Tag color="green">allowed</Tag> : <Tag>blocked</Tag>}</Descriptions.Item>
                    <Descriptions.Item label="Latency">{formatMs(latest.score.latency_ms)}</Descriptions.Item>
                  </Descriptions>
                  <Alert
                    type={latest.status === 'verified' ? 'success' : 'warning'}
                    showIcon
                    message="Evidence Path"
                    description={(
                      <Text copyable={{ text: latest.evidence_path }} style={{ wordBreak: 'break-all' }}>
                        {latest.evidence_path}
                      </Text>
                    )}
                  />
                  <AttemptTimeline attempts={latest.attempts || []} />
                </Space>
              ) : (
                <Empty description="Run a verification loop to create evidence" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col xs={24} lg={9}>
            <Card title="Repair Evidence" variant="borderless" style={{ borderRadius: 8 }}>
              <RepairList repairs={latest?.repairs || []} />
            </Card>
          </Col>
          <Col xs={24} lg={15}>
            <Card
              title="Recent Harness Loops"
              extra={<Text type="secondary">{recentLoops.length} saved</Text>}
              variant="borderless"
              style={{ borderRadius: 8 }}
            >
              <Table
                dataSource={recentLoops}
                columns={loopColumns}
                rowKey="loop_id"
                size="small"
                pagination={{ pageSize: 6 }}
                scroll={{ x: 900 }}
                locale={{ emptyText: 'No saved harness evidence yet' }}
              />
            </Card>
          </Col>
        </Row>

        <Alert
          type="info"
          showIcon
          icon={<FileProtectOutlined />}
          style={{ marginTop: 16, borderRadius: 8 }}
          message="Demo path"
          description="Use the seeded demo_draft_extract_email Draft Skill with Local SkillOS. The first attempt fails because output.email is missing, the repair loop patches the implementation, and the repaired version is promoted to S3 after verifier pass."
        />
      </Spin>
    </div>
  )
}
