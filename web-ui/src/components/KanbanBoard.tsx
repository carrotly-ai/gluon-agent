import { ScrollArea } from '@/components/ui/scroll-area'
import { RunCard } from './RunCard'
import type { Run, RunStatus } from '@/lib/types'
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
  onRunClick: (run: Run) => void
  onCancelRun: (run: Run) => void
}

const COLUMNS: Record<RunStatus, { label: string; dot: string }> = {
  pending: { label: 'Queued', dot: 'bg-yellow-500' },
  running: { label: 'Running', dot: 'bg-blue-500' },
  completed: { label: 'Completed', dot: 'bg-emerald-500' },
  failed: { label: 'Failed', dot: 'bg-red-500' },
  cancelled: { label: 'Cancelled', dot: 'bg-zinc-500' },
}

function KanbanColumn({ status, runs, label, onRunClick, onCancelRun }: KanbanColumnProps) {
  const config = COLUMNS[status]

  return (
    <div className="flex flex-col w-72 shrink-0 bg-zinc-900/50 rounded-lg border border-zinc-800">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800">
        <div className={cn('w-2 h-2 rounded-full', config.dot)} />
        <span className="text-xs font-medium text-zinc-300">{label}</span>
        <span className="ml-auto text-xs text-zinc-600">{runs.length}</span>
      </div>

      {/* Cards */}
      <ScrollArea className="flex-1 max-h-[calc(100vh-8rem)]">
        <div className="p-2 space-y-2">
          {runs.length === 0 ? (
            <p className="text-xs text-zinc-600 text-center py-6">No runs</p>
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
  const runsByStatus = runs.reduce<Record<RunStatus, Run[]>>(
    (acc, run) => {
      acc[run.status].push(run)
      return acc
    },
    { pending: [], running: [], completed: [], failed: [], cancelled: [] }
  )

  Object.values(runsByStatus).forEach((columnRuns) => {
    columnRuns.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
  })

  const columnOrder: RunStatus[] = ['pending', 'running', 'completed', 'failed', 'cancelled']

  return (
    <div className="flex gap-3 overflow-x-auto p-4 h-full">
      {columnOrder.map((status) => (
        <KanbanColumn
          key={status}
          status={status}
          runs={runsByStatus[status]}
          label={COLUMNS[status].label}
          onRunClick={onRunClick}
          onCancelRun={onCancelRun}
        />
      ))}
    </div>
  )
}
