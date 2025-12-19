import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  AgentMessageData,
  WSMessage,
  AgentMessageWSMessage,
  ProgressMessage,
  TokenUpdateMessage,
} from '@/lib/types'

/** Progress state for a run */
export interface RunProgress {
  turns: number
  tool_calls: number
  elapsed_seconds: number
}

/** Token/cost state for a run */
export interface RunTokens {
  input_tokens: number
  output_tokens: number
  estimated_cost_usd: number
}

interface WebSocketState {
  connected: boolean
  subscribed: boolean
}

interface UseRunLogStreamOptions {
  /** Maximum number of messages to keep in memory (default: 1000) */
  maxMessages?: number
  /** Whether to auto-scroll to bottom on new messages */
  autoScroll?: boolean
}

/**
 * Hook for streaming real-time log updates for a specific run.
 *
 * Subscribes to WebSocket updates for the given run_id and provides:
 * - Streaming agent messages (text, tool_use, errors)
 * - Progress updates (turns, tool calls, elapsed time)
 * - Token/cost updates
 *
 * @param runId - The run ID to stream logs for
 * @param options - Configuration options
 */
export function useRunLogStream(
  runId: string | null,
  options: UseRunLogStreamOptions = {}
) {
  const { maxMessages = 1000 } = options

  const [state, setState] = useState<WebSocketState>({
    connected: false,
    subscribed: false,
  })
  const [messages, setMessages] = useState<AgentMessageData[]>([])
  const [progress, setProgress] = useState<RunProgress | null>(null)
  const [tokens, setTokens] = useState<RunTokens | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<number | undefined>(undefined)
  const currentRunIdRef = useRef<string | null>(null)

  // Clear messages when run changes
  useEffect(() => {
    if (runId !== currentRunIdRef.current) {
      setMessages([])
      setProgress(null)
      setTokens(null)
      currentRunIdRef.current = runId
    }
  }, [runId])

  const handleMessage = useCallback(
    (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as WSMessage

        // Only process messages for our run
        if ('run_id' in data && data.run_id !== runId) {
          return
        }

        switch (data.type) {
          case 'agent_message': {
            const msg = data as AgentMessageWSMessage
            setMessages((prev) => {
              const newMessages = [...prev, msg.message]
              // Cap at maxMessages to prevent memory growth
              if (newMessages.length > maxMessages) {
                return newMessages.slice(-maxMessages)
              }
              return newMessages
            })
            break
          }
          case 'progress': {
            const prog = data as ProgressMessage
            setProgress({
              turns: prog.turns,
              tool_calls: prog.tool_calls,
              elapsed_seconds: prog.elapsed_seconds,
            })
            break
          }
          case 'token_update': {
            const tok = data as TokenUpdateMessage
            setTokens({
              input_tokens: tok.input_tokens,
              output_tokens: tok.output_tokens,
              estimated_cost_usd: tok.estimated_cost_usd,
            })
            break
          }
          case 'subscribed': {
            setState((prev) => ({ ...prev, subscribed: true }))
            break
          }
          case 'unsubscribed': {
            setState((prev) => ({ ...prev, subscribed: false }))
            break
          }
        }
      } catch (err) {
        console.error('[RunLogStream] Failed to parse message:', err)
      }
    },
    [runId, maxMessages]
  )

  const connect = useCallback(() => {
    if (!runId) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/api/ws`

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setState({ connected: true, subscribed: false })
      console.log('[RunLogStream] Connected, subscribing to', runId.slice(0, 8))
      // Subscribe to this run's logs
      ws.send(JSON.stringify({ type: 'subscribe_logs', run_id: runId }))
    }

    ws.onclose = (event) => {
      setState({ connected: false, subscribed: false })
      console.log('[RunLogStream] Disconnected', event.code)

      // Auto-reconnect if we still have a run to watch
      if (currentRunIdRef.current) {
        reconnectTimeoutRef.current = window.setTimeout(() => {
          console.log('[RunLogStream] Reconnecting...')
          connect()
        }, 3000)
      }
    }

    ws.onerror = () => {
      console.error('[RunLogStream] WebSocket error')
    }

    ws.onmessage = handleMessage
  }, [runId, handleMessage])

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = undefined
    }
    if (wsRef.current) {
      // Unsubscribe before closing
      if (wsRef.current.readyState === WebSocket.OPEN && currentRunIdRef.current) {
        wsRef.current.send(
          JSON.stringify({ type: 'unsubscribe_logs', run_id: currentRunIdRef.current })
        )
      }
      wsRef.current.close()
      wsRef.current = null
    }
    setState({ connected: false, subscribed: false })
  }, [])

  // Connect when runId is set, disconnect when cleared or unmounted
  useEffect(() => {
    if (runId) {
      connect()
    } else {
      disconnect()
    }

    return () => {
      disconnect()
    }
  }, [runId, connect, disconnect])

  // Clear all state
  const clear = useCallback(() => {
    setMessages([])
    setProgress(null)
    setTokens(null)
  }, [])

  return {
    ...state,
    messages,
    progress,
    tokens,
    clear,
  }
}
