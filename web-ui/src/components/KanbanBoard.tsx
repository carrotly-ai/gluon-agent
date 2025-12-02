import { ScrollArea } from '@/components/ui/scroll-area'
import { RunCard } from './RunCard'
import type { Run, RunStatus } from '@/lib/types'

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

const COLUMNS: Record<RunStatus, string> = {
  pending: 'Queue',
  running: 'Active',
  completed: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

function KanbanColumn({ status, runs, label, onRunClick, onCancelRun }: KanbanColumnProps) {
  return (
    <div className="column">
      {/* Header */}
      <div className="column-header">
        <div className={`mark mark-${status}`} />
        <span className="column-title">{label}</span>
        <span className="column-count">{runs.length}</span>
      </div>

      {/* Cards with Ma spacing */}
      <ScrollArea className="flex-1">
        <div className="p-3 space-y-2">
          {runs.length === 0 ? (
            <p className="text-caption text-center py-8 opacity-40">Empty</p>
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
    <div className="flex gap-0 overflow-x-auto h-full">
      {columnOrder.map((status, i) => (
        <div key={status} className="flex">
          <KanbanColumn
            status={status}
            runs={runsByStatus[status]}
            label={COLUMNS[status]}
            onRunClick={onRunClick}
            onCancelRun={onCancelRun}
          />
          {i < columnOrder.length - 1 && (
            <div className="w-px bg-[rgba(163,163,163,0.08)]" />
          )}
        </div>
      ))}
    </div>
  )
}
