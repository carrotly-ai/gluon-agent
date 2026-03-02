import { ExternalLink, RefreshCw, RotateCcw, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { cancelMerge, fetchMergeQueue, retryMerge } from '@/lib/api'
import { formatRelativeTime } from '@/lib/timestamps'
import type { MergeQueueEntry, MergeQueueStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

function statusBadge(status: MergeQueueStatus) {
  const styles: Record<MergeQueueStatus, string> = {
    pending: 'bg-[var(--color-harvest)]/15 text-[var(--color-harvest)]',
    testing: 'bg-[var(--color-sky)]/15 text-[var(--color-sky)]',
    merging: 'bg-[var(--color-sky)]/15 text-[var(--color-sky)]',
    merged: 'bg-[var(--color-jade)]/15 text-[var(--color-jade)]',
    conflict: 'bg-[var(--color-vermillion)]/15 text-[var(--color-vermillion)]',
    failed: 'bg-[var(--color-vermillion)]/15 text-[var(--color-vermillion)]',
    cancelled: 'bg-[var(--color-stone)]/10 text-[var(--color-stone)]/60',
  }
  return styles[status] || styles.pending
}

export function MergeQueuePage() {
  const [entries, setEntries] = useState<MergeQueueEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState<string>('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchMergeQueue({
        status: statusFilter || undefined,
        limit: 100,
      })
      setEntries(data.entries)
    } catch (err) {
      console.error('Failed to load merge queue:', err)
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    load()
    const interval = setInterval(load, 15000)
    return () => clearInterval(interval)
  }, [load])

  const handleRetry = async (entryId: string) => {
    try {
      const updated = await retryMerge(entryId)
      setEntries((prev) => prev.map((e) => (e.id === updated.id ? updated : e)))
    } catch (err) {
      console.error('Failed to retry merge:', err)
    }
  }

  const handleCancel = async (entryId: string) => {
    try {
      const updated = await cancelMerge(entryId)
      setEntries((prev) => prev.map((e) => (e.id === updated.id ? updated : e)))
    } catch (err) {
      console.error('Failed to cancel merge:', err)
    }
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="border-b border-[rgba(163,163,163,0.1)] px-4 sm:px-6 py-3 flex items-center justify-between gap-3 shrink-0">
        <h2 className="text-body font-normal tracking-wide text-[var(--color-paper)]">
          Merge Queue
        </h2>
        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1 text-[var(--color-stone)]"
          >
            <option value="">All Statuses</option>
            <option value="pending">Pending</option>
            <option value="testing">Testing</option>
            <option value="merging">Merging</option>
            <option value="merged">Merged</option>
            <option value="conflict">Conflict</option>
            <option value="failed">Failed</option>
          </select>

          <button
            onClick={load}
            className="p-1.5 rounded-sm hover:bg-[rgba(163,163,163,0.1)] transition-colors text-[var(--color-stone)]"
            title="Refresh"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {loading && entries.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <div className="mark mark-running w-2 h-2" />
          </div>
        ) : entries.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <p className="text-caption text-[var(--color-stone)]/60">Merge queue is empty</p>
          </div>
        ) : (
          <table className="w-full text-caption">
            <thead className="sticky top-0 bg-[var(--color-ink)] z-10">
              <tr className="border-b border-[rgba(163,163,163,0.1)]">
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal">
                  Branch
                </th>
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-24">
                  PR
                </th>
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-24">
                  Status
                </th>
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-24">
                  Conflicts
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
              {entries.map((entry) => (
                <tr
                  key={entry.id}
                  className="border-b border-[rgba(163,163,163,0.05)] hover:bg-[rgba(163,163,163,0.03)]"
                >
                  <td className="px-4 py-2 text-[var(--color-paper)] font-mono text-[11px]">
                    {entry.branch_name}
                  </td>
                  <td className="px-4 py-2">
                    {entry.pr_number ? (
                      <a
                        href={entry.pr_url || '#'}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-[var(--color-sky)] hover:underline"
                      >
                        #{entry.pr_number}
                        <ExternalLink className="w-3 h-3" />
                      </a>
                    ) : (
                      <span className="text-[var(--color-stone)]/40">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={cn(
                        'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-[10px] uppercase tracking-wider',
                        statusBadge(entry.status)
                      )}
                    >
                      {(entry.status === 'testing' || entry.status === 'merging') && (
                        <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
                      )}
                      {entry.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-[var(--color-stone)]">
                    {entry.conflict_count > 0 ? (
                      <span className="text-[var(--color-vermillion)]">
                        {entry.conflict_count}/{entry.max_retries}
                      </span>
                    ) : (
                      <span className="text-[var(--color-stone)]/40">0</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-[var(--color-stone)]/60 whitespace-nowrap">
                    {formatRelativeTime(entry.created_at)}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex items-center justify-end gap-1">
                      {(entry.status === 'conflict' || entry.status === 'failed') && (
                        <button
                          onClick={() => handleRetry(entry.id)}
                          className="p-1 text-[var(--color-stone)]/40 hover:text-[var(--color-sky)] transition-colors"
                          title="Retry"
                        >
                          <RotateCcw className="w-3.5 h-3.5" />
                        </button>
                      )}
                      {entry.status !== 'merged' && entry.status !== 'cancelled' && (
                        <button
                          onClick={() => handleCancel(entry.id)}
                          className="p-1 text-[var(--color-stone)]/40 hover:text-[var(--color-vermillion)] transition-colors"
                          title="Cancel"
                        >
                          <X className="w-3.5 h-3.5" />
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

      {/* Error details tooltip area */}
      {entries.some((e) => e.last_error) && (
        <div className="border-t border-[rgba(163,163,163,0.1)] px-4 py-2 shrink-0">
          <details className="text-caption">
            <summary className="text-[var(--color-stone)]/60 cursor-pointer hover:text-[var(--color-stone)]">
              Show error details
            </summary>
            <div className="mt-2 space-y-1">
              {entries
                .filter((e) => e.last_error)
                .map((e) => (
                  <div
                    key={e.id}
                    className="font-mono text-[11px] text-[var(--color-vermillion)]/80"
                  >
                    <span className="text-[var(--color-stone)]/60">{e.branch_name}:</span>{' '}
                    {e.last_error}
                  </div>
                ))}
            </div>
          </details>
        </div>
      )}
    </div>
  )
}
