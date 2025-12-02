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

const COLUMN_CONFIG: Record<RunStatus, { label: string; color: string; glow?: string }> = {
  pending: { label: 'QUEUED', color: 'bg-[#ffbe0b]', glow: 'shadow-[0_0_10px_rgba(255,190,11,0.3)]' },
  running: { label: 'ACTIVE', color: 'bg-[#00f5ff]', glow: 'shadow-[0_0_10px_rgba(0,245,255,0.3)]' },
  completed: { label: 'COMPLETED', color: 'bg-[#39ff14]' },
  failed: { label: 'FAILED', color: 'bg-[#ff3366]' },
  cancelled: { label: 'ABORTED', color: 'bg-[#6b7280]' },
}

function KanbanColumn({ status, runs, label, onRunClick, onCancelRun }: KanbanColumnProps) {
  const config = COLUMN_CONFIG[status]
  const isActiveColumn = status === 'running' || status === 'pending'

  return (
    <div className={cn(
      'kanban-column flex flex-col',
      isActiveColumn && config.glow
    )}>
      {/* Column header */}
      <div className="kanban-column-header">
        <div className={cn(
          'w-3 h-3 rounded-full',
          config.color,
          status === 'running' && 'animate-pulse'
        )} />
        <h3 className="kanban-column-title" style={{ color: status === 'running' ? '#00f5ff' : status === 'pending' ? '#ffbe0b' : '#888' }}>
          {label}
        </h3>
        <span className="kanban-column-count ml-auto">
          {runs.length}
        </span>
      </div>

      {/* Cards */}
      <ScrollArea className="flex-1">
        <div className="p-3 space-y-3">
          {runs.length === 0 ? (
            <div className="text-center py-8">
              <p className="font-mono text-xs text-[#444]">NO MISSIONS</p>
            </div>
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
    <div className="flex gap-4 overflow-x-auto p-6 min-h-[calc(100vh-5rem)]">
      {columnOrder.map((status) => (
        <KanbanColumn
          key={status}
          status={status}
          runs={runsByStatus[status]}
          label={COLUMN_CONFIG[status].label}
          onRunClick={onRunClick}
          onCancelRun={onCancelRun}
        />
      ))}
    </div>
  )
}
