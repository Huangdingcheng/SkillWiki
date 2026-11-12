import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
  HistoricalBenchFlowRun,
  CurrentBenchFlowRun,
  FormalBenchFlowCondition,
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
  local_skillwiki: 'Local SkillWiki',
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

function observedNumber(value: number | null | undefined, currency = false) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'Not reported'
  return currency ? `$${value.toFixed(6)}` : String(value)
}

function HistoricalBenchFlowEvidence() {
  const [runs, setRuns] = useState<HistoricalBenchFlowRun[]>([])
  const [total, setTotal] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [detail, setDetail] = useState<HistoricalBenchFlowRun | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const detailRequest = useRef(0)

  const loadRuns = useCallback(async () => {
    setLoading(true)
    setError(null)
    setDetail(null)
    setDetailError(null)
    setSelectedRunId(null)
    setDetailLoading(false)
    detailRequest.current += 1
    try {
      const response = await harnessApi.benchflowRuns()
      setRuns(response.runs)
      setTotal(response.total)
    } catch (err) {
      setRuns([])
      setTotal(null)
      setError(getApiErrorMessage(err, 'Load historical BenchFlow evidence failed'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadRuns() }, 0)
    return () => window.clearTimeout(timer)
  }, [loadRuns])

  const loadDetail = async (runId: string) => {
    const request = ++detailRequest.current
    setSelectedRunId(runId)
    setDetail(null)
    setDetailError(null)
    setDetailLoading(true)
    try {
      const response = await harnessApi.benchflowRun(runId)
      if (request !== detailRequest.current) return
      setDetail(response)
    } catch (err) {
      if (request !== detailRequest.current) return
      setDetailError(getApiErrorMessage(err, 'Load historical run details failed'))
    } finally {
      if (request === detailRequest.current) setDetailLoading(false)
    }
  }

  const columns: TableColumnsType<HistoricalBenchFlowRun> = [
    { title: 'Historical run', render: (_, run) => <Space direction="vertical" size={0}><Text strong>{run.label}</Text><Text code>{run.run_id}</Text><Text type="secondary">{run.task_id} · {run.skill_mode}</Text></Space> },
    { title: 'Agent / requested model', render: (_, run) => <Space direction="vertical" size={0}><Text>{run.agent}</Text><Text type="secondary">{run.requested_model}</Text></Space> },
    { title: 'Observed reward', render: (_, run) => observedNumber(run.observed_reward) },
    { title: 'Estimated USD (not a bill)', render: (_, run) => observedNumber(run.cost?.usd, true) },
    { title: 'Evidence integrity', render: (_, run) => <Tag color={run.evidence_integrity === 'verified' ? 'blue' : 'red'}>{run.evidence_integrity}</Tag> },
    { title: 'Admission', render: (_, run) => <Text>{run.admission_status}</Text> },
    { title: 'Details', render: (_, run) => <Button size="small" disabled={loading} onClick={() => void loadDetail(run.run_id)}>View details</Button> },
  ]

  return (
    <Card title="Historical BenchFlow real-run evidence" variant="borderless" style={{ borderRadius: 8, marginBottom: 20 }}
      extra={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => void loadRuns()}>Refresh historical evidence</Button>}>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Alert type="warning" showIcon message="Historical observation only — current Skill version is not bound"
          description="These records are separate from verification loops. Observed rewards do not verify the selected Skill. Source-manifest proof is missing; model-result eligibility and promotion remain blocked. USD costs are estimates, not provider bills. Refresh and details only read saved evidence." />
        {error && <Alert type="error" showIcon message="Historical evidence request failed" description={error} />}
        {total !== null && !error && <Text type="secondary">{total} historical record(s) reported by the backend</Text>}
        <Table dataSource={runs} columns={columns} rowKey="run_id" size="small" loading={loading} pagination={{ pageSize: 5 }} scroll={{ x: 1100 }}
          locale={{ emptyText: error ? 'Evidence unavailable because the request failed' : loading ? 'Loading historical records' : 'No historical BenchFlow records reported' }} />
        {selectedRunId && <Card size="small" title={<Text code>{selectedRunId}</Text>}
          extra={<Button size="small" loading={detailLoading} onClick={() => void loadDetail(selectedRunId)}>Refresh details</Button>}>
          {detailError && <Alert type="error" showIcon message="Historical detail request failed" description={detailError} />}
          {detailLoading && <Spin description="Loading saved run details" />}
          {detail && <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions size="small" column={{ xs: 1, md: 2 }} bordered>
              <Descriptions.Item label="Evidence kind">{detail.evidence_kind}</Descriptions.Item>
              <Descriptions.Item label="Task">{detail.task_id}</Descriptions.Item>
              <Descriptions.Item label="Task digest" span={2}><Text code copyable>{detail.task_digest}</Text></Descriptions.Item>
              <Descriptions.Item label="Agent">{detail.agent}</Descriptions.Item>
              <Descriptions.Item label="Requested model">{detail.requested_model}</Descriptions.Item>
              <Descriptions.Item label="Skill mode">{detail.skill_mode}</Descriptions.Item>
              <Descriptions.Item label="Sandbox">{detail.sandbox}</Descriptions.Item>
              <Descriptions.Item label="Observed reward">{observedNumber(detail.observed_reward)} (historical observation)</Descriptions.Item>
              <Descriptions.Item label="Estimated USD">{observedNumber(detail.cost?.usd, true)} — not a bill</Descriptions.Item>
              <Descriptions.Item label="Tokens: input / output / total">{observedNumber(detail.cost?.input_tokens)} / {observedNumber(detail.cost?.output_tokens)} / {observedNumber(detail.cost?.total_tokens)}</Descriptions.Item>
              <Descriptions.Item label="Usage source">{detail.cost?.usage_source || 'Not reported'}</Descriptions.Item>
              <Descriptions.Item label="Evidence integrity">{detail.evidence_integrity}</Descriptions.Item>
              <Descriptions.Item label="Runtime attestation valid">{String(detail.runtime_attestation_valid)}</Descriptions.Item>
              <Descriptions.Item label="Admission status">{detail.admission_status}</Descriptions.Item>
              <Descriptions.Item label="Admission reason">{detail.admission_reason}</Descriptions.Item>
              <Descriptions.Item label="Current Skill binding">Unbound (skill_binding: {JSON.stringify(detail.skill_binding)})</Descriptions.Item>
              <Descriptions.Item label="Source manifest present">{String(detail.source_manifest_present)} — proof missing</Descriptions.Item>
              <Descriptions.Item label="Model result eligible">{String(detail.model_result_eligible)}</Descriptions.Item>
              <Descriptions.Item label="Promotion allowed">{String(detail.promotion_allowed)} — blocked</Descriptions.Item>
            </Descriptions>
            {detail.limitations.length > 0 && <Alert type="warning" showIcon message="Evidence limitations" description={<ul style={{ margin: 0, paddingLeft: 20 }}>{detail.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul>} />}
            <Text strong>Raw artifact references</Text>
            <Table dataSource={detail.raw_artifacts} rowKey="path" size="small" pagination={false} scroll={{ x: 800 }}
              columns={[{ title: 'Path', dataIndex: 'path', render: value => <Text code copyable style={{ wordBreak: 'break-all' }}>{value}</Text> }, { title: 'SHA-256', dataIndex: 'sha256', render: value => <Text code copyable style={{ wordBreak: 'break-all' }}>{value}</Text> }, { title: 'Bytes', dataIndex: 'size' }]}
              locale={{ emptyText: 'No raw artifact references returned' }} />
            <Text strong>Cost metadata (estimate, not billing evidence)</Text>
            {renderJson(detail.cost)}
          </Space>}
        </Card>}
      </Space>
    </Card>
  )
}

function CurrentBenchFlowEvidence() {
  const [runs, setRuns] = useState<CurrentBenchFlowRun[]>([])
  const [matrix, setMatrix] = useState<FormalBenchFlowCondition[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const loadRequest = useRef(0)
  const load = useCallback(() => {
    const request = ++loadRequest.current
    return harnessApi.currentBenchflowRuns().then(response => {
      if (request !== loadRequest.current) return
      setRuns(response.runs)
      setMatrix(response.formal_matrix || [])
    }).catch(err => {
      if (request === loadRequest.current) {
        setError(getApiErrorMessage(err, 'Load current BenchFlow evidence failed'))
      }
    }).finally(() => {
      if (request === loadRequest.current) setLoading(false)
    })
  }, [])
  useEffect(() => {
    void load()
    return () => { loadRequest.current += 1 }
  }, [load])
  const refresh = () => {
    setLoading(true)
    setError(null)
    void load()
  }
  const columns: TableColumnsType<CurrentBenchFlowRun> = [
    { title: 'Condition', render: (_, run) => <Space direction="vertical" size={0}><Text strong>{run.condition}</Text><Text code>{run.run_id}</Text></Space> },
    { title: 'Status', render: (_, run) => <Tag color={run.status === 'completed' ? 'green' : 'volcano'}>{run.status}</Tag> },
    { title: 'Skill / model', render: (_, run) => <Space direction="vertical" size={0}><Text>{run.skill_mode}</Text><Text type="secondary">{run.observed_model || 'Not observed'}</Text></Space> },
    { title: 'Reward', render: (_, run) => run.observed_reward == null ? 'Not reported' : String(run.observed_reward) },
    { title: 'Evidence', render: (_, run) => <Tag color={run.evidence_integrity === 'verified' ? 'blue' : 'red'}>{run.evidence_integrity}</Tag> },
  ]
  const matrixColumns: TableColumnsType<FormalBenchFlowCondition> = [
    { title: 'Formal slot', dataIndex: 'formal_slot' },
    { title: 'Condition', dataIndex: 'condition' },
    { title: 'Status', render: (_, row) => <Tag color={row.status === 'completed' ? 'green' : 'default'}>{row.status}</Tag> },
    { title: 'Verifier', render: (_, row) => row.verifier_ran ? 'Ran' : 'Not run' },
    { title: 'Reward', render: (_, row) => row.observed_reward == null ? 'Not reported' : String(row.observed_reward) },
    { title: 'Attempts', dataIndex: 'attempt_count' },
    { title: 'Latest attempt', render: (_, row) => row.run_id || 'Not started' },
  ]
  return <Card title="Current M2 BenchFlow runs" variant="borderless" style={{ borderRadius: 8, marginBottom: 20 }} extra={<Button icon={<ReloadOutlined />} loading={loading} onClick={refresh}>Refresh current runs</Button>}>
    {error && <Alert type="error" showIcon message={error} />}
    <Alert type="info" showIcon message="Fresh runs only" description="These rows are receipt-bound to the current M2/M5 evidence directory. Historical pilots and fixtures are shown separately; a row never grants promotion." style={{ marginBottom: 12 }} />
    <Text strong>Formal comparison: six conditions</Text>
    <Table rowKey="formal_slot" loading={loading} columns={matrixColumns} dataSource={matrix} pagination={false} style={{ marginTop: 8, marginBottom: 20 }} />
    <Text strong>Registered attempts (separate from the six-condition denominator)</Text>
    <Table rowKey="run_id" loading={loading} columns={columns} dataSource={runs} pagination={false} locale={{ emptyText: 'No fresh M2 runs registered yet' }} />
  </Card>
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
  const [harness, setHarness] = useState<HarnessKind>('local_skillwiki')
  const [maxAttempts, setMaxAttempts] = useState(3)
  const [timeoutS, setTimeoutS] = useState(120)
  const [allowRepair, setAllowRepair] = useState(true)
  const [promoteOnPass, setPromoteOnPass] = useState(true)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<HarnessVerifyLoopResponse | null>(null)
  const [recentLoops, setRecentLoops] = useState<HarnessVerifyLoopResponse[]>([])
  const loadSequence = useRef(0)

  const querySkillId = useMemo(() => new URLSearchParams(location.search).get('skill_id'), [location.search])

  const selectedSkill = useMemo(
    () => skills.find(skill => skill.skill_id === selectedId) || null,
    [selectedId, skills],
  )

  const loadData = useCallback(async (preferredId?: string | null) => {
    const sequence = ++loadSequence.current
    setLoading(true)
    setError(null)
    try {
      const [draftList, loopList] = await Promise.all([
        skillsApi.list({ state: 'S2', limit: 200 }),
        harnessApi.list(20),
      ])
      const explicitId = preferredId || querySkillId
      // Recovery drafts and older identities are intentionally absent from the
      // default latest-only catalog. Resolve the linked identity explicitly.
      const linkedSkill = explicitId && !draftList.some(skill => skill.skill_id === explicitId)
        ? await skillsApi.get(explicitId)
        : null
      const skillList = linkedSkill ? [linkedSkill, ...draftList] : draftList
      if (sequence !== loadSequence.current) return
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
      if (sequence !== loadSequence.current) return
      setSelectedId(null)
      setSkills([])
      setResult(null)
      setError(getApiErrorMessage(err, 'Load harness data failed'))
    } finally {
      if (sequence === loadSequence.current) setLoading(false)
    }
  }, [querySkillId])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadData()
    }, 0)
    return () => {
      window.clearTimeout(timer)
      loadSequence.current += 1
    }
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

  const selectedLoops = recentLoops.filter(loop => loop.skill_id === selectedId)
  const latest = (result?.skill_id === selectedId ? result : null) || selectedLoops[0] || null
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

      <HistoricalBenchFlowEvidence />
      <CurrentBenchFlowEvidence />

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
                    options={skills.filter(skill => skill.state === 'S2' || skill.skill_id === selectedId).map(skill => ({
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
                      { label: 'Local', value: 'local_skillwiki' },
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
          description="Use the seeded demo_draft_extract_email Draft Skill with Local SkillWiki. The first attempt fails because output.email is missing, the repair loop patches the implementation, and the repaired version is promoted to S3 after verifier pass."
        />
      </Spin>
    </div>
  )
}
