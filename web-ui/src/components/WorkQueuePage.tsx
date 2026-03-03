import { Plus, RefreshCw, RotateCcw, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { addToQueue, cancelQueueItem, fetchWorkQueue, releaseQueueItem } from '@/lib/api'
import { formatRelativeTime } from '@/lib/timestamps'
import type { Project, WorkQueueItem, WorkQueueStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

function statusBadge(status: WorkQueueStatus) {
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

interface WorkQueuePageProps {
  projects: Project[]
}

export function WorkQueuePage({ projects }: WorkQueuePageProps) {
  const [items, setItems] = useState<WorkQueueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [projectFilter, setProjectFilter] = useState<string>('')
  const [showAddDialog, setShowAddDialog] = useState(false)

  // Add dialog state
  const [addProjectId, setAddProjectId] = useState('')
  const [addPrompt, setAddPrompt] = useState('')
  const [addPriority, setAddPriority] = useState(10)
  const [adding, setAdding] = useState(false)

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
    const interval = setInterval(load, 10000)
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

  const handleAdd = async () => {
    if (!addProjectId || !addPrompt.trim()) return
    setAdding(true)
    try {
      const item = await addToQueue({
        project_id: addProjectId,
        prompt: addPrompt.trim(),
        priority: addPriority,
      })
      setItems((prev) => [item, ...prev])
      setShowAddDialog(false)
      setAddPrompt('')
      setAddPriority(10)
    } catch (err) {
      console.error('Failed to add to queue:', err)
    } finally {
      setAdding(false)
    }
  }

  // Unique projects in queue for filter
  const queueProjects = [...new Set(items.map((i) => i.project_id))].sort()

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="border-b border-[rgba(163,163,163,0.1)] px-4 sm:px-6 py-3 flex items-center justify-between gap-3 shrink-0">
        <h2 className="text-body font-normal tracking-wide text-[var(--color-paper)]">
          Work Queue
        </h2>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Project filter */}
          <select
            value={projectFilter}
            onChange={(e) => setProjectFilter(e.target.value)}
            className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1 text-[var(--color-stone)]"
          >
            <option value="">All Projects</option>
            {queueProjects.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>

          {/* Status filter */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1 text-[var(--color-stone)]"
          >
            <option value="">All Statuses</option>
            <option value="pending">Pending</option>
            <option value="claimed">Claimed</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>

          <button
            onClick={load}
            className="p-1.5 rounded-sm hover:bg-[rgba(163,163,163,0.1)] transition-colors text-[var(--color-stone)]"
            title="Refresh"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
          </button>

          <button
            onClick={() => setShowAddDialog(true)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-caption uppercase tracking-widest text-[var(--color-void)] bg-[var(--color-paper)] hover:opacity-90 transition-colors rounded-sm"
          >
            <Plus className="w-3 h-3" />
            <span>Add Task</span>
          </button>
        </div>
      </div>

      {/* Add Dialog */}
      {showAddDialog && (
        <div className="border-b border-[rgba(163,163,163,0.1)] px-4 sm:px-6 py-3 bg-[rgba(163,163,163,0.03)]">
          <div className="flex flex-col gap-2 max-w-xl">
            <select
              value={addProjectId}
              onChange={(e) => setAddProjectId(e.target.value)}
              className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1.5 text-[var(--color-stone)]"
            >
              <option value="">Select project...</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <textarea
              value={addPrompt}
              onChange={(e) => setAddPrompt(e.target.value)}
              placeholder="Task prompt..."
              rows={2}
              className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1.5 text-[var(--color-paper)] placeholder:text-[var(--color-stone)]/40 resize-none"
            />
            <div className="flex items-center gap-3">
              <label className="text-caption text-[var(--color-stone)]/60">
                Priority: {addPriority}
              </label>
              <input
                type="range"
                min={1}
                max={20}
                value={addPriority}
                onChange={(e) => setAddPriority(Number(e.target.value))}
                className="flex-1 max-w-32"
              />
              <button
                onClick={handleAdd}
                disabled={adding || !addProjectId || !addPrompt.trim()}
                className="px-3 py-1 text-caption uppercase tracking-widest text-[var(--color-void)] bg-[var(--color-paper)] hover:opacity-90 disabled:opacity-40 transition-colors rounded-sm"
              >
                {adding ? 'Adding...' : 'Add'}
              </button>
              <button
                onClick={() => setShowAddDialog(false)}
                className="p-1 text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {loading && items.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <div className="mark mark-running w-2 h-2" />
          </div>
        ) : items.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <p className="text-caption text-[var(--color-stone)]/60">Work queue is empty</p>
          </div>
        ) : (
          <table className="w-full text-caption">
            <thead className="sticky top-0 bg-[var(--color-ink)] z-10">
              <tr className="border-b border-[rgba(163,163,163,0.1)]">
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-12">
                  Pri
                </th>
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-28">
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
                <th className="text-right px-4 py-2 text-[var(--color-stone)]/60 font-normal w-20">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.id}
                  className="border-b border-[rgba(163,163,163,0.05)] hover:bg-[rgba(163,163,163,0.03)]"
                >
                  <td className="px-4 py-2 text-[var(--color-stone)] font-mono text-center">
                    {item.priority}
                  </td>
                  <td className="px-4 py-2 text-[var(--color-stone)] truncate max-w-28">
                    {item.project_id}
                  </td>
                  <td className="px-4 py-2 text-[var(--color-paper)] truncate max-w-xs">
                    {item.prompt}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={cn(
                        'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-[10px] uppercase tracking-wider',
                        statusBadge(item.status)
                      )}
                    >
                      {item.status === 'running' && (
                        <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
                      )}
                      {item.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-[var(--color-stone)]/60 whitespace-nowrap">
                    {formatRelativeTime(item.created_at)}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex items-center justify-end gap-1">
                      {(item.status === 'pending' || item.status === 'claimed') && (
                        <button
                          onClick={() => handleCancel(item.id)}
                          className="p-1 text-[var(--color-stone)]/40 hover:text-[var(--color-vermillion)] transition-colors"
                          title="Cancel"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      )}
                      {item.status === 'claimed' && (
                        <button
                          onClick={() => handleRelease(item.id)}
                          className="p-1 text-[var(--color-stone)]/40 hover:text-[var(--color-harvest)] transition-colors"
                          title="Release back to pending"
                        >
                          <RotateCcw className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
