import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { XCircle, Clock, Timer, AlertCircle } from 'lucide-react'
import type { Run, RunStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

interface RunCardProps {
  run: Run
  onClick: () => void
  onCancel?: () => void
}

const STATUS_STYLES: Record<RunStatus, { badge: string; icon: string }> = {
  pending: { badge: 'bg-yellow-500/10 text-yellow-600 border-yellow-500/20', icon: 'text-yellow-500' },
  running: { badge: 'bg-blue-500/10 text-blue-600 border-blue-500/20', icon: 'text-blue-500' },
  completed: { badge: 'bg-green-500/10 text-green-600 border-green-500/20', icon: 'text-green-500' },
  failed: { badge: 'bg-red-500/10 text-red-600 border-red-500/20', icon: 'text-red-500' },
  cancelled: { badge: 'bg-gray-500/10 text-gray-600 border-gray-500/20', icon: 'text-gray-500' },
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
  const styles = STATUS_STYLES[run.status]
  const isActive = run.status === 'running' || run.status === 'pending'

  return (
    <Card
      className={cn(
        'cursor-pointer transition-all hover:shadow-md hover:border-zinc-300 dark:hover:border-zinc-700',
        isActive && 'border-l-4 border-l-blue-500'
      )}
      onClick={onClick}
    >
      <CardHeader className="pb-2 pt-3 px-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium truncate" title={run.prompt}>
              {run.prompt.length > 60 ? `${run.prompt.slice(0, 60)}...` : run.prompt}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {run.project_name}
            </p>
          </div>
          <Badge variant="outline" className={cn('text-xs shrink-0', styles.badge)}>
            {run.status}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="pt-0 pb-3 px-3">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <div className="flex items-center gap-3">
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
          {isActive && onCancel && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 w-6 p-0 hover:bg-red-100 hover:text-red-600"
              onClick={(e) => {
                e.stopPropagation()
                onCancel()
              }}
              title="Cancel"
            >
              <XCircle className="h-4 w-4" />
            </Button>
          )}
        </div>
        {run.error_message && (
          <div className="mt-2 flex items-start gap-1 text-xs text-red-600">
            <AlertCircle className="h-3 w-3 mt-0.5 shrink-0" />
            <span className="truncate">{run.error_message}</span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
