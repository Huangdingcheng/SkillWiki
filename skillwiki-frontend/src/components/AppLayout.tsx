import { useState } from 'react'
import { Layout, Menu, Switch, Badge, Tooltip, Avatar, Tag, Grid, Button } from 'antd'
import {
  DashboardOutlined,
  BookOutlined,
  ApartmentOutlined,
  PlayCircleOutlined,
  SyncOutlined,
  ExperimentOutlined,
  CloudUploadOutlined,
  BranchesOutlined,
  BulbOutlined,
  BulbFilled,
  WifiOutlined,
  RocketOutlined,
  BarChartOutlined,
  SafetyCertificateOutlined,
  TranslationOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import { motion } from 'framer-motion'
import { useAppStore } from '@/store/appStore'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useT } from '@/i18n'

const { Header, Sider, Content } = Layout
const { useBreakpoint } = Grid

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)
  const screens = useBreakpoint()
  const navigate = useNavigate()
  const location = useLocation()
  const { darkMode, toggleDark, wsEvents, lang, setLang } = useAppStore()
  const t = useT(lang)
  useWebSocket()

  const isNarrow = screens.xs && !screens.md
  const siderCollapsed = isNarrow ? true : collapsed
  const unreadEvents = wsEvents.filter(e => e.type !== 'pong' && e.type !== 'connected').length

  const menuItems = [
    {
      type: 'group' as const,
      label: t.groupOverview,
      children: [
        { key: '/', icon: <DashboardOutlined />, label: t.menuDashboard },
        { key: '/evaluation', icon: <BarChartOutlined />, label: t.menuEvaluation },
      ],
    },
    {
      type: 'group' as const,
      label: t.groupSkillMgmt,
      children: [
        { key: '/ingest', icon: <CloudUploadOutlined />, label: t.menuIngest },
        { key: '/wiki', icon: <BookOutlined />, label: t.menuWiki },
        { key: '/graph', icon: <ApartmentOutlined />, label: t.menuGraph },
        { key: '/lifecycle', icon: <ExperimentOutlined />, label: t.menuLifecycle },
        { key: '/versions', icon: <BranchesOutlined />, label: t.menuVersions },
      ],
    },
    {
      type: 'group' as const,
      label: t.groupAgentEvolution,
      children: [
        { key: '/harness', icon: <SafetyCertificateOutlined />, label: t.menuHarness },
        { key: '/execution', icon: <PlayCircleOutlined />, label: t.menuExecution },
        { key: '/evolution', icon: <SyncOutlined />, label: t.menuEvolution },
        {
          key: '/demo',
          icon: <RocketOutlined />,
          label: (
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {t.menuDemo}
              <Tag color="red" style={{ fontSize: 10, padding: '0 4px', lineHeight: '16px', marginLeft: 2 }}>DEMO</Tag>
            </span>
          ),
        },
      ],
    },
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        collapsible
        collapsed={siderCollapsed}
        onCollapse={setCollapsed}
        collapsedWidth={isNarrow ? 64 : 80}
        width={isNarrow ? 64 : 200}
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
          {!siderCollapsed ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Avatar
                style={{ background: 'linear-gradient(135deg, #1677ff, #722ed1)', flexShrink: 0 }}
                size={32}
              >
                S
              </Avatar>
              <span style={{ fontWeight: 800, fontSize: 16, color: darkMode ? '#fff' : '#1677ff', whiteSpace: 'nowrap' }}>
                SkillWiki
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
          inlineCollapsed={siderCollapsed}
          style={{ borderRight: 0, marginTop: 8 }}
        />
      </Sider>

      <Layout>
        <Header
          style={{
            background: darkMode ? '#141414' : '#fff',
            padding: isNarrow ? '0 12px' : '0 24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
            boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
            zIndex: 9,
          }}
        >
          <div
            style={{
              fontWeight: 600,
              color: darkMode ? '#fff' : '#333',
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {isNarrow ? t.appTitleShort : t.appTitle}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Tooltip title={`${unreadEvents} ${t.liveEvents}`}>
              <Badge count={Math.min(unreadEvents, 99)} size="small">
                <WifiOutlined style={{ fontSize: 18, color: '#52c41a', cursor: 'pointer' }} />
              </Badge>
            </Tooltip>
            <Tooltip title={darkMode ? t.switchLight : t.switchDark}>
              <Switch
                checked={darkMode}
                onChange={toggleDark}
                checkedChildren={<BulbFilled />}
                unCheckedChildren={<BulbOutlined />}
              />
            </Tooltip>
            <Tooltip title={t.langToggle}>
              <Button
                size="small"
                icon={<TranslationOutlined />}
                onClick={() => setLang(lang === 'en' ? 'zh' : 'en')}
                style={{ fontWeight: 600, minWidth: 56 }}
              >
                {t.langToggle}
              </Button>
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
