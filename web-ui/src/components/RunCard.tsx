import { XCircle, Clock, Timer, AlertTriangle, CheckCircle2, Ban, Loader2 } from 'lucide-react'
import type { Run, RunStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

interface RunCardProps {
  run: Run
  onClick: () => void
  onCancel?: () => void
}

const STATUS_CONFIG: Record<RunStatus, {
  color: string
  textColor: string
  icon: React.ElementType
  label: string
}> = {
  pending: {
    color: '#ffbe0b',
    textColor: 'text-[#ffbe0b]',
    icon: Clock,
    label: 'QUEUED'
  },
  running: {
    color: '#00f5ff',
    textColor: 'text-[#00f5ff]',
    icon: Loader2,
    label: 'ACTIVE'
  },
  completed: {
    color: '#39ff14',
    textColor: 'text-[#39ff14]',
    icon: CheckCircle2,
    label: 'COMPLETE'
  },
  failed: {
    color: '#ff3366',
    textColor: 'text-[#ff3366]',
    icon: AlertTriangle,
    label: 'FAILED'
  },
  cancelled: {
    color: '#6b7280',
    textColor: 'text-[#6b7280]',
    icon: Ban,
    label: 'ABORTED'
  },
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

function formatTime(dateStr: string | null): string {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function RunCard({ run, onClick, onCancel }: RunCardProps) {
  const config = STATUS_CONFIG[run.status]
  const StatusIcon = config.icon
  const isActive = run.status === 'running' || run.status === 'pending'

  return (
    <div
      className={cn(
        'mission-card cursor-pointer',
        `status-${run.status}`
      )}
      onClick={onClick}
    >
      {/* Card header */}
      <div className="p-3 pb-2">
        <div className="flex items-start justify-between gap-2">
          {/* Status indicator + prompt */}
          <div className="flex items-start gap-2 flex-1 min-w-0">
            <div className={cn('status-indicator shrink-0 mt-1', `status-${run.status}`)} />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-[#e4e4e7] truncate" title={run.prompt}>
                {run.prompt.length > 50 ? `${run.prompt.slice(0, 50)}...` : run.prompt}
              </p>
              <p className="font-mono text-xs text-[#666] mt-0.5">
                {run.project_name}
              </p>
            </div>
          </div>

          {/* Status badge */}
          <div className={cn(
            'flex items-center gap-1.5 px-2 py-1 rounded text-xs font-mono shrink-0',
            'bg-[#1a1a24] border border-[#2a2a3a]',
            config.textColor
          )}>
            <StatusIcon className={cn('w-3 h-3', run.status === 'running' && 'animate-spin')} />
            <span>{config.label}</span>
          </div>
        </div>
      </div>

      {/* Card footer */}
      <div className="px-3 pb-3 pt-1 border-t border-[#1f1f2a]">
        <div className="flex items-center justify-between">
          {/* Metadata */}
          <div className="flex items-center gap-3 text-xs font-mono text-[#666]">
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {formatTime(run.created_at)}
            </span>
            {run.duration_seconds !== null && (
              <span className="flex items-center gap-1">
                <Timer className="h-3 w-3" />
                {formatDuration(run.duration_seconds)}
              </span>
            )}
          </div>

          {/* Cancel button */}
          {isActive && onCancel && (
            <button
              className="mission-button danger !px-2 !py-1 text-xs"
              onClick={(e) => {
                e.stopPropagation()
                onCancel()
              }}
              title="Abort mission"
            >
              <XCircle className="h-3 w-3" />
            </button>
          )}
        </div>

        {/* Error message */}
        {run.error_message && (
          <div className="mt-2 flex items-start gap-1.5 text-xs text-[#ff3366] bg-[#ff3366]/10 px-2 py-1.5 rounded">
            <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
            <span className="truncate">{run.error_message}</span>
          </div>
        )}
      </div>
    </div>
  )
}
