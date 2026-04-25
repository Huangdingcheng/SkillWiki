import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { ConfigProvider, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { useAppStore } from '@/store/appStore'
import AppLayout from '@/components/AppLayout'
import Dashboard from '@/pages/Dashboard'
import SkillWiki from '@/pages/SkillWiki'
import SkillGraph from '@/pages/SkillGraph'
import AgentExecution from '@/pages/AgentExecution'
import Evolution from '@/pages/Evolution'
import LifecycleDemo from '@/pages/LifecycleDemo'
import KnowledgeImport from '@/pages/KnowledgeImport'
import VersionControl from '@/pages/VersionControl'
import SelfEvolutionDemo from '@/pages/SelfEvolutionDemo'
import './index.css'

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
