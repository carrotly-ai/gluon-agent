import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchServerVersion,
  getBuildVersion,
  isNewerVersionAvailable,
  type VersionInfo,
} from '../lib/version'

interface UseVersionCheckOptions {
  /** Polling interval in milliseconds (default: 5 minutes) */
  pollInterval?: number
  /** Whether to check on tab focus (default: true) */
  checkOnFocus?: boolean
  /** Whether to check immediately on mount (default: true) */
  checkOnMount?: boolean
}

interface VersionCheckState {
  /** Whether a newer version is available */
  updateAvailable: boolean
  /** The current build version (what's loaded in browser) */
  buildVersion: VersionInfo
  /** The server version (what's currently deployed) */
  serverVersion: VersionInfo | null
  /** Whether we're currently checking for updates */
  checking: boolean
  /** Any error that occurred during checking */
  error: string | null
  /** Manually trigger an update check */
  checkNow: () => Promise<void>
  /** Dismiss the update banner (persists until next version change) */
  dismiss: () => void
  /** Whether the update has been dismissed */
  dismissed: boolean
}

const DISMISSED_VERSION_KEY = 'gluon-dismissed-version'
const DEFAULT_POLL_INTERVAL = 5 * 60 * 1000 // 5 minutes

/**
 * Hook to check for application updates by comparing build version with server version.
 *
 * Features:
 * - Polls server version periodically
 * - Checks on tab focus (for users returning to the app)
 * - Allows dismissing update notification until next version change
 * - Skips checks in development mode
 */
export function useVersionCheck(options: UseVersionCheckOptions = {}): VersionCheckState {
  const {
    pollInterval = DEFAULT_POLL_INTERVAL,
    checkOnFocus = true,
    checkOnMount = true,
  } = options

  const [buildVersion] = useState<VersionInfo>(() => getBuildVersion())
  const [serverVersion, setServerVersion] = useState<VersionInfo | null>(null)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(() => {
    return localStorage.getItem(DISMISSED_VERSION_KEY)
  })

  // Track if component is mounted to avoid state updates after unmount
  const mountedRef = useRef(true)

  const checkVersion = useCallback(async () => {
    // Skip in development mode
    if (buildVersion.environment === 'development') {
      return
    }

    setChecking(true)
    setError(null)

    try {
      const version = await fetchServerVersion()
      if (mountedRef.current) {
        setServerVersion(version)
      }
    } catch (err) {
      if (mountedRef.current) {
        setError(err instanceof Error ? err.message : 'Failed to check for updates')
      }
    } finally {
      if (mountedRef.current) {
        setChecking(false)
      }
    }
  }, [buildVersion.environment])

  // Determine if update is available (and not dismissed)
  const updateAvailable =
    serverVersion !== null &&
    isNewerVersionAvailable(buildVersion, serverVersion) &&
    dismissedVersion !== serverVersion.full_version

  // Dismiss handler - persists the dismissed version
  const dismiss = useCallback(() => {
    if (serverVersion) {
      localStorage.setItem(DISMISSED_VERSION_KEY, serverVersion.full_version)
      setDismissedVersion(serverVersion.full_version)
    }
  }, [serverVersion])

  // Check on mount
  useEffect(() => {
    if (checkOnMount) {
      checkVersion()
    }
  }, [checkOnMount, checkVersion])

  // Set up polling
  useEffect(() => {
    if (buildVersion.environment === 'development') {
      return
    }

    const interval = setInterval(checkVersion, pollInterval)
    return () => clearInterval(interval)
  }, [pollInterval, checkVersion, buildVersion.environment])

  // Check on tab focus
  useEffect(() => {
    if (!checkOnFocus || buildVersion.environment === 'development') {
      return
    }

    const handleFocus = () => {
      checkVersion()
    }

    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [checkOnFocus, checkVersion, buildVersion.environment])

  // Cleanup on unmount
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  return {
    updateAvailable,
    buildVersion,
    serverVersion,
    checking,
    error,
    checkNow: checkVersion,
    dismiss,
    dismissed: dismissedVersion === serverVersion?.full_version,
  }
}
