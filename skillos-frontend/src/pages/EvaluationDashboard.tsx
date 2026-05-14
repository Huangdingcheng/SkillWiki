import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Progress,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ReloadOutlined } from '@ant-design/icons'
import { evaluationApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import type {
  EvaluationDashboardResponse,
  EvaluationDemoBenchmark,
  EvaluationDemoRow,
  EvaluationLlmRow,
  EvaluationModeTotal,
  EvaluationSearchEval,
  EvaluationSearchRow,
} from '@/api/types'

const { Text, Paragraph } = Typography

const modeLabels: Record<string, string> = {
  no_skill: 'No Skill',
  raw_prompt: 'Raw Prompt',
  with_skill: 'With Skill',
  fallback: 'Fallback Planner',
  llm: 'LLM Planner',
}

const statusColors: Record<string, string> = {
  success: 'green',
  passed: 'green',
  failed: 'red',
  functional_failure: 'red',
  api_failure: 'volcano',
  skipped: 'default',
  incomplete_llm: 'gold',
  with_skill: 'blue',
}

function formatPercent(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 'N/A'
  return `${(value * 100).toFixed(1)}%`
}

function percentNumber(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0
  return Math.round(value * 1000) / 10
}

function formatLatency(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 'N/A'
  return `${value.toFixed(value >= 10 ? 0 : 2)} ms`
}

function statusTag(status?: string | null) {
  if (!status) return <Text type="secondary">N/A</Text>
  return <Tag color={statusColors[status] || 'default'}>{status}</Tag>
}

function verifierTag(value?: boolean | null) {
  if (value === true) return <Tag color="green">pass</Tag>
  if (value === false) return <Tag color="red">fail</Tag>
  return <Text type="secondary">N/A</Text>
}

function artifactTime(artifact: { generated_at?: string | null; updated_at?: string | null }) {
  return artifact.generated_at || artifact.updated_at || 'N/A'
}

function ModeCard({ mode, total }: { mode: string; total?: EvaluationModeTotal }) {
  const success = total?.success ?? 0
  const count = total?.total ?? 0
  const rate = total?.success_rate ?? total?.success_rate_excluding_api_failures ?? null
  return (
    <div style={{ background: '#fafafa', borderRadius: 8, padding: 16 }}>
      <Statistic
        title={modeLabels[mode] || mode}
        value={`${success}/${count}`}
        styles={{ content: { color: rate && rate > 0 ? '#1677ff' : '#8c8c8c', fontWeight: 700 } }}
      />
      <Progress
        percent={percentNumber(rate)}
        size="small"
        status={rate && rate > 0 ? 'active' : 'normal'}
        style={{ marginTop: 8 }}
      />
      <Text type="secondary">{formatPercent(rate)}</Text>
    </div>
  )
}

function SuccessRateBars({ artifact }: { artifact: EvaluationDemoBenchmark }) {
  const modes = ['no_skill', 'raw_prompt', 'with_skill']
  return (
    <Space orientation="vertical" style={{ width: '100%' }} size={12}>
      {modes.map(mode => {
        const total = artifact.mode_totals[mode]
        const pct = percentNumber(total?.success_rate)
        return (
          <div key={mode}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <Text>{modeLabels[mode]}</Text>
              <Text strong>{formatPercent(total?.success_rate)}</Text>
            </div>
            <div style={{ height: 12, background: '#f0f0f0', borderRadius: 4, overflow: 'hidden' }}>
              <div
                style={{
                  width: `${pct}%`,
                  height: '100%',
                  background: mode === 'with_skill' ? '#1677ff' : '#d9d9d9',
                }}
              />
            </div>
          </div>
        )
      })}
    </Space>
  )
}

function DemoBenchmarkPanel({ artifact }: { artifact: EvaluationDemoBenchmark }) {
  const columns: ColumnsType<EvaluationDemoRow> = [
    {
      title: 'Task',
      dataIndex: 'task_id',
      key: 'task_id',
      width: 260,
      render: value => <Text code>{value}</Text>,
    },
    {
      title: 'Category',
      dataIndex: 'domain',
      key: 'domain',
      width: 110,
      render: value => <Tag>{value || 'unknown'}</Tag>,
    },
    {
      title: 'No Skill',
      dataIndex: 'no_skill',
      key: 'no_skill',
      width: 110,
      render: statusTag,
    },
    {
      title: 'Raw Prompt',
      dataIndex: 'raw_prompt',
      key: 'raw_prompt',
      width: 120,
      render: statusTag,
    },
    {
      title: 'With Skill',
      dataIndex: 'with_skill',
      key: 'with_skill',
      width: 120,
      render: statusTag,
    },
    {
      title: 'Verifier',
      key: 'verifier',
      width: 120,
      render: (_, row) => verifierTag(row.with_skill_verifier_passed),
    },
    {
      title: 'Latency',
      key: 'latency',
      width: 120,
      render: (_, row) => formatLatency(row.with_skill_latency_ms),
    },
    {
      title: 'Failure Reason',
      dataIndex: 'failure_reason',
      key: 'failure_reason',
      render: value => (
        <Paragraph ellipsis={{ rows: 2, expandable: true, symbol: 'more' }} style={{ marginBottom: 0 }}>
          {value || 'N/A'}
        </Paragraph>
      ),
    },
  ]

  return (
    <Card
      title="Demo Benchmark"
      extra={<Text type="secondary">{artifact.source_file} · {artifactTime(artifact)}</Text>}
      variant="borderless"
      style={{ borderRadius: 8 }}
    >
      <Row gutter={[16, 16]}>
        {['no_skill', 'raw_prompt', 'with_skill'].map(mode => (
          <Col xs={24} md={8} key={mode}>
            <ModeCard mode={mode} total={artifact.mode_totals[mode]} />
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={8}>
          <div style={{ background: '#fafafa', borderRadius: 8, padding: 16 }}>
            <Text strong style={{ display: 'block', marginBottom: 12 }}>Success Rate</Text>
            <SuccessRateBars artifact={artifact} />
          </div>
        </Col>
        <Col xs={24} lg={16}>
          <Table
            dataSource={artifact.rows}
            columns={columns}
            rowKey="task_id"
            size="small"
            pagination={{ pageSize: 8 }}
            scroll={{ x: 980 }}
            expandable={{
              expandedRowRender: row => (
                <Space orientation="vertical" size={4}>
                  <Text>No Skill latency: {formatLatency(row.no_skill_latency_ms)} · verifier: {verifierTag(row.no_skill_verifier_passed)}</Text>
                  <Text>Raw Prompt latency: {formatLatency(row.raw_prompt_latency_ms)} · verifier: {verifierTag(row.raw_prompt_verifier_passed)}</Text>
                  <Text>With Skill latency: {formatLatency(row.with_skill_latency_ms)} · verifier: {verifierTag(row.with_skill_verifier_passed)}</Text>
                </Space>
              ),
            }}
          />
        </Col>
      </Row>
    </Card>
  )
}

function SearchEvalPanel({ artifact }: { artifact: EvaluationSearchEval }) {
  const summary = artifact.comparison as {
    lexical?: { top1_hit_rate?: number; top3_hit_rate?: number }
    hybrid?: { top1_hit_rate?: number; top3_hit_rate?: number }
    delta?: { top1_hit_rate?: number; top3_hit_rate?: number }
  }
  const columns: ColumnsType<EvaluationSearchRow> = [
    { title: 'Query', dataIndex: 'query', key: 'query', width: 240 },
    {
      title: 'Expected',
      dataIndex: 'expected_skill_ids',
      key: 'expected_skill_ids',
      width: 180,
      render: ids => ((ids as string[]) || []).map(id => <Tag key={id}>{id}</Tag>),
    },
    { title: 'Lexical Top', dataIndex: 'lexical_top_skill', key: 'lexical_top_skill', width: 170 },
    { title: 'Hybrid Top', dataIndex: 'hybrid_top_skill', key: 'hybrid_top_skill', width: 170 },
    {
      title: 'Lexical Hit',
      dataIndex: 'lexical_top1_hit',
      key: 'lexical_top1_hit',
      width: 110,
      render: verifierTag,
    },
    {
      title: 'Hybrid Hit',
      dataIndex: 'hybrid_top1_hit',
      key: 'hybrid_top1_hit',
      width: 110,
      render: verifierTag,
    },
  ]
  return (
    <Card
      title="Search Evaluation"
      extra={<Text type="secondary">{artifact.source_file} · {artifact.schema_version || 'schema N/A'}</Text>}
      variant="borderless"
      style={{ borderRadius: 8 }}
    >
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={8}>
          <Statistic title="Queries" value={artifact.query_count} />
        </Col>
        <Col xs={24} sm={8}>
          <Statistic title="Lexical Top-1" value={formatPercent(summary.lexical?.top1_hit_rate)} />
        </Col>
        <Col xs={24} sm={8}>
          <Statistic title="Hybrid Top-1" value={formatPercent(summary.hybrid?.top1_hit_rate)} />
        </Col>
      </Row>
      <Alert
        type="info"
        showIcon
        title={`Observed hybrid delta: Top-1 ${formatPercent(summary.delta?.top1_hit_rate)}, Top-3 ${formatPercent(summary.delta?.top3_hit_rate)}`}
        style={{ marginBottom: 16 }}
      />
      <Table
        dataSource={artifact.rows}
        columns={columns}
        rowKey={row => row.query_id || row.query || 'query-row'}
        size="small"
        pagination={{ pageSize: 6 }}
        scroll={{ x: 980 }}
      />
    </Card>
  )
}

function LlmPlannerPanel({ artifact }: { artifact: EvaluationDashboardResponse['artifacts']['llm_planner'] }) {
  const columns: ColumnsType<EvaluationLlmRow> = [
    { title: 'Task', dataIndex: 'task_id', key: 'task_id', width: 240, render: value => <Text code>{value}</Text> },
    { title: 'Fallback', dataIndex: 'fallback_status', key: 'fallback_status', width: 140, render: statusTag },
    { title: 'LLM', dataIndex: 'llm_status', key: 'llm_status', width: 120, render: statusTag },
    { title: 'API State', dataIndex: 'llm_api_error_type', key: 'llm_api_error_type', width: 140, render: value => value || 'N/A' },
    {
      title: 'Failure Reason',
      dataIndex: 'llm_failure_reason',
      key: 'llm_failure_reason',
      render: value => (
        <Paragraph ellipsis={{ rows: 2, expandable: true, symbol: 'more' }} style={{ marginBottom: 0 }}>
          {value || 'N/A'}
        </Paragraph>
      ),
    },
  ]
  return (
    <Card
      title="Planner Evaluation"
      extra={<Text type="secondary">{artifact.source_file} · {artifactTime(artifact)}</Text>}
      variant="borderless"
      style={{ borderRadius: 8 }}
    >
      <Alert
        type="warning"
        showIcon
        title="LLM planner rows marked skipped are incomplete runs, not model-quality failures."
        style={{ marginBottom: 16 }}
      />
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {['fallback', 'llm'].map(mode => (
          <Col xs={24} sm={12} key={mode}>
            <ModeCard mode={mode} total={artifact.mode_totals[mode]} />
          </Col>
        ))}
      </Row>
      <Table
        dataSource={artifact.rows}
        columns={columns}
        rowKey={row => row.task_id || 'planner-row'}
        size="small"
        pagination={{ pageSize: 6 }}
        scroll={{ x: 920 }}
      />
    </Card>
  )
}

export default function EvaluationDashboard() {
  const [data, setData] = useState<EvaluationDashboardResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadData = useCallback(async (initial = false) => {
    if (initial) setLoading(true)
    else setRefreshing(true)
    try {
      const response = await evaluationApi.dashboard()
      setData(response)
      setError(null)
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, 'Evaluation data load failed'))
    } finally {
      if (initial) setLoading(false)
      else setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    evaluationApi.dashboard()
      .then(response => {
        if (!active) return
        setData(response)
        setError(null)
      })
      .catch((e: unknown) => {
        if (!active) return
        setError(getApiErrorMessage(e, 'Evaluation data load failed'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const warnings = useMemo(() => data?.warnings ?? [], [data])
  const demo = data?.artifacts.demo_benchmark
  const search = data?.artifacts.search_eval
  const planner = data?.artifacts.llm_planner

  if (loading && !data) {
    return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Evaluation Dashboard</h2>
          <Text type="secondary">Demo benchmark evidence for paper-ready screenshots</Text>
        </div>
        <Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {data ? `Loaded ${data.generated_at}` : 'Not loaded'}
          </Text>
          <Button icon={<ReloadOutlined spin={refreshing} />} loading={refreshing} onClick={() => loadData(false)}>
            Refresh
          </Button>
        </Space>
      </div>

      {error && (
        <Alert
          type="error"
          showIcon
          title="Evaluation data load failed"
          description={error}
          action={<Button size="small" onClick={() => loadData(true)}>Retry</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {warnings.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title="Artifact warnings"
          description={warnings.join(' | ')}
          style={{ marginBottom: 16 }}
        />
      )}

      {!data?.results_dir_present && (
        <Alert
          type="info"
          showIcon
          title="Benchmark results directory is not present in this runtime."
          style={{ marginBottom: 16 }}
        />
      )}

      <Space orientation="vertical" size={16} style={{ width: '100%' }}>
        {demo?.available ? (
          <DemoBenchmarkPanel artifact={demo} />
        ) : (
          <Card variant="borderless" style={{ borderRadius: 8 }}>
            <Empty description={demo?.error || 'No demo benchmark result available'} />
          </Card>
        )}

        {search?.available ? (
          <SearchEvalPanel artifact={search} />
        ) : (
          <Card title="Search Evaluation" variant="borderless" style={{ borderRadius: 8 }}>
            <Empty description={search?.error || 'No search evaluation result available'} />
          </Card>
        )}

        {planner?.available ? (
          <LlmPlannerPanel artifact={planner} />
        ) : (
          <Card title="Planner Evaluation" variant="borderless" style={{ borderRadius: 8 }}>
            <Empty description={planner?.error || 'No planner evaluation result available'} />
          </Card>
        )}
      </Space>
    </div>
  )
}
