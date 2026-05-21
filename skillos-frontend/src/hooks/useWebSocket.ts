import { useEffect, useRef } from 'react'
import { useAppStore } from '@/store/appStore'

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const pushWsEvent = useAppStore(s => s.pushWsEvent)

  useEffect(() => {
    let closedByEffect = false
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const connect = () => {
      const ws = new WebSocket(`${protocol}://${window.location.host}/ws`)
      wsRef.current = ws

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          pushWsEvent(msg.event, msg.data)
        } catch {
          // ignore
        }
      }

      ws.onclose = () => {
        if (!closedByEffect) {
          reconnectTimer = setTimeout(connect, 1500)
        }
      }

      ws.onerror = () => {
        ws.close()
      }
    }
    connect()

    const ping = setInterval(() => {
      const ws = wsRef.current
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, 30_000)

    return () => {
      closedByEffect = true
      clearInterval(ping)
      if (reconnectTimer) clearTimeout(reconnectTimer)
      wsRef.current?.close()
    }
  }, [pushWsEvent])

  return wsRef
}
