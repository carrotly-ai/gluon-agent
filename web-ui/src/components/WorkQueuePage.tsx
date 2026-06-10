import { Check, ClipboardList, Pencil, Plus, RotateCcw, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { addToQueue, cancelQueueItem, fetchWorkQueue, releaseQueueItem } from '@/lib/api'
import { POLL_NORMAL } from '@/lib/polling'
import { formatRelativeTime } from '@/lib/timestamps'
import type { Project, WorkQueueItem, WorkQueueStatus } from '@/lib/types'
import { cn } from '@/lib/utils'
import { DataPage } from './ui/DataPage'
import { FilterBar } from './ui/FilterBar'
import { PageHeader } from './ui/PageHeader'
import { StatusDot } from './ui/StatusDot'

function statusBadge(status: WorkQueueStatus): string {
  const styles: Record<WorkQueueStatus, string> = {
    pending: 'bg-[var(--color-harvest)]/15 text-[var(--color-harvest)]',
    claimed: 'bg-[var(--color-sky)]/15 text-[var(--color-sky)]',
    running: 'bg-[var(--color-sky)]/15 text-[var(--color-sky)]',
    completed: 'bg-[var(--color-jade)]/15 text-[var(--color-jade)]',
    failed: 'bg-[var(--color-vermillion)]/15 text-[var(--color-vermillion)]',
    cancelled: 'bg-[var(--color-stone)]/10 text-[var(--color-stone)]/60',
  }
  return styles[status] || styles.pending
}

// Priority buckets — named so users have calibration. Numeric value persisted.
type PriorityBucket = { value: number; label: string }
const PRIORITY_BUCKETS: PriorityBucket[] = [
  { value: 5, label: 'Low' },
  { value: 10, label: 'Normal' },
  { value: 15, label: 'High' },
  { value: 20, label: 'Urgent' },
]

function priorityLabel(value: number): string {
  // Round to nearest bucket.
  const bucket = PRIORITY_BUCKETS.reduce((best, b) =>
    Math.abs(b.value - value) < Math.abs(best.value - value) ? b : best
  )
  return bucket.label
}

interface WorkQueuePageProps {
  projects: Project[]
}

interface EditingDraft {
  /** Existing item id if we're editing, undefined if we're creating. */
  id?: string
  projectId: string
  prompt: string
  priority: number
}

export function WorkQueuePage({ projects }: WorkQueuePageProps) {
  const [items, setItems] = useState<WorkQueueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [projectFilter, setProjectFilter] = useState<string>('')
  const [search, setSearch] = useState('')
  const [draft, setDraft] = useState<EditingDraft | null>(null)
  const [adding, setAdding] = useState(false)

  const projectsById = useMemo(() => {
    const m = new Map<string, Project>()
    for (const p of projects) m.set(p.id, p)
    return m
  }, [projects])

  const resolveProjectName = useCallback(
    (id: string): string => projectsById.get(id)?.name ?? `${id.slice(0, 8)}…`,
    [projectsById]
  )

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchWorkQueue({
        project_id: projectFilter || undefined,
        status: statusFilter || undefined,
        limit: 100,
      })
      setItems(data.items)
    } catch (err) {
      console.error('Failed to load work queue:', err)
    } finally {
      setLoading(false)
    }
  }, [statusFilter, projectFilter])

  useEffect(() => {
    load()
    const interval = setInterval(load, POLL_NORMAL)
    return () => clearInterval(interval)
  }, [load])

  const handleCancel = async (itemId: string) => {
    try {
      const updated = await cancelQueueItem(itemId)
      setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)))
    } catch (err) {
      console.error('Failed to cancel item:', err)
    }
  }

  const handleRelease = async (itemId: string) => {
    try {
      const updated = await releaseQueueItem(itemId)
      setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)))
    } catch (err) {
      console.error('Failed to release item:', err)
    }
  }

  const openCreate = () => setDraft({ projectId: '', prompt: '', priority: 10 })

  const openEdit = (item: WorkQueueItem) => {
    // Only allow editing pending items — once claimed/running the queue worker
    // has it. The UI signals this by gating the edit button on status, but
    // we still defensively reject here.
    if (item.status !== 'pending') return
    setDraft({
      id: item.id,
      projectId: item.project_id,
      prompt: item.prompt,
      priority: item.priority,
    })
  }

  const closeDraft = () => setDraft(null)

  const submitDraft = async () => {
    if (!draft) return
    if (!draft.projectId || !draft.prompt.trim()) return
    setAdding(true)
    try {
      // Edit flow: we don't have a PATCH endpoint, so an edit creates a new
      // item and removes the old one. Create FIRST — only cancel the original
      // after the replacement succeeds, so a failed re-create never destroys
      // the existing item.
      // TODO(backend): add PATCH /work-queue/{id} so edits don't burn an id.
      const item = await addToQueue({
        project_id: draft.projectId,
        prompt: draft.prompt.trim(),
        priority: draft.priority,
      })
      if (draft.id) {
        try {
          await cancelQueueItem(draft.id)
        } catch (cancelErr) {
          // The replacement exists; failing to remove the old item is not fatal.
          console.error('Failed to remove the old queue item after edit:', cancelErr)
        }
      }
      setItems((prev) => {
        const filtered = draft.id ? prev.filter((i) => i.id !== draft.id) : prev
        return [item, ...filtered]
      })
      setDraft(null)
    } catch (err) {
      console.error('Failed to save queue item:', err)
      toast.error(err instanceof Error ? err.message : 'Failed to save queue item')
    } finally {
      setAdding(false)
    }
  }

  // Unique projects in queue for filter
  const queueProjectIds = [...new Set(items.map((i) => i.project_id))].sort()

  const filtered = items.filter((i) => {
    if (!search) return true
    const q = search.toLowerCase()
    return (
      i.prompt.toLowerCase().includes(q) ||
      resolveProjectName(i.project_id).toLowerCase().includes(q)
    )
  })

  const isEmpty = !loading && filtered.length === 0

  return (
    <DataPage>
      <PageHeader
        title="Work Queue"
        icon={ClipboardList}
        count={filtered.length}
        countLabel="item"
        actions={
          <button
            type="button"
            onClick={openCreate}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-caption uppercase tracking-widest text-[var(--color-void)] bg-[var(--color-paper)] hover:opacity-90 transition-colors rounded-sm"
          >
            <Plus className="w-3 h-3" />
            Add task
          </button>
        }
      />

      <FilterBar
        filters={
          <>
            <select
              value={projectFilter}
              onChange={(e) => setProjectFilter(e.target.value)}
              className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1 text-[var(--color-stone)]"
              aria-label="Filter by project"
            >
              <option value="">All projects</option>
              {queueProjectIds.map((id) => (
                <option key={id} value={id}>
                  {resolveProjectName(id)}
                </option>
              ))}
            </select>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1 text-[var(--color-stone)]"
              aria-label="Filter by status"
            >
              <option value="">All statuses</option>
              <option value="pending">Pending</option>
              <option value="claimed">Claimed</option>
              <option value="running">Running</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
            </select>
          </>
        }
        search={{
          value: search,
          onChange: setSearch,
          placeholder: 'Search prompt / project…',
          ariaLabel: 'Search work queue',
        }}
        refresh={load}
        refreshing={loading}
      />

      {/* Add/Edit Dialog */}
      {draft && (
        <DraftPanel
          draft={draft}
          setDraft={setDraft}
          projects={projects}
          adding={adding}
          onSubmit={submitDraft}
          onCancel={closeDraft}
        />
      )}

      <DataPage.Body>
        {loading && items.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <div className="mark mark-running w-2 h-2" />
          </div>
        ) : isEmpty ? (
          <WorkQueueEmptyState
            searching={search.length > 0 || statusFilter !== '' || projectFilter !== ''}
            onCreate={openCreate}
          />
        ) : (
          <table className="w-full text-caption">
            <thead className="sticky top-0 bg-[var(--color-ink)] z-10">
              <tr className="border-b border-[rgba(163,163,163,0.1)]">
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-16">
                  Pri
                </th>
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-40">
                  Project
                </th>
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal">
                  Prompt
                </th>
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-24">
                  Status
                </th>
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-20">
                  Age
                </th>
                <th className="text-right px-4 py-2 text-[var(--color-stone)]/60 font-normal w-24">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => {
                const isEditable = item.status === 'pending'
                return (
                  <tr
                    key={item.id}
                    className={cn(
                      'border-b border-[rgba(163,163,163,0.05)] hover:bg-[rgba(163,163,163,0.03)] group transition-colors',
                      isEditable && 'cursor-pointer'
                    )}
                    onClick={isEditable ? () => openEdit(item) : undefined}
                  >
                    <td className="px-4 py-2 text-[var(--color-stone)]">
                      <span
                        className="text-caption uppercase tracking-wider"
                        title={`Priority value: ${item.priority}`}
                      >
                        {priorityLabel(item.priority)}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-[var(--color-stone)] truncate max-w-40">
                      {resolveProjectName(item.project_id)}
                    </td>
                    <td className="px-4 py-2 text-[var(--color-paper)] truncate max-w-xs">
                      {item.prompt}
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className={cn(
                          'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-micro uppercase tracking-wider',
                          statusBadge(item.status)
                        )}
                      >
                        {item.status === 'running' && <StatusDot state="running" size="sm" />}
                        {item.status}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-[var(--color-stone)]/60 whitespace-nowrap">
                      {formatRelativeTime(item.created_at)}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {isEditable && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation()
                              openEdit(item)
                            }}
                            className="p-1 text-[var(--color-stone)]/40 hover:text-[var(--color-paper)] transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                            aria-label="Edit item"
                            title="Edit"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {(item.status === 'pending' || item.status === 'claimed') && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation()
                              void handleCancel(item.id)
                            }}
                            className="p-1 text-[var(--color-stone)]/40 hover:text-[var(--color-vermillion)] transition-colors"
                            aria-label="Cancel item"
                            title="Cancel"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {item.status === 'claimed' && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation()
                              void handleRelease(item.id)
                            }}
                            className="p-1 text-[var(--color-stone)]/40 hover:text-[var(--color-harvest)] transition-colors"
                            aria-label="Release item to pending"
                            title="Release back to pending"
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </DataPage.Body>
    </DataPage>
  )
}

