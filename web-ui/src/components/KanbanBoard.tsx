import { useState } from 'react'
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

const COLUMNS: { status: RunStatus; label: string }[] = [
  { status: 'pending', label: 'Queue' },
  { status: 'running', label: 'Active' },
  { status: 'completed', label: 'Done' },
  { status: 'failed', label: 'Failed' },
  { status: 'cancelled', label: 'Cancelled' },
]

function KanbanColumn({ status, runs, label, onRunClick, onCancelRun }: KanbanColumnProps) {
  return (
    <div className="column">
      {/* Header - hidden on mobile (we use tabs instead) */}
      <div className="column-header hidden md:flex">
        <div className={`mark mark-${status}`} />
        <span className="column-title">{label}</span>
        <span className="column-count">{runs.length}</span>
      </div>

      {/* Cards */}
      <ScrollArea className="flex-1">
        <div className="p-2 sm:p-3 space-y-2">
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
  const [activeTab, setActiveTab] = useState<RunStatus>('running')

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

  return (
    <div className="kanban-container">
      {/* Mobile: Tab navigation */}
      <div className="kanban-tabs md:hidden">
        {COLUMNS.map(({ status, label }) => (
          <button
            key={status}
            className={cn('kanban-tab', activeTab === status && 'active')}
            onClick={() => setActiveTab(status)}
          >
            <span className={`mark mark-${status} inline-block mr-2`} />
            {label}
            <span className="ml-1 opacity-50">({runsByStatus[status].length})</span>
          </button>
        ))}
      </div>

      {/* Mobile: Single column view */}
      <div className="kanban-column-mobile md:hidden">
        <KanbanColumn
          status={activeTab}
          runs={runsByStatus[activeTab]}
          label={COLUMNS.find(c => c.status === activeTab)?.label || ''}
          onRunClick={onRunClick}
          onCancelRun={onCancelRun}
        />
      </div>

      {/* Desktop: Horizontal columns */}
      <div className="kanban-columns">
        {COLUMNS.map(({ status, label }, i) => (
          <div key={status} className="flex">
            <KanbanColumn
              status={status}
              runs={runsByStatus[status]}
              label={label}
              onRunClick={onRunClick}
              onCancelRun={onCancelRun}
            />
            {i < COLUMNS.length - 1 && <div className="kanban-divider" />}
          </div>
        ))}
      </div>
    </div>
  )
}
