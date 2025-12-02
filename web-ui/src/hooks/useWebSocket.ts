import { useCallback, useEffect, useRef, useState } from 'react'
import type { WSMessage, Run } from '@/lib/types'

interface WebSocketState {
  connected: boolean
  error: string | null
}

type MessageHandler = (message: WSMessage) => void

export function useWebSocket(onMessage: MessageHandler) {
  const [state, setState] = useState<WebSocketState>({ connected: false, error: null })
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<number | undefined>(undefined)

  const connect = useCallback(() => {
    // Determine WebSocket URL based on current location
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/api/ws`

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setState({ connected: true, error: null })
      console.log('[WS] Connected')
    }

    ws.onclose = (event) => {
      setState({ connected: false, error: null })
      console.log('[WS] Disconnected', event.code)

      // Auto-reconnect after 3 seconds
      reconnectTimeoutRef.current = window.setTimeout(() => {
        console.log('[WS] Reconnecting...')
        connect()
      }, 3000)
    }

    ws.onerror = () => {
      setState(prev => ({ ...prev, error: 'WebSocket error' }))
    }

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as WSMessage
        onMessage(message)
      } catch (err) {
        console.error('[WS] Failed to parse message:', err)
      }
    }
  }, [onMessage])

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
    }
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
  }, [])

  const send = useCallback((data: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data))
    }
  }, [])

  const subscribeToLogs = useCallback((runId: string) => {
    send({ type: 'subscribe_logs', run_id: runId })
  }, [send])

  const unsubscribeFromLogs = useCallback((runId: string) => {
    send({ type: 'unsubscribe_logs', run_id: runId })
  }, [send])

  const ping = useCallback(() => {
    send({ type: 'ping' })
  }, [send])

  useEffect(() => {
    connect()
    return () => disconnect()
  }, [connect, disconnect])

  return {
    ...state,
    subscribeToLogs,
    unsubscribeFromLogs,
    ping,
  }
}

/** Hook for managing runs state with WebSocket updates */
export function useRunsWithWebSocket() {
  const [runs, setRuns] = useState<Run[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const handleMessage = useCallback((message: WSMessage) => {
    if (message.type === 'run_created') {
      const runMessage = message as { type: 'run_created'; run: Run }
      setRuns(prev => [runMessage.run, ...prev])
    } else if (message.type === 'run_updated') {
      const runMessage = message as { type: 'run_updated'; run: Run }
      setRuns(prev => prev.map(r => r.id === runMessage.run.id ? runMessage.run : r))
    }
  }, [])

  const { connected } = useWebSocket(handleMessage)

  // Initial fetch
  useEffect(() => {
    async function load() {
      try {
        const response = await fetch('/api/runs?limit=100')
        if (!response.ok) throw new Error('Failed to fetch runs')
        const data = await response.json()
        setRuns(data)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  return { runs, loading, error, connected, setRuns }
}
