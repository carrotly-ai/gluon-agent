import { Archive, ExternalLink, GitBranch, RefreshCw, Square, X, Zap } from 'lucide-react'
import { formatFullDateTime, formatRelativeTime } from '@/lib/timestamps'
import type { CircuitState, HealthClassification, Run } from '@/lib/types'
import { cn } from '@/lib/utils'

// Circuit state color mapping
function getCircuitStateColor(state: CircuitState): string {
  switch (state) {
    case 'CLOSED':
      return 'text-green-400'
    case 'HALF_OPEN':
      return 'text-yellow-400'
    case 'OPEN':
      return 'text-red-400'
    default:
      return 'text-[var(--color-stone)]/60'
  }
}

function getCircuitStateBg(state: CircuitState): string {
  switch (state) {
    case 'CLOSED':
      return 'bg-green-400/15'
    case 'HALF_OPEN':
      return 'bg-yellow-400/15'
    case 'OPEN':
      return 'bg-red-400/15'
    default:
      return 'bg-[var(--color-stone)]/15'
  }
}

function getHealthDotColor(classification: HealthClassification): string {
  switch (classification) {
    case 'healthy':
      return 'bg-[var(--color-jade)]'
    case 'slow':
      return 'bg-[var(--color-harvest)]'
    case 'looping':
      return 'bg-orange-400'
    case 'stuck':
    case 'zombie':
      return 'bg-[var(--color-vermillion)]'
    case 'needs_context_reset':
      return 'bg-[var(--color-vermillion)]'
    default:
      return 'bg-[var(--color-stone)]/40'
  }
}

interface RunCardProps {
  run: Run
  onClick: () => void
  onCancel?: () => void
  onArchive?: () => void
  onStopLoop?: () => void
}

// Map status to CSS variable for border color
function getStatusBorderColor(status: string, isRecovering?: boolean): string {
  // Amber for recovery state (distinct from running)
  if (isRecovering) return '#f59e0b'

  switch (status) {
    case 'running':
      return 'var(--color-sky)'
    case 'pending':
      return 'var(--color-stone)'
    case 'completed':
      return 'var(--color-jade)'
    case 'review':
      return '#a855f7' // Purple for review
    case 'failed':
      return 'var(--color-vermillion)'
    case 'cancelled':
      return 'var(--color-stone)'
    default:
      return 'var(--color-stone)'
  }
}

