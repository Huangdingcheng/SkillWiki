import { useState } from 'react'
import {
  Alert, Badge, Button, Card, Col, Collapse, Descriptions, Divider, Input, Progress,
  Row, Select, Space, Steps, Tabs, Tag, Typography, Upload,
} from 'antd'
import type { UploadProps } from 'antd'
import {
  ApiOutlined, CheckCircleOutlined, CloudUploadOutlined, CodeOutlined,
  CompressOutlined, DatabaseOutlined, FileSearchOutlined, FileTextOutlined,
  FilterOutlined, LoadingOutlined, PlayCircleOutlined, SafetyCertificateOutlined,
} from '@ant-design/icons'
import { AnimatePresence, motion } from 'framer-motion'
import { Link, useNavigate } from 'react-router-dom'
import { ingestApi, lifecycleApi } from '@/api/client'
import { getApiErrorMessage } from '@/api/errors'
import type {
  CandidateAuditResult,
  CandidateSkillReviewRequest,
  CreatedSkill,
  ExperienceUnit,
  IngestResponse,
  SkillEvaluation,
  SkillProvenance,
  SkillType,
} from '@/api/types'

const { TextArea } = Input
const { Dragger } = Upload
const { Paragraph, Text } = Typography
const MAX_IMPORT_FILE_BYTES = 2 * 1024 * 1024

const SOURCE_TYPES = [
  {
    key: 'trajectory',
    label: 'Trajectory',
    icon: <PlayCircleOutlined />,
    color: '#1677ff',
    placeholder: `Paste a trajectory, for example:
1. Open https://example.com/login
2. Click the username field
3. Type admin
4. Click the password field
5. Type the password
6. Click the login button
7. Wait for dashboard navigation and confirm success`,
  },
  {
    key: 'document',
    label: 'Document',
    icon: <FileTextOutlined />,
    color: '#52c41a',
    placeholder: `Paste a technical note or operating procedure.

Include inputs, expected outputs, constraints, and reusable steps where possible.`,
  },
  {
    key: 'api_doc',
    label: 'API Doc',
    icon: <ApiOutlined />,
    color: '#722ed1',
    placeholder: `Paste an API document or OpenAPI fragment.

POST /api/login
Request: { "username": "string", "password": "string" }
Response: { "token": "string", "user_id": "string" }`,
  },
  {
    key: 'script',
    label: 'Script',
    icon: <CodeOutlined />,
    color: '#fa8c16',
    placeholder: `Paste Python, JavaScript, or shell code.

async def login(page, username: str, password: str) -> bool:
    await page.goto("https://example.com/login")
    await page.fill("#username", username)
    await page.fill("#password", password)
    await page.click("#submit")
    return True`,
  },
  {
    key: 'past_skills',
    label: 'Past Skills',
    icon: <DatabaseOutlined />,
    color: '#13c2c2',
    placeholder: `Paste legacy Skill JSON, YAML, Markdown, or free text.

[
  {
    "name": "legacy_login_flow",
    "description": "Log in by filling credentials and waiting for the dashboard.",
    "steps": ["open login page", "fill username", "fill password", "click submit"],
    "dependencies": ["click_element", "type_text"],
    "skill_type": "functional"
  }
]`,
  },
]

const TYPE_COLOR: Record<string, string> = {
  atomic: 'blue',
  functional: 'purple',
  strategic: 'gold',
}

const PIPELINE_STAGES = [
  { title: 'Extractor', icon: <FilterOutlined />, desc: 'Extract source-local actions' },
  { title: 'Normalizer', icon: <CompressOutlined />, desc: 'Normalize experience structure' },
  { title: 'Summarizer', icon: <FileSearchOutlined />, desc: 'Draft candidate Skill evidence' },
  { title: 'Indexer', icon: <DatabaseOutlined />, desc: 'Prepare search keywords' },
]

const PREVIEW_BLOCK_STYLE = {
  background: '#f6f8fa',
  border: '1px solid #edf0f3',
  borderRadius: 8,
  padding: 12,
  whiteSpace: 'pre-wrap' as const,
  wordBreak: 'break-word' as const,
  maxHeight: 220,
  overflow: 'auto',
  fontSize: 12,
}

type SourceType = typeof SOURCE_TYPES[number]['key']

interface ApiDocContract {
  kind: 'api_doc'
  title: string
  method: string
  endpoint: string
  requiredParams: string[]
  optionalParams: string[]
  requestSchema: Record<string, unknown>
  responseSchema: Record<string, unknown>
  exampleCall: string
}

interface ScriptContract {
  kind: 'script'
  title: string
  language: string
  entrypoint: string
  args: string[]
  returns: string
  dependencies: string[]
  sideEffects: string[]
  runnableHint: string
  codeSnippet: string
}

type ResourceContract = ApiDocContract | ScriptContract | null

