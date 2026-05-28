import { BarChart3, Check, ChevronDown } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { fetchUsageByDay, fetchUsageByProject, fetchUsageRuns, fetchUsageSummary } from '@/lib/api'
import { formatCost, formatTokens, projectColor } from '@/lib/format'
import { formatDateWithContext } from '@/lib/timestamps'
import type { DailyUsage, ProjectUsage, RunUsageItem, UsageSummary } from '@/lib/types'
import { cn } from '@/lib/utils'
import { DataPage } from './ui/DataPage'
import { PageHeader } from './ui/PageHeader'

type SortField = 'cost' | 'date' | 'tokens'
type SortOrder = 'asc' | 'desc'

const CHART_DAYS = 30
const CHART_TOP_PROJECTS = 6 // remainder bucketed as "Other"

export function UsagePage() {
  const [summary, setSummary] = useState<UsageSummary | null>(null)
  const [projectUsage, setProjectUsage] = useState<ProjectUsage[]>([])
  const [dailyUsage, setDailyUsage] = useState<DailyUsage[]>([])
  const [runs, setRuns] = useState<RunUsageItem[]>([])
  const [loading, setLoading] = useState(true)
  const [sortField, setSortField] = useState<SortField>('cost')
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc')
  const [expandedProject, setExpandedProject] = useState<string | null>(null)

  useEffect(() => {
    async function load() {
      setLoading(true)
      try {
        const since = new Date(Date.now() - CHART_DAYS * 24 * 60 * 60 * 1000).toISOString()
        const [summaryData, projectData, dailyData, runsData] = await Promise.all([
          fetchUsageSummary(),
          fetchUsageByProject(),
          fetchUsageByDay({ since }),
          fetchUsageRuns({ sort_by: sortField, sort_order: sortOrder, limit: 50 }),
        ])
        setSummary(summaryData)
        setProjectUsage(projectData)
        setDailyUsage(dailyData)
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

  const isEmpty = !loading && (summary?.total_runs ?? 0) === 0 && projectUsage.length === 0

  return (
    <DataPage>
      <PageHeader title="Usage" icon={BarChart3} />

      <DataPage.Body>
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <div className="mark mark-running w-2 h-2" />
          </div>
        ) : isEmpty ? (
          <UsageEmptyState />
        ) : (
          <div className="p-4 sm:p-6">
            {/* Primary Metrics */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
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
            </div>
            {/* Secondary Metrics - compact row */}
            <div className="flex items-center gap-6 mb-6 px-1 text-caption text-[var(--color-stone)]/70">
              <span>
                Avg/Run:{' '}
                <span className="text-mono text-[var(--color-paper)]/70">
                  {formatCost(
                    (summary?.total_cost_usd ?? 0) / Math.max(summary?.total_runs ?? 1, 1)
                  )}
                </span>
              </span>
              <span>
                Avg/Day:{' '}
                <span className="text-mono text-[var(--color-paper)]/70">
                  {formatCost((summary?.week_cost_usd ?? 0) / 7)}
                </span>
              </span>
              <span>
                Projects:{' '}
                <span className="text-mono text-[var(--color-paper)]/70">
                  {projectUsage.length}
                </span>
              </span>
            </div>

            {/* 30-day chart */}
            <UsageChart daily={dailyUsage} projects={projectUsage} />

            {/* Two Column Layout */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-6">
              {/* Cost by Project */}
              <div className="lg:col-span-1">
                <div className="border border-[rgba(163,163,163,0.1)] rounded-sm">
                  <div className="px-4 py-3 border-b border-[rgba(163,163,163,0.08)]">
                    <h2 className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
                      Cost by Project
                    </h2>
                  </div>
                  <div className="divide-y divide-[rgba(163,163,163,0.06)]">
                    {projectUsage.length === 0 ? (
                      <div className="p-4 text-center text-body text-[var(--color-stone)]/50">
                        No project usage data yet
                      </div>
                    ) : (
                      projectUsage.slice(0, 10).map((project) => (
                        <div key={project.project_id}>
                          <button
                            type="button"
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
                                style={{ backgroundColor: projectColor(project.project_name) }}
                              />
                              <span className="text-title text-[var(--color-paper)]">
                                {project.project_name}
                              </span>
                            </div>
                            <div className="flex items-center gap-3">
                              <span className="text-mono text-body text-[var(--color-harvest)]">
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
                            <div className="px-4 pb-3 pt-0 ml-5 text-caption text-[var(--color-stone)]/70 space-y-1">
                              <div className="flex justify-between">
                                <span>Runs</span>
                                <span className="text-mono">{project.run_count}</span>
                              </div>
                              <div className="flex justify-between">
                                <span>Input tokens</span>
                                <span className="text-mono">
                                  {formatTokens(project.input_tokens)}
                                </span>
                              </div>
                              <div className="flex justify-between">
                                <span>Output tokens</span>
                                <span className="text-mono">
                                  {formatTokens(project.output_tokens)}
                                </span>
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
                    <h2 className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
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
                      <div className="p-4 text-center text-body text-[var(--color-stone)]/50">
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
                                <span className="text-body text-[var(--color-paper)]/70">
                                  {run.project_name}
                                </span>
                                <span className="text-mono text-caption text-[var(--color-stone)]/50">
                                  {run.id.slice(0, 8)}
                                </span>
                              </div>
                              <p className="text-body text-[var(--color-paper)] line-clamp-1">
                                {run.prompt}
                              </p>
                              <div className="flex items-center gap-3 mt-1.5 text-caption text-[var(--color-stone)]/60">
                                <span>{formatDateWithContext(run.created_at)}</span>
                                {run.model_used && (
                                  <span className="text-mono">{run.model_used}</span>
                                )}
                                <span className="text-mono">
                                  {formatTokens(run.input_tokens)} →{' '}
                                  {formatTokens(run.output_tokens)}
                                </span>
                              </div>
                            </div>
                            <div className="shrink-0 text-right">
                              <span
                                className={cn(
                                  'text-mono text-title',
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
        )}
      </DataPage.Body>
    </DataPage>
  )
}

// ============================================================
// Chart
// ============================================================

/**
 * Hand-rolled 30-day daily-cost chart.
 *
 * The backend currently exposes `GET /api/usage/by-day` which returns daily
 * totals (no per-project breakdown). We render a single area series of the
 * daily total. Per-project stacking is stubbed via `approximateStack` which
 * fans the total across projects in proportion to each project's monthly
 * share — that's directional, not authoritative, and the caveat in the
 * footer says so honestly.
 *
 * TODO(backend): expose `GET /api/usage/by-day-by-project` returning
 * `{ date, project_id, project_name, cost_usd }[]` so the chart can stack
 * truthfully instead of approximating.
 */
function UsageChart({ daily, projects }: { daily: DailyUsage[]; projects: ProjectUsage[] }) {
  const series = useMemo(() => buildSeries(daily, projects), [daily, projects])
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)

  if (series.days.length === 0) {
    return (
      <div className="border border-[rgba(163,163,163,0.1)] rounded-sm p-6 text-center">
        <p className="text-caption text-[var(--color-stone)]/50">
          No daily usage data available for the last {CHART_DAYS} days yet.
        </p>
      </div>
    )
  }

  // Chart dimensions
  const W = 800 // viewBox width; will scale to container
  const H = 180
  const padL = 8
  const padR = 8
  const padT = 12
  const padB = 22
  const chartW = W - padL - padR
  const chartH = H - padT - padB
  const dx = chartW / Math.max(series.days.length - 1, 1)

  const maxTotal = Math.max(0.0001, ...series.days.map((d) => d.total))
  const yScale = (v: number) => padT + (1 - v / maxTotal) * chartH

  // Build stacked path for each project layer (top→bottom for paint order)
  const layers = series.projectNames.map((name, layerIdx) => {
    // For each day, cumulative bottom (sum of layers below + current) and top (sum of layers below).
    const points: { x: number; yTop: number; yBot: number; value: number }[] = []
    for (let i = 0; i < series.days.length; i++) {
      let below = 0
      for (let j = 0; j < layerIdx; j++)
        below += series.days[i].byProject[series.projectNames[j]] ?? 0
      const current = series.days[i].byProject[name] ?? 0
      const yBot = yScale(below)
      const yTop = yScale(below + current)
      points.push({ x: padL + i * dx, yTop, yBot, value: current })
    }
    const topPath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.yTop}`).join(' ')
    const botPath = points
      .slice()
      .reverse()
      .map((p) => `L ${p.x} ${p.yBot}`)
      .join(' ')
    return {
      name,
      d: `${topPath} ${botPath} Z`,
      color: projectColor(name, 55, 55),
    }
  })

  const hovered = hoverIdx !== null ? series.days[hoverIdx] : null

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * W
    const i = Math.round((x - padL) / dx)
    const clamped = Math.max(0, Math.min(series.days.length - 1, i))
    setHoverIdx(clamped)
  }

  return (
    <div className="border border-[rgba(163,163,163,0.1)] rounded-sm">
      <div className="px-4 py-3 border-b border-[rgba(163,163,163,0.08)] flex items-center justify-between">
        <h2 className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
          Last {CHART_DAYS} Days
        </h2>
        <span className="text-caption text-[var(--color-stone)]/50">
          {formatCost(series.totalCost)} total
        </span>
      </div>
      <div className="relative px-2 py-3">
        {/* SVG chart */}
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full h-auto"
          preserveAspectRatio="none"
          role="img"
          aria-label={`Daily cost for the last ${CHART_DAYS} days, total ${formatCost(series.totalCost)}`}
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHoverIdx(null)}
        >
          {/* Hairline baseline */}
          <line
            x1={padL}
            x2={W - padR}
            y1={padT + chartH}
            y2={padT + chartH}
            stroke="rgba(163,163,163,0.15)"
            strokeWidth={1}
          />
          {/* Stacked area paths */}
          {layers.map((layer) => (
            <path key={layer.name} d={layer.d} fill={layer.color} opacity={0.65} />
          ))}
          {/* Hover marker */}
          {hoverIdx !== null && (
            <line
              x1={padL + hoverIdx * dx}
              x2={padL + hoverIdx * dx}
              y1={padT}
              y2={padT + chartH}
              stroke="rgba(229,229,229,0.4)"
              strokeWidth={1}
              strokeDasharray="2,2"
            />
          )}
          {/* X-axis labels: every 5 days */}
          {series.days.map((d, i) => {
            if (i % 5 !== 0 && i !== series.days.length - 1) return null
            return (
              <text
                key={d.date}
                x={padL + i * dx}
                y={H - 6}
                textAnchor="middle"
                fill="rgba(176,176,176,0.5)"
                fontSize={10}
                style={{ letterSpacing: '0.03em' }}
              >
                {formatChartDate(d.date)}
              </text>
            )
          })}
        </svg>

        {/* Tooltip */}
        {hovered && (
          <div
            className="absolute top-2 right-3 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm px-3 py-2 pointer-events-none min-w-[180px] shadow-lg"
            aria-hidden="true"
          >
            <p className="text-caption text-[var(--color-stone)]/60 mb-1">
              {formatChartDate(hovered.date, true)}
            </p>
            <p className="text-mono text-body text-[var(--color-harvest)] mb-2">
              {formatCost(hovered.total)}
            </p>
            <div className="space-y-0.5">
              {series.projectNames
                .map((name) => ({ name, value: hovered.byProject[name] ?? 0 }))
                .filter((p) => p.value > 0)
                .sort((a, b) => b.value - a.value)
                .map((p) => (
                  <div
                    key={p.name}
                    className="flex items-center justify-between gap-3 text-caption"
                  >
                    <span className="flex items-center gap-1.5 truncate max-w-[110px]">
                      <span
                        className="w-1.5 h-1.5 rounded-full shrink-0"
                        style={{ backgroundColor: projectColor(p.name) }}
                      />
                      <span className="text-[var(--color-stone)] truncate">{p.name}</span>
                    </span>
                    <span className="text-mono text-[var(--color-paper)]/70 shrink-0">
                      {formatCost(p.value)}
                    </span>
                  </div>
                ))}
            </div>
          </div>
        )}
      </div>
      <p className="px-4 pb-3 text-caption text-[var(--color-stone)]/50">
        Per-project stack is approximated from each project's share of monthly spend — the backend
        doesn't yet expose true daily per-project totals. Daily totals are exact.
      </p>
    </div>
  )
}

interface ChartSeries {
  days: { date: string; total: number; byProject: Record<string, number> }[]
  projectNames: string[]
  totalCost: number
}

function buildSeries(daily: DailyUsage[], projects: ProjectUsage[]): ChartSeries {
  // 1. Build a 30-day window ending today, fill missing days with 0.
  const dayMap = new Map<string, number>()
  for (const d of daily) {
    // Normalise to YYYY-MM-DD (server may include time).
    const date = d.date.slice(0, 10)
    dayMap.set(date, (dayMap.get(date) ?? 0) + d.cost_usd)
  }

  const days: ChartSeries['days'] = []
  for (let i = CHART_DAYS - 1; i >= 0; i--) {
    const dt = new Date()
    dt.setHours(0, 0, 0, 0)
    dt.setDate(dt.getDate() - i)
    const date = dt.toISOString().slice(0, 10)
    days.push({ date, total: dayMap.get(date) ?? 0, byProject: {} })
  }

  // 2. Pick top N projects, lump rest into "Other".
  const sortedProjects = [...projects].sort((a, b) => b.cost_usd - a.cost_usd)
  const top = sortedProjects.slice(0, CHART_TOP_PROJECTS)
  const others = sortedProjects.slice(CHART_TOP_PROJECTS)
  const totalProjectCost = sortedProjects.reduce((s, p) => s + p.cost_usd, 0) || 1
  const projectNames: string[] = top.map((p) => p.project_name)
  if (others.length > 0) projectNames.push('Other')

  // 3. Approximate per-day per-project split using monthly share. Each day's
  //    total is split in proportion to each project's monthly share. This is
  //    not exact — see TODO above — but it's directionally honest and the
  //    chart caveat says so.
  const shares: Record<string, number> = {}
  for (const p of top) shares[p.project_name] = p.cost_usd / totalProjectCost
  if (others.length > 0) {
    shares.Other = others.reduce((s, p) => s + p.cost_usd, 0) / totalProjectCost
  }

  for (const day of days) {
    for (const name of projectNames) {
      day.byProject[name] = day.total * (shares[name] ?? 0)
    }
  }

  const totalCost = days.reduce((s, d) => s + d.total, 0)
  return { days, projectNames, totalCost }
}

function formatChartDate(iso: string, long = false): string {
  // iso = 'YYYY-MM-DD'
  const dt = new Date(`${iso}T00:00:00`)
  if (long) {
    return dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric', weekday: 'short' })
  }
  return dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

// ============================================================
// Sub-components
// ============================================================

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
      <p className="text-caption uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
        {label}
      </p>
      <p
        className="text-mono text-[1.25rem] sm:text-[1.5rem] font-light"
        style={{ color: accentColor }}
      >
        {value}
      </p>
      <p className="text-caption text-[var(--color-stone)]/50 mt-1">{subValue}</p>
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
      type="button"
      className={cn(
        'px-2 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors flex items-center gap-1',
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

function UsageEmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-6 gap-3 max-w-md mx-auto">
      <BarChart3 className="w-8 h-8 text-[var(--color-stone)]/30" />
      <div>
        <p className="text-display text-[var(--color-paper)] mb-1">No spend recorded yet</p>
        <p className="text-body text-[var(--color-stone)]/60">
          Run an agent to start tracking cost by project. Daily totals, project breakdowns, and
          per-run details will land here automatically.
        </p>
      </div>
      <ul className="text-caption text-[var(--color-stone)]/50 text-left mt-2 flex flex-col gap-1">
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Daily, weekly, and monthly spend
          roll-ups
        </li>
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> 30-day stacked chart by project
        </li>
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Input / output token counts per
          run
        </li>
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Sortable run list — find the big
          spenders quickly
        </li>
      </ul>
    </div>
  )
}
