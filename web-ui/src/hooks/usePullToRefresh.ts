import { useCallback, useEffect, useRef, useState } from 'react'

export interface PullToRefreshOptions {
  /** Callback when refresh is triggered */
  onRefresh: () => Promise<void>
  /** Pull distance required to trigger refresh (default: 60) */
  threshold?: number
  /** Maximum pull distance (default: 120) */
  maxPull?: number
  /** Resistance factor after threshold (default: 0.4) */
  resistance?: number
  /** Whether pull-to-refresh is disabled */
  disabled?: boolean
}

export interface PullToRefreshState {
  /** Whether user is currently pulling */
  isPulling: boolean
  /** Current pull distance in pixels */
  pullDistance: number
  /** Whether refresh is in progress */
  isRefreshing: boolean
  /** Whether pull distance exceeds threshold */
  canRelease: boolean
}

export interface PullToRefreshResult extends PullToRefreshState {
  /** Ref to attach to the scrollable container */
  containerRef: React.RefObject<HTMLDivElement | null>
  /** Handler props to spread on the container */
  handlers: {
    onTouchStart: (e: React.TouchEvent) => void
    onTouchMove: (e: React.TouchEvent) => void
    onTouchEnd: () => void
  }
}

export function usePullToRefresh({
  onRefresh,
  threshold = 60,
  maxPull = 120,
  resistance = 0.4,
  disabled = false,
}: PullToRefreshOptions): PullToRefreshResult {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const startYRef = useRef<number>(0)
  const startScrollTopRef = useRef<number>(0)
  const isPullingRef = useRef<boolean>(false)

  const [state, setState] = useState<PullToRefreshState>({
    isPulling: false,
    pullDistance: 0,
    isRefreshing: false,
    canRelease: false,
  })

  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      if (disabled || state.isRefreshing) return

      const container = containerRef.current
      if (!container) return

      // Only activate if scrolled to top
      const scrollTop = container.scrollTop
      startScrollTopRef.current = scrollTop

      if (scrollTop <= 0) {
        startYRef.current = e.touches[0].clientY
        isPullingRef.current = true
      }
    },
    [disabled, state.isRefreshing]
  )

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (!isPullingRef.current || disabled || state.isRefreshing) return

      const container = containerRef.current
      if (!container) return

      // If we've scrolled down since starting, cancel pull
      if (container.scrollTop > 0) {
        isPullingRef.current = false
        setState((prev) => ({ ...prev, isPulling: false, pullDistance: 0, canRelease: false }))
        return
      }

      const currentY = e.touches[0].clientY
      let delta = currentY - startYRef.current

      // Only track downward pulls
      if (delta < 0) {
        delta = 0
      }

      // Apply resistance after threshold
      let pullDistance: number
      if (delta > threshold) {
        const excess = delta - threshold
        pullDistance = threshold + excess * resistance
      } else {
        pullDistance = delta
      }

      // Cap at maxPull
      pullDistance = Math.min(pullDistance, maxPull)

      // Prevent default scroll if we're pulling
      if (pullDistance > 0) {
        e.preventDefault()
      }

      setState({
        isPulling: pullDistance > 0,
        pullDistance,
        isRefreshing: false,
        canRelease: delta >= threshold,
      })
    },
    [disabled, state.isRefreshing, threshold, resistance, maxPull]
  )

  const handleTouchEnd = useCallback(async () => {
    if (!isPullingRef.current || disabled) return

    isPullingRef.current = false

    const { canRelease } = state

    if (canRelease && !state.isRefreshing) {
      // Trigger refresh
      setState({
        isPulling: false,
        pullDistance: threshold, // Hold at threshold during refresh
        isRefreshing: true,
        canRelease: false,
      })

      try {
        await onRefresh()
      } finally {
        setState({
          isPulling: false,
          pullDistance: 0,
          isRefreshing: false,
          canRelease: false,
        })
      }
    } else {
      // Reset without refresh
      setState({
        isPulling: false,
        pullDistance: 0,
        isRefreshing: false,
        canRelease: false,
      })
    }
  }, [disabled, state, threshold, onRefresh])

  // Clean up on unmount or when disabled changes
  useEffect(() => {
    if (disabled) {
      isPullingRef.current = false
      setState({
        isPulling: false,
        pullDistance: 0,
        isRefreshing: false,
        canRelease: false,
      })
    }
  }, [disabled])

  return {
    ...state,
    containerRef,
    handlers: {
      onTouchStart: handleTouchStart,
      onTouchMove: handleTouchMove,
      onTouchEnd: handleTouchEnd,
    },
  }
}
