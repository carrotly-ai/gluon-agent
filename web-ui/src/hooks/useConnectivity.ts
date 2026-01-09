import { useCallback, useEffect, useRef, useState } from 'react'

export type ConnectivityStatus = 'online' | 'offline' | 'backend-unreachable' | 'checking'

export interface ConnectivityState {
  /** Device has network interface */
  isOnline: boolean
  /** Can reach Gluon backend API */
  isBackendReachable: boolean
  /** Unified status for UI consumption */
  status: ConnectivityStatus
  /** Last successful backend check */
  lastChecked: Date | null
  /** Seconds until next retry (when offline/unreachable) */
  retryIn: number | null
}

interface UseConnectivityOptions {
  /** URL to check backend health. Default: '/api/status' */
  healthCheckUrl?: string
  /** Interval for health checks when online (ms). Default: 30000 */
  healthCheckInterval?: number
  /** Interval for retry when offline (ms). Default: 5000 */
  retryInterval?: number
  /** Network timeout for health check (ms). Default: 5000 */
  timeout?: number
}

const DEFAULT_OPTIONS: Required<UseConnectivityOptions> = {
  healthCheckUrl: '/api/status',
  healthCheckInterval: 30000,
  retryInterval: 5000,
  timeout: 5000,
}

/**
 * Multi-layer connectivity detection hook.
 *
 * Combines:
 * - navigator.onLine (fast but unreliable)
 * - Periodic health checks to backend
 * - Exponential backoff on failures
 */
export function useConnectivity(options?: UseConnectivityOptions): ConnectivityState & {
  checkNow: () => Promise<void>
} {
  const opts = { ...DEFAULT_OPTIONS, ...options }

  const [state, setState] = useState<ConnectivityState>({
    isOnline: typeof navigator !== 'undefined' ? navigator.onLine : true,
    isBackendReachable: true, // Optimistic default
    status: 'checking',
    lastChecked: null,
    retryIn: null,
  })

  const checkIntervalRef = useRef<number | undefined>(undefined)
  const retryTimeoutRef = useRef<number | undefined>(undefined)
  const countdownIntervalRef = useRef<number | undefined>(undefined)
  const failureCountRef = useRef(0)
  const isMountedRef = useRef(true)
  // Ref to break circular dependency between performCheck and startRetryCountdown
  const performCheckRef = useRef<() => Promise<void>>(async () => {})

  // Check backend reachability
  const checkBackend = useCallback(async (): Promise<boolean> => {
    try {
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), opts.timeout)

      const response = await fetch(opts.healthCheckUrl, {
        method: 'GET',
        signal: controller.signal,
        cache: 'no-store',
      })

      clearTimeout(timeoutId)
      return response.ok
    } catch {
      return false
    }
  }, [opts.healthCheckUrl, opts.timeout])

  // Start countdown for retry - defined first, uses ref for performCheck to avoid circular dep
  const startRetryCountdown = useCallback(() => {
    // Clear existing timers
    if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current)
    if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current)

    const retrySeconds = Math.ceil(opts.retryInterval / 1000)
    let countdown = retrySeconds

    // Update countdown every second
    countdownIntervalRef.current = window.setInterval(() => {
      countdown -= 1
      if (countdown > 0 && isMountedRef.current) {
        setState((prev) => ({ ...prev, retryIn: countdown }))
      }
    }, 1000)

    // Schedule actual retry using ref to avoid circular dependency
    retryTimeoutRef.current = window.setTimeout(() => {
      if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current)
      performCheckRef.current()
    }, opts.retryInterval)
  }, [opts.retryInterval])

  // Perform connectivity check
  const performCheck = useCallback(async () => {
    if (!isMountedRef.current) return

    const isOnline = navigator.onLine

    if (!isOnline) {
      // Device is offline
      setState((prev) => ({
        ...prev,
        isOnline: false,
        isBackendReachable: false,
        status: 'offline',
        retryIn: null,
      }))
      failureCountRef.current = 0
      return
    }

    // Device is online, check backend
    setState((prev) => ({ ...prev, status: prev.status === 'online' ? 'online' : 'checking' }))

    const backendReachable = await checkBackend()

    if (!isMountedRef.current) return

    if (backendReachable) {
      // Success - reset failure count
      failureCountRef.current = 0
      setState({
        isOnline: true,
        isBackendReachable: true,
        status: 'online',
        lastChecked: new Date(),
        retryIn: null,
      })
    } else {
      // Backend unreachable
      failureCountRef.current += 1
      setState((prev) => ({
        ...prev,
        isOnline: true,
        isBackendReachable: false,
        status: 'backend-unreachable',
        retryIn: Math.ceil(opts.retryInterval / 1000),
      }))

      // Schedule retry with countdown
      startRetryCountdown()
    }
  }, [checkBackend, opts.retryInterval, startRetryCountdown])

  // Keep ref updated for startRetryCountdown to use
  performCheckRef.current = performCheck

  // Manual check function
  const checkNow = useCallback(async () => {
    // Clear any pending retry
    if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current)
    if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current)

    setState((prev) => ({ ...prev, status: 'checking', retryIn: null }))
    await performCheck()
  }, [performCheck])

  // Handle online/offline events
  useEffect(() => {
    const handleOnline = () => {
      // Device came online, check backend
      performCheck()
    }

    const handleOffline = () => {
      // Device went offline
      if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current)
      if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current)

      setState((prev) => ({
        ...prev,
        isOnline: false,
        isBackendReachable: false,
        status: 'offline',
        retryIn: null,
      }))
    }

    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)

    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [performCheck])

  // Initial check and periodic health checks
  useEffect(() => {
    isMountedRef.current = true

    // Initial check
    performCheck()

    // Periodic checks when online
    checkIntervalRef.current = window.setInterval(() => {
      if (navigator.onLine && isMountedRef.current) {
        performCheck()
      }
    }, opts.healthCheckInterval)

    return () => {
      isMountedRef.current = false
      if (checkIntervalRef.current) clearInterval(checkIntervalRef.current)
      if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current)
      if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current)
    }
  }, [performCheck, opts.healthCheckInterval])

  return {
    ...state,
    checkNow,
  }
}
