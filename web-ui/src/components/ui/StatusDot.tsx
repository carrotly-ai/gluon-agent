import { cn } from '@/lib/utils'

/**
 * Status glyph + optional label, formalising the `.mark mark-{state}` system
 * from index.css as a component. The seven states are the canonical run
 * statuses — anything else should be added to index.css first.
 *
 * Replaces ad-hoc `<span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />`
 * implementations scattered across RunCard, ActivityPage, WorkQueuePage,
 * MergeQueuePage, ListViewPage. Use this component instead of reinventing.
 */
export type StatusState =
  | 'pending'
  | 'running'
  | 'completed'
  | 'review'
  | 'failed'
  | 'cancelled'
  | 'recovering'

export interface StatusDotProps {
  state: StatusState
  /** Glyph size. Default 'md' matches the .mark base (6px). */
  size?: 'sm' | 'md' | 'lg'
  /** Optional uppercase label rendered alongside the glyph. */
  label?: string
  className?: string
}

const sizeClasses: Record<NonNullable<StatusDotProps['size']>, string> = {
  sm: 'w-1 h-1',
  md: 'w-1.5 h-1.5',
  lg: 'w-2 h-2',
}

// 'recovering' is not in the .mark-* family — falls back to harvest + breathe.
const recoveringStyle = {
  background: 'var(--color-harvest)',
}

export function StatusDot({ state, size = 'md', label, className }: StatusDotProps) {
  const isRecovering = state === 'recovering'

  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <span
        className={cn(
          'mark',
          !isRecovering && `mark-${state}`,
          isRecovering && 'mark-running', // reuse breathe animation
          sizeClasses[size]
        )}
        style={isRecovering ? recoveringStyle : undefined}
        aria-hidden="true"
      />
      {label ? (
        <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
          {label}
        </span>
      ) : null}
      {!label ? <span className="sr-only">{state}</span> : null}
    </span>
  )
}
