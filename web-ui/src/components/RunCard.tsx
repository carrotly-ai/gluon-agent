import {
  Archive,
  CheckCircle2,
  Circle,
  GitBranch,
  GitPullRequest,
  MessageCircleQuestion,
  RefreshCw,
  Square,
  X,
  XCircle,
  Zap,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { StatusDot, type StatusState } from '@/components/ui/StatusDot'
import { useNotificationCenter } from '@/hooks/useNotificationCenter'
import { formatFullDateTime, formatRelativeTime } from '@/lib/timestamps'
import type { CircuitState, HealthClassification, Run } from '@/lib/types'
import { cn } from '@/lib/utils'

// Short, redundant state words so card state never relies on colour alone.
const STATE_LABELS: Record<StatusState, string> = {
  pending: 'Queued',
  running: 'Running',
  completed: 'Done',
  review: 'Review',
  failed: 'Failed',
  cancelled: 'Cancelled',
  recovering: 'Recovering',
}

// Map a run's effective status to the canonical StatusDot glyph state.
function toStatusState(effectiveStatus: string, isRecovering?: boolean): StatusState {
  if (isRecovering) return 'recovering'
  switch (effectiveStatus) {
    case 'running':
      return 'running'
    case 'completed':
      return 'completed'
    case 'review':
      return 'review'
    case 'failed':
      return 'failed'
    case 'cancelled':
      return 'cancelled'
    default:
      return 'pending'
  }
}

function CiIcon({ ci, prStatus }: { ci?: string | null; prStatus?: string | null }) {
  if (prStatus === 'merged') return <GitPullRequest className="w-2.5 h-2.5" />
  if (prStatus === 'closed') return <XCircle className="w-2.5 h-2.5" />
  if (ci === 'success') return <CheckCircle2 className="w-2.5 h-2.5" />
  if (ci === 'failure') return <XCircle className="w-2.5 h-2.5" />
  if (ci === 'pending') return <Circle className="w-2.5 h-2.5 animate-pulse" />
  return <GitPullRequest className="w-2.5 h-2.5" />
}

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
  if (isRecovering) return 'var(--color-harvest)'

  switch (status) {
    case 'running':
      return 'var(--color-sky)'
    case 'pending':
      return 'var(--color-stone)'
    case 'completed':
      return 'var(--color-jade)'
    case 'review':
      return 'var(--color-orchid)' // Purple for review
    case 'failed':
      return 'var(--color-vermillion)'
    case 'cancelled':
      return 'var(--color-stone)'
    default:
      return 'var(--color-stone)'
  }
}

