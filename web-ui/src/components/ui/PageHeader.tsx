import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Page-level header used at the top of secondary pages (Schedules, Activity,
 * Work Queue, Merge Queue, List, Sessions, Admin/Users, Usage, Settings).
 *
 * Anatomy (left-to-right):
 *   [icon?]  H1 (text-display)  [count?]  [subtitle?]              [actions?]
 *
 * Spacing & rule match the gold-standard reference in SchedulesPage.tsx — a
 * hairline `border-b border-stone/10` and `px-4 sm:px-6 py-3` so the rhythm is
 * consistent across every page. Don't reinvent this.
 */
export interface PageHeaderProps {
  title: string
  icon?: LucideIcon
  /** Optional count chip rendered after the title, e.g. `12 schedules`. */
  count?: number
  /** Singular form for the count chip. Used as both label and plural-by-adding-s. */
  countLabel?: string
  /** Optional subtitle line in text-caption beneath the title row. */
  subtitle?: string
  /** Right-aligned action slot: primary button, button group, etc. */
  actions?: ReactNode
  className?: string
}

export function PageHeader({
  title,
  icon: Icon,
  count,
  countLabel,
  subtitle,
  actions,
  className,
}: PageHeaderProps) {
  const countDisplay =
    count !== undefined && countLabel
      ? `${count} ${count === 1 ? countLabel : `${countLabel}s`}`
      : undefined

  return (
    <div
      className={cn(
        'shrink-0 border-b border-[rgba(163,163,163,0.1)] px-4 sm:px-6 py-3',
        className
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          {Icon ? <Icon className="w-4 h-4 text-[var(--color-stone)]/70 shrink-0" /> : null}
          <h1 className="text-display text-[var(--color-paper)] truncate">{title}</h1>
          {countDisplay ? (
            <span className="text-caption text-[var(--color-stone)]/50 ml-2 shrink-0">
              {countDisplay}
            </span>
          ) : null}
        </div>
        {actions ? <div className="flex items-center gap-2 shrink-0">{actions}</div> : null}
      </div>
      {subtitle ? <p className="text-caption text-[var(--color-stone)] mt-1">{subtitle}</p> : null}
    </div>
  )
}
