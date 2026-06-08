import { lazy, Suspense } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { ConfigProvider, Spin, theme } from 'antd'
import enUS from 'antd/locale/en_US'
import zhCN from 'antd/locale/zh_CN'
import { useAppStore } from '@/store/appStore'
import AppLayout from '@/components/AppLayout'

const Dashboard = lazy(() => import('@/pages/Dashboard'))
const EvaluationDashboard = lazy(() => import('@/pages/EvaluationDashboard'))
const SkillWiki = lazy(() => import('@/pages/SkillWiki'))
const SkillGraph = lazy(() => import('@/pages/SkillGraph'))
const AgentExecution = lazy(() => import('@/pages/AgentExecution'))
const HarnessVerification = lazy(() => import('@/pages/HarnessVerification'))
const Evolution = lazy(() => import('@/pages/Evolution'))
const LifecycleDemo = lazy(() => import('@/pages/LifecycleDemo'))
const KnowledgeImport = lazy(() => import('@/pages/KnowledgeImport'))
const VersionControl = lazy(() => import('@/pages/VersionControl'))
const SelfEvolutionDemo = lazy(() => import('@/pages/SelfEvolutionDemo'))

function PageFallback() {
  return (
    <div style={{ minHeight: 360, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <Spin size="large" description="Loading page..." />
    </div>
  )
}

export default function App() {
  const darkMode = useAppStore(s => s.darkMode)
  const lang = useAppStore(s => s.lang)

  return (
    <ConfigProvider
      locale={lang === 'zh' ? zhCN : enUS}
      theme={{
        algorithm: darkMode ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: {
          colorPrimary: '#1677ff',
          borderRadius: 8,
          fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        },
      }}
    >
      <BrowserRouter>
        <AppLayout>
          <Suspense fallback={<PageFallback />}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/evaluation" element={<EvaluationDashboard />} />
              <Route path="/wiki" element={<SkillWiki />} />
              <Route path="/graph" element={<SkillGraph />} />
              <Route path="/execution" element={<AgentExecution />} />
              <Route path="/harness" element={<HarnessVerification />} />
              <Route path="/evolution" element={<Evolution />} />
              <Route path="/lifecycle" element={<LifecycleDemo />} />
              <Route path="/ingest" element={<KnowledgeImport />} />
              <Route path="/versions" element={<VersionControl />} />
              <Route path="/demo" element={<SelfEvolutionDemo />} />
            </Routes>
          </Suspense>
        </AppLayout>
      </BrowserRouter>
    </ConfigProvider>
  )
}
