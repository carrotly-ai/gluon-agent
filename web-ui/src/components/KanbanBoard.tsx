import { useState, useCallback } from 'react'
import {
  DndContext,
  DragOverlay,
  closestCorners,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragStartEvent,
  type DragEndEvent,
  type DragOverEvent,
} from '@dnd-kit/core'
import { useSortable } from '@dnd-kit/sortable'
import { useDroppable } from '@dnd-kit/core'
import { CSS } from '@dnd-kit/utilities'
import { ScrollArea } from '@/components/ui/scroll-area'
import { RunCard } from './RunCard'
import type { Run, RunStatus, KanbanColumn } from '@/lib/types'
import { isTransitionAllowed } from '@/lib/types'
import { updateRunStatus } from '@/lib/api'
import { cn } from '@/lib/utils'

interface KanbanBoardProps {
  runs: Run[]
  onRunClick: (run: Run) => void
  onCancelRun: (run: Run) => void
  onArchiveRun: (run: Run) => void
  onRunUpdate?: (run: Run) => void
}

// Column configuration with "review" as virtual column
const COLUMNS: { status: KanbanColumn; label: string }[] = [
  { status: 'pending', label: 'Queue' },
  { status: 'running', label: 'Active' },
  { status: 'review', label: 'Review' },
  { status: 'completed', label: 'Done' },
  { status: 'failed', label: 'Failed' },
  { status: 'cancelled', label: 'Cancelled' },
]

// Draggable RunCard wrapper
interface DraggableRunCardProps {
  run: Run
  onClick: () => void
  onCancel?: () => void
  onArchive?: () => void
  isDragging?: boolean
}

function DraggableRunCard({ run, onClick, onCancel, onArchive, isDragging }: DraggableRunCardProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
  } = useSortable({ id: run.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
    >
      <RunCard run={run} onClick={onClick} onCancel={onCancel} onArchive={onArchive} />
    </div>
  )
}

// Droppable column wrapper
interface DroppableColumnProps {
  status: KanbanColumn
  runs: Run[]
  label: string
  onRunClick: (run: Run) => void
  onCancelRun: (run: Run) => void
  onArchiveRun: (run: Run) => void
  isOver?: boolean
  canDrop?: boolean
  activeRun?: Run | null
}

