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
      <div className="flex items-start gap-3">
        <div className={cn('mark mt-1.5 shrink-0', `mark-${run.status}`)} />
        <div className="flex-1 min-w-0">
          <p className="text-title text-[#fafaf9] truncate leading-relaxed" title={run.prompt}>
            {run.prompt.length > 45 ? `${run.prompt.slice(0, 45)}...` : run.prompt}
          </p>
        </div>
      </div>

      {/* Bottom row: metadata */}
      <div className="flex items-center justify-between mt-3 pl-[18px]">
        <div className="flex items-center gap-4">
          <span className="text-mono text-[#a3a3a3]/60">{run.project_name}</span>
          <span className="text-mono text-[#a3a3a3]/40">{formatTime(run.created_at)}</span>
          {run.duration_seconds !== null && (
            <span className="text-mono text-[#a3a3a3]/40">{formatDuration(run.duration_seconds)}</span>
          )}
        </div>

        {isActive && onCancel && (
          <button
            className="p-1 text-[#a3a3a3]/40 hover:text-[#c73e3a] transition-colors"
            onClick={(e) => {
              e.stopPropagation()
              onCancel()
            }}
          >
            <X className="w-3 h-3" />
          </button>
        )}
      </div>

      {/* Error - vermillion accent */}
      {run.error_message && (
        <p className="text-caption accent-vermillion mt-3 pl-[18px] truncate">
          {run.error_message}
        </p>
      )}
    </div>
  )
}
