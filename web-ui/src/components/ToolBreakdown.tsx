/**
 * Per-run tool usage breakdown (Theme C3).
 *
 * Parses the run's messages.jsonl client-side and aggregates tool calls by
 * tool name. Shows a sorted bar-chart-style list: tool name, call count,
 * percentage of total calls, proportional fill bar, and first/last timestamps.
 *
 * Note on cost attribution: the Claude SDK reports total cost per run via
 * `ResultMessage.total_cost_usd` but does NOT attribute cost per individual
 * tool call. What we CAN show is frequency + recency distribution, which is
 * the most honest directional signal — "most of this run's effort went to
 * Read calls" tells an operator where to look without pretending to know
 * dollar attribution that isn't available.
 */

import { Wrench } from 'lucide-react'
import { useMemo } from 'react'

interface ToolBreakdownProps {
  /** Raw messages.jsonl content (one JSON object per line). */
  messages: string
  /** Total cost of the run, if known. Shown as context alongside counts. */
  totalCostUsd?: number | null
}

interface ToolStat {
  name: string
  count: number
  percent: number
  firstTs: string | null
  lastTs: string | null
}

interface ParsedMessage {
  type?: string
  timestamp?: string
  metadata?: { tool?: string }
}

function parseToolStats(messages: string): { stats: ToolStat[]; total: number } {
  if (!messages) return { stats: [], total: 0 }

  const counts = new Map<string, { count: number; first: string | null; last: string | null }>()

  for (const line of messages.split('\n')) {
    if (!line.trim()) continue
    let m: ParsedMessage
    try {
      m = JSON.parse(line) as ParsedMessage
    } catch {
      continue
    }
    if (m.type !== 'tool_use') continue
    const name = m.metadata?.tool || 'unknown'
    const ts = m.timestamp ?? null

    const prev = counts.get(name)
    if (prev) {
      prev.count += 1
      if (ts && (!prev.last || ts > prev.last)) prev.last = ts
      if (ts && (!prev.first || ts < prev.first)) prev.first = ts
    } else {
      counts.set(name, { count: 1, first: ts, last: ts })
    }
  }

  const total = Array.from(counts.values()).reduce((s, v) => s + v.count, 0)
  const stats: ToolStat[] = Array.from(counts.entries())
    .map(([name, v]) => ({
      name,
      count: v.count,
      percent: total > 0 ? (v.count / total) * 100 : 0,
      firstTs: v.first,
      lastTs: v.last,
    }))
    .sort((a, b) => b.count - a.count)

  return { stats, total }
}

function formatRelativeTime(ts: string | null): string {
  if (!ts) return ''
  try {
    const date = new Date(ts)
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ''
  }
}

/**
 * Color-coded dot per tool category. Matches the palette the Messages tab uses
 * for tool calls so users can visually correlate across tabs.
 */
function toolColorClass(name: string): string {
  const n = name.toLowerCase()
  if (n === 'read' || n === 'grep' || n === 'glob' || n === 'ls') {
    return 'bg-[var(--color-sky)]'
  }
  if (n === 'write' || n === 'edit' || n === 'notebookedit') {
    return 'bg-[var(--color-harvest)]'
  }
  if (n === 'bash') {
    return 'bg-[var(--color-vermillion)]'
  }
  if (n === 'todowrite') {
    return 'bg-[var(--color-jade)]'
  }
  if (n.startsWith('mcp__') || n === 'task') {
    return 'bg-[var(--color-orchid)]'
  }
  if (n === 'webfetch' || n === 'websearch') {
    return 'bg-[var(--color-sky)]'
  }
  return 'bg-[var(--color-stone)]/50'
}

export function ToolBreakdown({ messages, totalCostUsd }: ToolBreakdownProps) {
  const { stats, total } = useMemo(() => parseToolStats(messages), [messages])

  if (total === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-[var(--color-stone)]/60">
        <Wrench className="w-8 h-8 mb-3 text-[var(--color-stone)]/40" />
        <p className="text-body">No tool calls recorded for this run yet.</p>
      </div>
    )
  }

  const costCopy =
    totalCostUsd != null && totalCostUsd > 0
      ? `${total} tool call${total === 1 ? '' : 's'} · $${totalCostUsd.toFixed(4)} total run cost`
      : `${total} tool call${total === 1 ? '' : 's'}`

  return (
    <div className="space-y-4 p-4">
      {/* Summary row */}
      <div className="flex items-baseline justify-between gap-4 pb-3 border-b border-[rgba(163,163,163,0.1)]">
        <div>
          <p className="text-title text-[var(--color-paper)]">Tool Usage</p>
          <p className="text-caption text-[var(--color-stone)]/60 mt-1">{costCopy}</p>
        </div>
        <span className="text-caption text-[var(--color-stone)]/50">
          {stats.length} distinct tool{stats.length === 1 ? '' : 's'}
        </span>
      </div>

      {/* Per-tool list */}
      <ul className="space-y-2">
        {stats.map((stat) => (
          <li key={stat.name} className="flex items-center gap-3">
            {/* Color dot */}
            <span
              className={`w-2 h-2 rounded-full shrink-0 ${toolColorClass(stat.name)}`}
              aria-hidden="true"
            />

            {/* Tool name + count */}
            <div className="w-40 shrink-0">
              <span className="text-body text-[var(--color-paper)] truncate inline-block max-w-full align-middle">
                {stat.name}
              </span>
              <span className="text-mono text-caption text-[var(--color-stone)]/50 ml-2">
                {stat.count}
              </span>
            </div>

            {/* Percentage bar */}
            <div className="flex-1 h-2 bg-[rgba(163,163,163,0.08)] rounded-sm overflow-hidden">
              <div
                className={`h-full ${toolColorClass(stat.name)}`}
                style={{ width: `${stat.percent}%` }}
              />
            </div>

            {/* Percent text */}
            <span className="text-mono text-caption text-[var(--color-stone)]/70 w-12 text-right shrink-0">
              {stat.percent.toFixed(1)}%
            </span>

            {/* First/last timestamp range (desktop only) */}
            <span className="hidden md:inline text-caption text-[var(--color-stone)]/40 w-36 text-right shrink-0">
              {stat.firstTs === stat.lastTs
                ? formatRelativeTime(stat.firstTs)
                : `${formatRelativeTime(stat.firstTs)} – ${formatRelativeTime(stat.lastTs)}`}
            </span>
          </li>
        ))}
      </ul>

      {/* Honest caveat: we can't attribute $ per tool call */}
      <p className="text-caption text-[var(--color-stone)]/50 pt-2 border-t border-[rgba(163,163,163,0.08)]">
        Costs are reported per run, not per tool call — the SDK doesn't attribute dollars to
        individual calls. The counts and timing above show where the agent spent its effort, which
        is the most reliable proxy for where budget went.
      </p>
    </div>
  )
}