export function RunCard({ run, onClick, onCancel, onArchive, onStopLoop }: RunCardProps) {
  const isActive = run.status === 'running' || run.status === 'pending'
  const isRecovering = run.is_recovering

  // Determine status for border color
  const effectiveStatus =
    run.status === 'completed' && run.use_worktree && run.branch_name && run.pr_status !== 'merged'
      ? 'review'
      : run.status

  // Card is in "done" state - completed and not awaiting review
  const isDone = run.status === 'completed' && effectiveStatus !== 'review'

  return (
    <div
      className={cn(
        'card hover-whisper cursor-grab active:cursor-grabbing group relative',
        run.status === 'running' && !isRecovering && 'card-running overflow-visible',
        isRecovering && 'card-recovering overflow-visible'
      )}
      style={{
        borderLeft: `3px solid ${getStatusBorderColor(effectiveStatus, isRecovering)}`,
      }}
      onClick={onClick}
    >
      {/* Shimmer stripe overlay for recovering cards */}
      {isRecovering && (
        <div
          className="stripe-shimmer absolute top-0 bottom-0 w-[3px]"
          style={{ backgroundColor: '#f59e0b', left: '-3px' }}
        />
      )}

      {/* Pulsing stripe overlay for running cards (non-recovery) */}
      {run.status === 'running' && !isRecovering && (
        <div
          className="stripe-pulse absolute top-0 bottom-0 w-[3px]"
          style={{ backgroundColor: getStatusBorderColor(run.status), left: '-3px' }}
        />
      )}

      {/* Action buttons - always visible for non-active cards */}
      {onArchive && !isActive && (
        <div className="absolute top-2 right-2 flex items-center gap-1">
          <button
            className="p-1.5 text-[var(--color-stone)]/30 hover:text-[var(--color-stone)] hover:bg-[var(--color-paper)]/10 rounded-sm transition-colors"
            onClick={(e) => {
              e.stopPropagation()
              onArchive()
            }}
            title="Archive"
          >
            <Archive className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Prompt - no mark indicator */}
      <p
        className="text-title text-[var(--color-paper)] leading-relaxed break-words"
        style={{
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
        }}
        title={run.prompt}
      >
        {run.prompt}
      </p>

      {/* Bottom row: metadata */}
      <div className="flex items-center justify-between mt-2 sm:mt-3 gap-2">
        <div className="flex items-center gap-2 sm:gap-4 flex-wrap min-w-0">
          {/* Recovering badge */}
          {isRecovering && (
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-[0.5rem] uppercase bg-[rgba(245,158,11,0.15)] text-amber-400">
              <RefreshCw className="w-2.5 h-2.5 animate-spin" />
              Recovering
            </span>
          )}
          <span className="text-mono text-[var(--color-stone)]/60 truncate max-w-[100px] sm:max-w-none">
            {run.project_name}
          </span>
          {run.health_classification && run.status === 'running' && (
            <span
              className={cn(
                'w-2 h-2 rounded-full shrink-0',
                getHealthDotColor(run.health_classification)
              )}
              title={`Health: ${run.health_classification}`}
            />
          )}
          {run.use_worktree && (
            <span
              className="flex items-center gap-1 text-purple-400"
              title={run.branch_name || 'Worktree'}
            >
              <GitBranch className="w-3 h-3" />
              {run.branch_name && (
                <span className="text-caption truncate max-w-[80px]">{run.branch_name}</span>
              )}
            </span>
          )}
          {run.pr_number && run.pr_url && (
            <a
              href={run.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                'flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-[0.5rem] uppercase transition-colors',
                run.pr_status === 'open' &&
                  'bg-[rgba(34,197,94,0.15)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]',
                run.pr_status === 'merged' && 'bg-[rgba(168,85,247,0.15)] text-purple-400',
                run.pr_status === 'closed' && 'bg-[rgba(239,68,68,0.15)] text-red-400',
                run.pr_status === 'draft' &&
                  'bg-[rgba(163,163,163,0.15)] text-[var(--color-stone)]',
                !run.pr_status && 'bg-[rgba(163,163,163,0.15)] text-[var(--color-stone)]'
              )}
              onClick={(e) => e.stopPropagation()}
              title={`Open PR #${run.pr_number}`}
            >
              <span>PR #{run.pr_number}</span>
              <ExternalLink className="w-2.5 h-2.5" />
            </a>
          )}
          <span
            className="text-mono text-[var(--color-stone)]/55 hidden sm:inline cursor-help"
            title={formatFullDateTime(run.created_at)}
          >
            {formatRelativeTime(run.created_at)}
          </span>
          {/* {run.duration_seconds !== null && (
            <span className="text-mono text-[var(--color-stone)]/55">
              {formatDuration(run.duration_seconds)}
            </span>
          )} */}
        </div>

        {isActive && onCancel && (
          <button
            className="p-1.5 sm:p-1 text-[var(--color-stone)]/55 hover:text-[var(--color-vermillion)] transition-colors shrink-0"
            onClick={(e) => {
              e.stopPropagation()
              onCancel()
            }}
          >
            <X className="w-4 h-4 sm:w-3 sm:h-3" />
          </button>
        )}
      </div>

      {/* Ralph Loop Progress - hide for done cards to keep them simple */}
      {run.ralph_enabled && !isDone && (
        <div className="mt-2 sm:mt-3 space-y-1.5">
          {/* Progress bar */}
          <div className="flex items-center gap-2">
            <RefreshCw
              className={cn(
                'w-3 h-3 text-[var(--color-sky)]',
                run.status === 'running' && 'animate-spin'
              )}
            />
            <div className="flex-1 h-1.5 bg-[rgba(163,163,163,0.15)] rounded-full overflow-hidden">
              <div
                className="h-full bg-[var(--color-sky)] rounded-full transition-all duration-300"
                style={{
                  width: `${Math.min(100, ((run.loop_count || 0) / (run.max_loops || 50)) * 100)}%`,
                }}
              />
            </div>
            <span className="text-caption text-[var(--color-stone)]/60 min-w-[50px] text-right">
              {run.loop_count || 0}/{run.max_loops || 50}
            </span>
            {/* Stop Loop button - only for running ralph tasks */}
            {run.status === 'running' && onStopLoop && (
              <button
                className="p-1 text-[var(--color-stone)]/50 hover:text-amber-400 hover:bg-amber-400/10 rounded-sm transition-colors"
                onClick={(e) => {
                  e.stopPropagation()
                  onStopLoop()
                }}
                title="Stop loop gracefully"
              >
                <Square className="w-3 h-3" />
              </button>
            )}
          </div>

          {/* Status row: Circuit state (if not CLOSED) + cost */}
          <div className="flex items-center gap-2 text-caption">
            {/* Circuit state badge - only show if NOT CLOSED (abnormal state) */}
            {run.circuit_state && run.circuit_state !== 'CLOSED' && (
              <span
                className={cn(
                  'flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-[0.5rem] uppercase',
                  getCircuitStateBg(run.circuit_state),
                  getCircuitStateColor(run.circuit_state)
                )}
                title={`Circuit: ${run.circuit_state}`}
              >
                <Zap className="w-2.5 h-2.5" />
                {run.circuit_state}
              </span>
            )}

            {/* Completion confidence (shown if > 0) */}
            {(run.completion_confidence || 0) > 0 && (
              <span className="text-[var(--color-stone)]/50">
                {Math.round(run.completion_confidence || 0)}% confident
              </span>
            )}

            {/* Cost display */}
            {run.cost_usd != null && run.cost_usd > 0 && (
              <span className="text-[var(--color-stone)]/50 ml-auto">
                ${run.cost_usd.toFixed(2)}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Chain Step Progress - show for formula runs */}
      {run.chain_id && !isDone && (
        <div className="mt-2 sm:mt-3 space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-caption text-[var(--color-stone)]/60">
              Step {(run.chain_step_index ?? 0) + 1}/{run.chain_total_steps ?? 0}
            </span>
            <div className="flex-1 h-1.5 bg-[rgba(163,163,163,0.15)] rounded-full overflow-hidden">
              <div
                className="h-full bg-[var(--color-violet)] rounded-full transition-all duration-300"
                style={{
                  width: `${(((run.chain_step_index ?? 0) + 1) / (run.chain_total_steps || 1)) * 100}%`,
                }}
              />
            </div>
            <span className="text-caption text-[var(--color-stone)]/80 font-medium">
              {run.chain_step_name}
            </span>
          </div>
        </div>
      )}

      {/* Completed formula summary */}
      {run.chain_id && isDone && (
        <div className="mt-1 flex items-center gap-1.5">
          <span className="text-caption text-[var(--color-stone)]/50">
            Formula: {run.chain_total_steps} steps
          </span>
        </div>
      )}

      {/* Recovery progress OR Error message */}
      {isRecovering ? (
        <p className="text-caption text-amber-400/80 mt-2 sm:mt-3 pl-4 sm:pl-[18px]">
          <RefreshCw className="w-3 h-3 inline mr-1 animate-spin" />
          Processing item {run.recovery_item_count || 0}...
          {run.cost_usd != null && run.cost_usd > 0 && (
            <span className="ml-2 text-[var(--color-stone)]/55">${run.cost_usd.toFixed(2)}</span>
          )}
        </p>
      ) : (
        run.error_message && (
          <p
            className="text-caption accent-vermillion mt-2 sm:mt-3 pl-4 sm:pl-[18px] break-words"
            style={{
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {run.error_message}
          </p>
        )
      )}
    </div>
  )
}