function DraftPanel({
  draft,
  setDraft,
  projects,
  adding,
  onSubmit,
  onCancel,
}: {
  draft: EditingDraft
  setDraft: (next: EditingDraft) => void
  projects: Project[]
  adding: boolean
  onSubmit: () => void
  onCancel: () => void
}) {
  const isEdit = draft.id !== undefined
  return (
    <div className="border-b border-[rgba(163,163,163,0.1)] px-4 sm:px-6 py-3 bg-[rgba(163,163,163,0.03)] shrink-0">
      <div className="flex items-center justify-between mb-2">
        <p className="text-caption uppercase tracking-widest text-[var(--color-stone)]/70">
          {isEdit ? 'Edit task' : 'New task'}
        </p>
        <button
          type="button"
          onClick={onCancel}
          className="p-1 text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]"
          aria-label="Close"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="flex flex-col gap-2 max-w-xl">
        <select
          value={draft.projectId}
          onChange={(e) => setDraft({ ...draft, projectId: e.target.value })}
          className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1.5 text-[var(--color-stone)]"
          aria-label="Project"
        >
          <option value="">Select project…</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <textarea
          value={draft.prompt}
          onChange={(e) => setDraft({ ...draft, prompt: e.target.value })}
          placeholder="Task prompt…"
          rows={2}
          className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1.5 text-[var(--color-paper)] placeholder:text-[var(--color-stone)]/40 resize-none focus:outline-none focus:border-[var(--color-stone)]/40"
        />
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-caption text-[var(--color-stone)]/60">Priority</span>
          <div className="flex items-center gap-0.5 bg-[rgba(163,163,163,0.06)] rounded-sm p-0.5">
            {PRIORITY_BUCKETS.map((b) => {
              const active = draft.priority === b.value
              return (
                <button
                  key={b.value}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setDraft({ ...draft, priority: b.value })}
                  className={cn(
                    'px-2 py-0.5 text-caption uppercase tracking-widest rounded-sm transition-colors',
                    active
                      ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                      : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                  )}
                  title={`Priority value ${b.value}`}
                >
                  {b.label} <span className="text-[var(--color-stone)]/40">({b.value})</span>
                </button>
              )
            })}
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={onSubmit}
              disabled={adding || !draft.projectId || !draft.prompt.trim()}
              className="px-3 py-1 text-caption uppercase tracking-widest text-[var(--color-void)] bg-[var(--color-paper)] hover:opacity-90 disabled:opacity-40 transition-colors rounded-sm"
            >
              {adding ? 'Saving…' : isEdit ? 'Save' : 'Add'}
            </button>
            <button
              type="button"
              onClick={onCancel}
              className="px-3 py-1 text-caption uppercase tracking-widest text-[var(--color-stone)] hover:text-[var(--color-paper)] border border-[rgba(163,163,163,0.15)] rounded-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function WorkQueueEmptyState({
  searching,
  onCreate,
}: {
  searching: boolean
  onCreate: () => void
}) {
  if (searching) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6 gap-3 max-w-md mx-auto">
        <ClipboardList className="w-8 h-8 text-[var(--color-stone)]/30" />
        <div>
          <p className="text-display text-[var(--color-paper)] mb-1">No matching tasks</p>
          <p className="text-body text-[var(--color-stone)]/60">
            Try clearing filters. Search runs over prompt text and project name.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-6 gap-3 max-w-md mx-auto">
      <ClipboardList className="w-8 h-8 text-[var(--color-stone)]/30" />
      <div>
        <p className="text-display text-[var(--color-paper)] mb-1">Nothing queued yet</p>
        <p className="text-body text-[var(--color-stone)]/60">
          Queue items are processed in priority order when agent capacity is free. Add a task and a
          worker will pick it up the moment one of your projects has a slot.
        </p>
      </div>
      <button
        type="button"
        onClick={onCreate}
        className="flex items-center gap-1.5 px-3 py-1.5 text-caption uppercase tracking-widest text-[var(--color-void)] bg-[var(--color-paper)] hover:opacity-90 rounded-sm"
      >
        <Plus className="w-3 h-3" />
        Add your first task
      </button>
      <ul className="text-caption text-[var(--color-stone)]/50 text-left mt-2 flex flex-col gap-1">
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Named priority buckets — Low,
          Normal, High, Urgent
        </li>
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Click any pending row to edit
          prompt or re-prioritise
        </li>
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Cancel pending or claimed items;
          release stuck claims
        </li>
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Auto-refreshes every 10s — no
          page reload needed
        </li>
      </ul>
    </div>
  )
}
