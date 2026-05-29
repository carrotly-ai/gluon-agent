import {
  CalendarClock,
  CheckCircle2,
  Clock,
  GitBranch,
  Pause,
  Pencil,
  Play,
  Plus,
  Rocket,
  Trash2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  deleteSchedule,
  disableSchedule,
  enableSchedule,
  fetchProjects,
  fetchSchedules,
  fireScheduleNow,
} from '@/lib/api'
import { formatRelativeTime } from '@/lib/timestamps'
import type { Project, TaskSchedule } from '@/lib/types'
import { cn } from '@/lib/utils'
import { ScheduleEditorDialog } from './ScheduleEditorDialog'
import { DataPage } from './ui/DataPage'
import { PageHeader } from './ui/PageHeader'

/**
 * Dedicated page for browsing and managing user-defined recurring tasks.
 *
 * Layout: a single table with name / project / recurrence summary / next fire /
 * last fire / enabled toggle / row actions. Edit / Create open the
 * ``ScheduleEditorDialog`` modal. Spawned runs land in the regular list view —
 * they're not shown here, but a "View runs" deep link will be added when that
 * gets a dedicated drilldown.
 */
export function SchedulesPage() {
  const [schedules, setSchedules] = useState<TaskSchedule[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<TaskSchedule | null>(null)
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set())

  const reload = useCallback(async () => {
    try {
      const [list, projs] = await Promise.all([fetchSchedules(), fetchProjects()])
      setSchedules(list.schedules)
      setProjects(projs)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load schedules')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const markBusy = (id: string, busy: boolean) =>
    setBusyIds((prev) => {
      const next = new Set(prev)
      busy ? next.add(id) : next.delete(id)
      return next
    })

  const handleToggleEnabled = async (s: TaskSchedule) => {
    markBusy(s.id, true)
    try {
      const updated = s.is_enabled ? await disableSchedule(s.id) : await enableSchedule(s.id)
      setSchedules((prev) => prev.map((x) => (x.id === updated.id ? updated : x)))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to toggle')
    } finally {
      markBusy(s.id, false)
    }
  }

  const handleFireNow = async (s: TaskSchedule) => {
    markBusy(s.id, true)
    try {
      const run = await fireScheduleNow(s.id)
      toast.success('Fired', {
        description: `Spawned run ${run.id.slice(0, 8)}…`,
        action: {
          label: 'View',
          onClick: () => {
            window.location.href = '/list'
          },
        },
      })
      await reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to fire')
    } finally {
      markBusy(s.id, false)
    }
  }

  const handleDelete = async (s: TaskSchedule) => {
    if (!window.confirm(`Delete schedule "${s.name}"? Spawned runs will not be deleted.`)) return
    markBusy(s.id, true)
    try {
      await deleteSchedule(s.id)
      setSchedules((prev) => prev.filter((x) => x.id !== s.id))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to delete')
    } finally {
      markBusy(s.id, false)
    }
  }

  const handleSaved = (saved: TaskSchedule) => {
    setSchedules((prev) => {
      if (prev.some((x) => x.id === saved.id))
        return prev.map((x) => (x.id === saved.id ? saved : x))
      return [saved, ...prev]
    })
  }

  // Sort: enabled first, then by next_fire_at ascending (sooner = higher priority).
  const sorted = useMemo(() => {
    return [...schedules].sort((a, b) => {
      if (a.is_enabled !== b.is_enabled) return a.is_enabled ? -1 : 1
      const an = a.next_fire_at ? new Date(a.next_fire_at).getTime() : Infinity
      const bn = b.next_fire_at ? new Date(b.next_fire_at).getTime() : Infinity
      return an - bn
    })
  }, [schedules])

  return (
    <DataPage>
      <PageHeader
        title="Schedules"
        icon={CalendarClock}
        count={sorted.length}
        countLabel="schedule"
        actions={
          <button
            type="button"
            className="flex items-center gap-1.5 px-3 py-1.5 text-caption uppercase tracking-widest text-[var(--color-void)] bg-[var(--color-paper)] hover:opacity-90 rounded-sm"
            onClick={() => {
              setEditing(null)
              setEditorOpen(true)
            }}
          >
            <Plus className="w-3 h-3" />
            New schedule
          </button>
        }
      />

      <DataPage.Body>
        {loading && (
          <div className="flex items-center justify-center h-full">
            <div className="mark mark-running w-2 h-2" />
          </div>
        )}
        {!loading && error && (
          <div className="flex items-center justify-center h-full px-4">
            <p className="text-caption accent-vermillion text-center">{error}</p>
          </div>
        )}
        {!loading && !error && sorted.length === 0 && (
          <EmptyState
            onCreate={() => {
              setEditing(null)
              setEditorOpen(true)
            }}
          />
        )}
        {!loading && !error && sorted.length > 0 && (
          <table className="w-full text-body">
            <thead className="sticky top-0 bg-[var(--color-void)] border-b border-[rgba(163,163,163,0.1)]">
              <tr className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60">
                <th className="text-left px-4 py-2">Name</th>
                <th className="text-left px-4 py-2">When</th>
                <th className="text-left px-4 py-2">Project</th>
                <th className="text-left px-4 py-2">Next fire</th>
                <th className="text-left px-4 py-2">Last fire</th>
                <th className="text-left px-4 py-2">Runs</th>
                <th className="text-right px-4 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((s) => (
                <tr
                  key={s.id}
                  className={cn(
                    'border-b border-[rgba(163,163,163,0.05)] hover:bg-[var(--color-paper)]/3 transition-colors',
                    !s.is_enabled && 'opacity-60'
                  )}
                >
                  <td className="px-4 py-2.5">
                    <div className="flex flex-col">
                      <button
                        type="button"
                        className="text-left text-[var(--color-paper)] hover:underline truncate max-w-[220px]"
                        onClick={() => {
                          setEditing(s)
                          setEditorOpen(true)
                        }}
                        title={s.name}
                      >
                        {s.name}
                      </button>
                      {s.use_worktree && (
                        <span className="flex items-center gap-1 text-caption text-purple-400/70 mt-0.5">
                          <GitBranch className="w-2.5 h-2.5" />
                          Worktree
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="text-[var(--color-paper)]">{s.summary}</span>
                    <p className="text-caption text-[var(--color-stone)]/40 font-mono mt-0.5">
                      {s.schedule_cron}
                    </p>
                  </td>
                  <td className="px-4 py-2.5 text-[var(--color-stone)]">{s.project_name}</td>
                  <td className="px-4 py-2.5 text-[var(--color-stone)]">
                    {s.next_fire_at ? (
                      <span title={new Date(s.next_fire_at).toLocaleString()}>
                        {formatRelativeTime(s.next_fire_at)}
                      </span>
                    ) : (
                      <span className="text-[var(--color-stone)]/40">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-[var(--color-stone)]">
                    {s.last_fired_at ? (
                      <span title={new Date(s.last_fired_at).toLocaleString()}>
                        {formatRelativeTime(s.last_fired_at)}
                      </span>
                    ) : (
                      <span className="text-[var(--color-stone)]/40">never</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="tabular-nums text-[var(--color-stone)]">{s.run_count}</span>
                    {s.active_run_count > 0 && (
                      <span className="ml-1 text-caption text-[var(--color-sky)]">
                        ({s.active_run_count} active)
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      <IconButton
                        title={s.is_enabled ? 'Disable (pause)' : 'Enable'}
                        onClick={() => void handleToggleEnabled(s)}
                        disabled={busyIds.has(s.id)}
                      >
                        {s.is_enabled ? (
                          <Pause className="w-3 h-3" />
                        ) : (
                          <Play className="w-3 h-3" />
                        )}
                      </IconButton>
                      <IconButton
                        title="Fire now"
                        onClick={() => void handleFireNow(s)}
                        disabled={busyIds.has(s.id)}
                        accent
                      >
                        <Rocket className="w-3 h-3" />
                      </IconButton>
                      <IconButton
                        title="Edit"
                        onClick={() => {
                          setEditing(s)
                          setEditorOpen(true)
                        }}
                      >
                        <Pencil className="w-3 h-3" />
                      </IconButton>
                      <IconButton
                        title="Delete"
                        onClick={() => void handleDelete(s)}
                        disabled={busyIds.has(s.id)}
                        danger
                      >
                        <Trash2 className="w-3 h-3" />
                      </IconButton>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </DataPage.Body>

      <ScheduleEditorDialog
        open={editorOpen}
        schedule={editing}
        projects={projects}
        onClose={() => setEditorOpen(false)}
        onSaved={handleSaved}
      />
    </DataPage>
  )
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-6 gap-3 max-w-md mx-auto">
      <CalendarClock className="w-8 h-8 text-[var(--color-stone)]/30" />
      <div>
        <p className="text-display text-[var(--color-paper)] mb-1">No schedules yet</p>
        <p className="text-body text-[var(--color-stone)]/60">
          Schedule a recurring task and it will spawn runs in the list view automatically. Great for
          morning audits, hourly polling, or weekly cleanups.
        </p>
      </div>
      <button
        type="button"
        className="flex items-center gap-1.5 px-3 py-1.5 text-caption uppercase tracking-widest text-[var(--color-void)] bg-[var(--color-paper)] hover:opacity-90 rounded-sm"
        onClick={onCreate}
      >
        <Plus className="w-3 h-3" />
        Create your first schedule
      </button>
      <ul className="text-caption text-[var(--color-stone)]/50 text-left mt-2 flex flex-col gap-1">
        <li className="flex items-center gap-2">
          <CheckCircle2 className="w-3 h-3 text-[var(--color-jade)]/60" /> Local timezone (defaults
          to your browser)
        </li>
        <li className="flex items-center gap-2">
          <CheckCircle2 className="w-3 h-3 text-[var(--color-jade)]/60" /> Per-schedule concurrency
          policy (skip / cancel & restart / overlap)
        </li>
        <li className="flex items-center gap-2">
          <CheckCircle2 className="w-3 h-3 text-[var(--color-jade)]/60" /> Friendly checkboxes — or
          drop into raw cron when you need it
        </li>
        <li className="flex items-center gap-2">
          <Clock className="w-3 h-3 text-[var(--color-stone)]/60" /> Spawned runs appear in the
          regular List view with a chip linking back here
        </li>
      </ul>
    </div>
  )
}

function IconButton({
  children,
  title,
  onClick,
  disabled,
  accent,
  danger,
}: {
  children: React.ReactNode
  title: string
  onClick: () => void
  disabled?: boolean
  accent?: boolean
  danger?: boolean
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'p-1.5 rounded-sm transition-colors',
        disabled && 'opacity-30 cursor-not-allowed',
        !disabled && accent && 'text-[var(--color-sky)] hover:bg-[rgba(102,178,255,0.1)]',
        !disabled &&
          danger &&
          'text-[var(--color-stone)]/40 hover:text-[var(--color-vermillion)] hover:bg-[var(--color-vermillion)]/10',
        !disabled &&
          !accent &&
          !danger &&
          'text-[var(--color-stone)]/50 hover:text-[var(--color-paper)] hover:bg-[var(--color-paper)]/5'
      )}
    >
      {children}
    </button>
  )
}
