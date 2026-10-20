import { AlertCircle, ChevronDown, ChevronRight, ExternalLink, GitMerge, X } from 'lucide-react'
import { Fragment, useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { cancelMerge, fetchMergeQueue } from '@/lib/api'
import { POLL_SLOW } from '@/lib/polling'
import { formatRelativeTime } from '@/lib/timestamps'
import type { MergeQueueEntry, MergeQueueStatus } from '@/lib/types'
import { cn } from '@/lib/utils'
import { DataPage } from './ui/DataPage'
import { FilterBar } from './ui/FilterBar'
import { PageHeader } from './ui/PageHeader'

function statusBadge(status: MergeQueueStatus): string {
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
  const [search, setSearch] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchMergeQueue({
        status: statusFilter || undefined,
        limit: 100,
      })
      setEntries(data.entries)
    } catch (err) {
      // Stable id so the 15s refresh loop doesn't stack duplicate toasts.
      console.error('Failed to load merge queue:', err)
      toast.error('Could not load the merge queue', { id: 'merge-queue-load' })
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    load()
    const interval = setInterval(load, POLL_SLOW)
    return () => clearInterval(interval)
  }, [load])

  const handleCancel = async (entryId: string) => {
    try {
      const updated = await cancelMerge(entryId)
      setEntries((prev) => prev.map((e) => (e.id === updated.id ? updated : e)))
    } catch (err) {
      console.error('Failed to cancel merge:', err)
      toast.error(err instanceof Error ? err.message : 'Failed to cancel merge')
    }
  }

  const filtered = entries.filter((e) => {
    if (!search) return true
    const q = search.toLowerCase()
    return (
      e.branch_name.toLowerCase().includes(q) ||
      e.pr_number?.toString().includes(q) ||
      e.last_error?.toLowerCase().includes(q)
    )
  })

  const isEmpty = !loading && filtered.length === 0

  return (
    <DataPage>
      <PageHeader title="Merge Queue" icon={GitMerge} count={filtered.length} countLabel="branch" />

      <p className="px-4 pb-3 text-body text-[var(--color-stone)]/60">
        Historical entries are shown here. To resolve conflicts or merge changes, open the run or
        its pull request.
      </p>

      <FilterBar
        filters={
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1 text-[var(--color-stone)]"
            aria-label="Filter by status"
          >
            <option value="">All Statuses</option>
            <option value="pending">Pending</option>
            <option value="testing">Testing</option>
            <option value="merging">Merging</option>
            <option value="merged">Merged</option>
            <option value="conflict">Conflict</option>
            <option value="failed">Failed</option>
          </select>
        }
        search={{
          value: search,
          onChange: setSearch,
          placeholder: 'Search branch / PR / error…',
          ariaLabel: 'Search merge queue',
        }}
        refresh={load}
        refreshing={loading}
      />

      <DataPage.Body>
        {loading && entries.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <div className="mark mark-running w-2 h-2" />
          </div>
        ) : isEmpty ? (
          <MergeQueueEmptyState searching={search.length > 0 || statusFilter !== ''} />
        ) : (
          <table className="w-full text-caption">
            <thead className="sticky top-0 bg-[var(--color-ink)] z-10">
              <tr className="border-b border-[rgba(163,163,163,0.1)]">
                <th className="text-left px-4 py-2 text-[var(--color-stone)]/60 font-normal w-6"></th>
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
              {filtered.map((entry) => {
                const hasError =
                  entry.status === 'conflict' || entry.status === 'failed' || !!entry.last_error
                const isExpanded = expandedId === entry.id

                return (
                  <Fragment key={entry.id}>
                    <tr
                      className={cn(
                        'border-b border-[rgba(163,163,163,0.05)] hover:bg-[rgba(163,163,163,0.03)]',
                        hasError && 'cursor-pointer'
                      )}
                      onClick={
                        hasError ? () => setExpandedId(isExpanded ? null : entry.id) : undefined
                      }
                    >
                      <td className="px-2 py-2 text-[var(--color-stone)]/40">
                        {hasError ? (
                          isExpanded ? (
                            <ChevronDown className="w-3 h-3" />
                          ) : (
                            <ChevronRight className="w-3 h-3" />
                          )
                        ) : null}
                      </td>
                      <td className="px-4 py-2 text-[var(--color-paper)] text-mono">
                        {entry.branch_name}
                      </td>
                      <td className="px-4 py-2">
                        {entry.pr_number ? (
                          <a
                            href={entry.pr_url || '#'}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
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
                            'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-micro uppercase tracking-wider',
                            statusBadge(entry.status)
                          )}
                        >
                          {entry.status}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-[var(--color-stone)]">
                        {entry.conflict_count > 0 ? (
                          <span
                            className="text-[var(--color-vermillion)] cursor-help"
                            title="Recorded conflicts"
                          >
                            {entry.conflict_count}
                          </span>
                        ) : (
                          <span className="text-[var(--color-stone)]/40" title="No conflicts">
                            0
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-[var(--color-stone)]/60 whitespace-nowrap">
                        {formatRelativeTime(entry.created_at)}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <a
                            href={`/board/${encodeURIComponent(entry.run_id)}`}
                            onClick={(e) => e.stopPropagation()}
                            className="text-[var(--color-sky)] hover:underline whitespace-nowrap"
                          >
                            Open run
                          </a>
                          {entry.status !== 'merged' && entry.status !== 'cancelled' && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                void handleCancel(entry.id)
                              }}
                              className="p-1 text-[var(--color-stone)]/40 hover:text-[var(--color-vermillion)] transition-colors"
                              aria-label="Cancel merge"
                              title="Cancel"
                            >
                              <X className="w-3.5 h-3.5" />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    {isExpanded && hasError && (
                      <tr className="bg-[var(--color-vermillion)]/[0.03]">
                        <td
                          colSpan={7}
                          className="px-4 py-3 border-b border-[rgba(163,163,163,0.05)]"
                        >
                          <div className="flex items-start gap-2 ml-6">
                            <AlertCircle className="w-3.5 h-3.5 text-[var(--color-vermillion)]/70 shrink-0 mt-0.5" />
                            <div className="flex-1 min-w-0">
                              <p className="text-caption text-[var(--color-stone)]/70 mb-1">
                                {entry.status === 'conflict'
                                  ? 'Merge conflict — manual resolution may be required.'
                                  : 'Merge failed.'}
                              </p>
                              {entry.last_error && (
                                <pre className="text-mono text-[var(--color-vermillion)]/90 whitespace-pre-wrap break-words">
                                  {entry.last_error}
                                </pre>
                              )}
                              <div className="flex items-center gap-2 mt-2">
                                {entry.status !== 'merged' && entry.status !== 'cancelled' && (
                                  <button
                                    type="button"
                                    onClick={() => void handleCancel(entry.id)}
                                    className="px-2 py-1 text-caption uppercase tracking-widest text-[var(--color-stone)] hover:text-[var(--color-vermillion)] border border-[rgba(163,163,163,0.2)] hover:border-[var(--color-vermillion)]/40 rounded-sm flex items-center gap-1"
                                  >
                                    <X className="w-3 h-3" />
                                    Cancel
                                  </button>
                                )}
                              </div>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </DataPage.Body>
    </DataPage>
  )
}

function MergeQueueEmptyState({ searching }: { searching: boolean }) {
  if (searching) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6 gap-3 max-w-md mx-auto">
        <GitMerge className="w-8 h-8 text-[var(--color-stone)]/30" />
        <div>
          <p className="text-display text-[var(--color-paper)] mb-1">No matching entries</p>
          <p className="text-body text-[var(--color-stone)]/60">
            Try clearing filters. Search runs over branch name, PR number, and error text.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-6 gap-3 max-w-md mx-auto">
      <GitMerge className="w-8 h-8 text-[var(--color-stone)]/30" />
      <div>
        <p className="text-display text-[var(--color-paper)] mb-1">No merge history</p>
        <p className="text-body text-[var(--color-stone)]/60">
          Open a completed run to review and merge its changes, or merge its pull request on GitHub.
        </p>
      </div>
    </div>
  )
}
