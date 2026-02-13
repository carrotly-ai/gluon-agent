import { useCallback, useState } from 'react'

/**
 * Hook for managing browser Notification API.
 *
 * Handles permission state, requesting permission, and showing notifications.
 * Gracefully degrades on non-HTTPS or unsupported browsers.
 */
export function useNotifications() {
  const supported = typeof window !== 'undefined' && 'Notification' in window
  const [permission, setPermission] = useState<NotificationPermission>(
    supported ? Notification.permission : 'denied'
  )

  const requestPermission = useCallback(async () => {
    if (!supported || Notification.permission !== 'default') return
    const result = await Notification.requestPermission()
    setPermission(result)
  }, [supported])

  const show = useCallback(
    (title: string, body?: string) => {
      if (supported && Notification.permission === 'granted') {
        new Notification(title, { body, icon: '/favicon.ico' })
      }
    },
    [supported]
  )

  return { permission, requestPermission, show, supported }
}
