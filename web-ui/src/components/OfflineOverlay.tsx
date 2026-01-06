import { RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ConnectivityStatus } from '@/hooks/useConnectivity'
import { cn } from '@/lib/utils'
import { OfflineRobot, type RobotState } from './OfflineRobot'

interface OfflineOverlayProps {
  /** Current connectivity status */
  status: ConnectivityStatus
  /** Seconds until next retry attempt */
  retryIn?: number | null
  /** Callback to manually retry connection */
  onRetry?: () => void
  /** Last successful connection time */
  lastConnected?: Date | null
}

const statusMessages: Record<
  Exclude<ConnectivityStatus, 'online'>,
  { title: string; subtitle: string }
> = {
  offline: {
    title: "You're offline",
    subtitle: 'Check your internet connection',
  },
  'backend-unreachable': {
    title: 'Searching for Gluon...',
    subtitle: 'The backend server might be starting up',
  },
  checking: {
    title: 'Connecting...',
    subtitle: 'Establishing connection to Gluon',
  },
}

function getRobotState(status: ConnectivityStatus): RobotState {
  switch (status) {
    case 'checking':
      return 'reconnecting'
    case 'backend-unreachable':
      return 'searching'
    default:
      return 'waiting'
  }
}

function formatLastConnected(date: Date | null | undefined): string | null {
  if (!date) return null

  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffSecs = Math.floor(diffMs / 1000)
  const diffMins = Math.floor(diffSecs / 60)
  const diffHours = Math.floor(diffMins / 60)

  if (diffSecs < 60) return 'just now'
  if (diffMins < 60) return `${diffMins} minute${diffMins !== 1 ? 's' : ''} ago`
  if (diffHours < 24) return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`
  return date.toLocaleDateString()
}

/**
 * Full-screen overlay displayed when the app cannot connect to the backend.
 *
 * Features:
 * - Animated robot character
 * - Status-specific messaging
 * - Retry countdown and manual retry button
 * - Last connected timestamp
 * - Respects light/dark theme
 */
export function OfflineOverlay({ status, retryIn, onRetry, lastConnected }: OfflineOverlayProps) {
  const [isRetrying, setIsRetrying] = useState(false)
  const message = statusMessages[status as keyof typeof statusMessages] || statusMessages.checking
  const robotState = getRobotState(status)
  const lastConnectedText = formatLastConnected(lastConnected)

  // Reset retrying state when status changes
  useEffect(() => {
    if (status === 'checking') {
      setIsRetrying(true)
    } else {
      setIsRetrying(false)
    }
  }, [status])

  const handleRetry = () => {
    setIsRetrying(true)
    onRetry?.()
  }

  return (
    <div className="offline-overlay">
      <div className="offline-overlay__content">
        {/* Robot */}
        <div className="offline-overlay__robot">
          <OfflineRobot state={robotState} size="lg" />
        </div>

        {/* Message */}
        <div className="offline-overlay__message">
          <h1 className="offline-overlay__title">{message.title}</h1>
          <p className="offline-overlay__subtitle">{message.subtitle}</p>
        </div>

        {/* Loading dots */}
        <div className="offline-overlay__dots">
          <span className="offline-overlay__dot" />
          <span className="offline-overlay__dot" />
          <span className="offline-overlay__dot" />
        </div>

        {/* Retry section */}
        <div className="offline-overlay__actions">
          {retryIn && retryIn > 0 && !isRetrying && (
            <p className="offline-overlay__countdown">Retrying in {retryIn}s</p>
          )}

          <button
            type="button"
            className={cn(
              'offline-overlay__retry-btn',
              isRetrying && 'offline-overlay__retry-btn--loading'
            )}
            onClick={handleRetry}
            disabled={isRetrying}
          >
            <RefreshCw className={cn('w-4 h-4', isRetrying && 'animate-spin')} />
            <span>{isRetrying ? 'Connecting...' : 'Try Again'}</span>
          </button>
        </div>

        {/* Last connected */}
        {lastConnectedText && (
          <p className="offline-overlay__last-connected">Last connected {lastConnectedText}</p>
        )}
      </div>
    </div>
  )
}
