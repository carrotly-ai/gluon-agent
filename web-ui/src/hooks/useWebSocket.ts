import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  CircuitState,
  NotificationCreatedMessage,
  PendingQuestionsMessage,
  QuestionAnsweredMessage,
  QuestionsExpiredMessage,
  Run,
  WSMessage,
} from '@/lib/types'

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
      setState((prev) => ({ ...prev, error: 'WebSocket error' }))
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

  const subscribeToLogs = useCallback(
    (runId: string) => {
      send({ type: 'subscribe_logs', run_id: runId })
    },
    [send]
  )

  const unsubscribeFromLogs = useCallback(
    (runId: string) => {
      send({ type: 'unsubscribe_logs', run_id: runId })
    },
    [send]
  )

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

/** Callbacks for notification center integration */
export interface NotificationHandlers {
  onNotificationCreated?: (msg: NotificationCreatedMessage) => void
  onPendingQuestions?: (msg: PendingQuestionsMessage) => void
  onQuestionAnswered?: (msg: QuestionAnsweredMessage) => void
  onQuestionsExpired?: (msg: QuestionsExpiredMessage) => void
  onBrowserNotification?: (title: string, body?: string) => void
}

/** Hook for managing runs state with WebSocket updates */
export function useRunsWithWebSocket(handlers?: NotificationHandlers) {
  const [runs, setRuns] = useState<Run[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  const handleMessage = useCallback((message: WSMessage) => {
    if (message.type === 'run_created') {
      const runMessage = message as { type: 'run_created'; run: Run }
      if (!runMessage.run.archived) {
        setRuns((prev) => [runMessage.run, ...prev])
      }
    } else if (message.type === 'run_updated') {
      const runMessage = message as { type: 'run_updated'; run: Run }
      if (runMessage.run.archived) {
        setRuns((prev) => prev.filter((r) => r.id !== runMessage.run.id))
      } else {
        setRuns((prev) => prev.map((r) => (r.id === runMessage.run.id ? runMessage.run : r)))
      }
      // Browser notification for terminal states when tab not focused
      if (
        ['completed', 'failed', 'cancelled'].includes(runMessage.run.status) &&
        typeof document !== 'undefined' &&
        document.hidden
      ) {
        handlersRef.current?.onBrowserNotification?.(
          `Run ${runMessage.run.status}`,
          runMessage.run.prompt?.substring(0, 60)
        )
      }
    } else if (message.type === 'loop_progress') {
      const loopMessage = message as {
        type: 'loop_progress'
        run_id: string
        loop_count: number
        max_loops: number
        circuit_state: CircuitState
        completion_confidence: number
        cost_usd: number
      }
      setRuns((prev) =>
        prev.map((r) =>
          r.id === loopMessage.run_id
            ? {
                ...r,
                loop_count: loopMessage.loop_count,
                max_loops: loopMessage.max_loops,
                circuit_state: loopMessage.circuit_state,
                completion_confidence: loopMessage.completion_confidence,
                cost_usd: loopMessage.cost_usd,
              }
            : r
        )
      )
    } else if (message.type === 'step_progress') {
      const stepMessage = message as {
        type: 'step_progress'
        run_id: string
        step_name: string
        step_index: number
        total_steps: number
        step_status: string
      }
      setRuns((prev) =>
        prev.map((r) =>
          r.id === stepMessage.run_id
            ? {
                ...r,
                chain_step_name: stepMessage.step_name,
                chain_step_index: stepMessage.step_index,
                chain_total_steps: stepMessage.total_steps,
              }
            : r
        )
      )
    } else if (message.type === 'notification_created') {
      handlersRef.current?.onNotificationCreated?.(message as NotificationCreatedMessage)
    } else if (message.type === 'pending_questions') {
      const qMsg = message as PendingQuestionsMessage
      handlersRef.current?.onPendingQuestions?.(qMsg)
      // Always fire browser notification for questions — they need attention
      handlersRef.current?.onBrowserNotification?.('Input required', qMsg.questions[0]?.header)
    } else if (message.type === 'question_answered') {
      handlersRef.current?.onQuestionAnswered?.(message as QuestionAnsweredMessage)
    } else if (message.type === 'questions_expired') {
      const expMsg = message as QuestionsExpiredMessage
      handlersRef.current?.onQuestionsExpired?.(expMsg)
      handlersRef.current?.onBrowserNotification?.(
        'Run paused',
        'Questions timed out — run has been paused'
      )
    }
  }, [])

  const { connected } = useWebSocket(handleMessage)

  // Fetch runs from API
  const fetchRunsData = useCallback(async () => {
    try {
      const response = await fetch('/api/runs?limit=100')
      if (!response.ok) throw new Error('Failed to fetch runs')
      const data = await response.json()
      setRuns(data)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    }
  }, [])

  // Initial fetch
  useEffect(() => {
    async function load() {
      await fetchRunsData()
      setLoading(false)
    }
    load()
  }, [fetchRunsData])

  // Manual refresh function for pull-to-refresh
  const refresh = useCallback(async () => {
    await fetchRunsData()
  }, [fetchRunsData])

  return { runs, loading, error, connected, setRuns, refresh }
}
