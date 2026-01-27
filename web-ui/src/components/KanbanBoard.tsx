import {
  closestCorners,
  DndContext,
  type DragEndEvent,
  type DragOverEvent,
  DragOverlay,
  type DragStartEvent,
  KeyboardSensor,
  PointerSensor,
  useDroppable,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import React, { useCallback, useState } from 'react'
import { PullToRefresh } from '@/components/PullToRefresh'
import { ScrollArea } from '@/components/ui/scroll-area'
import { updateRunStatus } from '@/lib/api'
import type { KanbanColumn, Run, RunStatus } from '@/lib/types'
import { isTransitionAllowed } from '@/lib/types'
import { cn } from '@/lib/utils'
import { RunCard } from './RunCard'

interface KanbanBoardProps {
  runs: Run[]
  onRunClick: (run: Run) => void
  onCancelRun: (run: Run) => void
  onArchiveRun: (run: Run) => void
  onStopLoop: (run: Run) => void
  onRunUpdate?: (run: Run) => void
  onRefresh?: () => Promise<void>
}

// Column configuration
// alwaysVisible: true = always show, false = only show if has cards
const COLUMNS: { status: KanbanColumn; label: string; alwaysVisible: boolean }[] = [
  { status: 'pending', label: 'Queue', alwaysVisible: false },
  { status: 'running', label: 'Active', alwaysVisible: true },
  { status: 'review', label: 'Review', alwaysVisible: true },
  { status: 'completed', label: 'Done', alwaysVisible: true },
  { status: 'failed', label: 'Failed', alwaysVisible: false },
  { status: 'cancelled', label: 'Cancelled', alwaysVisible: false },
]

// Draggable RunCard wrapper
interface DraggableRunCardProps {
  run: Run
  onClick: () => void
  onCancel?: () => void
  onArchive?: () => void
  onStopLoop?: () => void
  isDragging?: boolean
}

function DraggableRunCard({
  run,
  onClick,
  onCancel,
  onArchive,
  onStopLoop,
  isDragging,
}: DraggableRunCardProps) {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id: run.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <div ref={setNodeRef} style={style} {...attributes} {...listeners}>
      <RunCard
        run={run}
        onClick={onClick}
        onCancel={onCancel}
        onArchive={onArchive}
        onStopLoop={onStopLoop}
      />
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
  onStopLoop: (run: Run) => void
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
  onStopLoop,
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
        showDropIndicator &&
          isValidDrop &&
          'ring-2 ring-[var(--color-jade)]/50 bg-[var(--color-jade)]/5',
        showDropIndicator &&
          !isValidDrop &&
          'ring-2 ring-[var(--color-vermillion)]/30 bg-[var(--color-vermillion)]/5'
      )}
    >
      {/* Header - hidden on mobile */}
      <div className="column-header hidden md:flex">
        <div className={cn('mark', status === 'review' ? 'mark-review' : `mark-${status}`)} />
        <span className="column-title">{label}</span>
        <span className="column-count">{runs.length}</span>
      </div>

      {/* Cards - extra right padding ensures hover buttons aren't clipped by ScrollArea */}
      <ScrollArea className="flex-1">
        <div className="p-2 sm:p-3 sm:pr-8 space-y-2">
          {runs.length === 0 ? (
            <p className="text-caption text-center py-8 opacity-40">
              {status === 'review' ? 'No tasks awaiting review' : 'Empty'}
            </p>
          ) : (
            runs.map((run) => (
              <DraggableRunCard
                key={run.id}
                run={run}
                onClick={() => onRunClick(run)}
                onCancel={
                  status === 'running' || status === 'pending' || status === 'review'
                    ? () => onCancelRun(run)
                    : undefined
                }
                onArchive={
                  status !== 'running' && status !== 'pending' ? () => onArchiveRun(run) : undefined
                }
                onStopLoop={
                  run.ralph_enabled && status === 'running' ? () => onStopLoop(run) : undefined
                }
              />
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

export function KanbanBoard({
  runs,
  onRunClick,
  onCancelRun,
  onArchiveRun,
  onStopLoop,
  onRunUpdate,
  onRefresh,
}: KanbanBoardProps) {
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

  // Group runs by column status (review is now a real backend state)
  const runsByColumn = runs.reduce<Record<KanbanColumn, Run[]>>(
    (acc, run) => {
      const col = run.status as KanbanColumn
      if (col in acc) {
        acc[col].push(run)
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
  const handleDragStart = useCallback(
    (event: DragStartEvent) => {
      const run = runs.find((r) => r.id === event.active.id)
      setActiveRun(run || null)
    },
    [runs]
  )

  const handleDragOver = useCallback((event: DragOverEvent) => {
    setOverId(event.over?.id.toString() || null)
  }, [])

  const handleDragEnd = useCallback(
    async (event: DragEndEvent) => {
      const { active, over } = event
      setActiveRun(null)
      setOverId(null)

      if (!over) return

      const runId = active.id as string
      const run = runs.find((r) => r.id === runId)

      if (!run) return

      // Determine target column - over.id could be a column status OR a run ID
      // (when dropping on a card, dnd-kit returns that card's ID)
      const validColumns: KanbanColumn[] = [
        'pending',
        'running',
        'review',
        'completed',
        'failed',
        'cancelled',
      ]
      let targetColumn: KanbanColumn

      if (validColumns.includes(over.id as KanbanColumn)) {
        // Dropped directly on a column
        targetColumn = over.id as KanbanColumn
      } else {
        // Dropped on a card - find which column that card belongs to
        const targetRun = runs.find((r) => r.id === over.id)
        if (!targetRun) return
        targetColumn = targetRun.status as KanbanColumn
      }

      // Review state is entered automatically - no manual transitions TO review allowed
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
    },
    [runs, onRunUpdate]
  )

  // Check if dropping on a column is valid
  const canDropOnColumn = useCallback(
    (column: KanbanColumn) => {
      if (!activeRun) return false
      if (column === 'review') return false // Review is entered automatically, not via drag-drop
      return isTransitionAllowed(activeRun.status, column as RunStatus)
    },
    [activeRun]
  )

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
          {COLUMNS.filter(
            ({ status, alwaysVisible }) => alwaysVisible || runsByColumn[status].length > 0
          ).map(({ status, label }) => (
            <button
              key={status}
              className={cn('kanban-tab', activeTab === status && 'active')}
              onClick={() => setActiveTab(status)}
            >
              <span
                className={cn(
                  'mark inline-block mr-2',
                  status === 'review' ? 'mark-review' : `mark-${status}`
                )}
              />
              {label}
              <span className="ml-1 opacity-50">({runsByColumn[status].length})</span>
            </button>
          ))}
        </div>

        {/* Mobile: Single column view with pull-to-refresh (no DnD on mobile) */}
        <div className="kanban-column-mobile md:hidden">
          <PullToRefresh
            onRefresh={onRefresh || (async () => {})}
            disabled={!onRefresh || !!activeRun}
          >
            <DroppableColumn
              status={activeTab}
              runs={runsByColumn[activeTab]}
              label={COLUMNS.find((c) => c.status === activeTab)?.label || ''}
              onRunClick={onRunClick}
              onCancelRun={onCancelRun}
              onArchiveRun={onArchiveRun}
              onStopLoop={onStopLoop}
            />
          </PullToRefresh>
        </div>

        {/* Desktop: Horizontal columns with DnD */}
        <div className="kanban-columns">
          {COLUMNS.filter(
            ({ status, alwaysVisible }) => alwaysVisible || runsByColumn[status].length > 0
          ).map(({ status, label }, i, filteredCols) => (
            <React.Fragment key={status}>
              <DroppableColumn
                status={status}
                runs={runsByColumn[status]}
                label={label}
                onRunClick={onRunClick}
                onCancelRun={onCancelRun}
                onArchiveRun={onArchiveRun}
                onStopLoop={onStopLoop}
                isOver={overId === status}
                canDrop={canDropOnColumn(status)}
                activeRun={activeRun}
              />
              {i < filteredCols.length - 1 && <div className="kanban-divider" />}
            </React.Fragment>
          ))}
        </div>
      </div>

      {/* Drag overlay */}
      <DragOverlay>
        {activeRun && (
          <div className="opacity-90 shadow-lg rounded-lg">
            <RunCard run={activeRun} onClick={() => {}} />
          </div>
        )}
      </DragOverlay>
    </DndContext>
  )
}
