import { useState } from 'react'
import { Layout, Menu, Switch, Badge, Tooltip, Avatar } from 'antd'
import {
  DashboardOutlined,
  BookOutlined,
  PlayCircleOutlined,
  ExperimentOutlined,
  CloudUploadOutlined,
  BulbOutlined,
  BulbFilled,
  WifiOutlined,
  ControlOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import { motion } from 'framer-motion'
import { useAppStore } from '@/store/appStore'
import { useWebSocket } from '@/hooks/useWebSocket'
import AgentRuntimeMini from './AgentRuntimeMini'

const { Header, Sider, Content } = Layout

const menuItems = [
  { key: '/', icon: <DashboardOutlined />, label: 'Dashboard' },
  { key: '/ingest', icon: <CloudUploadOutlined />, label: 'Knowledge Import' },
  { key: '/wiki', icon: <BookOutlined />, label: 'Skill Wiki' },
  { key: '/manage', icon: <ControlOutlined />, label: 'Skill Manage' },
  { key: '/evaluation', icon: <ExperimentOutlined />, label: 'Evaluation' },
  { key: '/execution', icon: <PlayCircleOutlined />, label: 'Execution' },
]

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const { darkMode, toggleDark, wsEvents } = useAppStore()
  useWebSocket()

  const unreadEvents = wsEvents.filter(e => e.event !== 'pong' && e.event !== 'connected').length

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        theme={darkMode ? 'dark' : 'light'}
        style={{
          boxShadow: '2px 0 8px rgba(0,0,0,0.08)',
          zIndex: 10,
        }}
      >
        <motion.div
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '0 16px',
            borderBottom: '1px solid rgba(0,0,0,0.06)',
          }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
        >
          {!collapsed ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Avatar
                style={{ background: 'linear-gradient(135deg, #1677ff, #722ed1)', flexShrink: 0 }}
                size={32}
              >
                S
              </Avatar>
              <span style={{ fontWeight: 800, fontSize: 16, color: darkMode ? '#fff' : '#1677ff', whiteSpace: 'nowrap' }}>
                SkillOS
              </span>
            </div>
          ) : (
            <Avatar style={{ background: 'linear-gradient(135deg, #1677ff, #722ed1)' }} size={32}>S</Avatar>
          )}
        </motion.div>

        <Menu
          theme={darkMode ? 'dark' : 'light'}
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderRight: 0, marginTop: 8 }}
        />
        <AgentRuntimeMini collapsed={collapsed} />
      </Sider>

      <Layout>
        <Header
          style={{
            background: darkMode ? '#141414' : '#fff',
            padding: '0 24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
            zIndex: 9,
          }}
        >
          <div style={{ fontWeight: 600, color: darkMode ? '#fff' : '#333' }}>
            Skill-Centric Operating System for Self-Evolving Agents
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <Tooltip title={`${unreadEvents} 个实时事件`}>
              <Badge count={Math.min(unreadEvents, 99)} size="small">
                <WifiOutlined style={{ fontSize: 18, color: '#52c41a', cursor: 'pointer' }} />
              </Badge>
            </Tooltip>
            <Tooltip title={darkMode ? '切换亮色' : '切换暗色'}>
              <Switch
                checked={darkMode}
                onChange={toggleDark}
                checkedChildren={<BulbFilled />}
                unCheckedChildren={<BulbOutlined />}
              />
            </Tooltip>
          </div>
        </Header>

        <Content
          style={{
            background: darkMode ? '#1f1f1f' : '#f5f7fa',
            overflow: 'auto',
            minHeight: 'calc(100vh - 64px)',
          }}
        >
          <motion.div
            key={location.pathname}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
          >
            {children}
          </motion.div>
        </Content>
      </Layout>
    </Layout>
  )
}
