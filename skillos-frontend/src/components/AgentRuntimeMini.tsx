import { useEffect, useMemo, useState } from 'react'
import { Button, Empty, Tag, Timeline, Tooltip } from 'antd'
import { ClearOutlined, WifiOutlined } from '@ant-design/icons'
import { motion } from 'framer-motion'
import { executionApi } from '@/api/client'
import { useAppStore } from '@/store/appStore'

const PHASE_LABELS: Record<string, string> = {
  understand_task: 'Understanding',
  read_graph_context: 'Graph Context',
  select_skills: 'Selecting Skills',
  execute_plan: 'Executing',
  finish_execution: 'Finished',
  idle: 'Idle',
}

export default function AgentRuntimeMini({ collapsed = false }: { collapsed?: boolean }) {
  const [polledEvents, setPolledEvents] = useState<{ time: string; event: string; data: unknown }[]>([])
  const { wsEvents, clearWsEvents } = useAppStore()

  useEffect(() => {
    let cancelled = false
    const refreshActivity = () => {
      executionApi.activity()
        .then(events => {
          if (!cancelled) setPolledEvents(events)
        })
        .catch(() => {
          if (!cancelled) setPolledEvents([])
        })
    }
    refreshActivity()
    const timer = setInterval(refreshActivity, 1000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  const allEvents = useMemo(
    () => [...wsEvents, ...polledEvents].filter(e => e.event !== 'pong' && e.event !== 'connected'),
    [wsEvents, polledEvents],
  )
  const agentEvents = allEvents.filter(e => e.event === 'agent_activity')
  const currentAgentData = (agentEvents[0]?.data || {}) as Record<string, unknown>
  const phase = String(currentAgentData.phase || 'idle')
  const message = String(currentAgentData.message || 'Waiting for task')
  const selected = Array.isArray(currentAgentData.selected)
    ? currentAgentData.selected as string[]
    : Array.isArray(currentAgentData.steps)
      ? currentAgentData.steps as string[]
      : []
  const nodes = Array.isArray(currentAgentData.nodes) ? currentAgentData.nodes as string[] : []
  const active = phase !== 'idle' && phase !== 'finish_execution'

  if (collapsed) {
    return (
      <Tooltip title={`${PHASE_LABELS[phase] || phase}: ${message}`}>
        <div style={{ display: 'flex', justifyContent: 'center', padding: '12px 0' }}>
          <motion.div
            animate={{ rotate: active ? 360 : 0 }}
            transition={{ duration: 5, repeat: active ? Infinity : 0, ease: 'linear' }}
            style={{
              width: 34,
              height: 34,
              borderRadius: '50%',
              background: active
                ? 'conic-gradient(from 120deg, #1677ff, #52c41a, #faad14, #1677ff)'
                : 'linear-gradient(135deg, #e6f4ff, #f6ffed)',
              padding: 3,
            }}
          >
            <div style={{
              width: '100%',
              height: '100%',
              borderRadius: '50%',
              background: '#fff',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 800,
              fontSize: 11,
              color: '#1677ff',
            }}>
              AI
            </div>
          </motion.div>
        </div>
      </Tooltip>
    )
  }

  return (
    <div
      style={{
        margin: '12px 10px',
        padding: 12,
        borderRadius: 16,
        maxWidth: 180,
        overflow: 'hidden',
        background: 'linear-gradient(145deg, #f8fbff 0%, #effaf4 58%, #fff9e8 100%)',
        border: '1px solid rgba(22,119,255,0.12)',
        boxShadow: '0 10px 24px rgba(22,119,255,0.10)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ position: 'relative', width: 46, height: 46, flex: '0 0 auto' }}>
          {[0, 1].map(i => (
            <motion.div
              key={i}
              animate={{ scale: active ? [1, 1.16, 1] : 1, opacity: active ? [0.28, 0.08, 0.28] : 0.12 }}
              transition={{ duration: 2.2, repeat: active ? Infinity : 0, delay: i * 0.28 }}
              style={{
                position: 'absolute',
                inset: 4 + i * 6,
                borderRadius: '50%',
                border: '1px solid rgba(22,119,255,0.35)',
              }}
            />
          ))}
          <motion.div
            animate={{ rotate: active ? 360 : 0 }}
            transition={{ duration: 6, repeat: active ? Infinity : 0, ease: 'linear' }}
            style={{
              position: 'absolute',
              inset: 10,
              borderRadius: '50%',
              background: active
                ? 'conic-gradient(from 120deg, #1677ff, #52c41a, #faad14, #1677ff)'
                : 'linear-gradient(135deg, #d6eaff, #eaf8df)',
              padding: 3,
            }}
          >
            <div style={{
              width: '100%',
              height: '100%',
              borderRadius: '50%',
              background: '#fff',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 800,
              fontSize: 10,
              color: '#1677ff',
            }}>
              AI
            </div>
          </motion.div>
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <Tag
            color={active ? 'blue' : phase === 'finish_execution' ? 'green' : 'default'}
            style={{
              marginBottom: 4,
              maxWidth: '100%',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            <WifiOutlined /> {PHASE_LABELS[phase] || phase}
          </Tag>
          <div
            style={{
              fontWeight: 700,
              fontSize: 12,
              color: '#1f2937',
              lineHeight: 1.25,
              wordBreak: 'break-word',
              overflowWrap: 'anywhere',
            }}
          >
            {message.length > 64 ? `${message.slice(0, 64)}...` : message}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 10 }}>
        {selected.slice(0, 2).map(item => (
          <Tooltip key={item} title={item}>
            <Tag color="purple" style={{ fontSize: 10, maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis' }}>{item}</Tag>
          </Tooltip>
        ))}
        {nodes.slice(0, 2).map(item => (
          <Tooltip key={item} title={item}>
            <Tag color="cyan" style={{ fontSize: 10, maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis' }}>{item}</Tag>
          </Tooltip>
        ))}
        {selected.length === 0 && nodes.length === 0 && <Tag style={{ fontSize: 10 }}>No active context</Tag>}
      </div>

      <div style={{ marginTop: 10, maxHeight: 128, overflowY: 'auto' }}>
        {allEvents.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span style={{ fontSize: 11 }}>No events</span>} />
        ) : (
          <Timeline
            items={allEvents.slice(0, 5).map(e => ({
              color: e.event.includes('error') || e.event.includes('fail') ? 'red' : e.event.includes('complete') ? 'green' : 'blue',
              children: (
                <div style={{ fontSize: 11 }}>
                  <Tag style={{ fontSize: 10, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis' }}>{e.event}</Tag>
                  <span style={{ color: '#8c8c8c', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis' }}>{e.time}</span>
                </div>
              ),
            }))}
          />
        )}
      </div>

      <Button
        size="small"
        block
        icon={<ClearOutlined />}
        onClick={() => {
          clearWsEvents()
          setPolledEvents([])
        }}
      >
        Clear Runtime
      </Button>
    </div>
  )
}
