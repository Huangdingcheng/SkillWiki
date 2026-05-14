// SkillOS API type definitions

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
  inputs?: SkillParameter[]
  outputs?: SkillParameter[]
  input_schema?: {
    type?: string
    properties?: Record<string, {
      type?: string
      description?: string
      default?: unknown
    }>
    required?: string[]
  }
  output_schema?: {
    type?: string
    properties?: Record<string, {
      type?: string
      description?: string
      default?: unknown
    }>
    required?: string[]
  }
  preconditions: string[]
  postconditions: string[]
}

export interface SkillImplementation {
  language: string
  code?: string
  prompt_template?: string
  tool_calls?: string[]
  sub_skill_ids?: string[]
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

export interface SkillEvaluation {
  verifier_specs: Record<string, unknown>[]
  test_case_refs: string[]
  benchmark_task_ids: string[]
  validation_summary?: string | null
}

export interface SkillProvenance {
  source_type: string
  source_ids: string[]
  parent_skill_ids: string[]
  created_by_agent?: string | null
  creation_context: Record<string, unknown>
}

export interface SkillCreateRequest {
  name: string
  description: string
  skill_type: SkillType
  state?: SkillState
  tags?: string[]
  interface: SkillInterface
  implementation?: SkillImplementation
  evaluation?: SkillEvaluation
  provenance?: SkillProvenance
  author?: string
}

export interface SkillUpdateRequest {
  description?: string
  tags?: string[]
  interface?: SkillInterface
  implementation?: SkillImplementation
  evaluation?: SkillEvaluation
  author?: string
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
  evaluation: SkillEvaluation
  metrics: SkillMetrics
  created_at: string
  updated_at: string
}

export interface SkillFull extends SkillSummary {
  interface: SkillInterface
  implementation?: SkillImplementation
  provenance?: SkillProvenance | null
}

export interface ExperienceUnit {
  unit_id: string
  source_type: string
  raw_content: string
  extracted_actions: string[]
  normalized_actions: Record<string, unknown>[]
  summary: string
  proposed_skill_name?: string
  proposed_description?: string
  proposed_type?: string
  confidence: number
  index_keywords: string[]
  index_embedding_hint: string
}

export interface CreatedSkill {
  skill_id: string
  name: string
  skill_type: string
  state: string
  version: string
}

export interface IngestResponse {
  success: boolean
  source_type: string
  unit_count: number
  token_usage: number
  errors: string[]
  units: ExperienceUnit[]
  created_skills?: CreatedSkill[]
}

export interface CandidateSkillReviewRequest {
  source_type: string
  unit_id: string
  raw_content?: string
  name: string
  description: string
  skill_type: SkillType
  tags: string[]
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  preconditions: string[]
  postconditions: string[]
  prompt_template: string
  provenance?: SkillProvenance | null
  evaluation: SkillEvaluation
  author?: string
}

export interface CandidateAuditResult {
  skill_id: string
  skill_name: string
  passed: boolean
  schema_ok: boolean
  safety_ok: boolean
  postcondition_ok: boolean
  issues: string[]
  warnings: string[]
  recommendations: string[]
  audit_score: number
}

export interface CandidateCreateResponse {
  success: boolean
  created_skill: CreatedSkill
  audit: CandidateAuditResult
}

export interface EvaluationModeTotal {
  success?: number
  total?: number
  success_rate?: number
  functional_failure?: number
  api_failure?: number
  skipped?: number
  success_rate_excluding_api_failures?: number
}

export interface EvaluationDemoRow {
  task_id: string
  domain?: string
  no_skill?: string
  raw_prompt?: string
  with_skill?: string
  winner?: string
  failure_reason?: string
  no_skill_latency_ms?: number | null
  raw_prompt_latency_ms?: number | null
  with_skill_latency_ms?: number | null
  no_skill_verifier_passed?: boolean | null
  raw_prompt_verifier_passed?: boolean | null
  with_skill_verifier_passed?: boolean | null
}

export interface EvaluationArtifactBase {
  available: boolean
  source_file: string
  generated_at?: string | null
  updated_at?: string | null
  error?: string | null
}

export interface EvaluationDemoBenchmark extends EvaluationArtifactBase {
  task_count: number
  mode_totals: Record<string, EvaluationModeTotal>
  rows: EvaluationDemoRow[]
  raw_result_file?: string | null
}

export interface EvaluationSearchRow {
  query_id?: string
  query?: string
  domain?: string
  expected_skill_ids: string[]
  lexical_top_skill?: string | null
  hybrid_top_skill?: string | null
  lexical_best_rank?: number | null
  hybrid_best_rank?: number | null
  lexical_top1_hit?: boolean | null
  hybrid_top1_hit?: boolean | null
  hybrid_topk_hit?: boolean | null
}

export interface EvaluationSearchEval extends EvaluationArtifactBase {
  benchmark?: string | null
  schema_version?: string | null
  query_count: number
  summary: Record<string, unknown>
  comparison: Record<string, unknown>
  rows: EvaluationSearchRow[]
}

export interface EvaluationLlmRow {
  task_id?: string
  fallback_status?: string
  fallback_selected?: string[]
  fallback_failure_reason?: string
  llm_status?: string
  llm_selected?: string[]
  llm_api_error_type?: string
  llm_failure_reason?: string
  winner?: string
}

export interface EvaluationLlmPlanner extends EvaluationArtifactBase {
  benchmark?: string | null
  task_count: number
  mode_totals: Record<string, EvaluationModeTotal>
  rows: EvaluationLlmRow[]
}

export interface EvaluationDashboardResponse {
  generated_at: string
  results_dir_present: boolean
  warnings: string[]
  artifacts: {
    demo_benchmark: EvaluationDemoBenchmark
    search_eval: EvaluationSearchEval
    llm_planner: EvaluationLlmPlanner
  }
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
  kind?: string
  description?: string
  metadata?: Record<string, unknown>
}

export interface GraphEdgeData {
  id: string
  source: string
  target: string
  edge_type: string
  weight: number
  confidence?: number
  metadata?: Record<string, unknown>
}

export interface GraphData {
  nodes: GraphNodeData[]
  edges: GraphEdgeData[]
  stats: Record<string, unknown>
}

export type GraphViewMode = 'skill_only' | 'provenance' | 'version_impact'

export interface SkillGraphProjectionEdgeData extends GraphEdgeData {
  confidence: number
  metadata: Record<string, unknown>
}

export interface SkillGraphProjectionData {
  nodes: GraphNodeData[]
  edges: SkillGraphProjectionEdgeData[]
  metadata: Record<string, unknown>
  validation_evidence: Record<string, Record<string, unknown>[]>
  stats: Record<string, unknown>
}

export interface HeterogeneousGraphNodeData {
  id: string
  kind: string
  name: string
  description: string
  metadata: Record<string, unknown>
}

export interface HeterogeneousGraphEdgeData {
  id: string
  source: string
  target: string
  edge_type: string
  weight: number
  metadata: Record<string, unknown>
}

export interface HeterogeneousGraphData {
  nodes: HeterogeneousGraphNodeData[]
  edges: HeterogeneousGraphEdgeData[]
  stats: Record<string, unknown>
}

export interface GraphViewNodeData {
  id: string
  name: string
  kind: string
  description?: string
  skill_type?: string | null
  state?: string | null
  tags: string[]
  version?: string | null
  granularity_level?: number | null
  success_rate?: number | null
  usage_count?: number | null
  metadata: Record<string, unknown>
}

export interface GraphViewEdgeData {
  id: string
  source: string
  target: string
  edge_type: string
  weight: number
  confidence?: number | null
  metadata: Record<string, unknown>
}

export interface GraphViewData {
  view: GraphViewMode
  source_endpoint: string
  nodes: GraphViewNodeData[]
  edges: GraphViewEdgeData[]
  stats: Record<string, unknown>
  metadata: Record<string, unknown>
  validation_evidence: Record<string, Record<string, unknown>[]>
}

export interface MaintenanceProposal {
  proposal_id: string
  skill_id: string
  trigger: string
  recommended_action: string
  evidence: string[]
  root_cause?: string
  patch_hint: string
  feedback_sources?: string[]
  targets_to_fix?: string[]
  invariants_to_preserve?: string[]
  validation_plan?: string[]
  validation_status?: 'untested' | 'repaired' | 'verified' | 'failed' | 'merged' | 'deprioritized'
  attempt_count?: number
  max_attempts?: number
  reviewer_notes?: string
  confidence: number
  requires_human_review: boolean
  status: 'pending' | 'accepted' | 'rejected' | 'superseded'
  source: string
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
  next_action?: MaintenanceProposalNextAction | null
}

export interface MaintenanceProposalNextAction {
  action: string
  method: string
  endpoint: string
  required_payload_fields: string[]
  reason: string
}

export interface MaintenanceProposalListResponse {
  proposals: MaintenanceProposal[]
  total: number
  pending_count: number
}

export interface StructuredDiffEntry {
  field_path?: string
  field?: string
  change_type?: string
  type?: string
  old_value?: unknown
  new_value?: unknown
  category?: string
  is_breaking?: boolean
  review_recommendation?: string
  [key: string]: unknown
}

export interface MaintenanceReviewRequest {
  proposal_id: string
  patched_skill: Record<string, unknown>
  reason?: string
  author?: string
}

export interface MaintenanceReviewResponse {
  skill_id: string
  proposal_id: string
  branch_name: string
  base_commit: string
  head_commit: string
  snapshot_path: string
  structured_diff: StructuredDiffEntry[]
  has_breaking_changes: boolean
  review_status: string
  reason: string
  author: string
}

export interface SnapshotCommitRequest {
  author?: string
  message?: string | null
}

export interface SnapshotCommitResponse {
  skill_id: string
  skill_name: string
  version: string
  snapshot_path: string
  commit: string
  message: string
}

export interface SnapshotHistoryItem {
  commit_hash: string
  author: string
  authored_at: string
  subject: string
  changed_paths: string[]
}

export interface SnapshotHistoryResponse {
  skill_id: string
  snapshot_path: string
  history: SnapshotHistoryItem[]
}

export interface SnapshotDiffRequest {
  from_ref: string
  to_ref?: string
  from_version?: string
  to_version?: string
}

export interface SnapshotDiffResponse {
  skill_id: string
  snapshot_path: string
  from_snapshot_path?: string | null
  to_snapshot_path?: string | null
  from_ref: string
  to_ref: string
  raw_diff: string
  diffs: StructuredDiffEntry[]
  has_breaking_changes: boolean
  review_recommendation: string
}

export interface SkillReleaseRecord {
  tag_name: string
  commit: string
  snapshot_path: string
  skill_id: string
  skill_name: string
  version: string
}

export interface SkillRollbackRecord {
  source_ref: string
  restore_commit: string
  restored_snapshot_path: string
  commit_message: string
  skill_id: string
  skill_name: string
  version: string
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
  maintenance_proposal?: MaintenanceProposal | null
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
  step_index: number
  skill_id: string
  skill_name: string
  status: string
  input_mapping?: Record<string, unknown>
  outputs?: Record<string, unknown>
  result?: Record<string, unknown> | null
  error?: string | null
  latency_ms?: number | null
}

export interface RetrievedSkill {
  skill_id: string
  name: string
  description: string
  skill_type: string
  score: number
  match_reason: string
}

export interface ExecutionExperienceUnit {
  unit_id: string
  source_type: 'agent_execution' | string
  source_execution_id: string
  raw_content: string
  extracted_actions: string[]
  normalized_actions: Record<string, unknown>[]
  summary: string
  proposed_skill_name?: string | null
  proposed_description?: string | null
  proposed_type?: string | null
  confidence: number
  index_keywords: string[]
  index_embedding_hint: string
  metadata: Record<string, unknown>
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
  experience_unit?: ExecutionExperienceUnit | null
  suggested_skill?: Record<string, unknown>
  verifier_passed?: boolean | null
  verifier_summary?: Record<string, unknown> | null
}

export interface ExecutionHistoryItem {
  execution_id: string
  goal: string
  status: string
  step_count: number
  success_count: number
  total_latency_ms: number
  retrieved_skill_count: number
  created_at: string
  experience_unit_id?: string | null
  experience_source_type?: string | null
}

export interface OverviewStats {
  total_skills: number
  by_state: Record<string, number>
  by_type: Record<string, number>
  total_executions: number
  avg_success_rate: number
  graph_stats: Record<string, unknown>
}
