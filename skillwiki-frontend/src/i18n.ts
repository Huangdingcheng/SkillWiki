import type { AppLang } from '@/store/appStore'

export const T = {
  en: {
    appTitle: 'SkillWiki: A Living Knowledge Infrastructure for Agent Skills',
    appTitleShort: 'SkillWiki',
    groupOverview: 'Overview',
    groupSkillMgmt: 'Skill Management',
    groupAgentEvolution: 'Agent & Evolution',
    menuDashboard: 'Dashboard',
    menuEvaluation: 'Evaluation',
    menuWiki: 'Skill Wiki',
    menuGraph: 'Knowledge Graph',
    menuVersions: 'Version Control',
    menuLifecycle: 'Lifecycle',
    menuDemo: 'Self-Evolution Demo',
    menuExecution: 'Agent Execution',
    menuHarness: 'Harness Verification',
    menuEvolution: 'Evolution',
    menuIngest: 'Knowledge Import',
    liveEvents: 'live events',
    switchDark: 'Switch to dark mode',
    switchLight: 'Switch to light mode',
    langToggle: 'ZH',
  },
  zh: {
    appTitle: 'SkillWiki：面向智能体技能的活性知识基础设施',
    appTitleShort: 'SkillWiki',
    groupOverview: '总览',
    groupSkillMgmt: '技能管理',
    groupAgentEvolution: '智能体与演化',
    menuDashboard: '仪表盘',
    menuEvaluation: '评测',
    menuWiki: '技能库',
    menuGraph: '知识图谱',
    menuVersions: '版本控制',
    menuLifecycle: '生命周期',
    menuDemo: '自进化演示',
    menuExecution: '智能体执行',
    menuHarness: '验证框架',
    menuEvolution: '演化中心',
    menuIngest: '知识导入',
    liveEvents: '实时事件',
    switchDark: '切换暗色模式',
    switchLight: '切换亮色模式',
    langToggle: 'English',
  },
} as const

export function useT(lang: AppLang) {
  return T[lang]
}
