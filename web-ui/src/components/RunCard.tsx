import { X } from 'lucide-react'
import type { Run } from '@/lib/types'
import { cn } from '@/lib/utils'

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

export function RunCard({ run, onClick, onCancel }: RunCardProps) {
  const isActive = run.status === 'running' || run.status === 'pending'

  return (
    <div className="card hover-whisper" onClick={onClick}>
      {/* Top row: mark + prompt */}
      <div className="flex items-start gap-2 sm:gap-3">
        <div className={cn('mark mt-1.5', `mark-${run.status}`)} />
        <div className="flex-1 min-w-0">
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
        </div>
      </div>

      {/* Bottom row: metadata */}
      <div className="flex items-center justify-between mt-2 sm:mt-3 pl-4 sm:pl-[18px] gap-2">
        <div className="flex items-center gap-2 sm:gap-4 flex-wrap min-w-0">
          <span className="text-mono text-[var(--color-stone)]/60 truncate max-w-[100px] sm:max-w-none">
            {run.project_name}
          </span>
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
