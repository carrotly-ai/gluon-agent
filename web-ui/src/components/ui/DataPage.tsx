import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Layout shell for data-list pages: a vertical stack with header, filter bar,
 * and a flexing body. Just enforces the standard rhythm — there's no logic.
 *
 *   <DataPage>
 *     <PageHeader title="Schedules" icon={CalendarClock} count={12} countLabel="schedule" />
 *     <FilterBar filters={…} refresh={reload} />
 *     <DataPage.Body>
 *       <table>…</table>
 *     </DataPage.Body>
 *   </DataPage>
 */
export interface DataPageProps {
  children: ReactNode
  className?: string
}

export function DataPage({ children, className }: DataPageProps) {
  return (
    <div className={cn('flex-1 flex flex-col overflow-hidden min-h-0', className)}>{children}</div>
  )
}

interface DataPageBodyProps {
  children: ReactNode
  className?: string
}

function DataPageBody({ children, className }: DataPageBodyProps) {
  return <div className={cn('flex-1 overflow-y-auto', className)}>{children}</div>
}

DataPage.Body = DataPageBody