export function RunCard({ run, onClick, onCancel, onArchive, onStopLoop }: RunCardProps) {
  const { pendingQuestions } = useNotificationCenter()
  const hasPendingQuestions = pendingQuestions.some((q) => q.run_id === run.id)
  const isActive = run.status === 'running' || run.status === 'pending'
  const isRecovering = run.is_recovering

  // Determine status for border color
  const effectiveStatus =
    run.status === 'completed' && run.use_worktree && run.branch_name && run.pr_status !== 'merged'
      ? 'review'
      : run.status

  // Card is in "done" state - completed and not awaiting review
  const isDone = run.status === 'completed' && effectiveStatus !== 'review'

  // Canonical glyph state for the lead StatusDot.
  const cardState = toStatusState(effectiveStatus, isRecovering)
  // Redundant text label so state is never communicated by colour alone — the
  // glyph + word carry it together (the left-border stripe is now decorative).
  const stateLabel = STATE_LABELS[cardState]

  // Blocked-waiting-for-user is the single most important CTA a card can carry.
  // When active the whole card signals it and all other metadata yields.
  const needsInput = hasPendingQuestions

  // ── Metadata badges, prioritised by signal ──────────────────────────────
  // The row could otherwise carry recovering + max-turns + project + health +
  // branch + PR all at once. Establish an explicit priority and cap how many
  // render simultaneously; the rest collapse into a "+N" overflow chip. Tokyo-
  // Minimal restraint — the relative timestamp is kept separate (it's not part
  // of the competing cluster) and the colour-only health dot is dropped as soon
  // as any higher-signal badge is present.
  const MAX_BADGES = 3
  type Badge = { key: string; node: ReactNode; collapsedLabel: string }
  const badges: Badge[] = []

  if (isRecovering) {
    badges.push({
      key: 'recovering',
      collapsedLabel: 'Recovering',
      node: (
        <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-micro uppercase bg-[rgba(245,158,11,0.15)] text-amber-400">
          <RefreshCw className="w-2.5 h-2.5 animate-spin" />
          Recovering
        </span>
      ),
    })
  }
  if (run.stop_reason === 'max_turns') {
    badges.push({
      key: 'max_turns',
      collapsedLabel: 'Max Turns',
      node: (
        <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-micro uppercase bg-[rgba(245,158,11,0.15)] text-amber-400">
          Max Turns
        </span>
      ),
    })
  }
  if (run.pr_number && run.pr_url) {
    badges.push({
      key: 'pr',
      collapsedLabel: `PR #${run.pr_number}`,
      node: (
        <a
          href={run.pr_url}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            'flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-micro transition-colors',
            run.pr_status === 'merged' && 'bg-[rgba(168,85,247,0.15)] text-purple-400',
            run.pr_status === 'closed' && 'bg-[rgba(239,68,68,0.15)] text-red-400',
            run.pr_status === 'open' &&
              run.ci_status === 'success' &&
              'bg-[rgba(34,197,94,0.15)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]',
            run.pr_status === 'open' &&
              run.ci_status === 'failure' &&
              'bg-[rgba(239,68,68,0.15)] text-red-400 hover:bg-[rgba(239,68,68,0.25)]',
            run.pr_status === 'open' &&
              run.ci_status === 'pending' &&
              'bg-[rgba(234,179,8,0.15)] text-yellow-400 hover:bg-[rgba(234,179,8,0.25)]',
            run.pr_status === 'open' &&
              !run.ci_status &&
              'bg-[rgba(34,197,94,0.15)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]',
            run.pr_status === 'draft' && 'bg-[rgba(163,163,163,0.15)] text-[var(--color-stone)]'
          )}
          onClick={(e) => e.stopPropagation()}
          title={`PR #${run.pr_number}${run.ci_status ? ` · CI: ${run.ci_status}` : ''}`}
        >
          <CiIcon ci={run.ci_status} prStatus={run.pr_status} />
          <span>#{run.pr_number}</span>
        </a>
      ),
    })
  }
  if (run.use_worktree) {
    badges.push({
      key: 'branch',
      collapsedLabel: run.branch_name || 'Worktree',
      node: (
        <span
          className="flex items-center gap-1 text-purple-400"
          title={run.branch_name || 'Worktree'}
        >
          <GitBranch className="w-3 h-3" />
          {run.branch_name && (
            <span className="text-caption truncate max-w-[80px]">{run.branch_name}</span>
          )}
        </span>
      ),
    })
  }
  // Project name (only when a custom_title already leads) — secondary identity,
  // lowest badge priority.
  if (run.custom_title) {
    badges.push({
      key: 'project',
      collapsedLabel: run.project_name,
      node: (
        <span className="text-mono text-[var(--color-stone)]/60 truncate max-w-[100px] sm:max-w-none">
          {run.project_name}
        </span>
      ),
    })
  }
  // Health dot — colour-only, weakest signal: keep only when it would otherwise
  // be the sole badge on a running card.
  const showHealthDot =
    !!run.health_classification && run.status === 'running' && badges.length === 0
  if (showHealthDot && run.health_classification) {
    badges.push({
      key: 'health',
      collapsedLabel: `Health: ${run.health_classification}`,
      node: (
        <span
          className={cn(
            'w-2 h-2 rounded-full shrink-0',
            getHealthDotColor(run.health_classification)
          )}
          title={`Health: ${run.health_classification}`}
        />
      ),
    })
  }

  const visibleBadges = badges.slice(0, MAX_BADGES)
  const collapsedBadges = badges.slice(MAX_BADGES)

  return (
    <div
      className={cn(
        'card hover-whisper cursor-grab active:cursor-grabbing group relative',
        run.status === 'running' && !isRecovering && 'card-running overflow-visible',
        isRecovering && 'card-recovering overflow-visible',
        needsInput && 'overflow-visible'
      )}
      style={{
        borderLeft: needsInput
          ? '3px solid var(--color-sky)'
          : `3px solid ${getStatusBorderColor(effectiveStatus, isRecovering)}`,
      }}
      onClick={onClick}
    >
      {/* Needs-input: pulsing sky ring around the whole card. Reuses the
          index.css `breathe` keyframe (no new CSS) on an inset overlay so only
          the border glows — the card content stays legible. */}
      {needsInput && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -inset-px rounded-[inherit] ring-2 ring-[var(--color-sky)] animate-[breathe_2s_ease-in-out_infinite]"
        />
      )}
      {/* Shimmer stripe overlay for recovering cards */}
      {isRecovering && (
        <div
          className="stripe-shimmer absolute top-0 bottom-0 w-[3px]"
          style={{ backgroundColor: 'var(--color-harvest)', left: '-3px' }}
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

      {/* Lead: status glyph + identity. Prefer a derived custom_title as the
          lead line; otherwise the project name (highest-variance field). The
          prompt is demoted to a single-line secondary preview below. */}
      <div className={cn('min-w-0', !isActive && onArchive && 'pr-7')}>
        {/* Eyebrow: glyph + redundant state word. State is conveyed by glyph +
            label together, never by the stripe colour alone. */}
        <StatusDot state={cardState} size="md" label={stateLabel} className="min-w-0" />
        {run.custom_title ? (
          <p
            className="text-title text-[var(--color-paper)] leading-snug break-words line-clamp-1 mt-1"
            title={run.custom_title}
          >
            {run.custom_title}
          </p>
        ) : (
          <p className="text-mono text-[var(--color-paper)] leading-snug truncate mt-1">
            {run.project_name}
          </p>
        )}
        <p
          className="text-caption text-[var(--color-stone)]/55 leading-snug truncate"
          title={run.prompt}
        >
          {run.prompt}
        </p>
      </div>

      {/* Needs-input affordance: the single most important CTA a card carries.
          Full-width, sky-toned, breathing glyph — replaces the old 9px pill. */}
      {needsInput && (
        <div className="mt-2 sm:mt-3 flex items-center gap-2 rounded-sm bg-[var(--color-sky)]/[0.12] px-2.5 py-1.5">
          <MessageCircleQuestion className="w-3.5 h-3.5 text-[var(--color-sky)] shrink-0 animate-[breathe_2s_ease-in-out_infinite]" />
          <span className="text-caption font-medium text-[var(--color-sky)]">Needs your input</span>
        </div>
      )}

      {/* Bottom row: metadata. When the card needs input, all secondary
          metadata yields (dims) so the sky CTA above is unmistakably dominant. */}
      <div className="flex items-center justify-between mt-2 sm:mt-3 gap-2">
        <div
          className={cn(
            'flex items-center gap-2 sm:gap-4 flex-wrap min-w-0',
            needsInput && 'opacity-40'
          )}
        >
          {/* Prioritised badges (cap MAX_BADGES); overflow collapses to "+N". */}
          {visibleBadges.map((b) => (
            <span key={b.key} className="contents">
              {b.node}
            </span>
          ))}
          {collapsedBadges.length > 0 && (
            <span
              className="px-1.5 py-0.5 rounded-sm text-micro uppercase bg-[rgba(163,163,163,0.12)] text-[var(--color-stone)]/70 shrink-0"
              title={collapsedBadges.map((b) => b.collapsedLabel).join(' · ')}
            >
              +{collapsedBadges.length}
            </span>
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
                  'flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-micro uppercase',
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

      {/* Chain Step Progress - show only while actively running */}
      {run.chain_id && isActive && (
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

      {/* Completed formula summary - show when not actively running */}
      {run.chain_id && !isActive && (
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
