import { create } from 'zustand'
import type { SkillSummary, SystemHealth } from '@/api/types'

export type AppLang = 'en' | 'zh'

export interface WsEvent {
  type: string
  payload: unknown
  timestamp: string
}

interface AppStore {
  // Theme
  darkMode: boolean
  toggleDark: () => void

  // Language
  lang: AppLang
  setLang: (lang: AppLang) => void

  // Skill list cache
  skills: SkillSummary[]
  setSkills: (skills: SkillSummary[]) => void

  // System health
  health: SystemHealth | null
  setHealth: (h: SystemHealth) => void

  // WebSocket event log
  wsEvents: WsEvent[]
  pushWsEvent: (event: WsEvent) => void
  clearWsEvents: () => void

  // Selected Skill
  selectedSkillId: string | null
  setSelectedSkillId: (id: string | null) => void
}

export const useAppStore = create<AppStore>((set) => ({
  darkMode: false,
  toggleDark: () => set(s => ({ darkMode: !s.darkMode })),

  lang: 'en',
  setLang: (lang) => set({ lang }),

  skills: [],
  setSkills: (skills) => set({ skills }),

  health: null,
  setHealth: (health) => set({ health }),

  wsEvents: [],
  pushWsEvent: (event) =>
    set(s => ({
      wsEvents: [
        event,
        ...s.wsEvents.slice(0, 99),
      ],
    })),
  clearWsEvents: () => set({ wsEvents: [] }),

  selectedSkillId: null,
  setSelectedSkillId: (id) => set({ selectedSkillId: id }),
}))
