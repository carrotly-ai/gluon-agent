import { RefreshCw, Search } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Standard filter bar sitting directly under PageHeader on list-style pages.
 *
 * Anatomy (left-to-right):
 *   [filters slot]  [search?]  [refresh?]                 [actions slot]
 *
 * Use the `filters` slot for `<select>` dropdowns styled with the project's
 * existing convention: text-caption bg-transparent border border-stone/15
 * rounded-sm px-2 py-1. (See MergeQueuePage and WorkQueuePage for examples.)
 */
export interface FilterBarProps {
  /** Left-aligned slot for filter selects, badge filters, etc. */
  filters?: ReactNode
  /** Optional search input. Renders with a leading Search icon. */
  search?: {
    value: string
    onChange: (v: string) => void
    placeholder?: string
    /** Accessible label for the input. Defaults to "Search". */
    ariaLabel?: string
  }
  /** Optional refresh button. The icon spins when `refreshing` is true. */
  refresh?: () => void
  refreshing?: boolean
  /** Right-aligned slot for primary actions (e.g. "New schedule"). */
  actions?: ReactNode
  className?: string
}

export function FilterBar({
  filters,
  search,
  refresh,
  refreshing = false,
  actions,
  className,
}: FilterBarProps) {
  return (
    <div
      className={cn(
        'shrink-0 px-4 sm:px-6 py-2 border-b border-[rgba(163,163,163,0.06)]',
        'flex items-center gap-2 flex-wrap',
        className
      )}
    >
      {filters ? <div className="flex items-center gap-2 flex-wrap">{filters}</div> : null}

      {search ? (
        <label className="flex items-center gap-1.5 text-caption text-[var(--color-stone)] border border-[rgba(163,163,163,0.15)] rounded-sm px-2 py-1 bg-transparent focus-within:border-[var(--color-stone)]/40 transition-colors">
          <Search className="w-3 h-3 shrink-0" aria-hidden="true" />
          <input
            type="search"
            value={search.value}
            onChange={(e) => search.onChange(e.target.value)}
            placeholder={search.placeholder}
            aria-label={search.ariaLabel ?? 'Search'}
            className="bg-transparent outline-none text-[var(--color-paper)] placeholder:text-[var(--color-stone)]/50 min-w-[140px]"
          />
        </label>
      ) : null}

      {refresh ? (
        <button
          type="button"
          onClick={refresh}
          aria-label="Refresh"
          className="p-1.5 rounded-sm hover:bg-[rgba(163,163,163,0.1)] transition-colors text-[var(--color-stone)]"
        >
          <RefreshCw className={cn('w-3.5 h-3.5', refreshing && 'animate-spin')} />
        </button>
      ) : null}

      {actions ? <div className="ml-auto flex items-center gap-2">{actions}</div> : null}
    </div>
  )
}
