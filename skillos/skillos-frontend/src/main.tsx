import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { ConfigProvider, Spin, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { useAppStore } from '@/store/appStore'
import AppLayout from '@/components/AppLayout'
import './index.css'

const Dashboard = lazy(() => import('@/pages/Dashboard'))
const SkillWiki = lazy(() => import('@/pages/SkillWiki'))
const SkillGraph = lazy(() => import('@/pages/SkillGraph'))
const AgentExecution = lazy(() => import('@/pages/AgentExecution'))
const Evolution = lazy(() => import('@/pages/Evolution'))
const LifecycleDemo = lazy(() => import('@/pages/LifecycleDemo'))
const KnowledgeImport = lazy(() => import('@/pages/KnowledgeImport'))
const VersionControl = lazy(() => import('@/pages/VersionControl'))
const SelfEvolutionDemo = lazy(() => import('@/pages/SelfEvolutionDemo'))

function PageFallback() {
  return (
    <div style={{ minHeight: 360, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <Spin size="large" description="加载页面..." />
    </div>
  )
}

function App() {
  const darkMode = useAppStore(s => s.darkMode)

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: darkMode ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: {
          colorPrimary: '#1677ff',
          borderRadius: 8,
          fontFamily: "'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif",
        },
      }}
    >
      <BrowserRouter>
        <AppLayout>
          <Suspense fallback={<PageFallback />}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/wiki" element={<SkillWiki />} />
              <Route path="/graph" element={<SkillGraph />} />
              <Route path="/execution" element={<AgentExecution />} />
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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
