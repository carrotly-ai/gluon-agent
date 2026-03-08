import { RefreshCw, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { cleanupActivity, fetchActivity } from '@/lib/api'
import { formatRelativeTime } from '@/lib/timestamps'
import type { ActivityEvent } from '@/lib/types'
import { cn } from '@/lib/utils'

const TIME_RANGES = [
  { label: 'Last 1h', hours: 1 },
  { label: 'Last 24h', hours: 24 },
  { label: 'Last 7d', hours: 168 },
  { label: 'All', hours: 0 },
] as const

function actionBadgeColor(action: string): string {
  if (action.includes('completed') || action.includes('success') || action.includes('merged'))
    return 'bg-[var(--color-jade)]/15 text-[var(--color-jade)]'
  if (action.includes('failed') || action.includes('error') || action.includes('conflict'))
    return 'bg-[var(--color-vermillion)]/15 text-[var(--color-vermillion)]'
  if (action.includes('started') || action.includes('running') || action.includes('claimed'))
    return 'bg-[var(--color-sky)]/15 text-[var(--color-sky)]'
  if (action.includes('alert') || action.includes('stuck') || action.includes('looping'))
    return 'bg-[var(--color-harvest)]/15 text-[var(--color-harvest)]'
  return 'bg-[var(--color-stone)]/10 text-[var(--color-stone)]'
}

export function ActivityPage() {
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [actionFilter, setActionFilter] = useState<string>('')
  const [actorFilter, setActorFilter] = useState<string>('')
  const [timeRange, setTimeRange] = useState(24) // hours, 0 = all
  const [cleaning, setCleaning] = useState(false)
  const [cleanupMessage, setCleanupMessage] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const since =
        timeRange > 0 ? new Date(Date.now() - timeRange * 60 * 60 * 1000).toISOString() : undefined
      const data = await fetchActivity({
        actor: actorFilter || undefined,
        action: actionFilter || undefined,
        since,
        limit: 200,
      })
      setEvents(data.events)
    } catch (err) {
      console.error('Failed to load activity:', err)
    } finally {
      setLoading(false)
    }
  }, [actionFilter, actorFilter, timeRange])

  useEffect(() => {
    load()
    const interval = setInterval(load, 10000)
    return () => clearInterval(interval)
  }, [load])

  // Derive unique actions and actors for filter dropdowns
  const uniqueActions = [...new Set(events.map((e) => e.action))].sort()
  const uniqueActors = [...new Set(events.map((e) => e.actor))].sort()

  const handleCleanup = async () => {
    setCleaning(true)
    setCleanupMessage(null)
    try {
      const result = await cleanupActivity(90)
      if (result.deleted > 0) {
        setCleanupMessage(`Deleted ${result.deleted} old events`)
        load()
      } else {
        setCleanupMessage('No events older than 90 days')
      }
      setTimeout(() => setCleanupMessage(null), 3000)
    } catch (err) {
      console.error('Failed to cleanup activity:', err)
    } finally {
      setCleaning(false)
    }
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="border-b border-[rgba(163,163,163,0.1)] px-4 sm:px-6 py-3 flex items-center justify-between gap-3 shrink-0">
        <h2 className="text-body font-normal tracking-wide text-[var(--color-paper)]">Activity</h2>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Action filter */}
          <select
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1 text-[var(--color-stone)]"
          >
            <option value="">All Actions</option>
            {uniqueActions.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>

          {/* Actor filter */}
          <select
            value={actorFilter}
            onChange={(e) => setActorFilter(e.target.value)}
            className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1 text-[var(--color-stone)]"
          >
            <option value="">All Actors</option>
            {uniqueActors.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>

          {/* Time range */}
          <div className="flex items-center gap-0.5 bg-[rgba(163,163,163,0.06)] rounded-sm p-0.5">
            {TIME_RANGES.map(({ label, hours }) => (
              <button
                key={hours}
                className={cn(
                  'px-2 py-0.5 text-caption rounded-sm transition-colors',
                  timeRange === hours
                    ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                    : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                )}
                onClick={() => setTimeRange(hours)}
              >
                {label}
              </button>
            ))}
          </div>

          <button
            onClick={load}
            className="p-1.5 rounded-sm hover:bg-[rgba(163,163,163,0.1)] transition-colors text-[var(--color-stone)]"
            title="Refresh"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
          </button>

          <button
            onClick={handleCleanup}
            disabled={cleaning}
            className="flex items-center gap-1 px-2 py-1 text-caption text-[var(--color-stone)]/60 hover:text-[var(--color-vermillion)] transition-colors"
            title="Cleanup events older than 90 days"
          >
            <Trash2 className="w-3 h-3" />
            <span className="hidden sm:inline">Cleanup</span>
          </button>
          {cleanupMessage && (
            <span className="text-caption text-[var(--color-stone)]/50">{cleanupMessage}</span>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {loading && events.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <div className="mark mark-running w-2 h-2" />
          </div>
        ) : events.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <p className="text-caption text-[var(--color-stone)]/60">No activity events found</p>
          </div>
        ) : (
          <table className="w-full text-caption">
            <thead className="sticky top-0 bg-[var(--color-ink)] z-10">
              <tr className="border-b border-[rgba(163,163,163,0.1)]">
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-24">
                  Time
                </th>
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-32">
                  Actor
                </th>
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-40">
                  Action
                </th>
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-20">
                  Result
                </th>
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal">
                  Message
                </th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr
                  key={event.id}
                  className="border-b border-[rgba(163,163,163,0.05)] hover:bg-[rgba(163,163,163,0.03)]"
                >
                  <td className="px-4 py-2 text-[var(--color-stone)]/60 whitespace-nowrap">
                    {formatRelativeTime(event.timestamp)}
                  </td>
                  <td className="px-4 py-2 text-[var(--color-stone)] font-mono text-[11px] truncate max-w-32">
                    {event.actor}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={cn(
                        'inline-block px-1.5 py-0.5 rounded-sm text-[10px] uppercase tracking-wider',
                        actionBadgeColor(event.action)
                      )}
                    >
                      {event.action}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-[var(--color-stone)]/60">{event.result ?? '—'}</td>
                  <td className="px-4 py-2 text-[var(--color-stone)] truncate max-w-xs">
                    {event.message ?? '—'}
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
