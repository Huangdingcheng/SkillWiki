import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Popconfirm,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import type { TableColumnsType } from 'antd'
import {
  BranchesOutlined,
  DiffOutlined,
  HistoryOutlined,
  PlusOutlined,
  ReloadOutlined,
  RocketOutlined,
  RollbackOutlined,
  SaveOutlined,
  TagOutlined,
} from '@ant-design/icons'
import { motion } from 'framer-motion'
import { useLocation } from 'react-router-dom'
import { lifecycleApi, skillsApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import type {
  BusinessDiffEntry,
  BusinessDiffSummary,
  NewVersionRequest,
  SkillReleaseRecord,
  SkillRollbackRecord,
  SkillSummary,
  SnapshotCommitResponse,
  SnapshotDiffResponse,
  SnapshotHistoryItem,
  StructuredDiffEntry,
} from '@/api/types'

const { Text } = Typography

const STATE_COLOR: Record<string, string> = {
  S4: 'green',
  S3: 'cyan',
  S2: 'blue',
  S1: 'orange',
  S5: 'gold',
  S6: 'red',
  S7: 'default',
  S0: 'purple',
}

const STATE_LABEL: Record<string, string> = {
  S0: 'Raw',
  S1: 'Candidate',
  S2: 'Draft',
  S3: 'Verified',
  S4: 'Released',
  S5: 'Degraded',
  S6: 'Deprecated',
  S7: 'Archived',
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
  skill_name?: string
  current_version?: string
  business_diff?: BusinessDiffEntry[]
  business_summary?: BusinessDiffSummary
  breaking?: boolean
  suggested_bump?: 'major' | 'minor' | 'patch'
  history?: {
    record_id: string
    from_version?: string
    to_version: string
    change_type: string
    summary: string
    author: string
    created_at: string
    diff: DiffLine[]
    is_breaking: boolean
  }[]
}

interface VersionLabForm {
  bump: 'major' | 'minor' | 'patch'
  description?: string
  tags?: string
  interface_json?: string
  implementation_json?: string
  evaluation_json?: string
}

function semverCompare(a: string, b: string) {
  const pa = a.split('.').map(Number)
  const pb = b.split('.').map(Number)
  for (let i = 0; i < 3; i += 1) {
    if ((pa[i] || 0) !== (pb[i] || 0)) return (pb[i] || 0) - (pa[i] || 0)
  }
  return 0
}

function shortRef(ref?: string | null) {
  if (!ref) return ''
  return ref.length > 14 ? ref.slice(0, 12) : ref
}

function dateText(value?: string) {
  if (!value) return '-'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

function diffField(entry: StructuredDiffEntry) {
  return String(entry.field_path ?? entry.field ?? 'skill')
}

function diffChange(entry: StructuredDiffEntry) {
  return String(entry.change_type ?? entry.type ?? 'modified')
}

function diffCategory(entry: StructuredDiffEntry) {
  return String(entry.category ?? 'general')
}

function isBreakingDiff(entry: StructuredDiffEntry) {
  return entry.is_breaking === true || diffCategory(entry).toLowerCase().includes('breaking')
}

function renderValue(value: unknown) {
  if (value === undefined || value === null || value === '') {
    return <Text type="secondary">-</Text>
  }
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return <Text>{String(value)}</Text>
  }
  return (
    <pre style={{
      margin: 0,
      maxHeight: 120,
      overflow: 'auto',
      fontSize: 12,
      background: '#f6f8fa',
      borderRadius: 6,
      padding: 8,
    }}
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function DiffView({ lines }: { lines: DiffLine[] }) {
  if (!lines.length) return <Empty description="No version diff records" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  return (
    <div style={{ fontFamily: 'monospace', fontSize: 12 }}>
      {lines.map((line, i) => (
        <div key={`${line.field}-${i}`} style={{ marginBottom: 12 }}>
          <div style={{ fontWeight: 600, color: '#1677ff', marginBottom: 4 }}>
            {line.field}
            <Tag color={line.type === 'removed' ? 'red' : line.type === 'added' ? 'green' : 'orange'} style={{ marginLeft: 8 }}>
              {line.type}
            </Tag>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            {line.old_lines.length > 0 && (
              <div style={{ flex: 1, background: '#fff1f0', borderRadius: 4, padding: '4px 8px', border: '1px solid #ffccc7' }}>
                {line.old_lines.map((item, index) => (
                  <div key={`${line.field}-old-${index}`} style={{ color: '#cf1322' }}>- {item}</div>
                ))}
              </div>
            )}
            {line.new_lines.length > 0 && (
              <div style={{ flex: 1, background: '#f6ffed', borderRadius: 4, padding: '4px 8px', border: '1px solid #b7eb8f' }}>
                {line.new_lines.map((item, index) => (
                  <div key={`${line.field}-new-${index}`} style={{ color: '#389e0d' }}>+ {item}</div>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

function StructuredDiffTable({ diff }: { diff: SnapshotDiffResponse }) {
  const columns: TableColumnsType<StructuredDiffEntry> = [
    {
      title: 'Field',
      render: (_, record) => <Text code>{diffField(record)}</Text>,
    },
    {
      title: 'Change',
      render: (_, record) => (
        <Space>
          <Tag color={diffChange(record) === 'removed' ? 'red' : diffChange(record) === 'added' ? 'green' : 'blue'}>
            {diffChange(record)}
          </Tag>
          <Tag>{diffCategory(record)}</Tag>
          {isBreakingDiff(record) && <Tag color="red">BREAKING</Tag>}
        </Space>
      ),
    },
    {
      title: 'Before',
      dataIndex: 'old_value',
      render: renderValue,
    },
    {
      title: 'After',
      dataIndex: 'new_value',
      render: renderValue,
    },
  ]

  return (
    <Table
      dataSource={diff.diffs}
      columns={columns}
      rowKey={(_, index) => `${diff.from_ref}-${diff.to_ref}-${index}`}
      pagination={false}
      size="small"
      locale={{ emptyText: 'No structured field changes' }}
    />
  )
}

function jsonText(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2)
}

function parseJsonField(value: string | undefined, label: string) {
  const text = (value ?? '').trim()
  if (!text) return undefined
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(`${label} must be valid JSON`)
  }
}

function tagList(value?: string) {
  return (value ?? '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
}

function businessDiffColor(row: BusinessDiffEntry) {
  if (row.is_breaking) return 'red'
  if (row.category === 'interface') return 'orange'
  if (row.category === 'implementation') return 'blue'
  return 'default'
}

function BusinessDiffView({ diff, summary }: { diff: BusinessDiffEntry[]; summary?: BusinessDiffSummary }) {
  if (!diff.length) return <Empty description="No business-level changes" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {summary && (
        <Alert
          type={summary.breaking ? 'warning' : 'info'}
          showIcon
          message={summary.summary}
          description={`Suggested bump: ${summary.suggested_bump}. Categories: ${summary.categories.join(', ') || 'none'}.`}
        />
      )}
      <Table
        size="small"
        pagination={false}
        rowKey={(record, index) => `${record.field}-${index}`}
        dataSource={diff}
        columns={[
          {
            title: 'Field',
            render: (_, record) => <Text code>{record.field}</Text>,
          },
          {
            title: 'Change',
            render: (_, record) => (
              <Space wrap>
                <Tag color={businessDiffColor(record)}>{record.change_type}</Tag>
                <Tag>{record.category}</Tag>
                {record.is_breaking && <Tag color="red">BREAKING</Tag>}
              </Space>
            ),
          },
          {
            title: 'Before',
            render: (_, record) => renderValue(record.old_value),
          },
          {
            title: 'After',
            render: (_, record) => renderValue(record.new_value),
          },
        ]}
      />
    </Space>
  )
}

export default function VersionControl() {
  const location = useLocation()
  const [form] = Form.useForm<VersionLabForm>()
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedFull, setSelectedFull] = useState<import('@/api/types').SkillFull | null>(null)
  const [versions, setVersions] = useState<SkillSummary[]>([])
  const [loadingVersions, setLoadingVersions] = useState(false)
  const [bumpLoading, setBumpLoading] = useState(false)
  const [drawerSkill, setDrawerSkill] = useState<SkillSummary | null>(null)
  const [diffData, setDiffData] = useState<DiffData | null>(null)
  const [loadingDiff, setLoadingDiff] = useState(false)
  const [snapshotPath, setSnapshotPath] = useState('')
  const [snapshotHistory, setSnapshotHistory] = useState<SnapshotHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [snapshotLoading, setSnapshotLoading] = useState(false)
  const [snapshotDiffLoading, setSnapshotDiffLoading] = useState(false)
  const [releaseTagLoading, setReleaseTagLoading] = useState(false)
  const [restoreLoading, setRestoreLoading] = useState(false)
  const [sourceRef, setSourceRef] = useState('')
  const [snapshotDiff, setSnapshotDiff] = useState<SnapshotDiffResponse | null>(null)
  const [lastSnapshot, setLastSnapshot] = useState<SnapshotCommitResponse | null>(null)
  const [lastRelease, setLastRelease] = useState<SkillReleaseRecord | null>(null)
  const [lastRestore, setLastRestore] = useState<SkillRollbackRecord | null>(null)

  useEffect(() => {
    skillsApi.list({ limit: 200 }).then(setSkills).catch(err => {
      message.error(getApiErrorMessage(err, 'Load Skills failed'))
    })
  }, [])

  const loadVersions = useCallback(async (id: string) => {
    setLoadingVersions(true)
    try {
      const nextVersions = await skillsApi.versions(id)
      setVersions(nextVersions.sort((a, b) => semverCompare(a.version, b.version)))
    } finally {
      setLoadingVersions(false)
    }
  }, [])

  const loadDiff = useCallback(async (id: string) => {
    setLoadingDiff(true)
    try {
      const data = await lifecycleApi.getDiff(id)
      setDiffData(data as unknown as DiffData)
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Load version diff failed'))
    } finally {
      setLoadingDiff(false)
    }
  }, [])

  const loadSelectedFull = useCallback(async (id: string) => {
    try {
      const full = await skillsApi.getFull(id)
      setSelectedFull(full)
      form.setFieldsValue({
        bump: 'patch',
        description: full.description,
        tags: full.tags.join(', '),
        interface_json: jsonText(full.interface),
        implementation_json: jsonText(full.implementation ?? { language: 'python', prompt_template: '' }),
        evaluation_json: jsonText(full.evaluation),
      })
    } catch (err) {
      setSelectedFull(null)
      message.warning(getApiErrorMessage(err, 'Load editable Skill details failed'))
    }
  }, [form])

  const loadSnapshotHistory = useCallback(async (id: string, options: { silent?: boolean } = {}) => {
    setHistoryLoading(true)
    try {
      const data = await lifecycleApi.snapshotHistory(id)
      setSnapshotPath(data.snapshot_path)
      setSnapshotHistory(data.history)
      setSourceRef(prev => prev || data.history[0]?.commit_hash || '')
    } catch (err) {
      setSnapshotPath('')
      setSnapshotHistory([])
      if (!options.silent) {
        message.warning(getApiErrorMessage(err, 'Snapshot history is not available yet'))
      }
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  const handleSelect = useCallback((id: string, options: { loadCurrentDiff?: boolean } = {}) => {
    setSelectedId(id)
    setDiffData(null)
    setSnapshotDiff(null)
    setLastSnapshot(null)
    setLastRelease(null)
    setLastRestore(null)
    setSourceRef('')
    void loadVersions(id)
    void loadSelectedFull(id)
    void loadSnapshotHistory(id, { silent: true })
    if (options.loadCurrentDiff) {
      void loadDiff(id)
    }
  }, [loadDiff, loadSelectedFull, loadSnapshotHistory, loadVersions])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const query = new URLSearchParams(location.search)
      const querySkillId = query.get('skill_id')
      if (!querySkillId || selectedId === querySkillId) return
      if (skills.length > 0 && skills.some(skill => skill.skill_id === querySkillId)) {
        handleSelect(querySkillId, { loadCurrentDiff: Boolean(query.get('proposal_id')) })
      }
    }, 0)
    return () => window.clearTimeout(timer)
  }, [handleSelect, location.search, selectedId, skills])

  const handleBump = async (bump: 'major' | 'minor' | 'patch') => {
    if (!selectedId) return
    setBumpLoading(true)
    try {
      const newSkill = await lifecycleApi.newVersion(selectedId, bump)
      message.success(`Created version v${newSkill.version}`)
      const allSkills = await skillsApi.list({ limit: 200 })
      setSkills(allSkills)
      void loadVersions(selectedId)
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Create version failed'))
    } finally {
      setBumpLoading(false)
    }
  }

  const handleEditableVersion = async (values: VersionLabForm) => {
    if (!selectedId || !selectedFull) return
    setBumpLoading(true)
    try {
      const nextInterface = parseJsonField(values.interface_json, 'Interface')
      const nextImplementation = parseJsonField(values.implementation_json, 'Implementation')
      const nextEvaluation = parseJsonField(values.evaluation_json, 'Evaluation')
      const request: NewVersionRequest = {
        bump: values.bump,
        description: values.description,
        tags: tagList(values.tags),
        interface: nextInterface,
        implementation: nextImplementation,
        evaluation: nextEvaluation,
        author: 'version_lab',
      }
      const newSkill = await lifecycleApi.newVersion(selectedId, request)
      message.success(`Created draft version v${newSkill.version}`)
      const allSkills = await skillsApi.list({ limit: 200 })
      setSkills(allSkills)
      setSelectedId(newSkill.skill_id)
      await loadVersions(newSkill.skill_id)
      await loadSelectedFull(newSkill.skill_id)
      await loadDiff(newSkill.skill_id)
    } catch (err) {
      message.error(err instanceof Error ? err.message : getApiErrorMessage(err, 'Create editable version failed'))
    } finally {
      setBumpLoading(false)
    }
  }

  const handleRelease = async (id: string) => {
    try {
      await lifecycleApi.release(id)
      message.success('Released')
      if (selectedId) void loadVersions(selectedId)
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Release failed'))
    }
  }

  const handleCreateSnapshot = async () => {
    if (!selectedId) return
    setSnapshotLoading(true)
    try {
      const snapshot = await lifecycleApi.createSnapshot(selectedId, { author: 'human_reviewer' })
      setLastSnapshot(snapshot)
      setSourceRef(snapshot.commit)
      message.success(`Snapshot commit ${shortRef(snapshot.commit)} created`)
      await loadSnapshotHistory(selectedId, { silent: true })
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Create snapshot commit failed'))
    } finally {
      setSnapshotLoading(false)
    }
  }

  const handleSnapshotDiff = async (ref = sourceRef) => {
    if (!selectedId || !ref.trim()) {
      message.warning('Select or enter a base commit/tag first')
      return
    }
    setSnapshotDiffLoading(true)
    try {
      const data = await lifecycleApi.snapshotDiff(selectedId, {
        from_ref: ref.trim(),
        to_ref: 'HEAD',
      })
      setSnapshotDiff(data)
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Load snapshot structured diff failed'))
    } finally {
      setSnapshotDiffLoading(false)
    }
  }

  const handleReleaseTag = async (ref = sourceRef || 'HEAD') => {
    if (!selectedId) return
    setReleaseTagLoading(true)
    try {
      const release = await lifecycleApi.releaseTag(selectedId, ref.trim() || 'HEAD')
      setLastRelease(release)
      message.success(`Release tag ${release.tag_name} created`)
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Create release tag failed'))
    } finally {
      setReleaseTagLoading(false)
    }
  }

  const handleRestoreCommit = async (ref = sourceRef) => {
    if (!selectedId || !ref.trim()) {
      message.warning('Select or enter a source commit/tag first')
      return
    }
    setRestoreLoading(true)
    try {
      const restore = await lifecycleApi.restoreSnapshot(selectedId, ref.trim())
      setLastRestore(restore)
      message.success(`Restore commit ${shortRef(restore.restore_commit)} created`)
      await loadSnapshotHistory(selectedId, { silent: true })
    } catch (err) {
      message.error(getApiErrorMessage(err, 'Create restore commit failed'))
    } finally {
      setRestoreLoading(false)
    }
  }

  const selectedSkill = selectedId ? skills.find(skill => skill.skill_id === selectedId) : null
  const currentDiffHistory = diffData?.history ?? []
  const currentBusinessDiff = diffData?.business_diff ?? []
  const queryProposalId = new URLSearchParams(location.search).get('proposal_id')

  const versionColumns: TableColumnsType<SkillSummary> = [
    {
      title: 'Version',
      dataIndex: 'version',
      render: (version: string, record) => (
        <Space>
          <TagOutlined style={{ color: '#1677ff' }} />
          <Text code style={{ cursor: 'pointer' }} onClick={() => setDrawerSkill(record)}>v{version}</Text>
          {record.skill_id === selectedId && <Tag color="blue">HEAD</Tag>}
        </Space>
      ),
    },
    {
      title: 'State',
      dataIndex: 'state',
      render: (state: string) => <Badge color={STATE_COLOR[state] || 'default'} text={STATE_LABEL[state] || state} />,
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      render: dateText,
    },
    {
      title: 'Success Rate',
      dataIndex: 'metrics',
      render: (metrics: SkillSummary['metrics']) =>
        metrics.total_executions >= 5
          ? `${(metrics.success_rate * 100).toFixed(1)}%`
          : <Text type="secondary">N/A</Text>,
    },
    {
      title: 'Actions',
      render: (_, record) => (
        <Space>
          {(record.state === 'S2' || record.state === 'S3') && (
            <Popconfirm title="Release this version?" onConfirm={() => handleRelease(record.skill_id)}>
              <Button size="small" type="primary" icon={<RocketOutlined />}>Release</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  const snapshotColumns: TableColumnsType<SnapshotHistoryItem> = [
    {
      title: 'Commit',
      dataIndex: 'commit_hash',
      render: (commit: string) => <Text code copyable={{ text: commit }}>{shortRef(commit)}</Text>,
    },
    {
      title: 'Subject',
      dataIndex: 'subject',
      render: (subject: string) => <Text>{subject}</Text>,
    },
    {
      title: 'Author',
      dataIndex: 'author',
      width: 140,
    },
    {
      title: 'Time',
      dataIndex: 'authored_at',
      render: dateText,
      width: 180,
    },
    {
      title: 'Paths',
      dataIndex: 'changed_paths',
      render: (paths: string[]) => (
        <Space wrap>
          {paths.slice(0, 2).map(path => <Tag key={path}>{path}</Tag>)}
          {paths.length > 2 && <Tag>+{paths.length - 2}</Tag>}
        </Space>
      ),
    },
    {
      title: 'Actions',
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<DiffOutlined />} onClick={() => { setSourceRef(record.commit_hash); void handleSnapshotDiff(record.commit_hash) }}>
            Diff
          </Button>
          <Button size="small" icon={<TagOutlined />} loading={releaseTagLoading} onClick={() => handleReleaseTag(record.commit_hash)}>
            Tag
          </Button>
          <Popconfirm
            title="Create a restore commit from this snapshot?"
            description="This writes a new restore commit; it does not reset Git history."
            onConfirm={() => handleRestoreCommit(record.commit_hash)}
          >
            <Button size="small" danger icon={<RollbackOutlined />} loading={restoreLoading}>
              Restore commit
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h2 style={{ fontWeight: 700, marginBottom: 4 }}>Version Governance</h2>
        <p style={{ color: '#666', marginBottom: 24 }}>
          Review Skill versions, Git snapshots, structured diffs, release tags, and restore commits.
        </p>
      </motion.div>

      {queryProposalId && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="Accepted maintenance proposal"
          description={(
            <Space direction="vertical" size={2}>
              <Text>
                Proposal <Text code>{queryProposalId.slice(0, 8)}</Text> is ready for B-side review.
              </Text>
              <Text type="secondary">
                Submit a patched Skill through the maintenance review endpoint before creating any live Skill change.
              </Text>
            </Space>
          )}
        />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={16}>
          <Card
            title={<span><BranchesOutlined /> Version history</span>}
            variant="borderless"
            style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
            extra={selectedId && (
              <Space wrap>
                <Button size="small" icon={<DiffOutlined />} loading={loadingDiff} onClick={() => loadDiff(selectedId)}>
                  Legacy diff
                </Button>
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
            )}
          >
            <div style={{ marginBottom: 16 }}>
              <Select
                value={selectedId ?? undefined}
                placeholder="Select a Skill"
                style={{ width: '100%' }}
                onChange={value => handleSelect(value)}
                showSearch
                filterOption={(input, option) =>
                  String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                }
                options={[...new Map(skills.map(skill => [skill.name, skill])).values()].map(skill => ({
                  label: `${skill.name} (${STATE_LABEL[skill.state] || skill.state})`,
                  value: skill.skill_id,
                }))}
              />
            </div>

            <Table
              dataSource={versions}
              columns={versionColumns}
              rowKey="skill_id"
              loading={loadingVersions}
              size="small"
              pagination={false}
              locale={{ emptyText: selectedId ? 'No version records' : 'Select a Skill first' }}
            />
          </Card>

          <Card
            title={<span><PlusOutlined /> Version Lab</span>}
            variant="borderless"
            style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginTop: 16 }}
          >
            {!selectedId || !selectedFull ? (
              <Empty description="Select a Skill to create an editable draft version" />
            ) : (
              <Form
                form={form}
                layout="vertical"
                initialValues={{ bump: 'patch' }}
                onFinish={handleEditableVersion}
              >
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="Editable versions are created as Drafts"
                  description="Changing the interface or implementation creates a new S2 draft version that should be sent through harness verification before release."
                />
                <Row gutter={12}>
                  <Col xs={24} md={8}>
                    <Form.Item name="bump" label="Version bump">
                      <Select
                        options={[
                          { label: 'patch', value: 'patch' },
                          { label: 'minor', value: 'minor' },
                          { label: 'major', value: 'major' },
                        ]}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={16}>
                    <Form.Item name="tags" label="Tags">
                      <Input placeholder="comma-separated tags" />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item name="description" label="Description">
                  <Input.TextArea rows={2} />
                </Form.Item>
                <Collapse
                  items={[
                    {
                      key: 'interface',
                      label: 'Interface JSON',
                      children: (
                        <Form.Item name="interface_json" noStyle>
                          <Input.TextArea rows={10} style={{ fontFamily: 'monospace', fontSize: 12 }} />
                        </Form.Item>
                      ),
                    },
                    {
                      key: 'implementation',
                      label: 'Implementation JSON',
                      children: (
                        <Form.Item name="implementation_json" noStyle>
                          <Input.TextArea rows={8} style={{ fontFamily: 'monospace', fontSize: 12 }} />
                        </Form.Item>
                      ),
                    },
                    {
                      key: 'evaluation',
                      label: 'Evaluation JSON',
                      children: (
                        <Form.Item name="evaluation_json" noStyle>
                          <Input.TextArea rows={8} style={{ fontFamily: 'monospace', fontSize: 12 }} />
                        </Form.Item>
                      ),
                    },
                  ]}
                />
                <Space wrap style={{ marginTop: 16 }}>
                  <Button type="primary" htmlType="submit" icon={<PlusOutlined />} loading={bumpLoading}>
                    Create draft version
                  </Button>
                  <Button icon={<ReloadOutlined />} onClick={() => loadSelectedFull(selectedId)}>
                    Reset editor
                  </Button>
                </Space>
              </Form>
            )}
          </Card>

          <Card
            title={<span><SaveOutlined /> Git-backed governance snapshots</span>}
            variant="borderless"
            style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginTop: 16 }}
            extra={selectedId && (
              <Space wrap>
                <Button icon={<SaveOutlined />} loading={snapshotLoading} onClick={handleCreateSnapshot}>
                  Create snapshot
                </Button>
                <Button icon={<ReloadOutlined />} loading={historyLoading} onClick={() => loadSnapshotHistory(selectedId)}>
                  Refresh history
                </Button>
              </Space>
            )}
          >
            {!selectedId ? (
              <Empty description="Select a Skill to manage snapshots" />
            ) : (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                {snapshotPath && (
                  <Text type="secondary">Snapshot path: <Text code>{snapshotPath}</Text></Text>
                )}
                {lastSnapshot && (
                  <Alert
                    type="success"
                    showIcon
                    message="Snapshot commit created"
                    description={<Text code copyable={{ text: lastSnapshot.commit }}>{shortRef(lastSnapshot.commit)}</Text>}
                  />
                )}
                {lastRelease && (
                  <Alert
                    type="success"
                    showIcon
                    message="Release tag created"
                    description={<Text code copyable={{ text: lastRelease.tag_name }}>{lastRelease.tag_name}</Text>}
                  />
                )}
                {lastRestore && (
                  <Alert
                    type="warning"
                    showIcon
                    message="Restore commit created"
                    description={(
                      <Space direction="vertical" size={2}>
                        <Text code copyable={{ text: lastRestore.restore_commit }}>{shortRef(lastRestore.restore_commit)}</Text>
                        <Text type="secondary">{lastRestore.commit_message}</Text>
                      </Space>
                    )}
                  />
                )}

                <Space.Compact style={{ width: '100%' }}>
                  <Input
                    value={sourceRef}
                    onChange={event => setSourceRef(event.target.value)}
                    placeholder="Base commit, release tag, or source ref"
                  />
                  <Button icon={<DiffOutlined />} loading={snapshotDiffLoading} onClick={() => handleSnapshotDiff()}>
                    Structured diff
                  </Button>
                  <Button icon={<TagOutlined />} loading={releaseTagLoading} onClick={() => handleReleaseTag()}>
                    Release tag
                  </Button>
                  <Popconfirm
                    title="Create restore commit from this ref?"
                    description="This creates a new commit from the selected snapshot; it does not reset history."
                    onConfirm={() => handleRestoreCommit()}
                  >
                    <Button danger icon={<RollbackOutlined />} loading={restoreLoading}>
                      Restore commit
                    </Button>
                  </Popconfirm>
                </Space.Compact>

                <Table
                  dataSource={snapshotHistory}
                  columns={snapshotColumns}
                  rowKey="commit_hash"
                  loading={historyLoading}
                  size="small"
                  pagination={{ pageSize: 5 }}
                  locale={{ emptyText: 'No snapshot commits yet' }}
                />

                {snapshotDiff && (
                  <Card
                    size="small"
                    title={(
                      <Space>
                        <Text code>{`${shortRef(snapshotDiff.from_ref)} -> ${shortRef(snapshotDiff.to_ref)}`}</Text>
                        <Tag color={snapshotDiff.has_breaking_changes ? 'red' : 'green'}>
                          {snapshotDiff.has_breaking_changes ? 'BREAKING' : 'compatible'}
                        </Tag>
                      </Space>
                    )}
                    variant="borderless"
                    style={{ background: '#fafafa' }}
                  >
                    <Alert
                      type={snapshotDiff.has_breaking_changes ? 'warning' : 'info'}
                      showIcon
                      style={{ marginBottom: 12 }}
                      message={snapshotDiff.review_recommendation}
                    />
                    <StructuredDiffTable diff={snapshotDiff} />
                    {snapshotDiff.raw_diff && (
                      <Collapse
                        style={{ marginTop: 12 }}
                        items={[{
                          key: 'raw',
                          label: 'Raw Git diff',
                          children: (
                            <pre style={{
                              margin: 0,
                              maxHeight: 260,
                              overflow: 'auto',
                              fontSize: 12,
                              background: '#111827',
                              color: '#f9fafb',
                              borderRadius: 6,
                              padding: 12,
                            }}
                            >
                              {snapshotDiff.raw_diff}
                            </pre>
                          ),
                        }]}
                      />
                    )}
                  </Card>
                )}
              </Space>
            )}
          </Card>

          {diffData && (
            <Card
              title={<span><DiffOutlined /> Version change history - {diffData.skill_name ?? selectedSkill?.name ?? 'Skill'}</span>}
              variant="borderless"
              style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginTop: 16 }}
            >
              {currentBusinessDiff.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <BusinessDiffView diff={currentBusinessDiff} summary={diffData.business_summary} />
                </div>
              )}
              {currentDiffHistory.length === 0 ? (
                <Empty description="No version change records" />
              ) : (
                <Collapse
                  items={currentDiffHistory.map(item => ({
                    key: item.record_id,
                    label: (
                      <Space wrap>
                        <Text code>{`${item.from_version ?? 'previous'} -> ${item.to_version}`}</Text>
                        <Tag color={item.is_breaking ? 'red' : 'blue'}>{item.change_type}</Tag>
                        {item.is_breaking && <Tag color="red">BREAKING</Tag>}
                        <Text type="secondary" style={{ fontSize: 11 }}>{item.summary}</Text>
                      </Space>
                    ),
                    children: (
                      <div>
                        <div style={{ marginBottom: 8, color: '#666', fontSize: 12 }}>
                          {item.author} - {dateText(item.created_at)}
                        </div>
                        <DiffView lines={item.diff} />
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
            title={<span><HistoryOutlined /> Timeline</span>}
            variant="borderless"
            style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          >
            {versions.length > 0 || snapshotHistory.length > 0 ? (
              <Timeline
                items={[
                  ...snapshotHistory.slice(0, 8).map(item => ({
                    color: 'blue',
                    children: (
                      <div>
                        <div><Text code>{shortRef(item.commit_hash)}</Text> snapshot</div>
                        <div style={{ fontSize: 11, color: '#999' }}>{dateText(item.authored_at)}</div>
                        <div style={{ fontSize: 11, color: '#666' }}>{item.subject}</div>
                      </div>
                    ),
                  })),
                  ...versions.slice(0, 8).map(version => ({
                    color: STATE_COLOR[version.state] || 'blue',
                    children: (
                      <div>
                        <div>
                          <Text code>v{version.version}</Text>
                          <Badge
                            color={STATE_COLOR[version.state] || 'default'}
                            text={STATE_LABEL[version.state] || version.state}
                            style={{ marginLeft: 8 }}
                          />
                        </div>
                        <div style={{ fontSize: 11, color: '#999' }}>{dateText(version.created_at)}</div>
                        {version.metrics.total_executions > 0 && (
                          <div style={{ fontSize: 11, color: '#666' }}>
                            {version.metrics.total_executions} executions, {(version.metrics.success_rate * 100).toFixed(0)}% success
                          </div>
                        )}
                      </div>
                    ),
                  })),
                ]}
              />
            ) : (
              <div style={{ textAlign: 'center', color: '#999', padding: 40 }}>
                <BranchesOutlined style={{ fontSize: 32, marginBottom: 8 }} />
                <div>Select a Skill to view timeline</div>
              </div>
            )}
          </Card>

          {selectedSkill && (
            <Card
              title="Current version"
              variant="borderless"
              style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginTop: 16 }}
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="Name">{selectedSkill.name}</Descriptions.Item>
                <Descriptions.Item label="Version"><Text code>v{selectedSkill.version}</Text></Descriptions.Item>
                <Descriptions.Item label="State">
                  <Badge color={STATE_COLOR[selectedSkill.state]} text={STATE_LABEL[selectedSkill.state]} />
                </Descriptions.Item>
                <Descriptions.Item label="Type">
                  <Tag>{selectedSkill.skill_type}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="Executions">{selectedSkill.metrics.total_executions}</Descriptions.Item>
              </Descriptions>
            </Card>
          )}
        </Col>
      </Row>

      <Drawer
        title={drawerSkill ? `v${drawerSkill.version} details` : ''}
        open={!!drawerSkill}
        onClose={() => setDrawerSkill(null)}
        size="default"
      >
        {drawerSkill && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="Skill ID">
              <Text code copyable style={{ fontSize: 11 }}>{drawerSkill.skill_id}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="Version"><Text code>v{drawerSkill.version}</Text></Descriptions.Item>
            <Descriptions.Item label="State">
              <Badge color={STATE_COLOR[drawerSkill.state]} text={STATE_LABEL[drawerSkill.state]} />
            </Descriptions.Item>
            <Descriptions.Item label="Description">{drawerSkill.description}</Descriptions.Item>
            <Descriptions.Item label="Tags">
              {drawerSkill.tags.map(tag => <Tag key={tag}>{tag}</Tag>)}
            </Descriptions.Item>
            <Descriptions.Item label="Created">{dateText(drawerSkill.created_at)}</Descriptions.Item>
            <Descriptions.Item label="Updated">{dateText(drawerSkill.updated_at)}</Descriptions.Item>
            <Descriptions.Item label="Executions">{drawerSkill.metrics.total_executions}</Descriptions.Item>
            <Descriptions.Item label="Success rate">
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
