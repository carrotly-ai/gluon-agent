import { ScrollArea } from '@/components/ui/scroll-area'
import { RunCard } from './RunCard'
import type { Run, RunStatus } from '@/lib/types'
import { KANBAN_COLUMNS } from '@/lib/types'
import { cn } from '@/lib/utils'

interface KanbanBoardProps {
  runs: Run[]
  onRunClick: (run: Run) => void
  onCancelRun: (run: Run) => void
}

interface KanbanColumnProps {
  status: RunStatus
  runs: Run[]
  label: string
  color: string
  onRunClick: (run: Run) => void
  onCancelRun: (run: Run) => void
}

function KanbanColumn({ status, runs, label, color, onRunClick, onCancelRun }: KanbanColumnProps) {
  return (
    <div className="flex flex-col min-w-[280px] max-w-[320px] bg-zinc-100 dark:bg-zinc-900 rounded-lg">
      {/* Column header */}
      <div className="flex items-center gap-2 p-3 border-b border-zinc-200 dark:border-zinc-800">
        <div className={cn('w-2 h-2 rounded-full', color)} />
        <h3 className="font-medium text-sm">{label}</h3>
        <span className="ml-auto text-xs text-muted-foreground bg-zinc-200 dark:bg-zinc-800 px-2 py-0.5 rounded-full">
          {runs.length}
        </span>
      </div>

      {/* Cards */}
      <ScrollArea className="flex-1 p-2">
        <div className="space-y-2">
          {runs.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-4">No runs</p>
          ) : (
            runs.map((run) => (
              <RunCard
                key={run.id}
                run={run}
                onClick={() => onRunClick(run)}
                onCancel={
                  status === 'running' || status === 'pending'
                    ? () => onCancelRun(run)
                    : undefined
                }
              />
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

export function KanbanBoard({ runs, onRunClick, onCancelRun }: KanbanBoardProps) {
  // Group runs by status
  const runsByStatus = runs.reduce<Record<RunStatus, Run[]>>(
    (acc, run) => {
      acc[run.status].push(run)
      return acc
    },
    {
      pending: [],
      running: [],
      completed: [],
      failed: [],
      cancelled: [],
    }
  )

  // Sort runs within each column by created_at (newest first)
  Object.values(runsByStatus).forEach((columnRuns) => {
    columnRuns.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
  })

  // Column order: active statuses first, then completed states
  const columnOrder: RunStatus[] = ['pending', 'running', 'completed', 'failed', 'cancelled']

  return (
    <div className="flex gap-4 overflow-x-auto p-4 min-h-[calc(100vh-8rem)]">
      {columnOrder.map((status) => (
        <KanbanColumn
          key={status}
          status={status}
          runs={runsByStatus[status]}
          label={KANBAN_COLUMNS[status].label}
          color={KANBAN_COLUMNS[status].color}
          onRunClick={onRunClick}
          onCancelRun={onCancelRun}
        />
      ))}
    </div>
  )
}
