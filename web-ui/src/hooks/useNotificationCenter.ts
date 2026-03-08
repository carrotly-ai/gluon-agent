import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { fetchNotifications, markAllNotificationsRead, markNotificationRead } from '@/lib/api'
import type {
  GluonNotification,
  NotificationCreatedMessage,
  PendingQuestion,
  PendingQuestionsMessage,
  QuestionAnsweredMessage,
  QuestionsExpiredMessage,
} from '@/lib/types'

interface NotificationCenterState {
  notifications: GluonNotification[]
  unreadCount: number
  pendingQuestions: Array<PendingQuestion & { run_id: string }>

  fetchNotifications(): Promise<void>
  markRead(id: string): Promise<void>
  markAllRead(): Promise<void>

  handleNotificationEvent(msg: NotificationCreatedMessage): void
  handleQuestionEvent(msg: PendingQuestionsMessage): void
  handleQuestionAnswered(msg: QuestionAnsweredMessage): void
  handleQuestionsExpired(msg: QuestionsExpiredMessage): void
}

const defaultState: NotificationCenterState = {
  notifications: [],
  unreadCount: 0,
  pendingQuestions: [],
  fetchNotifications: async () => {},
  markRead: async () => {},
  markAllRead: async () => {},
  handleNotificationEvent: () => {},
  handleQuestionEvent: () => {},
  handleQuestionAnswered: () => {},
  handleQuestionsExpired: () => {},
}

export const NotificationCenterContext = createContext<NotificationCenterState>(defaultState)

export function useNotificationCenter() {
  return useContext(NotificationCenterContext)
}

export function useNotificationCenterProvider() {
  const [notifications, setNotifications] = useState<GluonNotification[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const [pendingQuestions, setPendingQuestions] = useState<
    Array<PendingQuestion & { run_id: string }>
  >([])
  const initialFetchDone = useRef(false)

  const doFetch = useCallback(async () => {
    try {
      const response = await fetchNotifications({ limit: 50 })
      setNotifications(response.notifications)
      setUnreadCount(response.unread_count)
    } catch {
      // Ignore fetch errors
    }
  }, [])

  // Initial fetch
  useEffect(() => {
    if (!initialFetchDone.current) {
      initialFetchDone.current = true
      doFetch()
    }
  }, [doFetch])

  const markRead = useCallback(async (id: string) => {
    try {
      await markNotificationRead(id)
      setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)))
      setUnreadCount((prev) => Math.max(0, prev - 1))
    } catch {
      // Ignore
    }
  }, [])

  const markAllRead = useCallback(async () => {
    try {
      await markAllNotificationsRead()
      setNotifications((prev) => prev.map((n) => ({ ...n, read: true })))
      setUnreadCount(0)
    } catch {
      // Ignore
    }
  }, [])

  const handleNotificationEvent = useCallback((msg: NotificationCreatedMessage) => {
    const n = msg.notification
    const newNotification: GluonNotification = {
      id: n.id,
      workspace_id: null,
      project_id: null,
      run_id: n.run_id,
      session_id: null,
      type: n.type,
      severity: n.severity,
      title: n.title,
      message: n.message,
      metadata: null,
      read: false,
      created_at: n.created_at,
      read_at: null,
    }
    setNotifications((prev) => [newNotification, ...prev].slice(0, 50))
    setUnreadCount((prev) => prev + 1)
  }, [])

  const handleQuestionEvent = useCallback((msg: PendingQuestionsMessage) => {
    const newQuestions = msg.questions.map((q) => ({
      id: q.id,
      run_id: msg.run_id,
      question_index: 0,
      question_text: q.question,
      header: q.header,
      options: q.options,
      multi_select: q.multi_select,
      status: 'pending' as const,
      created_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
      selected_labels: null,
      answer_source: null,
    }))
    setPendingQuestions((prev) => [...prev, ...newQuestions])
  }, [])

  const handleQuestionAnswered = useCallback((msg: QuestionAnsweredMessage) => {
    setPendingQuestions((prev) => prev.filter((q) => q.id !== msg.question_id))
  }, [])

  const handleQuestionsExpired = useCallback((msg: QuestionsExpiredMessage) => {
    // Remove expired questions from pending list (dismisses the modal)
    const expiredIds = new Set(msg.question_ids)
    setPendingQuestions((prev) => prev.filter((q) => !expiredIds.has(q.id)))
  }, [])

  return {
    notifications,
    unreadCount,
    pendingQuestions,
    fetchNotifications: doFetch,
    markRead,
    markAllRead,
    handleNotificationEvent,
    handleQuestionEvent,
    handleQuestionAnswered,
    handleQuestionsExpired,
  }
}
