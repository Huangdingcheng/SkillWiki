// SkillOS API 类型定义

export type SkillType = 'atomic' | 'functional' | 'strategic'
export type SkillState = 'S0' | 'S1' | 'S2' | 'S3' | 'S4' | 'S5' | 'S6' | 'S7'

export const STATE_LABELS: Record<SkillState, string> = {
  S0: 'Raw Experience',
  S1: 'Candidate',
  S2: 'Draft',
  S3: 'Verified',
  S4: 'Released',
  S5: 'Degraded',
  S6: 'Deprecated',
  S7: 'Archived',
}

export interface SkillParameter {
  name: string
  type: string
  description: string
  required: boolean
  default?: unknown
}

export interface SkillInterface {
  inputs: SkillParameter[]
  outputs: SkillParameter[]
  preconditions: string[]
  postconditions: string[]
}

export interface SkillImplementation {
  language: string
  code?: string
  prompt_template?: string
  tool_calls: string[]
  sub_skill_ids: string[]
}

export interface SkillMetrics {
  usage_count: number
  success_count: number
  failure_count: number
  avg_latency_ms: number
  p95_latency_ms: number
  last_used_at?: string
  // computed fields (serialized by backend)
  total_executions: number
  successful_executions: number
  failed_executions: number
  success_rate: number
}

export interface SkillSummary {
  skill_id: string
  name: string
  description: string
  skill_type: SkillType
  state: SkillState
  tags: string[]
  version: string
  granularity_level: number
  metrics: SkillMetrics
  created_at: string
  updated_at: string
}

export interface SkillFull extends SkillSummary {
  interface: SkillInterface
  implementation?: SkillImplementation
}

export interface SkillSearchResult {
  skill_id: string
  name: string
  description: string
  skill_type: SkillType
  state: SkillState
  tags: string[]
  version: string
  score: number
  match_reason: string
}

export interface GraphNodeData {
  id: string
  name: string
  skill_type: string
  state: string
  tags: string[]
  version: string
  granularity_level: number
  success_rate: number
  usage_count: number
}

export interface GraphEdgeData {
  id: string
  source: string
  target: string
  edge_type: string
  weight: number
}

export interface GraphData {
  nodes: GraphNodeData[]
  edges: GraphEdgeData[]
  stats: Record<string, unknown>
}

export interface HealthReport {
  skill_id: string
  skill_name: string
  status: 'healthy' | 'degraded' | 'critical' | 'stale' | 'unknown'
  success_rate: number
  usage_count: number
  avg_latency_ms: number
  issues: string[]
  recommendations: string[]
}

export interface SystemHealth {
  total_skills: number
  healthy_count: number
  degraded_count: number
  critical_count: number
  stale_count: number
  health_ratio: number
  skill_reports: HealthReport[]
}

export interface ExecutionStepResult {
  step_id: string
  skill_id: string
  skill_name: string
  status: string
  outputs: Record<string, unknown>
  latency_ms: number
  error?: string
}

export interface RetrievedSkill {
  skill_id: string
  name: string
  description: string
  skill_type: string
  score: number
  match_reason: string
}

export interface ExecutionResult {
  plan_id: string
  goal: string
  status: string
  steps: ExecutionStepResult[]
  total_latency_ms: number
  final_state: Record<string, unknown>
  retrieved_skills: RetrievedSkill[]
  experience_recorded: boolean
  suggested_skill?: Record<string, unknown>
}

export interface OverviewStats {
  total_skills: number
  by_state: Record<string, number>
  by_type: Record<string, number>
  total_executions: number
  avg_success_rate: number
  graph_stats: Record<string, unknown>
}