const RESOURCE_EXAMPLES: { sourceType: SourceType; title: string; content: string }[] = [
  {
    sourceType: 'api_doc',
    title: 'Login API',
    content: `POST /api/login
Description: Exchange user credentials for a bearer token.
Request: { "username": "string", "password": "string" }
Response: { "token": "string", "user_id": "string", "expires_at": "string" }
Errors: 401 when credentials are invalid.`,
  },
  {
    sourceType: 'api_doc',
    title: 'Weather API',
    content: `GET /v1/weather/current
Description: Fetch current weather by city.
Params: { "city": "string", "units": "metric|imperial" }
Response: { "city": "string", "temperature": "number", "condition": "string", "observed_at": "string" }`,
  },
  {
    sourceType: 'script',
    title: 'Playwright Login',
    content: `async def login(page, username: str, password: str) -> bool:
    await page.goto("https://example.com/login")
    await page.fill("#username", username)
    await page.fill("#password", password)
    await page.click("#submit")
    await page.wait_for_url("**/dashboard")
    return True`,
  },
  {
    sourceType: 'script',
    title: 'CSV Validator',
    content: `import csv

def validate_orders_csv(path: str) -> dict:
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    missing = [i for i, row in enumerate(rows, start=1) if not row.get("order_id")]
    return {"row_count": len(rows), "missing_order_id_rows": missing, "ok": not missing}`,
  },
  {
    sourceType: 'past_skills',
    title: 'Legacy Login Skill',
    content: `[
  {
    "name": "legacy_login_flow",
    "description": "Log in by filling credentials and waiting for the dashboard.",
    "steps": ["open login page", "fill username", "fill password", "click submit"],
    "dependencies": ["click_element", "type_text"],
    "skill_type": "functional",
    "input_schema": {
      "type": "object",
      "properties": {
        "username": { "type": "string" },
        "password": { "type": "string" }
      },
      "required": ["username", "password"]
    }
  }
]`,
  },
]

interface CandidateDraft {
  name: string
  description: string
  skillType: SkillType
  tagsText: string
  inputSchemaText: string
  outputSchemaText: string
  preconditionsText: string
  postconditionsText: string
  promptTemplate: string
  provenanceText: string
  evaluationText: string
  dependencyIds: string[]
  componentIds: string[]
  subSkillIds: string[]
  parentSkillIds: string[]
  toolCalls: string[]
}

