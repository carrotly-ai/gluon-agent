import { RefreshCw, X } from 'lucide-react'
import { useVersionCheck } from '../hooks/useVersionCheck'
import { cn } from '../lib/utils'

interface UpdateBannerProps {
  className?: string
}

/**
 * Banner component that displays when a new version of the app is available.
 *
 * Features:
 * - Shows version comparison (current → new)
 * - Refresh button to reload the app
 * - Dismiss button to hide until next version
 * - Auto-detects updates via polling
 */
export function UpdateBanner({ className }: UpdateBannerProps) {
  const { updateAvailable, buildVersion, serverVersion, dismiss } = useVersionCheck()

  if (!updateAvailable || !serverVersion) {
    return null
  }

  const handleRefresh = () => {
    // Guard against losing in-flight work: an open dialog or a textarea with
    // content is a pragmatic signal that the user has unsaved state.
    const hasOpenDialog = document.querySelector('[role="dialog"]') !== null
    const hasDirtyTextarea = Array.from(document.querySelectorAll('textarea')).some(
      (el) => el.value.trim().length > 0
    )
    if (hasOpenDialog || hasDirtyTextarea) {
      const proceed = window.confirm(
        'A new version is ready. Refresh now? Unsaved changes in open dialogs will be lost.'
      )
      if (!proceed) return
    }

    // Clear service worker caches before reloading
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.getRegistrations().then((registrations) => {
        for (const registration of registrations) {
          registration.update()
        }
      })
      // Also clear caches
      if ('caches' in window) {
        caches.keys().then((names) => {
          for (const name of names) {
            caches.delete(name)
          }
        })
      }
    }
    // Force reload from server
    window.location.reload()
  }

  return (
    <div
      className={cn(
        'bg-[var(--color-jade)]/10 border-b border-[var(--color-jade)]/20',
        'px-4 py-2 flex items-center justify-center gap-3',
        'text-[0.6875rem] text-[var(--color-jade)]',
        className
      )}
    >
      <RefreshCw className="w-3.5 h-3.5 flex-shrink-0" />
      <span>
        A new version is available ({buildVersion.version} → {serverVersion.version})
      </span>
      <button
        onClick={handleRefresh}
        className="px-2 py-0.5 bg-[var(--color-jade)] text-[var(--color-void)] rounded-sm hover:opacity-90 transition-opacity text-[0.625rem] uppercase tracking-wider font-medium"
      >
        Refresh
      </button>
      <button
        onClick={dismiss}
        className="p-1 hover:bg-[var(--color-jade)]/20 rounded-sm transition-colors"
        title="Dismiss until next update"
      >
        <X className="w-3 h-3" />
      </button>
    </div>
  )
}
