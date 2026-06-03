import { useEffect, useRef } from 'react'
import { useAppStore } from '@/store/appStore'
import type { WsEvent } from '@/store/appStore'

type RawWsMessage = {
  type?: unknown
  payload?: unknown
  timestamp?: unknown
  event?: unknown
  data?: unknown
}

function normalizeWsMessage(raw: RawWsMessage): WsEvent | null {
  const type = typeof raw.type === 'string'
    ? raw.type
    : typeof raw.event === 'string'
      ? raw.event
      : ''

  if (!type) return null

  return {
    type,
    payload: 'payload' in raw ? raw.payload : raw.data,
    timestamp: typeof raw.timestamp === 'string' ? raw.timestamp : new Date().toISOString(),
  }
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const pushWsEvent = useAppStore(s => s.pushWsEvent)

  useEffect(() => {
    if (import.meta.env.VITE_SKILLOS_DISABLE_WS === '1') {
      return
    }

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${protocol}://${window.location.host}/ws`)
    wsRef.current = ws

    ws.onmessage = (e) => {
      try {
        const msg = normalizeWsMessage(JSON.parse(e.data))
        if (msg) pushWsEvent(msg)
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
