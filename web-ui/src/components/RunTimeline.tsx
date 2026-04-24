/**
 * Horizontal run timeline (Theme C1 — MVP replay viewer).
 *
 * Renders a horizontal strip with a dot per tool call, positioned
 * proportionally by timestamp across the total run duration. Hover to
 * preview the tool name + primary input; click to focus, revealing a
 * detail card below with full inputs + reasoning. Prev / Next buttons
 * step through the sequence of tool calls.
 *
 * Design decisions:
 *  - Client-side from messages.jsonl (no new endpoint) — same approach
 *    as ToolBreakdown (C3) and the inline reasoning (C2)
 *  - Dot color reuses the per-category palette from ToolBreakdown so
 *    users can recognise tool categories visually across both views
 *  - No cumulative-cost axis in the MVP — the SDK doesn't attribute
 *    cost per call (see ToolBreakdown footnote). A future extension
 *    can lay per-turn total cost over this timeline once that data
 *    exists.
 */

import { ChevronLeft, ChevronRight, Lightbulb } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

interface RunTimelineProps {
  /** Raw messages.jsonl content (one JSON object per line). */
  messages: string
}

interface TimelineEntry {
  index: number
  timestamp: string
  tsMs: number
  tool: string
  inputPreview: string
  reasoning: string | null
  input: Record<string, unknown> | null
}

interface ParsedMessage {
  type?: string
  timestamp?: string
  content?: unknown
  metadata?: {
    tool?: string
    input?: unknown
    reasoning?: string
  }
}

function parseEntries(messages: string): TimelineEntry[] {
  if (!messages) return []
  const entries: TimelineEntry[] = []
  // Thread reasoning same way as StreamingLogViewer does — walk forward,
  // track the most recent assistant text/thinking, adopt on tool_use.
  let currentReasoning: string | null = null
  let index = 0
  for (const line of messages.split('\n')) {
    if (!line.trim()) continue
    let m: ParsedMessage
    try {
      m = JSON.parse(line) as ParsedMessage
    } catch {
      continue
    }
    if (m.type === 'user') {
      currentReasoning = null
      continue
    }
    if (m.type === 'thinking' || m.type === 'text') {
      const text = typeof m.content === 'string' ? m.content.trim() : ''
      if (text) currentReasoning = text
      continue
    }
    if (m.type !== 'tool_use') continue
    const tool = m.metadata?.tool || 'unknown'
    const input = (m.metadata?.input ?? null) as Record<string, unknown> | null
    const inputPreview = summarizeInput(input)
    const ts = m.timestamp ?? new Date().toISOString()
    entries.push({
      index: index++,
      timestamp: ts,
      tsMs: new Date(ts).getTime(),
      tool,
      inputPreview,
      reasoning: m.metadata?.reasoning ?? currentReasoning,
      input,
    })
  }
  return entries
}

function summarizeInput(input: Record<string, unknown> | null): string {
  if (!input) return ''
  // Prefer the most "primary" field for a given tool
  const primary =
    input.file_path ?? input.command ?? input.path ?? input.pattern ?? input.url ?? input.query
  if (primary != null) {
    const s = String(primary)
    return s.length > 80 ? `${s.slice(0, 77)}…` : s
  }
  // Fall back to the first string-valued field
  for (const [k, v] of Object.entries(input)) {
    if (typeof v === 'string') {
      const s = `${k}=${v}`
      return s.length > 80 ? `${s.slice(0, 77)}…` : s
    }
  }
  return ''
}

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
    return 'bg-purple-400'
  }
  if (n === 'webfetch' || n === 'websearch') {
    return 'bg-blue-400'
  }
  return 'bg-[var(--color-stone)]/50'
}

function formatTime(ts: string | null): string {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return ''
  }
}

function formatOffset(fromMs: number | null, toMs: number | null): string {
  if (fromMs == null || toMs == null) return ''
  const diff = Math.max(0, toMs - fromMs) / 1000
  if (diff < 60) return `+${diff.toFixed(1)}s`
  const m = Math.floor(diff / 60)
  const s = Math.round(diff % 60)
  return `+${m}m${s.toString().padStart(2, '0')}s`
}

