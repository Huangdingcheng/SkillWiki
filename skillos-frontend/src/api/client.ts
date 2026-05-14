import axios from 'axios'
import type {
  ExecutionResult,
  ExecutionHistoryItem,
  GraphData,
  HealthReport,
  OverviewStats,
  SkillFull,
  SkillSearchResult,
  SkillState,
  SkillSummary,
  SkillType,
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

  newVersion: (id: string, bump: 'major' | 'minor' | 'patch' = 'patch') =>
    http.post<SkillSummary>(`/lifecycle/${id}/new-version`, { bump }).then(r => r.data),

  getDiff: (id: string, compare_to?: string) =>
    http.get<Record<string, unknown>>(`/lifecycle/${id}/diff`, { params: compare_to ? { compare_to } : {} }).then(r => r.data),
}

// ── Graph ─────────────────────────────────────────────────────────────────────

export const graphApi = {
  full: (limit = 200) =>
    http.get<GraphData>('/graph', { params: { limit } }).then(r => r.data),

  subgraph: (skill_id: string, depth = 2) =>
    http.post<GraphData>('/graph/subgraph', { skill_id, depth }).then(r => r.data),

  stats: () =>
    http.get<Record<string, unknown>>('/graph/stats/overview').then(r => r.data),
}

// ── Execution ─────────────────────────────────────────────────────────────────

export const executionApi = {
  executeSkill: (skill_id: string, inputs: Record<string, unknown> = {}) =>
    http.post<ExecutionResult>('/execution/skill', { skill_id, inputs }).then(r => r.data),

  executePlan: (
    goal: string,
    context: Record<string, unknown> = {},
    orchestration_strategy: 'quality_first' | 'efficiency_first' | 'simplicity_first' = 'quality_first',
  ) =>
    http.post<ExecutionResult>('/execution/plan', { goal, context, orchestration_strategy }).then(r => r.data),

  getState: () =>
    http.get<Record<string, unknown>>('/execution/state').then(r => r.data),

  resetState: () =>
    http.delete('/execution/state').then(r => r.data),

  history: () =>
    http.get<ExecutionHistoryItem[]>('/execution/history').then(r => r.data),
}

// ── Evolution ─────────────────────────────────────────────────────────────────

export const evolutionApi = {
  systemHealth: () =>
    http.get<SystemHealth>('/evolution/health').then(r => r.data),

  skillHealth: (id: string) =>
    http.get<HealthReport>(`/evolution/health/${id}`).then(r => r.data),

  repair: (id: string) =>
    http.post(`/evolution/repair/${id}`).then(r => r.data),

  runCycle: () =>
    http.post('/evolution/cycle').then(r => r.data),
}

// ── Ingest ────────────────────────────────────────────────────────────────────

export interface IngestResponse {
  success: boolean
  source_type: string
  unit_count: number
  token_usage: number
  errors: string[]
  units: {
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
  }[]
  created_skills?: {
    skill_id: string
    name: string
    skill_type: string
    state: string
    version: string
  }[]
}

export const ingestApi = {
  parse: (source_type: string, content: string) =>
    http.post<IngestResponse>('/ingest/parse', { source_type, content }).then(r => r.data),

  parseAndCreate: (source_type: string, content: string) =>
    http.post<IngestResponse>('/ingest/parse-and-create', { source_type, content }).then(r => r.data),
}

// ── Stats ─────────────────────────────────────────────────────────────────────

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
    const [skills, health] = await Promise.all([
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
