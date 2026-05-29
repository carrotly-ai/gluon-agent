import { Bell, CheckCheck, Trash2, X } from 'lucide-react'
import { useCallback, useState } from 'react'
import { useNotificationCenter } from '@/hooks/useNotificationCenter'
import type { GluonNotification } from '@/lib/types'
import { cn } from '@/lib/utils'

function severityColor(severity: string): string {
  switch (severity) {
    case 'error':
      return 'text-[var(--color-vermillion)]'
    case 'warning':
      return 'text-[var(--color-harvest)]'
    case 'success':
      return 'text-[var(--color-jade)]'
    default:
      return 'text-[var(--color-sky)]'
  }
}

function NotificationItem({
  notification,
  onMarkRead,
  onNavigate,
}: {
  notification: GluonNotification
  onMarkRead: (id: string) => void
  onNavigate: (runId: string) => void
}) {
  const age = Math.floor((Date.now() - new Date(notification.created_at).getTime()) / 1000)
  const ageLabel =
    age < 60
      ? 'just now'
      : age < 3600
        ? `${Math.floor(age / 60)}m ago`
        : age < 86400
          ? `${Math.floor(age / 3600)}h ago`
          : `${Math.floor(age / 86400)}d ago`

  return (
    <button
      type="button"
      className={cn(
        'w-full text-left px-3 py-2.5 border-b border-[rgba(163,163,163,0.06)] hover:bg-[rgba(163,163,163,0.04)] transition-colors',
        !notification.read && 'bg-[rgba(102,178,255,0.03)]'
      )}
      onClick={() => {
        if (notification.run_id) onNavigate(notification.run_id)
        if (!notification.read) onMarkRead(notification.id)
      }}
    >
      <div className="flex items-start gap-2">
        {!notification.read && (
          <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-[var(--color-sky)] shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <p className={cn('text-body truncate', severityColor(notification.severity))}>
            {notification.title}
          </p>
          {notification.message && (
            <p className="text-body text-[var(--color-stone)]/60 truncate mt-0.5">
              {notification.message}
            </p>
          )}
          <p className="text-body text-[var(--color-stone)]/60 mt-0.5">{ageLabel}</p>
        </div>
      </div>
    </button>
  )
}

export function NotificationBell({
  onNavigateToRun,
}: {
  onNavigateToRun: (runId: string) => void
}) {
  const { notifications, unreadCount, markRead, markAllRead, clearAll } = useNotificationCenter()
  const [open, setOpen] = useState(false)

  // Reserve the danger colour (vermillion) for badges that actually represent a
  // failure; a routine unread notification (e.g. a completed run) shows the
  // informational sky colour instead.
  const hasUnreadError = notifications.some((n) => !n.read && n.severity === 'error')

  const handleToggle = useCallback(() => {
    setOpen((prev) => !prev)
  }, [])

  const handleClose = useCallback(() => {
    setOpen(false)
  }, [])

  return (
    <div className="relative">
      <button
        type="button"
        className="relative min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 inline-flex items-center justify-center md:p-1.5 rounded-sm hover:bg-[rgba(163,163,163,0.1)] transition-colors"
        onClick={handleToggle}
        title={unreadCount > 0 ? `${unreadCount} unread notifications` : 'Notifications'}
        aria-label={unreadCount > 0 ? `${unreadCount} unread notifications` : 'Notifications'}
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <Bell className="w-4 h-4 text-[var(--color-stone)]" />
        {unreadCount > 0 && (
          <span
            className={cn(
              'absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 flex items-center justify-center rounded-full text-micro text-white font-medium',
              hasUnreadError ? 'bg-[var(--color-vermillion)]' : 'bg-[var(--color-sky)]'
            )}
          >
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <>
          {/* Backdrop */}
          <div className="fixed inset-0 z-40" onClick={handleClose} />
          {/* Popover */}
          <div className="absolute right-0 top-full mt-1 w-80 max-h-96 z-50 bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-lg overflow-hidden flex flex-col">
            {/* Header */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-[rgba(163,163,163,0.1)]">
              <span className="text-body text-[var(--color-paper)] uppercase tracking-widest">
                Notifications
              </span>
              <div className="flex items-center gap-1">
                {unreadCount > 0 && (
                  <button
                    type="button"
                    className="min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 inline-flex items-center justify-center md:p-1 rounded-sm hover:bg-[rgba(163,163,163,0.1)] transition-colors"
                    onClick={() => markAllRead()}
                    title="Mark all read"
                    aria-label="Mark all read"
                  >
                    <CheckCheck className="w-3.5 h-3.5 text-[var(--color-stone)]/60" />
                  </button>
                )}
                {notifications.length > 0 && (
                  <button
                    type="button"
                    className="min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 inline-flex items-center justify-center md:p-1 rounded-sm hover:bg-[rgba(163,163,163,0.1)] transition-colors"
                    onClick={() => clearAll()}
                    title="Clear all notifications"
                    aria-label="Clear all notifications"
                  >
                    <Trash2 className="w-3.5 h-3.5 text-[var(--color-stone)]/60" />
                  </button>
                )}
                <button
                  type="button"
                  className="min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 inline-flex items-center justify-center md:p-1 rounded-sm hover:bg-[rgba(163,163,163,0.1)] transition-colors"
                  onClick={handleClose}
                  title="Close"
                  aria-label="Close notifications"
                >
                  <X className="w-3.5 h-3.5 text-[var(--color-stone)]/60" />
                </button>
              </div>
            </div>

            {/* List */}
            <div className="flex-1 overflow-y-auto">
              {notifications.length === 0 ? (
                <div className="px-3 py-8 text-center text-body text-[var(--color-stone)]/60">
                  No notifications
                </div>
              ) : (
                notifications.map((n) => (
                  <NotificationItem
                    key={n.id}
                    notification={n}
                    onMarkRead={markRead}
                    onNavigate={(runId) => {
                      onNavigateToRun(runId)
                      handleClose()
                    }}
                  />
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
