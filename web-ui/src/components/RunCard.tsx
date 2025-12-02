import { X, GitBranch } from 'lucide-react'
import type { Run } from '@/lib/types'

interface RunCardProps {
  run: Run
  onClick: () => void
  onCancel?: () => void
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return ''
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

function formatTime(dateStr: string | null): string {
  if (!dateStr) return ''
  return new Date(dateStr).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// Map status to CSS variable for border color
function getStatusBorderColor(status: string): string {
  switch (status) {
    case 'running':
      return 'var(--color-sky)'
    case 'pending':
      return 'var(--color-harvest)'
    case 'completed':
      return 'var(--color-jade)'
    case 'failed':
      return 'var(--color-vermillion)'
    case 'cancelled':
      return 'var(--color-stone)'
    default:
      return 'var(--color-stone)'
  }
}

export function RunCard({ run, onClick, onCancel }: RunCardProps) {
  const isActive = run.status === 'running' || run.status === 'pending'

  return (
    <div
      className="card hover-whisper cursor-grab active:cursor-grabbing"
      style={{ borderLeft: `3px solid ${getStatusBorderColor(run.status)}` }}
      onClick={onClick}
    >
      {/* Prompt - no mark indicator */}
      <p
        className="text-title text-[var(--color-paper)] leading-relaxed break-words"
        style={{
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden'
        }}
        title={run.prompt}
      >
        {run.prompt}
      </p>

      {/* Bottom row: metadata */}
      <div className="flex items-center justify-between mt-2 sm:mt-3 gap-2">
        <div className="flex items-center gap-2 sm:gap-4 flex-wrap min-w-0">
          <span className="text-mono text-[var(--color-stone)]/60 truncate max-w-[100px] sm:max-w-none">
            {run.project_name}
          </span>
          {run.use_worktree && (
            <span
              className="flex items-center gap-1 text-purple-400"
              title={run.branch_name || 'Worktree'}
            >
              <GitBranch className="w-3 h-3" />
              {run.branch_name && (
                <span className="text-[0.625rem] truncate max-w-[80px]">{run.branch_name}</span>
              )}
            </span>
          )}
          <span className="text-mono text-[var(--color-stone)]/55 hidden sm:inline">
            {formatTime(run.created_at)}
          </span>
          {run.duration_seconds !== null && (
            <span className="text-mono text-[var(--color-stone)]/55">
              {formatDuration(run.duration_seconds)}
            </span>
          )}
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

      {/* Error - vermillion accent */}
      {run.error_message && (
        <p
          className="text-caption accent-vermillion mt-2 sm:mt-3 pl-4 sm:pl-[18px] break-words"
          style={{
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden'
          }}
        >
          {run.error_message}
        </p>
      )}
    </div>
  )
}
