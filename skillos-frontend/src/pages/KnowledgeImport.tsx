import { useState } from 'react'
import {
  Alert, Badge, Button, Card, Col, Divider, Input, Progress,
  Row, Space, Steps, Tabs, Tag, Typography,
} from 'antd'
import {
  ApiOutlined, CheckCircleOutlined, CloudUploadOutlined, CodeOutlined,
  CompressOutlined, DatabaseOutlined, FileSearchOutlined, FileTextOutlined,
  FilterOutlined, LoadingOutlined, PlayCircleOutlined,
} from '@ant-design/icons'
import { AnimatePresence, motion } from 'framer-motion'
import { Link } from 'react-router-dom'
import { ingestApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import type { IngestResponse } from '@/api/client'

const { TextArea } = Input
const { Paragraph, Text } = Typography

const SOURCE_TYPES = [
  {
    key: 'trajectory',
    label: '操作轨迹',
    icon: <PlayCircleOutlined />,
    color: '#1677ff',
    placeholder: `粘贴操作轨迹，例如：
1. 打开浏览器并进入 https://example.com/login
2. 点击用户名输入框
3. 输入用户名 admin
4. 点击密码输入框
5. 输入密码
6. 点击登录按钮
7. 等待页面跳转并确认登录成功`,
  },
  {
    key: 'document',
    label: '文档/说明',
    icon: <FileTextOutlined />,
    color: '#52c41a',
    placeholder: `粘贴技术文档或操作说明，例如：
# 登录表单规范

## 输入字段
- username: 必填，4-20 个字符
- password: 必填，至少 8 位
- remember_me: 可选

## 操作步骤
1. 校验输入格式
2. 提交表单
3. 处理响应`,
  },
  {
    key: 'api_doc',
    label: 'API 文档',
    icon: <ApiOutlined />,
    color: '#722ed1',
    placeholder: `粘贴 API 文档或 OpenAPI 片段，例如：
POST /api/login
Content-Type: application/json

Request:
{
  "username": "string (required)",
  "password": "string (required)",
  "remember_me": "boolean (optional)"
}

Response 200:
{
  "token": "string",
  "user_id": "string",
  "expires_at": "datetime"
}`,
  },
  {
    key: 'script',
    label: '代码脚本',
    icon: <CodeOutlined />,
    color: '#fa8c16',
    placeholder: `粘贴 Python/JavaScript/Shell 脚本，例如：
async def login(page, username: str, password: str) -> bool:
    """Login to the target system."""
    await page.goto("https://example.com/login")
    await page.fill("#username", username)
    await page.fill("#password", password)
    await page.click("#submit")
    await page.wait_for_url("**/dashboard")
    return True`,
  },
]

const TYPE_COLOR: Record<string, string> = {
  atomic: 'blue',
  functional: 'purple',
  strategic: 'gold',
}

const PIPELINE_STAGES = [
  { title: 'Extractor', icon: <FilterOutlined />, desc: '提取原始动作序列' },
  { title: 'Normalizer', icon: <CompressOutlined />, desc: '规范化为结构化操作' },
  { title: 'Summarizer', icon: <FileSearchOutlined />, desc: '生成候选 Skill 摘要' },
  { title: 'Indexer', icon: <DatabaseOutlined />, desc: '生成检索关键词' },
]

export default function KnowledgeImport() {
  const [activeTab, setActiveTab] = useState('trajectory')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [pipelineStage, setPipelineStage] = useState(-1)
  const [result, setResult] = useState<IngestResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<'parse' | 'create'>('parse')

  const currentSource = SOURCE_TYPES.find(s => s.key === activeTab)!

  const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

  const handleSubmit = async (submitMode: 'parse' | 'create') => {
    if (!content.trim()) {
      setError('请先输入内容')
      return
    }

    setMode(submitMode)
    setLoading(true)
    setResult(null)
    setError(null)
    setPipelineStage(0)

    try {
      const stagePromise = (async () => {
        for (let i = 0; i < PIPELINE_STAGES.length; i++) {
          setPipelineStage(i)
          await sleep(400)
        }
      })()

      const apiPromise = submitMode === 'create'
        ? ingestApi.parseAndCreate(activeTab, content)
        : ingestApi.parse(activeTab, content)

      const [res] = await Promise.all([apiPromise, stagePromise])
      setPipelineStage(PIPELINE_STAGES.length)
      setResult(res)

    } catch (e: unknown) {
      const msg = getApiErrorMessage(e, '解析失败')
      setError(msg)
      setPipelineStage(-1)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ padding: 24 }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h2 style={{ fontWeight: 700, marginBottom: 4 }}>知识导入</h2>
        <p style={{ color: '#666', marginBottom: 24 }}>
          从轨迹、文档、API 文档或代码脚本中提取结构化经验，生成可进入 Wiki 管理的候选 Skill。
        </p>
      </motion.div>

      {error && (
        <Alert
          type="error"
          showIcon
          closable
          title={mode === 'create' ? '解析并创建 Skill 失败' : '解析失败'}
          description={error}
          style={{ marginBottom: 16 }}
          onClose={() => setError(null)}
        />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card
            variant="borderless"
            style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          >
            <Tabs
              activeKey={activeTab}
              onChange={key => {
                setActiveTab(key)
                setContent('')
                setResult(null)
                setError(null)
              }}
              items={SOURCE_TYPES.map(source => ({
                key: source.key,
                label: <span>{source.icon} {source.label}</span>,
                children: null,
              }))}
            />

            <div style={{ marginBottom: 12 }}>
              <Tag color={currentSource.color} style={{ marginBottom: 8 }}>
                {currentSource.icon} {currentSource.label}
              </Tag>
            </div>

            <TextArea
              value={content}
              onChange={event => setContent(event.target.value)}
              placeholder={currentSource.placeholder}
              rows={14}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />

            <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <Button
                type="primary"
                icon={<CloudUploadOutlined />}
                loading={loading}
                onClick={() => handleSubmit('parse')}
              >
                解析预览
              </Button>
              <Button
                type="primary"
                icon={<CheckCircleOutlined />}
                loading={loading}
                style={{ background: '#52c41a', borderColor: '#52c41a' }}
                onClick={() => handleSubmit('create')}
              >
                解析并创建 Skill
              </Button>
              <Text type="secondary" style={{ fontSize: 12 }}>
                预览只展示结果；创建会把候选 Skill 写入 Wiki。
              </Text>
            </div>

            {(loading || pipelineStage >= 0) && (
              <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} style={{ marginTop: 16 }}>
                <Divider style={{ margin: '12px 0' }} />
                <div style={{ marginBottom: 8 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>Experience Processing Pipeline</Text>
                </div>
                <Steps
                  size="small"
                  current={pipelineStage}
                  status={pipelineStage >= PIPELINE_STAGES.length ? 'finish' : 'process'}
                  items={PIPELINE_STAGES.map((stage, index) => ({
                    title: stage.title,
                    content: stage.desc,
                    icon: pipelineStage > index
                      ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                      : pipelineStage === index && loading
                        ? <LoadingOutlined style={{ color: '#1677ff' }} />
                        : stage.icon,
                  }))}
                />
              </motion.div>
            )}
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <AnimatePresence mode="wait">
            {result && (
              <motion.div
                key="result"
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 20 }}
              >
                <Card
                  title="解析结果"
                  variant="borderless"
                  style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
                  extra={
                    <Badge
                      status={result.success ? 'success' : 'error'}
                      text={result.success ? '成功' : '存在问题'}
                    />
                  }
                >
                  <Row gutter={[8, 8]} style={{ marginBottom: 12 }}>
                    <Col span={8}>
                      <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 24, fontWeight: 700, color: '#1677ff' }}>{result.unit_count}</div>
                        <Text type="secondary" style={{ fontSize: 12 }}>经验单元</Text>
                      </div>
                    </Col>
                    <Col span={8}>
                      <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 24, fontWeight: 700, color: '#722ed1' }}>{result.token_usage}</div>
                        <Text type="secondary" style={{ fontSize: 12 }}>Token 用量</Text>
                      </div>
                    </Col>
                    <Col span={8}>
                      <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 24, fontWeight: 700, color: '#ff4d4f' }}>{result.errors.length}</div>
                        <Text type="secondary" style={{ fontSize: 12 }}>错误数</Text>
                      </div>
                    </Col>
                  </Row>

                  {result.errors.length > 0 && (
                    <Alert
                      type="warning"
                      title="解析或创建过程中存在问题"
                      description={result.errors.join('; ')}
                      style={{ marginBottom: 12 }}
                    />
                  )}

                  {(result.created_skills?.length ?? 0) > 0 && (
                    <Alert
                      type="success"
                      showIcon
                      title={`已创建 ${result.created_skills?.length ?? 0} 个候选 Skill`}
                      description={
                        <Space wrap>
                          {result.created_skills?.map(skill => (
                            <Link key={skill.skill_id} to={`/wiki?skill_id=${encodeURIComponent(skill.skill_id)}`}>
                              <Tag color={TYPE_COLOR[skill.skill_type] || 'green'} style={{ cursor: 'pointer' }}>
                                {skill.name}
                              </Tag>
                            </Link>
                          ))}
                        </Space>
                      }
                      style={{ marginBottom: 12 }}
                    />
                  )}
                </Card>

                {result.units.length > 0 && (
                  <Card
                    title={`经验单元 (${result.units.length})`}
                    variant="borderless"
                    style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
                  >
                    <div>
                      {result.units.map((unit, index) => (
                        <motion.div
                          key={unit.unit_id}
                          initial={{ opacity: 0, y: 8 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ delay: index * 0.05 }}
                        >
                          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', padding: '12px 0' }}>
                            <div style={{ display: 'flex', gap: 8, marginBottom: 4, flexWrap: 'wrap', alignItems: 'center' }}>
                              {unit.proposed_skill_name && <Text strong>{unit.proposed_skill_name}</Text>}
                              {unit.proposed_type && (
                                <Tag color={TYPE_COLOR[unit.proposed_type] || 'default'}>
                                  {unit.proposed_type}
                                </Tag>
                              )}
                              <Progress
                                percent={Math.round(unit.confidence * 100)}
                                size="small"
                                style={{ width: 80 }}
                                strokeColor={unit.confidence > 0.7 ? '#52c41a' : '#faad14'}
                              />
                            </div>
                            {unit.proposed_description && (
                              <Paragraph style={{ margin: 0, fontSize: 12, color: '#666' }}>
                                {unit.proposed_description}
                              </Paragraph>
                            )}
                            {unit.summary && unit.summary !== unit.proposed_description && (
                              <Paragraph style={{ margin: '4px 0 0', fontSize: 12, color: '#666' }}>
                                {unit.summary}
                              </Paragraph>
                            )}
                            {unit.extracted_actions.length > 0 && (
                              <div style={{ marginTop: 4 }}>
                                {unit.extracted_actions.slice(0, 3).map((action, actionIndex) => (
                                  <Tag key={`${unit.unit_id}-action-${actionIndex}`} style={{ fontSize: 11, marginBottom: 2 }}>
                                    {action}
                                  </Tag>
                                ))}
                                {unit.extracted_actions.length > 3 && (
                                  <Text type="secondary" style={{ fontSize: 11 }}>
                                    +{unit.extracted_actions.length - 3} 更多
                                  </Text>
                                )}
                              </div>
                            )}
                            {unit.index_keywords.length > 0 && (
                              <div style={{ marginTop: 4 }}>
                                {unit.index_keywords.slice(0, 5).map(keyword => (
                                  <Tag key={keyword} color="default" style={{ fontSize: 11, marginBottom: 2 }}>
                                    {keyword}
                                  </Tag>
                                ))}
                              </div>
                            )}
                          </div>
                          {index < result.units.length - 1 && <Divider style={{ margin: '4px 0' }} />}
                        </motion.div>
                      ))}
                    </div>
                  </Card>
                )}
              </motion.div>
            )}

            {!result && !loading && (
              <motion.div
                key="empty"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
              >
                <Card
                  variant="borderless"
                  style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', textAlign: 'center', padding: 40 }}
                >
                  <CloudUploadOutlined style={{ fontSize: 48, color: '#d9d9d9', marginBottom: 12 }} />
                  <div style={{ color: '#999' }}>输入内容后点击解析</div>
                  <div style={{ marginTop: 16 }}>
                    <Space wrap>
                      {SOURCE_TYPES.map(source => (
                        <Tag
                          key={source.key}
                          color={activeTab === source.key ? source.color : undefined}
                          style={{ cursor: 'pointer' }}
                          onClick={() => setActiveTab(source.key)}
                        >
                          {source.icon} {source.label}
                        </Tag>
                      ))}
                    </Space>
                  </div>
                </Card>
              </motion.div>
            )}
          </AnimatePresence>
        </Col>
      </Row>
    </div>
  )
}
