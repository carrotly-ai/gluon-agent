import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { X, Clock, Timer, AlertCircle } from 'lucide-react'
import type { Run, RunStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

interface RunCardProps {
  run: Run
  onClick: () => void
  onCancel?: () => void
}

const STATUS_DOT: Record<RunStatus, string> = {
  pending: 'bg-yellow-500',
  running: 'bg-blue-500',
  completed: 'bg-emerald-500',
  failed: 'bg-red-500',
  cancelled: 'bg-zinc-500',
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

function formatTime(dateStr: string | null): string {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function RunCard({ run, onClick, onCancel }: RunCardProps) {
  const isActive = run.status === 'running' || run.status === 'pending'

  return (
    <Card
      className="p-3 cursor-pointer hover:bg-zinc-800/50 transition-colors border-zinc-800"
      onClick={onClick}
    >
      {/* Header */}
      <div className="flex items-start gap-2 mb-2">
        <div className={cn('w-2 h-2 rounded-full mt-1.5 shrink-0', STATUS_DOT[run.status])}>
          {run.status === 'running' && (
            <span className="block w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm text-zinc-200 truncate" title={run.prompt}>
            {run.prompt.length > 50 ? `${run.prompt.slice(0, 50)}...` : run.prompt}
          </p>
          <p className="text-xs text-zinc-500 mt-0.5">{run.project_name}</p>
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between text-xs text-zinc-500">
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatTime(run.created_at)}
          </span>
          {run.duration_seconds !== null && (
            <span className="flex items-center gap-1">
              <Timer className="w-3 h-3" />
              {formatDuration(run.duration_seconds)}
            </span>
          )}
        </div>
        {isActive && onCancel && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-zinc-500 hover:text-red-400 hover:bg-red-400/10"
            onClick={(e) => {
              e.stopPropagation()
              onCancel()
            }}
          >
            <X className="w-3 h-3" />
          </Button>
        )}
      </div>

      {/* Error */}
      {run.error_message && (
        <div className="mt-2 flex items-start gap-1.5 text-xs text-red-400">
          <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" />
          <span className="truncate">{run.error_message}</span>
        </div>
      )}
    </Card>
  )
}
