import { useState } from 'react'
import {
  Card, Tabs, Input, Button, Alert, Tag, Progress, Space,
  Row, Col, Typography, Divider, List, Badge, message, Steps,
} from 'antd'
import {
  CloudUploadOutlined, CodeOutlined, FileTextOutlined,
  ApiOutlined, PlayCircleOutlined, CheckCircleOutlined,
  FilterOutlined, CompressOutlined, FileSearchOutlined, DatabaseOutlined,
  LoadingOutlined,
} from '@ant-design/icons'
import { motion, AnimatePresence } from 'framer-motion'
import { Link } from 'react-router-dom'
import { ingestApi } from '@/api/client'
import type { IngestResponse } from '@/api/client'

const { TextArea } = Input
const { Text, Paragraph } = Typography

const SOURCE_TYPES = [
  {
    key: 'trajectory',
    label: '操作轨迹',
    icon: <PlayCircleOutlined />,
    color: '#1677ff',
    placeholder: `粘贴操作轨迹，例如：
1. 打开浏览器，导航到 https://example.com/login
2. 点击用户名输入框
3. 输入用户名 "admin"
4. 点击密码输入框
5. 输入密码
6. 点击登录按钮
7. 等待页面跳转，验证登录成功`,
  },
  {
    key: 'document',
    label: '文档/说明',
    icon: <FileTextOutlined />,
    color: '#52c41a',
    placeholder: `粘贴技术文档或操作说明，例如：
# 表单填写规范

## 登录表单
- 用户名：必填，长度 4-20 字符
- 密码：必填，至少 8 位，包含大小写字母和数字
- 记住我：可选复选框

## 操作步骤
1. 验证输入格式
2. 提交表单
3. 处理响应`,
  },
  {
    key: 'api_doc',
    label: 'API 文档',
    icon: <ApiOutlined />,
    color: '#722ed1',
    placeholder: `粘贴 API 文档或 OpenAPI 规范，例如：
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
    """登录到系统。"""
    await page.goto("https://example.com/login")
    await page.fill("#username", username)
    await page.fill("#password", password)
    await page.click("#submit")
    await page.wait_for_url("**/dashboard")
    return True`,
  },
]

const TYPE_COLOR: Record<string, string> = {
  atomic: 'blue', functional: 'purple', strategic: 'gold',
}

const PIPELINE_STAGES = [
  { title: 'Extractor', icon: <FilterOutlined />, desc: '提取原始动作序列' },
  { title: 'Normalizer', icon: <CompressOutlined />, desc: '规范化为标准格式' },
  { title: 'Summarizer', icon: <FileSearchOutlined />, desc: '生成 Skill 摘要' },
  { title: 'Indexer', icon: <DatabaseOutlined />, desc: '写入 Experience Store' },
]

export default function KnowledgeImport() {
  const [activeTab, setActiveTab] = useState('trajectory')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [pipelineStage, setPipelineStage] = useState(-1)
  const [result, setResult] = useState<IngestResponse | null>(null)
  const [mode, setMode] = useState<'parse' | 'create'>('parse')

  const currentSource = SOURCE_TYPES.find(s => s.key === activeTab)!

  const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

  const handleSubmit = async (submitMode: 'parse' | 'create') => {
    if (!content.trim()) {
      message.warning('请先输入内容')
      return
    }
    setMode(submitMode)
    setLoading(true)
    setResult(null)
    setPipelineStage(0)

    try {
      // 模拟 pipeline 阶段进度
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
      if (res.success) {
        message.success(`解析成功，提取到 ${res.unit_count} 个经验单元`)
      } else {
        message.warning('解析完成，但存在错误')
      }
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '解析失败')
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
          从轨迹、文档、API 文档或代码脚本中提取结构化经验，生成 Skill 候选。
        </p>
      </motion.div>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card
            bordered={false}
            style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
          >
            <Tabs
              activeKey={activeTab}
              onChange={k => { setActiveTab(k); setContent(''); setResult(null) }}
              items={SOURCE_TYPES.map(s => ({
                key: s.key,
                label: <span>{s.icon} {s.label}</span>,
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
              onChange={e => setContent(e.target.value)}
              placeholder={currentSource.placeholder}
              rows={14}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />

            <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
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
                "解析预览"仅展示结果，"创建 Skill"会将候选写入 Wiki
              </Text>
            </div>

            {/* Pipeline 阶段进度 */}
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
                  items={PIPELINE_STAGES.map((s, i) => ({
                    title: s.title,
                    description: s.desc,
                    icon: pipelineStage > i
                      ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                      : pipelineStage === i && loading
                        ? <LoadingOutlined style={{ color: '#1677ff' }} />
                        : s.icon,
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
                  bordered={false}
                  style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginBottom: 16 }}
                  extra={
                    <Badge
                      status={result.success ? 'success' : 'error'}
                      text={result.success ? '成功' : '失败'}
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
                      message={result.errors.join('; ')}
                      style={{ marginBottom: 12 }}
                    />
                  )}

                  {(result.created_skills?.length ?? 0) > 0 && (
                    <Alert
                      type="success"
                      showIcon
                      message={`已创建 ${result.created_skills?.length ?? 0} 个候选 Skill`}
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
                    bordered={false}
                    style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
                  >
                    <List
                      dataSource={result.units}
                      renderItem={(unit, i) => (
                        <motion.div
                          key={unit.unit_id}
                          initial={{ opacity: 0, y: 8 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ delay: i * 0.05 }}
                        >
                          <List.Item style={{ flexDirection: 'column', alignItems: 'flex-start', padding: '12px 0' }}>
                            <div style={{ display: 'flex', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
                              {unit.proposed_skill_name && (
                                <Text strong>{unit.proposed_skill_name}</Text>
                              )}
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
                                {unit.extracted_actions.slice(0, 3).map((a, j) => (
                                  <Tag key={j} style={{ fontSize: 11, marginBottom: 2 }}>{a}</Tag>
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
                          </List.Item>
                          {i < result.units.length - 1 && <Divider style={{ margin: '4px 0' }} />}
                        </motion.div>
                      )}
                    />
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
                  bordered={false}
                  style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', textAlign: 'center', padding: 40 }}
                >
                  <CloudUploadOutlined style={{ fontSize: 48, color: '#d9d9d9', marginBottom: 12 }} />
                  <div style={{ color: '#999' }}>输入内容后点击解析</div>
                  <div style={{ marginTop: 16 }}>
                    <Space wrap>
                      {SOURCE_TYPES.map(s => (
                        <Tag
                          key={s.key}
                          color={activeTab === s.key ? s.color : undefined}
                          style={{ cursor: 'pointer' }}
                          onClick={() => setActiveTab(s.key)}
                        >
                          {s.icon} {s.label}
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
