import { Activity, Check, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { cleanupActivity, fetchActivity } from '@/lib/api'
import { POLL_NORMAL } from '@/lib/polling'
import { formatRelativeTime } from '@/lib/timestamps'
import type { ActivityEvent } from '@/lib/types'
import { cn } from '@/lib/utils'
import { DataPage } from './ui/DataPage'
import { FilterBar } from './ui/FilterBar'
import { PageHeader } from './ui/PageHeader'

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
  const [search, setSearch] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
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
    const interval = setInterval(load, POLL_NORMAL)
    return () => clearInterval(interval)
  }, [load])

  // Derive unique actions and actors for filter dropdowns
  const uniqueActions = [...new Set(events.map((e) => e.action))].sort()
  const uniqueActors = [...new Set(events.map((e) => e.actor))].sort()

  const filtered = events.filter((e) => {
    if (!search) return true
    const q = search.toLowerCase()
    return (
      e.message?.toLowerCase().includes(q) ||
      e.actor.toLowerCase().includes(q) ||
      e.action.toLowerCase().includes(q) ||
      e.result?.toLowerCase().includes(q)
    )
  })

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

  const isEmpty = !loading && filtered.length === 0
  const hasFilters =
    search.length > 0 || actionFilter !== '' || actorFilter !== '' || timeRange !== 0

  return (
    <DataPage>
      <PageHeader
        title="Activity"
        icon={Activity}
        count={filtered.length}
        countLabel="event"
        actions={
          <>
            <button
              type="button"
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
          </>
        }
      />

      <FilterBar
        filters={
          <>
            <select
              value={actionFilter}
              onChange={(e) => setActionFilter(e.target.value)}
              className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1 text-[var(--color-stone)]"
              aria-label="Filter by action"
            >
              <option value="">All actions</option>
              {uniqueActions.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <select
              value={actorFilter}
              onChange={(e) => setActorFilter(e.target.value)}
              className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1 text-[var(--color-stone)]"
              aria-label="Filter by actor"
            >
              <option value="">All actors</option>
              {uniqueActors.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <div className="flex items-center gap-0.5 bg-[rgba(163,163,163,0.06)] rounded-sm p-0.5">
              {TIME_RANGES.map(({ label, hours }) => (
                <button
                  key={hours}
                  type="button"
                  aria-pressed={timeRange === hours}
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
          </>
        }
        search={{
          value: search,
          onChange: setSearch,
          placeholder: 'Search message / actor / action…',
          ariaLabel: 'Search activity',
        }}
        refresh={load}
        refreshing={loading}
      />

      <DataPage.Body>
        {loading && events.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <div className="mark mark-running w-2 h-2" />
          </div>
        ) : isEmpty ? (
          <ActivityEmptyState searching={hasFilters} />
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
              {filtered.map((event) => {
                const isExpanded = expandedId === event.id
                const hasMessage = !!event.message
                return (
                  <tr
                    key={event.id}
                    className={cn(
                      'border-b border-[rgba(163,163,163,0.05)] hover:bg-[rgba(163,163,163,0.03)]',
                      hasMessage && 'cursor-pointer'
                    )}
                    onClick={
                      hasMessage ? () => setExpandedId(isExpanded ? null : event.id) : undefined
                    }
                  >
                    <td className="px-4 py-2 text-[var(--color-stone)]/60 whitespace-nowrap align-top">
                      <span title={new Date(event.timestamp).toLocaleString()}>
                        {formatRelativeTime(event.timestamp)}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-[var(--color-stone)] text-mono truncate max-w-32 align-top">
                      {event.actor}
                    </td>
                    <td className="px-4 py-2 align-top">
                      <span
                        className={cn(
                          'inline-block px-1.5 py-0.5 rounded-sm text-micro uppercase',
                          actionBadgeColor(event.action)
                        )}
                      >
                        {event.action}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-[var(--color-stone)]/60 align-top">
                      {event.result ?? '—'}
                    </td>
                    <td
                      className={cn(
                        'px-4 py-2 text-[var(--color-stone)] align-top',
                        !isExpanded && 'truncate max-w-xs'
                      )}
                    >
                      {isExpanded && event.message ? (
                        <div className="whitespace-pre-wrap break-words text-body text-[var(--color-paper)]">
                          {event.message}
                        </div>
                      ) : (
                        (event.message ?? '—')
                      )}
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

function ActivityEmptyState({ searching }: { searching: boolean }) {
  if (searching) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6 gap-3 max-w-md mx-auto">
        <Activity className="w-8 h-8 text-[var(--color-stone)]/30" />
        <div>
          <p className="text-display text-[var(--color-paper)] mb-1">No matching activity</p>
          <p className="text-body text-[var(--color-stone)]/60">
            Try widening the time range or clearing filters. Search runs over message, actor,
            action, and result text.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-6 gap-3 max-w-md mx-auto">
      <Activity className="w-8 h-8 text-[var(--color-stone)]/30" />
      <div>
        <p className="text-display text-[var(--color-paper)] mb-1">Activity is empty</p>
        <p className="text-body text-[var(--color-stone)]/60">
          Events will appear here as agents run, get cancelled, or finish. This page is the forensic
          record — every action with an actor, an outcome, and a timestamp.
        </p>
      </div>
      <ul className="text-caption text-[var(--color-stone)]/50 text-left mt-2 flex flex-col gap-1">
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Filter by action, actor, or time
          range
        </li>
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Click a row to expand the full
          message inline
        </li>
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Auto-refreshes every 10 seconds
        </li>
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Cleanup trims events older than
          90 days
        </li>
      </ul>
    </div>
  )
}
