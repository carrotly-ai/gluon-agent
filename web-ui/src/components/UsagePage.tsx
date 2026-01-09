import { ChevronDown } from 'lucide-react'
import { useEffect, useState } from 'react'
import { fetchUsageByProject, fetchUsageRuns, fetchUsageSummary } from '@/lib/api'
import { formatDateWithContext } from '@/lib/timestamps'
import type { ProjectUsage, RunUsageItem, UsageSummary } from '@/lib/types'
import { cn } from '@/lib/utils'

function formatCost(cost: number | null): string {
  if (cost === null || cost === undefined) return '-'
  if (cost === 0) return '$0.00'
  if (cost < 0.01) return `$${cost.toFixed(4)}`
  return `$${cost.toFixed(2)}`
}

function formatTokens(tokens: number | null): string {
  if (tokens === null || tokens === undefined) return '-'
  if (tokens < 1000) return `${tokens}`
  if (tokens < 1000000) return `${(tokens / 1000).toFixed(1)}k`
  return `${(tokens / 1000000).toFixed(2)}M`
}

type SortField = 'cost' | 'date' | 'tokens'
type SortOrder = 'asc' | 'desc'

export function UsagePage() {
  const [summary, setSummary] = useState<UsageSummary | null>(null)
  const [projectUsage, setProjectUsage] = useState<ProjectUsage[]>([])
  const [runs, setRuns] = useState<RunUsageItem[]>([])
  const [loading, setLoading] = useState(true)
  const [sortField, setSortField] = useState<SortField>('cost')
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc')
  const [expandedProject, setExpandedProject] = useState<string | null>(null)

  useEffect(() => {
    async function load() {
      setLoading(true)
      try {
        const [summaryData, projectData, runsData] = await Promise.all([
          fetchUsageSummary(),
          fetchUsageByProject(),
          fetchUsageRuns({ sort_by: sortField, sort_order: sortOrder, limit: 50 }),
        ])
        setSummary(summaryData)
        setProjectUsage(projectData)
        setRuns(runsData)
      } catch (err) {
        console.error('Failed to load usage data:', err)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [sortField, sortOrder])

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortOrder((prev) => (prev === 'desc' ? 'asc' : 'desc'))
    } else {
      setSortField(field)
      setSortOrder('desc')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="mark mark-running w-2 h-2" />
      </div>
    )
  }

  return (
    <div className="h-full overflow-auto p-4 sm:p-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
        <SummaryCard
          label="Today"
          value={formatCost(summary?.today_cost_usd ?? 0)}
          subValue={`${summary?.today_runs ?? 0} runs`}
          accent="harvest"
        />
        <SummaryCard
          label="This Week"
          value={formatCost(summary?.week_cost_usd ?? 0)}
          subValue={`${summary?.week_runs ?? 0} runs`}
          accent="sky"
        />
        <SummaryCard
          label="This Month"
          value={formatCost(summary?.month_cost_usd ?? 0)}
          subValue={`${summary?.month_runs ?? 0} runs`}
          accent="jade"
        />
        <SummaryCard
          label="Avg/Run"
          value={formatCost((summary?.total_cost_usd ?? 0) / Math.max(summary?.total_runs ?? 1, 1))}
          subValue="average cost"
          accent="stone"
        />
        <SummaryCard
          label="Avg/Day"
          value={formatCost((summary?.week_cost_usd ?? 0) / 7)}
          subValue="past 7 days"
          accent="stone"
        />
        <SummaryCard
          label="Projects"
          value={String(projectUsage.length)}
          subValue="active"
          accent="stone"
        />
      </div>

      {/* Two Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Cost by Project */}
        <div className="lg:col-span-1">
          <div className="border border-[rgba(163,163,163,0.1)] rounded-sm">
            <div className="px-4 py-3 border-b border-[rgba(163,163,163,0.08)]">
              <h2 className="text-[0.6875rem] uppercase tracking-widest text-[var(--color-stone)]">
                Cost by Project
              </h2>
            </div>
            <div className="divide-y divide-[rgba(163,163,163,0.06)]">
              {projectUsage.length === 0 ? (
                <div className="p-4 text-center text-[0.75rem] text-[var(--color-stone)]/50">
                  No usage data yet
                </div>
              ) : (
                projectUsage.slice(0, 10).map((project) => (
                  <div key={project.project_id}>
                    <button
                      className="w-full px-4 py-3 flex items-center justify-between hover:bg-[var(--color-paper)]/3 transition-colors"
                      onClick={() =>
                        setExpandedProject(
                          expandedProject === project.project_id ? null : project.project_id
                        )
                      }
                    >
                      <div className="flex items-center gap-3">
                        <div
                          className="w-2 h-2 rounded-full"
                          style={{
                            backgroundColor: `hsl(${(project.project_name.charCodeAt(0) * 137) % 360}, 50%, 50%)`,
                          }}
                        />
                        <span className="text-[0.8125rem] text-[var(--color-paper)]">
                          {project.project_name}
                        </span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="text-mono text-[0.75rem] text-[var(--color-harvest)]">
                          {formatCost(project.cost_usd)}
                        </span>
                        <ChevronDown
                          className={cn(
                            'w-3 h-3 text-[var(--color-stone)]/50 transition-transform',
                            expandedProject === project.project_id && 'rotate-180'
                          )}
                        />
                      </div>
                    </button>
                    {expandedProject === project.project_id && (
                      <div className="px-4 pb-3 pt-0 ml-5 text-[0.6875rem] text-[var(--color-stone)]/70 space-y-1">
                        <div className="flex justify-between">
                          <span>Runs</span>
                          <span className="text-mono">{project.run_count}</span>
                        </div>
                        <div className="flex justify-between">
                          <span>Input tokens</span>
                          <span className="text-mono">{formatTokens(project.input_tokens)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span>Output tokens</span>
                          <span className="text-mono">{formatTokens(project.output_tokens)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span>Avg/run</span>
                          <span className="text-mono">
                            {formatCost(project.cost_usd / Math.max(project.run_count, 1))}
                          </span>
                        </div>
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Recent Runs with Cost */}
        <div className="lg:col-span-2">
          <div className="border border-[rgba(163,163,163,0.1)] rounded-sm">
            <div className="px-4 py-3 border-b border-[rgba(163,163,163,0.08)] flex items-center justify-between">
              <h2 className="text-[0.6875rem] uppercase tracking-widest text-[var(--color-stone)]">
                Recent Runs
              </h2>
              <div className="flex items-center gap-2">
                <SortButton
                  label="Cost"
                  active={sortField === 'cost'}
                  order={sortField === 'cost' ? sortOrder : undefined}
                  onClick={() => handleSort('cost')}
                />
                <SortButton
                  label="Date"
                  active={sortField === 'date'}
                  order={sortField === 'date' ? sortOrder : undefined}
                  onClick={() => handleSort('date')}
                />
                <SortButton
                  label="Tokens"
                  active={sortField === 'tokens'}
                  order={sortField === 'tokens' ? sortOrder : undefined}
                  onClick={() => handleSort('tokens')}
                />
              </div>
            </div>
            <div className="divide-y divide-[rgba(163,163,163,0.06)] max-h-[600px] overflow-y-auto">
              {runs.length === 0 ? (
                <div className="p-4 text-center text-[0.75rem] text-[var(--color-stone)]/50">
                  No runs yet
                </div>
              ) : (
                runs.map((run) => (
                  <div
                    key={run.id}
                    className="px-4 py-3 hover:bg-[var(--color-paper)]/3 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <div className={cn('mark', `mark-${run.status}`)} />
                          <span className="text-[0.75rem] text-[var(--color-paper)]/70">
                            {run.project_name}
                          </span>
                          <span className="text-mono text-[0.625rem] text-[var(--color-stone)]/50">
                            {run.id.slice(0, 8)}
                          </span>
                        </div>
                        <p className="text-[0.75rem] text-[var(--color-paper)] line-clamp-1">
                          {run.prompt}
                        </p>
                        <div className="flex items-center gap-3 mt-1.5 text-[0.625rem] text-[var(--color-stone)]/60">
                          <span>{formatDateWithContext(run.created_at)}</span>
                          {run.model_used && <span className="text-mono">{run.model_used}</span>}
                          <span className="text-mono">
                            {formatTokens(run.input_tokens)} → {formatTokens(run.output_tokens)}
                          </span>
                        </div>
                      </div>
                      <div className="shrink-0 text-right">
                        <span
                          className={cn(
                            'text-mono text-[0.8125rem]',
                            run.cost_usd && run.cost_usd > 0
                              ? 'text-[var(--color-harvest)]'
                              : 'text-[var(--color-stone)]/50'
                          )}
                        >
                          {formatCost(run.cost_usd)}
                        </span>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function SummaryCard({
  label,
  value,
  subValue,
  accent,
}: {
  label: string
  value: string
  subValue: string
  accent: 'harvest' | 'sky' | 'jade' | 'stone'
}) {
  const accentColor = {
    harvest: 'var(--color-harvest)',
    sky: 'var(--color-sky)',
    jade: 'var(--color-jade)',
    stone: 'var(--color-stone)',
  }[accent]

  return (
    <div className="p-4 border border-[rgba(163,163,163,0.1)] rounded-sm">
      <p className="text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
        {label}
      </p>
      <p
        className="text-mono text-[1.25rem] sm:text-[1.5rem] font-light"
        style={{ color: accentColor }}
      >
        {value}
      </p>
      <p className="text-[0.625rem] text-[var(--color-stone)]/50 mt-1">{subValue}</p>
    </div>
  )
}

function SortButton({
  label,
  active,
  order,
  onClick,
}: {
  label: string
  active: boolean
  order?: SortOrder
  onClick: () => void
}) {
  return (
    <button
      className={cn(
        'px-2 py-1 text-[0.5625rem] uppercase tracking-widest rounded-sm transition-colors flex items-center gap-1',
        active
          ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
          : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
      )}
      onClick={onClick}
    >
      {label}
      {active && <span className="text-[0.5rem]">{order === 'desc' ? '↓' : '↑'}</span>}
    </button>
  )
}
