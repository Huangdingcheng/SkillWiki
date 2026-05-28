import axios from 'axios'
import type {
  CandidateAuditResult,
  CandidateCreateResponse,
  CandidateSkillReviewRequest,
  EvaluationDashboardResponse,
  ExecutionExperienceUnit,
  ExecutionResult,
  ExecutionHistoryItem,
  GraphData,
  GraphViewData,
  GraphViewMode,
  HarnessLoopListResponse,
  HarnessVerifyLoopRequest,
  HarnessVerifyLoopResponse,
  HeterogeneousGraphData,
  HealthReport,
  IngestResponse,
  MaintenanceReviewRequest,
  MaintenanceReviewResponse,
  MaintenanceProposal,
  MaintenanceProposalListResponse,
  NewVersionRequest,
  OverviewStats,
  SkillReleaseRecord,
  SkillRollbackRecord,
  SkillCreateRequest,
  SkillFull,
  SkillGraphProjectionData,
  SkillSearchResult,
  SkillState,
  SkillSummary,
  SkillType,
  SkillUpdateRequest,
  SnapshotCommitRequest,
  SnapshotCommitResponse,
  SnapshotDiffRequest,
  SnapshotDiffResponse,
  SnapshotHistoryResponse,
  SystemHealth,
} from './types'

const http = axios.create({ baseURL: '/api/v1' })

// ── Skills ────────────────────────────────────────────────────────────────────