function toJson(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

function linesFromText(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map(item => item.trim())
    .filter(Boolean)
}

function tagsFromText(value: string): string[] {
  return value
    .split(',')
    .map(item => item.trim().toLowerCase())
    .filter(Boolean)
}

function toSnakeCase(value: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  const safe = normalized || 'imported_skill'
  return /^[a-z]/.test(safe) ? safe.slice(0, 96) : `skill_${safe}`.slice(0, 96)
}

function isSkillType(value?: string): value is SkillType {
  return value === 'atomic' || value === 'functional' || value === 'strategic'
}

function parseJsonObject<T>(value: string, label: string): T {
  const parsed = JSON.parse(value) as T
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object`)
  }
  return parsed
}

function parseLooseJsonObject(value: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>
    }
  } catch {
    return {}
  }
  return {}
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map(item => String(item).trim()).filter(Boolean)
    : []
}

function metadataRecord(unit: ExperienceUnit | null | undefined): Record<string, unknown> {
  return asRecord(unit?.metadata) ?? {}
}

function nestedRecord(parent: Record<string, unknown>, key: string): Record<string, unknown> {
  return asRecord(parent[key]) ?? {}
}

function extractJsonAfterLabel(content: string, label: string): Record<string, unknown> {
  const pattern = new RegExp(`(?:${label})\\s*:?\\s*(\\{[^\\n]+\\})`, 'i')
  const match = content.match(pattern)
  return match ? parseLooseJsonObject(match[1]) : {}
}

function inferJsonValueType(value: unknown): string {
  if (Array.isArray(value)) return 'array'
  if (value === null) return 'string'
  return typeof value === 'number' || typeof value === 'boolean' || typeof value === 'object'
    ? typeof value
    : 'string'
}

function schemaFromObject(value: Record<string, unknown>, fallbackDescription: string): Record<string, unknown> {
  const properties = Object.fromEntries(
    Object.entries(value).map(([key, fieldValue]) => [
      key,
      {
        type: inferJsonValueType(fieldValue),
        description: `${key} from parsed resource contract.`,
      },
    ]),
  )
  return {
    type: 'object',
    properties: Object.keys(properties).length ? properties : {
      payload: {
        type: 'object',
        description: fallbackDescription,
      },
    },
    required: Object.keys(properties),
  }
}

function buildResourceContract(sourceType: SourceType, content: string): ResourceContract {
  if (!content.trim()) return null

  if (sourceType === 'api_doc') {
    const firstLine = linesFromText(content)[0] ?? ''
    const routeMatch = content.match(/\b(GET|POST|PUT|PATCH|DELETE)\s+([/\w{}:.-]+)/i)
    const requestSchema = extractJsonAfterLabel(content, 'Request|Params|Parameters')
    const responseSchema = extractJsonAfterLabel(content, 'Response|Returns')
    const requiredParams = Object.keys(requestSchema)
    const title = firstLine.replace(/\b(GET|POST|PUT|PATCH|DELETE)\b/i, '').trim() || 'API skill'
    return {
      kind: 'api_doc',
      title,
      method: (routeMatch?.[1] ?? 'POST').toUpperCase(),
      endpoint: routeMatch?.[2] ?? '/api/unknown',
      requiredParams,
      optionalParams: [],
      requestSchema,
      responseSchema,
      exampleCall: `${(routeMatch?.[1] ?? 'POST').toUpperCase()} ${routeMatch?.[2] ?? '/api/unknown'}`,
    }
  }

  if (sourceType === 'script') {
    const language = /\basync\s+function\b|\bconst\s+\w+\s*=\s*async\b|\bfunction\s+\w+\s*\(/.test(content)
      ? 'javascript'
      : /\bdef\s+\w+\s*\(/.test(content)
        ? 'python'
        : content.trimStart().startsWith('#!') ? 'shell' : 'script'
    const functionMatch = content.match(/\b(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*([^:\n]+))?:/i)
      ?? content.match(/\b(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)/i)
    const entrypoint = functionMatch?.[1] ?? 'main'
    const args = (functionMatch?.[2] ?? '')
      .split(',')
      .map(item => item.trim().split(':')[0].trim())
      .filter(Boolean)
    const returns = functionMatch?.[3]?.trim() ?? (/\breturn\s+/.test(content) ? 'inferred from return statement' : 'side effects only')
    const dependencies = Array.from(new Set([
      ...Array.from(content.matchAll(/^\s*import\s+([\w.]+)/gm)).map(match => match[1]),
      ...Array.from(content.matchAll(/^\s*from\s+([\w.]+)\s+import/gm)).map(match => match[1]),
      ...Array.from(content.matchAll(/require\(["']([^"']+)["']\)/g)).map(match => match[1]),
    ]))
    const sideEffects = [
      /page\.|browser\.|goto\(|click\(|fill\(/.test(content) ? 'browser automation' : '',
      /open\(|write|append|csv\.|fs\./.test(content) ? 'file IO' : '',
      /fetch\(|axios|requests\.|http/.test(content) ? 'network call' : '',
    ].filter(Boolean)
    return {
      kind: 'script',
      title: entrypoint,
      language,
      entrypoint,
      args,
      returns,
      dependencies,
      sideEffects,
      runnableHint: args.length ? `${entrypoint}(${args.join(', ')})` : entrypoint,
      codeSnippet: content.slice(0, 800),
    }
  }

  return null
}

function contractInputSchema(contract: ResourceContract): Record<string, unknown> | null {
  if (!contract) return null
  if (contract.kind === 'api_doc') {
    return schemaFromObject(contract.requestSchema, `Request payload for ${contract.method} ${contract.endpoint}.`)
  }
  return {
    type: 'object',
    properties: Object.fromEntries(
      contract.args.map(arg => [
        arg,
        {
          type: 'string',
          description: `Argument for ${contract.entrypoint}.`,
        },
      ]),
    ),
    required: contract.args,
  }
}

function contractOutputSchema(contract: ResourceContract): Record<string, unknown> | null {
  if (!contract) return null
  if (contract.kind === 'api_doc') {
    return schemaFromObject(contract.responseSchema, `Response payload from ${contract.method} ${contract.endpoint}.`)
  }
  return {
    type: 'object',
    properties: {
      result: {
        type: 'object',
        description: contract.returns,
      },
      ok: {
        type: 'boolean',
        description: 'Whether the script completed successfully.',
      },
    },
    required: ['result'],
  }
}

function buildCandidateDraft(unit: ExperienceUnit, sourceType: SourceType): CandidateDraft {
  const metadata = metadataRecord(unit)
  const candidateInterface = nestedRecord(metadata, 'candidate_interface')
  const candidateImplementation = nestedRecord(metadata, 'candidate_implementation')
  const candidateRelations = nestedRecord(metadata, 'candidate_relations')
  const evidence = nestedRecord(metadata, 'ctx2skill_evidence')
  const selectedCandidate = nestedRecord(evidence, 'selected_candidate')
  const name = toSnakeCase(unit.proposed_skill_name || `skill_from_${sourceType}_${unit.unit_id}`)
  const description = unit.proposed_description || unit.summary || `Candidate Skill imported from ${sourceType}.`
  const skillType = isSkillType(unit.proposed_type) ? unit.proposed_type : 'atomic'
  const metadataTags = asStringArray(metadata.candidate_tags)
  const tags = Array.from(new Set([sourceType, 'candidate-review', ...metadataTags, ...unit.index_keywords.slice(0, 3)]))
  const contract = buildResourceContract(sourceType, unit.raw_content)

  const inputSchema = asRecord(candidateInterface.input_schema) ?? contractInputSchema(contract) ?? {
    type: 'object',
    properties: {
      context: {
        type: 'object',
        description: 'Runtime context distilled from the imported source experience.',
      },
    },
  }
  const outputSchema = asRecord(candidateInterface.output_schema) ?? contractOutputSchema(contract) ?? {
    type: 'object',
    properties: {
      result: {
        type: 'object',
        description: 'Structured result produced by the candidate Skill.',
      },
    },
  }
  const parentSkillIds = asStringArray(candidateRelations.parent_skill_ids)
  const dependencyIds = asStringArray(candidateRelations.dependency_ids)
  const componentIds = asStringArray(candidateRelations.component_ids)
  const subSkillIds = asStringArray(candidateImplementation.sub_skill_ids)
  const toolCalls = asStringArray(candidateImplementation.tool_calls)
  const provenance: SkillProvenance = {
    source_type: sourceType,
    source_ids: [unit.unit_id],
    parent_skill_ids: parentSkillIds,
    created_by_agent: 'human_reviewer',
    creation_context: {
      import_unit_id: unit.unit_id,
      source_confidence: unit.confidence,
      index_keywords: unit.index_keywords,
      raw_content_preview: unit.raw_content,
      resource_contract: contract,
      ctx2skill_evidence: metadata.ctx2skill_evidence ?? null,
      layering_reason: metadata.layering_reason ?? null,
      graph_relation_preview: metadata.graph_relation_preview ?? [],
    },
  }
  const verifierDescription = sourceType === 'document' || sourceType === 'past_skills'
    ? 'Replace Ctx2Skill-lite challenge replay with a deterministic verifier before S3/S4 release.'
    : 'Replace with deterministic verifier before S3/S4 release.'
  const evaluation: SkillEvaluation = {
    verifier_specs: [
      {
        type: 'placeholder',
        description: verifierDescription,
      },
    ],
    test_case_refs: [],
    benchmark_task_ids: [],
    validation_summary: 'Pending human validation. This Skill remains Candidate (S1).',
  }

  const contractPrompt = contract?.kind === 'api_doc'
    ? `Call ${contract.method} ${contract.endpoint} with reviewed parameters and validate the response schema.`
    : contract?.kind === 'script'
      ? `Run or adapt ${contract.entrypoint} using the reviewed arguments. Runnable hint: ${contract.runnableHint}.`
      : String(candidateImplementation.prompt_template || selectedCandidate.prompt_template || unit.summary || description)
  const metadataPreconditions = asStringArray(candidateInterface.preconditions)
  const metadataPostconditions = asStringArray(candidateInterface.postconditions)

  return {
    name,
    description,
    skillType,
    tagsText: tags.join(', '),
    inputSchemaText: toJson(inputSchema),
    outputSchemaText: toJson(outputSchema),
    preconditionsText: metadataPreconditions.length
      ? metadataPreconditions.join('\n')
      : contract?.kind === 'api_doc'
      ? `Endpoint ${contract.endpoint} is reachable.\nRequired parameters are available: ${contract.requiredParams.join(', ') || 'reviewed request payload'}.`
      : contract?.kind === 'script'
        ? `Runtime supports ${contract.language}.\nDependencies are available: ${contract.dependencies.join(', ') || 'none detected'}.`
        : '',
    postconditionsText: metadataPostconditions.length
      ? metadataPostconditions.join('\n')
      : contract?.kind === 'api_doc'
      ? `Response conforms to the reviewed schema for ${contract.method} ${contract.endpoint}.`
      : contract?.kind === 'script'
        ? `Entrypoint ${contract.entrypoint} completes and returns ${contract.returns}.`
        : 'The Skill returns a structured result aligned with the reviewed source experience.',
    promptTemplate: contractPrompt,
    provenanceText: toJson(provenance),
    evaluationText: toJson(evaluation),
    dependencyIds,
    componentIds,
    subSkillIds,
    parentSkillIds,
    toolCalls,
  }
}

export default function KnowledgeImport() {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<SourceType>('trajectory')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [auditing, setAuditing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [promoting, setPromoting] = useState(false)
  const [pipelineStage, setPipelineStage] = useState(-1)
  const [result, setResult] = useState<IngestResponse | null>(null)
  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, CandidateDraft>>({})
  const [auditResult, setAuditResult] = useState<CandidateAuditResult | null>(null)
  const [createdSkill, setCreatedSkill] = useState<CreatedSkill | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadedFileName, setLoadedFileName] = useState<string | null>(null)

  const currentSource = SOURCE_TYPES.find(s => s.key === activeTab)!
  const selectedUnit = result?.units.find(unit => unit.unit_id === selectedUnitId) ?? result?.units[0] ?? null
  const currentDraft = selectedUnit ? drafts[selectedUnit.unit_id] : undefined
  const sourceContract = buildResourceContract(activeTab, content)
  const selectedMetadata = metadataRecord(selectedUnit)
  const selectedEvidence = nestedRecord(selectedMetadata, 'ctx2skill_evidence')
  const selectedGraphPreview = Array.isArray(selectedMetadata.graph_relation_preview)
    ? selectedMetadata.graph_relation_preview
    : []
  const selectedLayeringReason = typeof selectedMetadata.layering_reason === 'string'
    ? selectedMetadata.layering_reason
    : ''

  const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

  const resetReviewState = () => {
    setAuditResult(null)
    setCreatedSkill(null)
  }

  const resetParsedContentState = () => {
    setResult(null)
    setDrafts({})
    setSelectedUnitId(null)
    resetReviewState()
  }

  const handleFileBeforeUpload: UploadProps['beforeUpload'] = async file => {
    if (file.size > MAX_IMPORT_FILE_BYTES) {
      setError('File is too large for browser-side import. Use a file under 2 MB.')
      return Upload.LIST_IGNORE
    }

    try {
      const text = await file.text()
      setContent(text)
      setLoadedFileName(file.name)
      setError(null)
      resetParsedContentState()
    } catch {
      setError('Could not read this file as text.')
    }
    return Upload.LIST_IGNORE
  }

  const handleParse = async () => {
    if (!content.trim()) {
      setError('Please enter source content first.')
      return
    }

    setLoading(true)
    setResult(null)
    setError(null)
    resetReviewState()
    setPipelineStage(0)

    try {
      const stagePromise = (async () => {
        for (let i = 0; i < PIPELINE_STAGES.length; i += 1) {
          setPipelineStage(i)
          await sleep(300)
        }
      })()

      const [res] = await Promise.all([ingestApi.parse(activeTab, content), stagePromise])
      const nextDrafts = Object.fromEntries(
        res.units.map(unit => [unit.unit_id, buildCandidateDraft(unit, activeTab)]),
      )
      setPipelineStage(PIPELINE_STAGES.length)
      setResult(res)
      setDrafts(nextDrafts)
      setSelectedUnitId(res.units[0]?.unit_id ?? null)
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, 'Parse failed'))
      setPipelineStage(-1)
    } finally {
      setLoading(false)
    }
  }

  const updateDraft = (patch: Partial<CandidateDraft>) => {
    if (!selectedUnit) return
    setDrafts(prev => ({
      ...prev,
      [selectedUnit.unit_id]: {
        ...prev[selectedUnit.unit_id],
        ...patch,
      },
    }))
  }

  const buildPayload = (): CandidateSkillReviewRequest => {
    if (!selectedUnit || !currentDraft) {
      throw new Error('Select a parsed experience unit first.')
    }
    const inputSchema = parseJsonObject<Record<string, unknown>>(currentDraft.inputSchemaText, 'Input schema')
    const outputSchema = parseJsonObject<Record<string, unknown>>(currentDraft.outputSchemaText, 'Output schema')
    const provenance = parseJsonObject<SkillProvenance>(currentDraft.provenanceText, 'Provenance')
    const evaluation = parseJsonObject<SkillEvaluation>(currentDraft.evaluationText, 'Evaluation')
    return {
      source_type: activeTab,
      unit_id: selectedUnit.unit_id,
      raw_content: selectedUnit.raw_content || content,
      name: toSnakeCase(currentDraft.name),
      description: currentDraft.description.trim(),
      skill_type: currentDraft.skillType,
      tags: tagsFromText(currentDraft.tagsText),
      input_schema: inputSchema,
      output_schema: outputSchema,
      preconditions: linesFromText(currentDraft.preconditionsText),
      postconditions: linesFromText(currentDraft.postconditionsText),
      prompt_template: currentDraft.promptTemplate,
      provenance,
      evaluation,
      dependency_ids: currentDraft.dependencyIds,
      component_ids: currentDraft.componentIds,
      sub_skill_ids: currentDraft.subSkillIds,
      parent_skill_ids: currentDraft.parentSkillIds,
      tool_calls: currentDraft.toolCalls,
      author: 'human_reviewer',
    }
  }

  const handleRunAudit = async () => {
    try {
      setAuditing(true)
      setError(null)
      const audit = await ingestApi.auditCandidate(buildPayload())
      setAuditResult(audit)
    } catch (err) {
      setError(err instanceof Error ? err.message : getApiErrorMessage(err, 'Audit failed'))
    } finally {
      setAuditing(false)
    }
  }

  const handleCreateCandidate = async () => {
    try {
      setCreating(true)
      setError(null)
      const response = await ingestApi.createCandidate(buildPayload())
      setCreatedSkill(response.created_skill)
      setAuditResult(response.audit)
    } catch (err) {
      setError(err instanceof Error ? err.message : getApiErrorMessage(err, 'Create candidate failed'))
    } finally {
      setCreating(false)
    }
  }

  const handlePromoteDraft = async () => {
    if (!createdSkill) return
    try {
      setPromoting(true)
      setError(null)
      const promoted = await lifecycleApi.transition(
        createdSkill.skill_id,
        'S2',
        'Promoted from reviewed import candidate',
      )
      setCreatedSkill({ ...createdSkill, state: promoted.state })
    } catch (err) {
      setError(getApiErrorMessage(err, 'Promote draft failed'))
    } finally {
      setPromoting(false)
    }
  }

  const selectUnit = (unitId: string) => {
    setSelectedUnitId(unitId)
    resetReviewState()
  }

  return (
    <div style={{ padding: 24 }}>
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h2 style={{ fontWeight: 700, marginBottom: 4 }}>Knowledge Import</h2>
        <p style={{ color: '#666', marginBottom: 24 }}>
          Parse experience sources into editable Candidate Skills. Imported Skills stay Candidate (S1)
          until a human reviewer audits, creates, and optionally promotes them to Draft (S2).
        </p>
      </motion.div>

      {error && (
        <Alert
          type="error"
          showIcon
          closable
          title="Parse failed"
          description={error}
          style={{ marginBottom: 16 }}
          onClose={() => setError(null)}
        />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={10}>
          <Card variant="borderless" style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
            <Tabs
              activeKey={activeTab}
              onChange={key => {
                setActiveTab(key as SourceType)
                setContent('')
                setLoadedFileName(null)
                setError(null)
                resetParsedContentState()
              }}
              items={SOURCE_TYPES.map(source => ({
                key: source.key,
                label: <span>{source.icon} {source.label}</span>,
                children: null,
              }))}
            />

            <Tag color={currentSource.color} style={{ marginBottom: 8 }}>
              {currentSource.icon} {currentSource.label}
            </Tag>

            <TextArea
              value={content}
              onChange={event => {
                setContent(event.target.value)
                setLoadedFileName(null)
              }}
              placeholder={currentSource.placeholder}
              rows={15}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />

            <Dragger
              accept=".txt,.md,.markdown,.json,.jsonl,.yaml,.yml,.py,.js,.ts,.tsx,.sh,.bash,.csv,.log,.html,.xml"
              beforeUpload={handleFileBeforeUpload}
              multiple={false}
              showUploadList={false}
              style={{ marginTop: 12, padding: '6px 0', background: '#fafafa' }}
            >
              <p className="ant-upload-drag-icon" style={{ marginBottom: 4 }}>
                <CloudUploadOutlined style={{ color: currentSource.color }} />
              </p>
              <p className="ant-upload-text" style={{ marginBottom: 0 }}>
                Drop a source file here or click to choose one
              </p>
              <p className="ant-upload-hint" style={{ marginBottom: 0 }}>
                The file is read locally into this {currentSource.label} input. Nothing is created until you parse and review it.
              </p>
            </Dragger>

            {loadedFileName && (
              <Alert
                type="success"
                showIcon
                style={{ marginTop: 10 }}
                message={`Loaded ${loadedFileName}`}
                description="Review the text above, then parse it for Candidate Review."
              />
            )}

            {(activeTab === 'api_doc' || activeTab === 'script' || activeTab === 'past_skills') && (
              <Space wrap style={{ marginTop: 10 }}>
                {RESOURCE_EXAMPLES
                  .filter(example => example.sourceType === activeTab)
                  .map(example => (
                    <Button
                      key={example.title}
                      size="small"
                      onClick={() => {
                        setContent(example.content)
                        setLoadedFileName(null)
                        resetParsedContentState()
                      }}
                    >
                      {example.title}
                    </Button>
                  ))}
              </Space>
            )}

            {sourceContract && (
              <Card size="small" style={{ marginTop: 12, background: '#fafafa', borderRadius: 8 }}>
                {sourceContract.kind === 'api_doc' ? (
                  <Descriptions size="small" column={1}>
                    <Descriptions.Item label="Endpoint">
                      <Space><Tag color="purple">{sourceContract.method}</Tag><Text code>{sourceContract.endpoint}</Text></Space>
                    </Descriptions.Item>
                    <Descriptions.Item label="Params">
                      {sourceContract.requiredParams.length
                        ? sourceContract.requiredParams.map(param => <Tag key={param}>{param}</Tag>)
                        : <Text type="secondary">No explicit request fields detected</Text>}
                    </Descriptions.Item>
                    <Descriptions.Item label="Response schema">
                      <pre style={PREVIEW_BLOCK_STYLE}>{toJson(sourceContract.responseSchema)}</pre>
                    </Descriptions.Item>
                  </Descriptions>
                ) : (
                  <Descriptions size="small" column={1}>
                    <Descriptions.Item label="Entrypoint">
                      <Space><Tag color="orange">{sourceContract.language}</Tag><Text code>{sourceContract.entrypoint}</Text></Space>
                    </Descriptions.Item>
                    <Descriptions.Item label="Args">
                      {sourceContract.args.length
                        ? sourceContract.args.map(arg => <Tag key={arg}>{arg}</Tag>)
                        : <Text type="secondary">No explicit args detected</Text>}
                    </Descriptions.Item>
                    <Descriptions.Item label="Returns">{sourceContract.returns}</Descriptions.Item>
                    <Descriptions.Item label="Effects">
                      {sourceContract.sideEffects.length
                        ? sourceContract.sideEffects.map(effect => <Tag key={effect}>{effect}</Tag>)
                        : <Text type="secondary">No side effects detected</Text>}
                    </Descriptions.Item>
                  </Descriptions>
                )}
              </Card>
            )}

            <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <Button type="primary" icon={<CloudUploadOutlined />} loading={loading} onClick={handleParse}>
                Parse for Candidate Review
              </Button>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Parsing does not release a Skill. It only prepares an S1 candidate draft.
              </Text>
            </div>

            {(loading || pipelineStage >= 0) && (
              <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} style={{ marginTop: 16 }}>
                <Divider style={{ margin: '12px 0' }} />
                <Text type="secondary" style={{ fontSize: 12 }}>Experience Processing Pipeline</Text>
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

          {result && (
            <Card
              title="Parsed Units"
              variant="borderless"
              style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', marginTop: 16 }}
              extra={<Badge status={result.success ? 'success' : 'error'} text={result.success ? 'Ready' : 'Check errors'} />}
            >
              <Row gutter={[8, 8]} style={{ marginBottom: 12 }}>
                <Col span={8}>
                  <div style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 22, fontWeight: 700, color: '#1677ff' }}>{result.unit_count}</div>
                    <Text type="secondary" style={{ fontSize: 12 }}>Units</Text>
                  </div>
                </Col>
                <Col span={8}>
                  <div style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 22, fontWeight: 700, color: '#722ed1' }}>{result.token_usage}</div>
                    <Text type="secondary" style={{ fontSize: 12 }}>Tokens</Text>
                  </div>
                </Col>
                <Col span={8}>
                  <div style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 22, fontWeight: 700, color: '#ff4d4f' }}>{result.errors.length}</div>
                    <Text type="secondary" style={{ fontSize: 12 }}>Errors</Text>
                  </div>
                </Col>
              </Row>

              {result.errors.length > 0 && (
                <Alert type="warning" title="Parser warnings" description={result.errors.join('; ')} style={{ marginBottom: 12 }} />
              )}

                <Space orientation="vertical" style={{ width: '100%' }}>
                {result.units.map(unit => (
                  <Button
                    key={unit.unit_id}
                    block
                    type={unit.unit_id === selectedUnit?.unit_id ? 'primary' : 'default'}
                    onClick={() => selectUnit(unit.unit_id)}
                    style={{ height: 'auto', textAlign: 'left', padding: 10 }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                      <span>{drafts[unit.unit_id]?.name ?? unit.proposed_skill_name ?? unit.unit_id}</span>
                      <Tag color={TYPE_COLOR[unit.proposed_type || 'atomic'] || 'default'}>{unit.proposed_type || 'atomic'}</Tag>
                    </div>
                    <Progress
                      percent={Math.round(unit.confidence * 100)}
                      size="small"
                      strokeColor={unit.confidence > 0.7 ? '#52c41a' : '#faad14'}
                    />
                  </Button>
                ))}
              </Space>
            </Card>
          )}
        </Col>

        <Col xs={24} lg={14}>
          <AnimatePresence mode="wait">
            {!selectedUnit || !currentDraft ? (
              <motion.div key="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                <Card
                  variant="borderless"
                  style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)', textAlign: 'center', padding: 40 }}
                >
                  <CloudUploadOutlined style={{ fontSize: 48, color: '#d9d9d9', marginBottom: 12 }} />
                  <div style={{ color: '#999' }}>Parse content to open the Candidate Review panel.</div>
                </Card>
              </motion.div>
            ) : (
              <motion.div key={selectedUnit.unit_id} initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }}>
                <Card
                  title="Candidate Review"
                  variant="borderless"
                  style={{ borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}
                  extra={<Tag color="orange">Candidate (S1), not Released</Tag>}
                >
                  <Alert
                    type="info"
                    showIcon
                    title="Human review boundary"
                    description="This panel turns parsed experience into a candidate Skill. Audit results are evidence, not release approval."
                    style={{ marginBottom: 16 }}
                  />

                  <Collapse
                    bordered={false}
                    items={[
                      {
                        key: 'raw',
                        label: 'Raw input preview',
                        children: (
                          <div style={PREVIEW_BLOCK_STYLE}>
                            {selectedUnit.raw_content || content}
                          </div>
                        ),
                      },
                      {
                        key: 'structured',
                        label: 'Structured experience preview',
                        children: (
                          <div>
                            <Paragraph>{selectedUnit.summary}</Paragraph>
                            <Space wrap style={{ marginBottom: 8 }}>
                              {selectedUnit.extracted_actions.map((action, index) => (
                                <Tag key={`${selectedUnit.unit_id}-action-${index}`}>{action}</Tag>
                              ))}
                            </Space>
                            <pre style={PREVIEW_BLOCK_STYLE}>
                              {toJson(selectedUnit.normalized_actions)}
                            </pre>
                          </div>
                        ),
                      },
                      ...(Object.keys(selectedEvidence).length
                        ? [{
                          key: 'ctx2skill',
                          label: 'Ctx2Skill Evidence',
                          children: (
                            <Descriptions column={1} bordered size="small">
                              <Descriptions.Item label="Paper method">
                                {String(selectedEvidence.paper_method || 'Ctx2Skill-lite')}
                              </Descriptions.Item>
                              <Descriptions.Item label="Selected reason">
                                {String(selectedEvidence.selected_reason || '')}
                              </Descriptions.Item>
                              <Descriptions.Item label="Challenges">
                                <pre style={PREVIEW_BLOCK_STYLE}>{toJson(selectedEvidence.challenges || [])}</pre>
                              </Descriptions.Item>
                              <Descriptions.Item label="Judge results">
                                <pre style={PREVIEW_BLOCK_STYLE}>{toJson(selectedEvidence.judge_results || [])}</pre>
                              </Descriptions.Item>
                              <Descriptions.Item label="Candidate scores">
                                <pre style={PREVIEW_BLOCK_STYLE}>{toJson(selectedEvidence.candidate_scores || [])}</pre>
                              </Descriptions.Item>
                            </Descriptions>
                          ),
                        }]
                        : []),
                      ...(selectedLayeringReason
                        ? [{
                          key: 'skillx-layer',
                          label: 'SkillX Layering',
                          children: <Alert type="info" showIcon message={currentDraft.skillType} description={selectedLayeringReason} />,
                        }]
                        : []),
                      ...(selectedGraphPreview.length
                        ? [{
                          key: 'graph-preview',
                          label: 'Graph Relation Preview',
                          children: <pre style={PREVIEW_BLOCK_STYLE}>{toJson(selectedGraphPreview)}</pre>,
                        }]
                        : []),
                      ...(activeTab === 'api_doc' || activeTab === 'script'
                        ? [{
                          key: 'contract',
                          label: 'Resource contract preview',
                          children: (() => {
                            const contract = buildResourceContract(activeTab, selectedUnit.raw_content || content)
                            if (!contract) return <Text type="secondary">No resource contract detected.</Text>
                            return contract.kind === 'api_doc' ? (
                              <Descriptions column={1} bordered size="small">
                                <Descriptions.Item label="Endpoint">
                                  <Space><Tag color="purple">{contract.method}</Tag><Text code>{contract.endpoint}</Text></Space>
                                </Descriptions.Item>
                                <Descriptions.Item label="Required params">
                                  {contract.requiredParams.length
                                    ? contract.requiredParams.map(param => <Tag key={param}>{param}</Tag>)
                                    : <Text type="secondary">None detected</Text>}
                                </Descriptions.Item>
                                <Descriptions.Item label="Request schema">
                                  <pre style={PREVIEW_BLOCK_STYLE}>{toJson(contract.requestSchema)}</pre>
                                </Descriptions.Item>
                                <Descriptions.Item label="Response schema">
                                  <pre style={PREVIEW_BLOCK_STYLE}>{toJson(contract.responseSchema)}</pre>
                                </Descriptions.Item>
                                <Descriptions.Item label="Example call">{contract.exampleCall}</Descriptions.Item>
                              </Descriptions>
                            ) : (
                              <Descriptions column={1} bordered size="small">
                                <Descriptions.Item label="Entrypoint"><Text code>{contract.entrypoint}</Text></Descriptions.Item>
                                <Descriptions.Item label="Language">{contract.language}</Descriptions.Item>
                                <Descriptions.Item label="Arguments">
                                  {contract.args.length
                                    ? contract.args.map(arg => <Tag key={arg}>{arg}</Tag>)
                                    : <Text type="secondary">None detected</Text>}
                                </Descriptions.Item>
                                <Descriptions.Item label="Returns">{contract.returns}</Descriptions.Item>
                                <Descriptions.Item label="Dependencies">
                                  {contract.dependencies.length
                                    ? contract.dependencies.map(dep => <Tag key={dep}>{dep}</Tag>)
                                    : <Text type="secondary">None detected</Text>}
                                </Descriptions.Item>
                                <Descriptions.Item label="Runnable hint"><Text code>{contract.runnableHint}</Text></Descriptions.Item>
                              </Descriptions>
                            )
                          })(),
                        }]
                        : []),
                    ]}
                    style={{ marginBottom: 16 }}
                  />

                  <Row gutter={[12, 12]}>
                    <Col xs={24} md={14}>
                      <Text strong>Candidate name</Text>
                      <Input
                        value={currentDraft.name}
                        onChange={event => updateDraft({ name: event.target.value })}
                        onBlur={() => updateDraft({ name: toSnakeCase(currentDraft.name) })}
                      />
                    </Col>
                    <Col xs={24} md={10}>
                      <Text strong>Skill type</Text>
                      <Select
                        value={currentDraft.skillType}
                        onChange={value => updateDraft({ skillType: value })}
                        style={{ width: '100%' }}
                        options={[
                          { label: 'atomic', value: 'atomic' },
                          { label: 'functional', value: 'functional' },
                          { label: 'strategic', value: 'strategic' },
                        ]}
                      />
                    </Col>
                    <Col span={24}>
                      <Text strong>Description</Text>
                      <TextArea
                        value={currentDraft.description}
                        onChange={event => updateDraft({ description: event.target.value })}
                        rows={2}
                      />
                    </Col>
                    <Col span={24}>
                      <Text strong>Tags</Text>
                      <Input
                        value={currentDraft.tagsText}
                        onChange={event => updateDraft({ tagsText: event.target.value })}
                        placeholder="comma,separated,tags"
                      />
                    </Col>
                    <Col span={24}>
                      <Text strong>Prompt template</Text>
                      <TextArea
                        value={currentDraft.promptTemplate}
                        onChange={event => updateDraft({ promptTemplate: event.target.value })}
                        rows={3}
                      />
                    </Col>
                    <Col xs={24} md={12}>
                      <Text strong>Input schema JSON</Text>
                      <TextArea
                        value={currentDraft.inputSchemaText}
                        onChange={event => updateDraft({ inputSchemaText: event.target.value })}
                        rows={8}
                        style={{ fontFamily: 'monospace', fontSize: 12 }}
                      />
                    </Col>
                    <Col xs={24} md={12}>
                      <Text strong>Output schema JSON</Text>
                      <TextArea
                        value={currentDraft.outputSchemaText}
                        onChange={event => updateDraft({ outputSchemaText: event.target.value })}
                        rows={8}
                        style={{ fontFamily: 'monospace', fontSize: 12 }}
                      />
                    </Col>
                    <Col xs={24} md={12}>
                      <Text strong>Preconditions</Text>
                      <TextArea
                        value={currentDraft.preconditionsText}
                        onChange={event => updateDraft({ preconditionsText: event.target.value })}
                        rows={3}
                        placeholder="One condition per line"
                      />
                    </Col>
                    <Col xs={24} md={12}>
                      <Text strong>Postconditions</Text>
                      <TextArea
                        value={currentDraft.postconditionsText}
                        onChange={event => updateDraft({ postconditionsText: event.target.value })}
                        rows={3}
                        placeholder="One condition per line"
                      />
                    </Col>
                    <Col xs={24} md={12}>
                      <Text strong>Provenance JSON</Text>
                      <TextArea
                        value={currentDraft.provenanceText}
                        onChange={event => updateDraft({ provenanceText: event.target.value })}
                        rows={8}
                        style={{ fontFamily: 'monospace', fontSize: 12 }}
                      />
                    </Col>
                    <Col xs={24} md={12}>
                      <Text strong>Evaluation placeholder JSON</Text>
                      <TextArea
                        value={currentDraft.evaluationText}
                        onChange={event => updateDraft({ evaluationText: event.target.value })}
                        rows={8}
                        style={{ fontFamily: 'monospace', fontSize: 12 }}
                      />
                    </Col>
                  </Row>

                  {auditResult && (
                    <Alert
                      type={auditResult.passed ? (auditResult.warnings.length ? 'warning' : 'success') : 'error'}
                      showIcon
                      style={{ marginTop: 16 }}
                      title={`Audit score ${Math.round(auditResult.audit_score * 100)}%`}
                      description={
                        <div>
                          <Space wrap style={{ marginBottom: 8 }}>
                            <Tag color={auditResult.schema_ok ? 'green' : 'red'}>schema</Tag>
                            <Tag color={auditResult.safety_ok ? 'green' : 'red'}>safety</Tag>
                            <Tag color={auditResult.postcondition_ok ? 'green' : 'red'}>postconditions</Tag>
                          </Space>
                          {auditResult.issues.map(issue => <div key={issue}>Issue: {issue}</div>)}
                          {auditResult.warnings.map(warning => <div key={warning}>Warning: {warning}</div>)}
                          {auditResult.recommendations.slice(0, 3).map(item => <div key={item}>Recommendation: {item}</div>)}
                        </div>
                      }
                    />
                  )}

                  {createdSkill && (
                    <Alert
                      type="success"
                      showIcon
                      style={{ marginTop: 16 }}
                      title={`Created ${createdSkill.name} as ${createdSkill.state}`}
                    description={
                        <Space wrap>
                          <Link to={`/wiki?skill_id=${encodeURIComponent(createdSkill.skill_id)}`}>
                            Open in Wiki
                          </Link>
                          {createdSkill.state === 'S2' && (
                            <Link to={`/harness?skill_id=${encodeURIComponent(createdSkill.skill_id)}`}>
                              Open Verification Loop
                            </Link>
                          )}
                          <Text type="secondary">Version {createdSkill.version}</Text>
                        </Space>
                      }
                    />
                  )}

                  <div style={{ marginTop: 16, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <Button icon={<SafetyCertificateOutlined />} loading={auditing} onClick={handleRunAudit}>
                      Run Auditor
                    </Button>
                    <Button type="primary" icon={<CheckCircleOutlined />} loading={creating} onClick={handleCreateCandidate}>
                      Create Candidate
                    </Button>
                    <Button disabled={!createdSkill || createdSkill.state !== 'S1'} loading={promoting} onClick={handlePromoteDraft}>
                      Promote Draft (S2)
                    </Button>
                    <Button
                      icon={<SafetyCertificateOutlined />}
                      disabled={!createdSkill || createdSkill.state !== 'S2'}
                      onClick={() => createdSkill && navigate(`/harness?skill_id=${encodeURIComponent(createdSkill.skill_id)}`)}
                    >
                      Verify Draft
                    </Button>
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
