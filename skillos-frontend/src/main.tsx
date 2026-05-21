import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { ConfigProvider, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { useAppStore } from '@/store/appStore'
import AppLayout from '@/components/AppLayout'
import Dashboard from '@/pages/Dashboard'
import SkillWiki from '@/pages/SkillWiki'
import AgentExecution from '@/pages/AgentExecution'
import KnowledgeImport from '@/pages/KnowledgeImport'
import SkillManage from '@/pages/SkillManage'
import Evaluation from '@/pages/Evaluation'
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
            <Route path="/ingest" element={<KnowledgeImport />} />
            <Route path="/wiki" element={<SkillWiki />} />
            <Route path="/manage" element={<SkillManage />} />
            <Route path="/evaluation" element={<Evaluation />} />
            <Route path="/execution" element={<AgentExecution />} />
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
