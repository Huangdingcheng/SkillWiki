import { useEffect, useRef } from 'react'
import { useAppStore } from '@/store/appStore'

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const pushWsEvent = useAppStore(s => s.pushWsEvent)

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
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

    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, 30_000)

    return () => {
      clearInterval(ping)
      ws.close()
    }
  }, [pushWsEvent])

  return wsRef
}
