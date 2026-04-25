import { create } from 'zustand'
import type { SkillSummary, SystemHealth } from '@/api/types'

interface AppStore {
  // 主题
  darkMode: boolean
  toggleDark: () => void

  // Skill 列表缓存
  skills: SkillSummary[]
  setSkills: (skills: SkillSummary[]) => void

  // 系统健康
  health: SystemHealth | null
  setHealth: (h: SystemHealth) => void

  // WebSocket 事件日志
  wsEvents: { time: string; event: string; data: unknown }[]
  pushWsEvent: (event: string, data: unknown) => void
  clearWsEvents: () => void

  // 选中的 Skill
  selectedSkillId: string | null
  setSelectedSkillId: (id: string | null) => void
}

export const useAppStore = create<AppStore>((set) => ({
  darkMode: false,
  toggleDark: () => set(s => ({ darkMode: !s.darkMode })),

  skills: [],
  setSkills: (skills) => set({ skills }),

  health: null,
  setHealth: (health) => set({ health }),

  wsEvents: [],
  pushWsEvent: (event, data) =>
    set(s => ({
      wsEvents: [
        { time: new Date().toLocaleTimeString(), event, data },
        ...s.wsEvents.slice(0, 99),
      ],
    })),
  clearWsEvents: () => set({ wsEvents: [] }),

  selectedSkillId: null,
  setSelectedSkillId: (id) => set({ selectedSkillId: id }),
}))