export const skillsApi = {
  list: (params?: { state?: SkillState; skill_type?: SkillType; limit?: number; offset?: number }) =>
    http.get<SkillSummary[]>('/skills', { params }).then(r => r.data),

  get: (id: string) =>
    http.get<SkillSummary>(`/skills/${id}`).then(r => r.data),

  getFull: (id: string) =>
    http.get<SkillFull>(`/skills/${id}/full`).then(r => r.data),

  create: (request: SkillCreateRequest) =>
    http.post<SkillSummary>('/skills', request).then(r => r.data),

  update: (id: string, request: SkillUpdateRequest) =>
    http.patch<SkillSummary>(`/skills/${id}`, request).then(r => r.data),

  search: (query: string, limit = 20) =>
    http.post<SkillSearchResult[]>('/skills/search', { query, limit }).then(r => r.data),

  versions: (id: string) =>
    http.get<SkillSummary[]>(`/skills/${id}/versions`).then(r => r.data),

  delete: (id: string) =>
    http.delete(`/skills/${id}`).then(r => r.data),
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

export const lifecycleApi = {
  release: (id: string) =>
    http.post<SkillSummary>(`/lifecycle/${id}/release`).then(r => r.data),

  deprecate: (id: string, reason: string) =>
    http.post<SkillSummary>(`/lifecycle/${id}/deprecate`, { reason }).then(r => r.data),

  transition: (id: string, new_state: SkillState, reason = '') =>
    http.post<SkillSummary>(`/lifecycle/${id}/transition`, { new_state, reason }).then(r => r.data),

  review: (id: string) =>
    http.post(`/lifecycle/${id}/review`).then(r => r.data),

  reviewAndRelease: (id: string) =>
    http.post<SkillSummary>(`/lifecycle/${id}/review-and-release`).then(r => r.data),

  newVersion: (
    id: string,
    requestOrBump: NewVersionRequest | 'major' | 'minor' | 'patch' = 'patch',
  ) => {
    const request = typeof requestOrBump === 'string' ? { bump: requestOrBump } : requestOrBump
    return http.post<SkillSummary>(`/lifecycle/${id}/new-version`, request).then(r => r.data)
  },

  getDiff: (id: string, compare_to?: string) =>
    http.get<Record<string, unknown>>(`/lifecycle/${id}/diff`, { params: compare_to ? { compare_to } : {} }).then(r => r.data),

  createSnapshot: (id: string, request: SnapshotCommitRequest = {}) =>
    http.post<SnapshotCommitResponse>(`/lifecycle/${id}/snapshot`, request).then(r => r.data),

  snapshotHistory: (id: string, max_count = 20) =>
    http.get<SnapshotHistoryResponse>(`/lifecycle/${id}/snapshot/history`, { params: { max_count } }).then(r => r.data),

  snapshotDiff: (id: string, request: SnapshotDiffRequest) =>
    http.get<SnapshotDiffResponse>(`/lifecycle/${id}/snapshot/diff`, { params: request }).then(r => r.data),

  releaseTag: (id: string, ref = 'HEAD') =>
    http.post<SkillReleaseRecord>(`/lifecycle/${id}/release-tag`, { ref }).then(r => r.data),

  restoreSnapshot: (id: string, source_ref: string) =>
    http.post<SkillRollbackRecord>(`/lifecycle/${id}/rollback`, { source_ref }).then(r => r.data),

  proposeMaintenanceChange: (id: string, request: MaintenanceReviewRequest) =>
    http.post<MaintenanceReviewResponse>(`/lifecycle/${id}/propose-maintenance-change`, request).then(r => r.data),
}

// ── Graph ─────────────────────────────────────────────────────────────────────

export const graphApi = {
  full: (limit = 200) =>
    http.get<GraphData>('/graph', { params: { limit } }).then(r => r.data),

  subgraph: (skill_id: string, depth = 2) =>
    http.post<GraphData>('/graph/subgraph', { skill_id, depth }).then(r => r.data),

  heterogeneous: () =>
    http.get<HeterogeneousGraphData>('/graph/heterogeneous').then(r => r.data),

  skillOnlyProjection: () =>
    http.get<SkillGraphProjectionData>('/graph/projection/skill-only').then(r => r.data),

  stats: () =>
    http.get<Record<string, unknown>>('/graph/stats/overview').then(r => r.data),

  view: (view: GraphViewMode, limit = 300) =>
    http.get<GraphViewData>('/graph/view', { params: { view, limit } }).then(r => r.data),
}

// ── Execution ─────────────────────────────────────────────────────────────────

export const executionApi = {
  executeSkill: (skill_id: string, inputs: Record<string, unknown> = {}) =>
    http.post<ExecutionResult>('/execution/skill', { skill_id, inputs }).then(r => r.data),

  executePlan: (goal: string, context: Record<string, unknown> = {}) =>
    http.post<ExecutionResult>('/execution/plan', { goal, context }).then(r => r.data),

  getState: () =>
    http.get<Record<string, unknown>>('/execution/state').then(r => r.data),

  resetState: () =>
    http.delete('/execution/state').then(r => r.data),

  history: () =>
    http.get<ExecutionHistoryItem[]>('/execution/history').then(r => r.data),

  experience: (execution_id: string) =>
    http.get<ExecutionExperienceUnit>(`/execution/history/${execution_id}/experience`).then(r => r.data),
}

// ── Evolution ─────────────────────────────────────────────────────────────────

export const harnessApi = {
  runVerifyLoop: (skill_id: string, request: HarnessVerifyLoopRequest = {}) =>
    http.post<HarnessVerifyLoopResponse>(`/harness/${skill_id}/verify-loop`, request).then(r => r.data),

  list: (limit = 20) =>
    http.get<HarnessLoopListResponse>('/harness', { params: { limit } }).then(r => r.data),

  get: (loop_id: string) =>
    http.get<Record<string, unknown>>(`/harness/${loop_id}`).then(r => r.data),
}

export const evolutionApi = {
  systemHealth: () =>
    http.get<SystemHealth>('/evolution/health').then(r => r.data),

  skillHealth: (id: string) =>
    http.get<HealthReport>(`/evolution/health/${id}`).then(r => r.data),

  repair: (id: string) =>
    http.post(`/evolution/repair/${id}`).then(r => r.data),

  runCycle: () =>
    http.post('/evolution/cycle').then(r => r.data),

  proposals: (status?: MaintenanceProposal['status']) =>
    http.get<MaintenanceProposalListResponse>('/evolution/proposals', { params: status ? { status } : {} }).then(r => r.data),

  acceptProposal: (id: string) =>
    http.post<MaintenanceProposal>(`/evolution/proposals/${id}/accept`).then(r => r.data),

  rejectProposal: (id: string) =>
    http.post<MaintenanceProposal>(`/evolution/proposals/${id}/reject`).then(r => r.data),
}

// ── Ingest ────────────────────────────────────────────────────────────────────

export const ingestApi = {
  parse: (source_type: string, content: string) =>
    http.post<IngestResponse>('/ingest/parse', { source_type, content }).then(r => r.data),

  parseAndCreate: (source_type: string, content: string) =>
    http.post<IngestResponse>('/ingest/parse-and-create', { source_type, content }).then(r => r.data),

  auditCandidate: (request: CandidateSkillReviewRequest) =>
    http.post<CandidateAuditResult>('/ingest/audit-candidate', request).then(r => r.data),

  createCandidate: (request: CandidateSkillReviewRequest) =>
    http.post<CandidateCreateResponse>('/ingest/create-candidate', request).then(r => r.data),
}

// ── Stats ─────────────────────────────────────────────────────────────────────

export const evaluationApi = {
  dashboard: () =>
    http.get<EvaluationDashboardResponse>('/evaluation/dashboard').then(r => r.data),
}

export interface EvolutionStats {
  total_skills: number
  auto_generated: number
  manual: number
  avg_reuse_rate: number
  avg_success_rate: number
  version_improved_count: number
  skills_by_category: Record<string, number>
  recent_activity: {
    skill_id: string
    name: string
    event: string
    state: string
    time: string
  }[]
}

export const statsApi = {
  overview: async (): Promise<OverviewStats> => {
    await Promise.all([
      skillsApi.list({ limit: 1 }),
      evolutionApi.systemHealth().catch(() => null),
    ])
    const allSkills = await skillsApi.list({ limit: 200 })
    const byState: Record<string, number> = {}
    const byType: Record<string, number> = {}
    let totalExec = 0
    let successRateSum = 0
    let ratedCount = 0
    for (const s of allSkills) {
      byState[s.state] = (byState[s.state] || 0) + 1
      byType[s.skill_type] = (byType[s.skill_type] || 0) + 1
      totalExec += s.metrics.total_executions
      if (s.metrics.total_executions >= 5) {
        successRateSum += s.metrics.success_rate
        ratedCount++
      }
    }
    return {
      total_skills: allSkills.length,
      by_state: byState,
      by_type: byType,
      total_executions: totalExec,
      avg_success_rate: ratedCount > 0 ? successRateSum / ratedCount : 1,
      graph_stats: {},
    }
  },

  evolutionStats: () =>
    http.get<EvolutionStats>('/skills/evolution-stats').then(r => r.data),
}
