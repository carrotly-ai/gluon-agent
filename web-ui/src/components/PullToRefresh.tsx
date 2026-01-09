import { RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'
import { type PullToRefreshOptions, usePullToRefresh } from '@/hooks/usePullToRefresh'
import { cn } from '@/lib/utils'

interface PullToRefreshProps extends PullToRefreshOptions {
  children: ReactNode
  className?: string
}

export function PullToRefresh({
  children,
  className,
  onRefresh,
  threshold = 60,
  maxPull = 120,
  resistance = 0.4,
  disabled = false,
}: PullToRefreshProps) {
  const { isPulling, pullDistance, isRefreshing, canRelease, containerRef, handlers } =
    usePullToRefresh({
      onRefresh,
      threshold,
      maxPull,
      resistance,
      disabled,
    })

  // Calculate visual properties based on pull distance
  const progress = Math.min(pullDistance / threshold, 1)
  const indicatorOpacity = Math.min(progress * 1.5, 1)
  const indicatorScale = 0.5 + progress * 0.5
  const rotation = pullDistance * 3 // Rotate as user pulls

  return (
    <div className={cn('pull-to-refresh-container', className)}>
      {/* Pull indicator */}
      <div
        className={cn(
          'pull-to-refresh-indicator',
          (isPulling || isRefreshing) && 'pull-to-refresh-indicator--visible'
        )}
        style={{
          transform: `translateY(${pullDistance - 50}px)`,
          opacity: indicatorOpacity,
        }}
      >
        <div
          className={cn(
            'pull-to-refresh-spinner',
            isRefreshing && 'pull-to-refresh-spinner--active'
          )}
          style={{
            transform: isRefreshing ? undefined : `scale(${indicatorScale}) rotate(${rotation}deg)`,
          }}
        >
          <RefreshCw className="w-5 h-5" />
        </div>
        <span className="pull-to-refresh-text">
          {isRefreshing ? 'Refreshing' : canRelease ? 'Release' : 'Pull to refresh'}
        </span>
      </div>

      {/* Scrollable content */}
      <div
        ref={containerRef}
        className="pull-to-refresh-content"
        style={{
          transform: isPulling || isRefreshing ? `translateY(${pullDistance}px)` : undefined,
        }}
        {...handlers}
      >
        {children}
      </div>
    </div>
  )
}