function DroppableColumn({
  status,
  runs,
  label,
  onRunClick,
  onCancelRun,
  onArchiveRun,
  isOver,
  canDrop,
  activeRun,
}: DroppableColumnProps) {
  const { setNodeRef } = useDroppable({ id: status })

  // Determine drop visual state
  const showDropIndicator = activeRun && isOver
  const isValidDrop = activeRun && canDrop

  return (
    <div
      ref={setNodeRef}
      className={cn(
        'column transition-all duration-200',
        showDropIndicator && isValidDrop && 'ring-2 ring-[var(--color-jade)]/50 bg-[var(--color-jade)]/5',
        showDropIndicator && !isValidDrop && 'ring-2 ring-[var(--color-vermillion)]/30 bg-[var(--color-vermillion)]/5'
      )}
    >
      {/* Header - hidden on mobile */}
      <div className="column-header hidden md:flex">
        <div className={cn('mark', status === 'review' ? 'mark-review' : `mark-${status}`)} />
        <span className="column-title">{label}</span>
        <span className="column-count">{runs.length}</span>
      </div>

      {/* Cards */}
      <ScrollArea className="flex-1">
        <div className="p-2 sm:p-3 space-y-2">
          {runs.length === 0 ? (
            <p className="text-caption text-center py-8 opacity-40">
              {status === 'review' ? 'No PRs pending review' : 'Empty'}
            </p>
          ) : (
            runs.map((run) => (
              <DraggableRunCard
                key={run.id}
                run={run}
                onClick={() => onRunClick(run)}
                onCancel={
                  status === 'running' || status === 'pending'
                    ? () => onCancelRun(run)
                    : undefined
                }
                onArchive={
                  status !== 'running' && status !== 'pending'
                    ? () => onArchiveRun(run)
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

export function KanbanBoard({ runs, onRunClick, onCancelRun, onArchiveRun, onRunUpdate }: KanbanBoardProps) {
  const [activeTab, setActiveTab] = useState<KanbanColumn>('running')
  const [activeRun, setActiveRun] = useState<Run | null>(null)
  const [overId, setOverId] = useState<string | null>(null)

  // DnD sensors - pointer (mouse) and keyboard
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 8 },
    }),
    useSensor(KeyboardSensor)
  )

  // Group runs by column status (review is a virtual column)
  const runsByColumn = runs.reduce<Record<KanbanColumn, Run[]>>(
    (acc, run) => {
      // "Review" column: completed runs with open PRs
      if (run.status === 'completed' && run.pr_status === 'open') {
        acc['review'].push(run)
      } else {
        // Use the actual status column
        const col = run.status as KanbanColumn
        if (col in acc) {
          acc[col].push(run)
        }
      }
      return acc
    },
    { pending: [], running: [], review: [], completed: [], failed: [], cancelled: [] }
  )

  // Sort each column by created_at descending
  Object.values(runsByColumn).forEach((columnRuns) => {
    columnRuns.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
  })

  // DnD handlers
  const handleDragStart = useCallback((event: DragStartEvent) => {
    const run = runs.find(r => r.id === event.active.id)
    setActiveRun(run || null)
  }, [runs])

  const handleDragOver = useCallback((event: DragOverEvent) => {
    setOverId(event.over?.id.toString() || null)
  }, [])

  const handleDragEnd = useCallback(async (event: DragEndEvent) => {
    const { active, over } = event
    setActiveRun(null)
    setOverId(null)

    if (!over) return

    const runId = active.id as string
    const targetColumn = over.id as KanbanColumn
    const run = runs.find(r => r.id === runId)

    if (!run) return

    // Review column is virtual - can't drop directly into it
    if (targetColumn === 'review') return

    // Check if the status is actually changing
    if (run.status === targetColumn) return

    // Validate transition
    if (!isTransitionAllowed(run.status, targetColumn)) {
      console.warn(`Transition from ${run.status} to ${targetColumn} not allowed`)
      return
    }

    try {
      const response = await updateRunStatus(runId, targetColumn)
      onRunUpdate?.(response.run)
    } catch (err) {
      console.error('Failed to update run status:', err)
    }
  }, [runs, onRunUpdate])

  // Check if dropping on a column is valid
  const canDropOnColumn = useCallback((column: KanbanColumn) => {
    if (!activeRun) return false
    if (column === 'review') return false // Virtual column
    return isTransitionAllowed(activeRun.status, column as RunStatus)
  }, [activeRun])

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCorners}
      onDragStart={handleDragStart}
      onDragOver={handleDragOver}
      onDragEnd={handleDragEnd}
    >
      <div className="kanban-container">
        {/* Mobile: Tab navigation */}
        <div className="kanban-tabs md:hidden">
          {COLUMNS.map(({ status, label }) => (
            <button
              key={status}
              className={cn('kanban-tab', activeTab === status && 'active')}
              onClick={() => setActiveTab(status)}
            >
              <span className={cn('mark inline-block mr-2', status === 'review' ? 'mark-review' : `mark-${status}`)} />
              {label}
              <span className="ml-1 opacity-50">({runsByColumn[status].length})</span>
            </button>
          ))}
        </div>

        {/* Mobile: Single column view (no DnD on mobile) */}
        <div className="kanban-column-mobile md:hidden">
          <DroppableColumn
            status={activeTab}
            runs={runsByColumn[activeTab]}
            label={COLUMNS.find(c => c.status === activeTab)?.label || ''}
            onRunClick={onRunClick}
            onCancelRun={onCancelRun}
            onArchiveRun={onArchiveRun}
          />
        </div>

        {/* Desktop: Horizontal columns with DnD */}
        <div className="kanban-columns">
          {COLUMNS.map(({ status, label }, i) => (
            <div key={status} className="flex">
              <DroppableColumn
                status={status}
                runs={runsByColumn[status]}
                label={label}
                onRunClick={onRunClick}
                onCancelRun={onCancelRun}
                onArchiveRun={onArchiveRun}
                isOver={overId === status}
                canDrop={canDropOnColumn(status)}
                activeRun={activeRun}
              />
              {i < COLUMNS.length - 1 && <div className="kanban-divider" />}
            </div>
          ))}
        </div>
      </div>

      {/* Drag overlay */}
      <DragOverlay>
        {activeRun && (
          <div className="opacity-90 shadow-lg rounded-lg">
            <RunCard
              run={activeRun}
              onClick={() => {}}
            />
          </div>
        )}
      </DragOverlay>
    </DndContext>
  )
}