export function RunTimeline({ messages }: RunTimelineProps) {
  const entries = useMemo(() => parseEntries(messages), [messages])
  const [selected, setSelected] = useState<number | null>(null)
  const [hovered, setHovered] = useState<number | null>(null)
  const focusRef = useRef<HTMLDivElement>(null)

  // Auto-scroll the detail card into view when selection changes (but not on
  // the very first render — don't yank the page around unannounced).
  const didMount = useRef(false)
  useEffect(() => {
    if (!didMount.current) {
      didMount.current = true
      return
    }
    if (selected == null) return
    focusRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [selected])

  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-[var(--color-stone)]/60">
        <p className="text-body">No tool calls to replay yet.</p>
      </div>
    )
  }

  const startMs = entries[0].tsMs
  const endMs = entries[entries.length - 1].tsMs
  const span = Math.max(1, endMs - startMs) // avoid div-by-zero for single-point runs

  const focused = selected != null ? entries[selected] : null
  const active = hovered ?? selected

  const goTo = (idx: number) => {
    const clamped = Math.max(0, Math.min(entries.length - 1, idx))
    setSelected(clamped)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header: summary + playback controls */}
      <div className="flex items-center justify-between gap-4 p-4 border-b border-[rgba(163,163,163,0.08)]">
        <div>
          <p className="text-title text-[var(--color-paper)]">Run Timeline</p>
          <p className="text-caption text-[var(--color-stone)]/60 mt-1">
            {entries.length} tool call{entries.length === 1 ? '' : 's'} ·{' '}
            {formatTime(entries[0].timestamp)} → {formatTime(entries[entries.length - 1].timestamp)}
          </p>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => goTo((selected ?? entries.length) - 1)}
            className="p-2 rounded-sm border border-[rgba(163,163,163,0.15)] text-[var(--color-stone)]/70 hover:text-[var(--color-paper)] hover:bg-[var(--color-paper)]/[0.04] disabled:opacity-40 disabled:cursor-not-allowed"
            disabled={selected === 0}
            aria-label="Previous tool call"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
          </button>
          <span className="text-caption text-mono text-[var(--color-stone)]/60 px-2 min-w-[60px] text-center">
            {selected != null ? `${selected + 1} / ${entries.length}` : `— / ${entries.length}`}
          </span>
          <button
            type="button"
            onClick={() => goTo((selected ?? -1) + 1)}
            className="p-2 rounded-sm border border-[rgba(163,163,163,0.15)] text-[var(--color-stone)]/70 hover:text-[var(--color-paper)] hover:bg-[var(--color-paper)]/[0.04] disabled:opacity-40 disabled:cursor-not-allowed"
            disabled={selected === entries.length - 1}
            aria-label="Next tool call"
          >
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Timeline strip */}
      <div className="p-4 pb-6">
        <div className="relative h-10">
          {/* Baseline */}
          <div className="absolute left-0 right-0 top-1/2 h-px bg-[rgba(163,163,163,0.15)] -translate-y-1/2" />

          {/* Dots */}
          {entries.map((e) => {
            const leftPct = ((e.tsMs - startMs) / span) * 100
            const isActive = active === e.index
            const isSelected = selected === e.index
            return (
              <button
                type="button"
                key={`${e.timestamp}-${e.index}`}
                className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 group"
                style={{ left: `${leftPct}%` }}
                onClick={() => setSelected(e.index)}
                onMouseEnter={() => setHovered(e.index)}
                onMouseLeave={() => setHovered(null)}
                aria-label={`${e.tool} at ${formatTime(e.timestamp)}`}
              >
                <span
                  className={`block rounded-full transition-all ${toolColorClass(e.tool)} ${
                    isSelected
                      ? 'w-3 h-3 ring-2 ring-[var(--color-paper)]/60'
                      : isActive
                        ? 'w-2.5 h-2.5'
                        : 'w-2 h-2'
                  }`}
                />
                {/* Hover tooltip */}
                {isActive && (
                  <div className="absolute left-1/2 -translate-x-1/2 -top-9 whitespace-nowrap pointer-events-none z-10 px-2 py-1 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.2)] rounded-sm text-caption">
                    <span className="text-[var(--color-paper)] font-mono font-medium">
                      {e.tool}
                    </span>
                    <span className="text-[var(--color-stone)]/50 ml-2 text-mono">
                      {formatOffset(startMs, e.tsMs)}
                    </span>
                  </div>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {/* Detail card for selected dot */}
      <div ref={focusRef} className="flex-1 overflow-auto border-t border-[rgba(163,163,163,0.08)]">
        {focused ? (
          <div className="p-4 space-y-4">
            {/* Header row */}
            <div className="flex items-baseline justify-between gap-4">
              <div className="flex items-center gap-2">
                <span
                  className={`w-2.5 h-2.5 rounded-full ${toolColorClass(focused.tool)}`}
                  aria-hidden="true"
                />
                <span className="text-title text-[var(--color-paper)] font-mono">
                  {focused.tool}
                </span>
                <span className="text-caption text-[var(--color-stone)]/50">
                  #{focused.index + 1} of {entries.length}
                </span>
              </div>
              <span className="text-caption text-mono text-[var(--color-stone)]/50">
                {formatTime(focused.timestamp)} · {formatOffset(startMs, focused.tsMs)}
              </span>
            </div>

            {/* Reasoning (C2 threading — same field) */}
            {focused.reasoning && (
              <div className="p-3 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm">
                <div className="flex items-center gap-1.5 mb-1.5">
                  <Lightbulb className="w-3 h-3 text-[var(--color-harvest)]/70" />
                  <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60">
                    Reasoning
                  </span>
                </div>
                <p className="text-body text-[var(--color-paper)]/70 whitespace-pre-wrap">
                  {focused.reasoning}
                </p>
              </div>
            )}

            {/* Inputs */}
            {focused.input && Object.keys(focused.input).length > 0 && (
              <div>
                <p className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60 mb-2">
                  Inputs
                </p>
                <div className="space-y-1">
                  {Object.entries(focused.input).map(([k, v]) => (
                    <div key={k} className="flex gap-2 text-body font-mono">
                      <span className="text-[var(--color-stone)]/50 shrink-0 min-w-[80px]">
                        {k}
                      </span>
                      <span className="text-[var(--color-paper)]/70 whitespace-pre-wrap break-all">
                        {typeof v === 'string' ? v : JSON.stringify(v)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="p-4 text-caption text-[var(--color-stone)]/50">
            Click a dot on the timeline or use the arrow buttons above to focus a tool call.
          </div>
        )}
      </div>
    </div>
  )
}
