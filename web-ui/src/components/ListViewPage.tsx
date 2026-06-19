import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  Beaker,
  Bell,
  Bug,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock,
  ExternalLink,
  FileText,
  GitBranch,
  GitMerge,
  GitPullRequest,
  Hammer,
  Inbox,
  MoreHorizontal,
  Pin,
  PinOff,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Sprout,
  X,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { useNotificationCenter } from '@/hooks/useNotificationCenter'
import { type AgentMessage, parseMessages } from '@/lib/agentMessage'
import {
  archiveRun,
  cancelRun,
  createPrForRun,
  fetchAttentionCounts,
  fetchLogs,
  fetchRun,
  fetchRunFiles,
  mergeRunBranch,
  queueFollowup,
  resumeRun,
  snoozeRun,
  unarchiveRun,
  updateRun,
} from '@/lib/api'
import { formatTokens } from '@/lib/format'
import { formatRelativeTime } from '@/lib/timestamps'
import type {
  AttentionCountsResponse,
  Run,
  RunDetail,
  RunFilesResponse,
  RunKind,
  RunStatus,
} from '@/lib/types'
import { cn } from '@/lib/utils'
import { ForkRunDialog } from './ForkRunDialog'
import { InlineTitleEditor } from './InlineTitleEditor'
import { SnoozePopover } from './SnoozePopover'
import { StreamingLogViewer } from './StreamingLogViewer'

// ---------------------------------------------------------------------------
// Types & helpers
// ---------------------------------------------------------------------------

type SortMode = 'activity' | 'created' | 'project'

const KIND_META: Record<RunKind, { icon: typeof Beaker; tone: string; label: string }> = {
  research: { icon: Beaker, tone: 'text-[var(--color-orchid)]/70', label: 'Research' },
  build: { icon: Hammer, tone: 'text-[var(--color-sky)]/70', label: 'Build' },
  docs: { icon: FileText, tone: 'text-[var(--color-stone)]/60', label: 'Docs' },
  bug: { icon: Bug, tone: 'text-[var(--color-vermillion)]/70', label: 'Bug' },
  review: { icon: ShieldCheck, tone: 'text-[var(--color-harvest)]/70', label: 'Review' },
  chore: { icon: Sprout, tone: 'text-[var(--color-stone)]/50', label: 'Chore' },
}

function getStatusDotClass(run: Run, hasPendingQuestion: boolean): string {
  if (hasPendingQuestion) return 'bg-[var(--color-harvest)] animate-pulse'
  switch (run.status) {
    case 'running':
      return 'bg-[var(--color-sky)] animate-pulse'
    case 'pending':
      return 'bg-[var(--color-stone)]/60'
    case 'review':
      return 'bg-[var(--color-orchid)]'
    case 'completed':
      return 'bg-[var(--color-jade)]'
    case 'failed':
      return 'bg-[var(--color-vermillion)]'
    case 'cancelled':
      return 'bg-[var(--color-stone)]/40'
    default:
      return 'bg-[var(--color-stone)]/40'
  }
}

function getStatusDotTitle(run: Run, hasPendingQuestion: boolean): string {
  if (hasPendingQuestion) return 'Waiting for input'
  return run.status.charAt(0).toUpperCase() + run.status.slice(1)
}

function lastActivityTs(run: Run): number {
  const candidates = [
    run.last_activity_at,
    run.completed_at,
    run.started_at,
    run.created_at,
  ].filter((v): v is string => typeof v === 'string')
  if (candidates.length === 0) return 0
  return new Date(candidates[0]).getTime()
}

function isAttentionRun(run: Run, hasPendingQuestion: boolean): boolean {
  if (hasPendingQuestion) return true
  if (run.status === 'failed') return true
  if (run.pr_mergeable === 'CONFLICTING') return true
  if (run.ci_status === 'failure') return true
  return false
}

function isSnoozedRun(run: Run): boolean {
  if (!run.snoozed_until) return false
  return new Date(run.snoozed_until).getTime() > Date.now()
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ListViewPageProps {
  runs: Run[]
  onRunUpdate: (run: Run) => void
  onRefresh: () => void
  onNewTaskForProject?: (projectName: string) => void
}

type PinnedSet = Set<string>

interface SectionEntry {
  /** Unique key for React. */
  key: string
  /** Display title (uppercase caption). */
  title: string
  /** Icon shown before the title. */
  icon: typeof Inbox
  /** The runs in this section, already sorted. */
  runs: Run[]
  /** Optional: count of attention-needing runs to badge. */
  attention?: number
  /** Initial collapsed state. */
  defaultCollapsed?: boolean
}

// ---------------------------------------------------------------------------
// Per-row context menu
// ---------------------------------------------------------------------------

interface RowMenuProps {
  open: boolean
  anchorRect: DOMRect | null
  run: Run
  isPinned: boolean
  onClose: () => void
  onTogglePin: () => void
  onSnooze: (anchorRect: DOMRect) => void
  onUnsnooze: () => void
  onSetKind: (kind: RunKind | null) => void
  onFork: () => void
  onArchive: () => void
  onUnarchive: () => void
  onCancel: () => void
}

function RowMenu(props: RowMenuProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [kindOpen, setKindOpen] = useState(false)

  useEffect(() => {
    if (!props.open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        props.onClose()
      }
    }
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) props.onClose()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [props.open, props.onClose, props])

  if (!props.open || !props.anchorRect) return null

  const top = props.anchorRect.bottom + 4
  const maxLeft = window.innerWidth - 200 - 8
  const left = Math.min(props.anchorRect.left - 160, maxLeft)
  const isActive = props.run.status === 'running' || props.run.status === 'pending'
  const snoozed = isSnoozedRun(props.run)

  return (
    <div
      ref={ref}
      role="menu"
      className={cn(
        'fixed z-50 min-w-[180px] bg-[var(--color-ink)]',
        'border border-[rgba(163,163,163,0.15)] rounded-md shadow-xl py-1 text-body'
      )}
      style={{ top, left: Math.max(left, 8) }}
    >
      <MenuItem
        label={props.isPinned ? 'Unpin' : 'Pin to top'}
        icon={props.isPinned ? PinOff : Pin}
        onClick={() => {
          props.onTogglePin()
          props.onClose()
        }}
      />
      {!isActive && !snoozed && (
        <MenuItem
          label="Snooze…"
          icon={Clock}
          shortcut="H"
          onClick={(rect) => {
            props.onSnooze(rect)
            props.onClose()
          }}
        />
      )}
      {snoozed && (
        <MenuItem
          label="Wake up now"
          icon={Clock}
          onClick={() => {
            props.onUnsnooze()
            props.onClose()
          }}
        />
      )}
      <div
        className="relative"
        onMouseEnter={() => setKindOpen(true)}
        onMouseLeave={() => setKindOpen(false)}
      >
        <button
          type="button"
          className="w-full flex items-center justify-between gap-2 px-3 py-1.5 hover:bg-[var(--color-paper)]/5 text-[var(--color-paper)]"
        >
          <span className="flex items-center gap-2">
            <Sprout className="w-3 h-3 opacity-60" />
            Set kind
          </span>
          <ChevronRight className="w-3 h-3 opacity-60" />
        </button>
        {kindOpen && (
          <div
            className={cn(
              'absolute left-full top-0 ml-1 min-w-[140px] bg-[var(--color-ink)]',
              'border border-[rgba(163,163,163,0.15)] rounded-md shadow-xl py-1'
            )}
          >
            {(Object.keys(KIND_META) as RunKind[]).map((k) => {
              const meta = KIND_META[k]
              const Icon = meta.icon
              return (
                <button
                  key={k}
                  type="button"
                  className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-[var(--color-paper)]/5"
                  onClick={() => {
                    props.onSetKind(k)
                    props.onClose()
                  }}
                >
                  <Icon className={cn('w-3 h-3', meta.tone)} />
                  <span className="text-[var(--color-paper)]">{meta.label}</span>
                  {props.run.kind === k && <Check className="w-3 h-3 ml-auto opacity-60" />}
                </button>
              )
            })}
            <div className="border-t border-[rgba(163,163,163,0.08)] mt-1 pt-1">
              <button
                type="button"
                className="w-full text-left px-3 py-1.5 hover:bg-[var(--color-paper)]/5 text-[var(--color-stone)]"
                onClick={() => {
                  props.onSetKind(null)
                  props.onClose()
                }}
              >
                Clear
              </button>
            </div>
          </div>
        )}
      </div>
      {/* Show Fork on rows that have started (proxy for "has an SDK session").
          The server validates this strictly; we just hide the obviously-unusable case. */}
      {props.run.started_at && (
        <MenuItem
          label="Fork…"
          icon={GitBranch}
          shortcut="F"
          onClick={() => {
            props.onFork()
            props.onClose()
          }}
        />
      )}
      <div className="border-t border-[rgba(163,163,163,0.08)] my-1" />
      {!isActive && !props.run.archived && (
        <MenuItem
          label="Archive"
          icon={Archive}
          shortcut="X"
          onClick={() => {
            props.onArchive()
            props.onClose()
          }}
        />
      )}
      {props.run.archived && (
        <MenuItem
          label="Unarchive"
          icon={ArchiveRestore}
          onClick={() => {
            props.onUnarchive()
            props.onClose()
          }}
        />
      )}
      {isActive && (
        <MenuItem
          label="Cancel"
          icon={X}
          tone="text-[var(--color-vermillion)]"
          onClick={() => {
            props.onCancel()
            props.onClose()
          }}
        />
      )}
    </div>
  )
}

interface MenuItemProps {
  label: string
  icon: typeof Pin
  shortcut?: string
  tone?: string
  onClick: (rect: DOMRect) => void
}

function MenuItem({ label, icon: Icon, shortcut, tone, onClick }: MenuItemProps) {
  return (
    <button
      type="button"
      className={cn(
        'w-full flex items-center justify-between gap-2 px-3 py-1.5',
        'hover:bg-[var(--color-paper)]/5',
        tone || 'text-[var(--color-paper)]'
      )}
      onClick={(e) => {
        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
        onClick(rect)
      }}
    >
      <span className="flex items-center gap-2">
        <Icon className="w-3 h-3 opacity-60" />
        {label}
      </span>
      {shortcut && (
        <span className="text-caption text-[var(--color-stone)]/40 tabular-nums">{shortcut}</span>
      )}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ListViewPage({
  runs,
  onRunUpdate,
  onRefresh,
  onNewTaskForProject,
}: ListViewPageProps) {
  const { pendingQuestions } = useNotificationCenter()
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [filesData, setFilesData] = useState<RunFilesResponse | null>(null)
  const [pinnedRuns, setPinnedRuns] = useState<PinnedSet>(() => {
    try {
      const stored = localStorage.getItem('gluon-pinned-runs')
      return stored ? new Set(JSON.parse(stored)) : new Set()
    } catch {
      return new Set()
    }
  })
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem('gluon-listview-collapsed')
      return stored ? new Set(JSON.parse(stored)) : new Set(['snoozed', 'archived'])
    } catch {
      return new Set(['snoozed', 'archived'])
    }
  })
  const [searchQuery, setSearchQuery] = useState('')
  const [sortMode, setSortMode] = useState<SortMode>(() => {
    return (localStorage.getItem('gluon-listview-sort') as SortMode) || 'activity'
  })
  const [compactMode, setCompactMode] = useState<boolean>(() => {
    return localStorage.getItem('gluon-listview-compact') === '1'
  })
  const [attention, setAttention] = useState<AttentionCountsResponse | null>(null)

  // Menu state
  const [menuRunId, setMenuRunId] = useState<string | null>(null)
  const [menuAnchor, setMenuAnchor] = useState<DOMRect | null>(null)
  const [snoozeForRunId, setSnoozeForRunId] = useState<string | null>(null)
  const [snoozeAnchor, setSnoozeAnchor] = useState<DOMRect | null>(null)
  const [forkDialogFor, setForkDialogFor] = useState<Run | null>(null)

  // Per-instance viewer state. messagesByRun is STATE (not a ref) so that when
  // the async log fetch populates it, StreamingLogViewer re-renders with the
  // real messages — including the final `usage` message the context-usage
  // footer needs. A ref here meant the footer never appeared in the list view.
  const [openedRunIds, setOpenedRunIds] = useState<Set<string>>(new Set())
  const [messagesByRun, setMessagesByRun] = useState<Map<string, AgentMessage[]>>(new Map())

  // Action states
  const [resumePrompt, setResumePrompt] = useState('')
  const [resuming, setResuming] = useState(false)
  const [queuing, setQueuing] = useState(false)
  const [merging, setMerging] = useState(false)
  const [creatingPr, setCreatingPr] = useState(false)
  const resumeTextareaRef = useRef<HTMLTextAreaElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const sidebarRef = useRef<HTMLDivElement>(null)

  // Persistence
  useEffect(() => {
    localStorage.setItem('gluon-pinned-runs', JSON.stringify([...pinnedRuns]))
  }, [pinnedRuns])
  useEffect(() => {
    localStorage.setItem('gluon-listview-collapsed', JSON.stringify([...collapsedSections]))
  }, [collapsedSections])
  useEffect(() => {
    localStorage.setItem('gluon-listview-sort', sortMode)
  }, [sortMode])
  useEffect(() => {
    localStorage.setItem('gluon-listview-compact', compactMode ? '1' : '0')
  }, [compactMode])

  // Fetch attention counts on mount and refresh on an interval. Re-running on
  // every `runs` change would thrash; the interval is enough.
  useEffect(() => {
    let cancelled = false
    fetchAttentionCounts()
      .then((c) => !cancelled && setAttention(c))
      .catch(() => {})
    const i = setInterval(() => {
      fetchAttentionCounts()
        .then((c) => !cancelled && setAttention(c))
        .catch(() => {})
    }, 30000)
    return () => {
      cancelled = true
      clearInterval(i)
    }
  }, [])

  // ---- Section computation -------------------------------------------------

  const pendingQuestionByRun = useMemo(() => {
    const m = new Set<string>()
    for (const q of pendingQuestions) m.add(q.run_id)
    return m
  }, [pendingQuestions])

  const filteredRuns = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return runs
    return runs.filter((r) => {
      const haystack = [r.custom_title, r.prompt, r.project_name, r.branch_name, r.kind]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return haystack.includes(q)
    })
  }, [runs, searchQuery])

  const sections = useMemo<SectionEntry[]>(() => {
    const sortRuns = (rs: Run[]): Run[] => {
      const cloned = [...rs]
      cloned.sort((a, b) => {
        const aPin = pinnedRuns.has(a.id)
        const bPin = pinnedRuns.has(b.id)
        if (aPin !== bPin) return aPin ? -1 : 1
        if (sortMode === 'activity') {
          return lastActivityTs(b) - lastActivityTs(a)
        }
        if (sortMode === 'created') {
          return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        }
        // project: name, then active runs first, then most-recent activity
        const byProject = a.project_name.localeCompare(b.project_name)
        if (byProject !== 0) return byProject
        const aActive = a.status === 'running' || a.status === 'pending'
        const bActive = b.status === 'running' || b.status === 'pending'
        if (aActive !== bActive) return aActive ? -1 : 1
        return lastActivityTs(b) - lastActivityTs(a)
      })
      return cloned
    }

    const needsAttention: Run[] = []
    const running: Run[] = []
    const snoozed: Run[] = []
    const archived: Run[] = []
    const byProject = new Map<string, Run[]>()

    for (const r of filteredRuns) {
      const isQ = pendingQuestionByRun.has(r.id)
      if (r.archived) {
        archived.push(r)
        continue
      }
      if (isSnoozedRun(r)) {
        snoozed.push(r)
        continue
      }
      if (isAttentionRun(r, isQ)) {
        needsAttention.push(r)
        continue
      }
      // In "Project" sort mode the user wants everything grouped under its
      // project, so running/pending tasks stay with their project instead of
      // being hoisted into a single global "Running" section. In activity/
      // created modes the cross-cutting Running section is the useful view.
      if ((r.status === 'running' || r.status === 'pending') && sortMode !== 'project') {
        running.push(r)
        continue
      }
      const list = byProject.get(r.project_name) ?? []
      list.push(r)
      byProject.set(r.project_name, list)
    }

    const result: SectionEntry[] = []
    if (needsAttention.length > 0) {
      result.push({
        key: 'needs',
        title: 'Needs you',
        icon: Bell,
        runs: sortRuns(needsAttention),
        attention: needsAttention.length,
      })
    }
    if (running.length > 0) {
      result.push({
        key: 'running',
        title: 'Running',
        icon: Play,
        runs: sortRuns(running),
      })
    }

    // Project sections — sorted alphabetically.
    const projectKeys = [...byProject.keys()].sort((a, b) => a.localeCompare(b))
    for (const name of projectKeys) {
      const projectRuns = byProject.get(name) ?? []
      result.push({
        key: `project:${name}`,
        title: name,
        icon: Inbox,
        runs: sortRuns(projectRuns),
      })
    }

    if (snoozed.length > 0) {
      result.push({
        key: 'snoozed',
        title: 'Snoozed',
        icon: Clock,
        runs: sortRuns(snoozed),
        defaultCollapsed: true,
      })
    }
    if (archived.length > 0) {
      result.push({
        key: 'archived',
        title: 'Archived',
        icon: Archive,
        runs: sortRuns(archived),
        defaultCollapsed: true,
      })
    }
    return result
  }, [filteredRuns, pinnedRuns, pendingQuestionByRun, sortMode])

  const flatVisibleRuns = useMemo(() => {
    const out: Run[] = []
    for (const s of sections) {
      if (collapsedSections.has(s.key)) continue
      out.push(...s.runs)
    }
    return out
  }, [sections, collapsedSections])

  // Two-pane (md+) auto-selects a run so the detail pane is never empty. On
  // mobile the layout is single-pane, so we must NOT auto-select — the user
  // lands on the list and taps into a run (otherwise "back to list" would
  // immediately bounce back to a re-selected detail).
  const [isTwoPane, setIsTwoPane] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(min-width: 768px)').matches
  )
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 768px)')
    const handler = (e: MediaQueryListEvent) => setIsTwoPane(e.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  // Auto-select first visible run if none selected (two-pane only)
  useEffect(() => {
    if (selectedRunId) {
      const stillVisible = flatVisibleRuns.some((r) => r.id === selectedRunId)
      if (stillVisible) return
      const stillExists = runs.some((r) => r.id === selectedRunId)
      if (stillExists) return
    }
    if (isTwoPane && flatVisibleRuns.length > 0) {
      setSelectedRunId(flatVisibleRuns[0].id)
    }
  }, [flatVisibleRuns, selectedRunId, runs, isTwoPane])

  // Fetch detail + messages when selection changes
  useEffect(() => {
    if (!selectedRunId) {
      setDetail(null)
      setFilesData(null)
      return
    }
    setOpenedRunIds((prev) => {
      if (prev.has(selectedRunId)) return prev
      const next = new Set(prev)
      next.add(selectedRunId)
      return next
    })
    let cancelled = false
    setLoadingDetail(true)
    Promise.all([
      fetchRun(selectedRunId),
      fetchLogs(selectedRunId, 'messages').catch(() => ({ content: '' })),
      fetchRunFiles(selectedRunId).catch(() => null),
    ])
      .then(([runDetail, messagesLog, files]) => {
        if (cancelled) return
        setDetail(runDetail)
        const parsed = parseMessages(messagesLog.content || '')
        setMessagesByRun((prev) => new Map(prev).set(selectedRunId, parsed))
        setFilesData(files)
        setLoadingDetail(false)
      })
      .catch(() => {
        if (!cancelled) setLoadingDetail(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedRunId])

  // Poll messages while selected run is active
  useEffect(() => {
    if (!selectedRunId) return
    const run = runs.find((r) => r.id === selectedRunId)
    if (!run || (run.status !== 'running' && run.status !== 'pending')) return
    const interval = setInterval(async () => {
      try {
        const [runDetail, messagesLog] = await Promise.all([
          fetchRun(selectedRunId),
          fetchLogs(selectedRunId, 'messages').catch(() => ({ content: '' })),
        ])
        setDetail(runDetail)
        setMessagesByRun((prev) =>
          new Map(prev).set(selectedRunId, parseMessages(messagesLog.content || ''))
        )
        onRunUpdate(runDetail)
      } catch {
        // ignore
      }
    }, 3000)
    return () => clearInterval(interval)
  }, [selectedRunId, runs, onRunUpdate])

  const selectedRun = useMemo(
    () => runs.find((r) => r.id === selectedRunId) ?? null,
    [runs, selectedRunId]
  )

  const isActive = selectedRun?.status === 'running' || selectedRun?.status === 'pending'
  const isResumable =
    selectedRun?.status === 'completed' ||
    selectedRun?.status === 'failed' ||
    selectedRun?.status === 'review' ||
    selectedRun?.status === 'cancelled'
  // We use `detail.session_id` when available (RunDetail has the Claude session
  // id); otherwise fall back to `started_at` as a proxy for "has a session".
  const canFork = Boolean(detail?.session_id) || Boolean(selectedRun?.started_at)

  // ---- Mutations ---------------------------------------------------------

  const togglePin = useCallback((runId: string) => {
    setPinnedRuns((prev) => {
      const next = new Set(prev)
      if (next.has(runId)) next.delete(runId)
      else next.add(runId)
      return next
    })
  }, [])

  const toggleSection = useCallback((key: string) => {
    setCollapsedSections((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const handleArchive = useCallback(
    async (runId: string) => {
      try {
        await archiveRun(runId)
        if (selectedRunId === runId) setSelectedRunId(null)
        onRefresh()
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to archive')
      }
    },
    [selectedRunId, onRefresh]
  )

  const handleUnarchive = useCallback(
    async (runId: string) => {
      try {
        const r = await unarchiveRun(runId)
        onRunUpdate(r)
        onRefresh()
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to unarchive')
      }
    },
    [onRunUpdate, onRefresh]
  )

  const handleCancel = useCallback(
    async (runId: string) => {
      try {
        const updated = await cancelRun(runId)
        onRunUpdate(updated)
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to cancel')
      }
    },
    [onRunUpdate]
  )

  const handleSnooze = useCallback(
    async (runId: string, until: string | null) => {
      try {
        const r = await snoozeRun(runId, until)
        onRunUpdate(r)
        if (until) {
          toast.success('Snoozed', {
            description: `Wakes ${new Date(until).toLocaleString()}`,
          })
        }
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to snooze')
      }
    },
    [onRunUpdate]
  )

  const handleSetTitle = useCallback(
    async (runId: string, next: string | null) => {
      try {
        const r = await updateRun(runId, { custom_title: next })
        onRunUpdate(r)
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to rename')
      }
    },
    [onRunUpdate]
  )

  const handleSetKind = useCallback(
    async (runId: string, kind: RunKind | null) => {
      try {
        const r = await updateRun(runId, { kind })
        onRunUpdate(r)
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to update kind')
      }
    },
    [onRunUpdate]
  )

  const handleResume = useCallback(async () => {
    if (!selectedRunId || !resumePrompt.trim()) return
    setResuming(true)
    try {
      await resumeRun(selectedRunId, resumePrompt.trim())
      setResumePrompt('')
      if (resumeTextareaRef.current) resumeTextareaRef.current.style.height = 'auto'
      const runDetail = await fetchRun(selectedRunId)
      setDetail(runDetail)
      onRunUpdate(runDetail)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to resume')
    } finally {
      setResuming(false)
    }
  }, [selectedRunId, resumePrompt, onRunUpdate])

  const handleQueueFollowup = useCallback(async () => {
    if (!selectedRunId || !resumePrompt.trim()) return
    setQueuing(true)
    try {
      await queueFollowup(selectedRunId, resumePrompt.trim())
      setResumePrompt('')
      if (resumeTextareaRef.current) resumeTextareaRef.current.style.height = 'auto'
      toast.success('Message queued')
      const runDetail = await fetchRun(selectedRunId)
      setDetail(runDetail)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to queue')
    } finally {
      setQueuing(false)
    }
  }, [selectedRunId, resumePrompt])

  const handleCreatePr = useCallback(async () => {
    if (!selectedRunId) return
    setCreatingPr(true)
    try {
      const result = await createPrForRun(selectedRunId)
      toast.success('PR created', {
        action: result.pr_url
          ? { label: 'View', onClick: () => window.open(result.pr_url, '_blank') }
          : undefined,
      })
      const runDetail = await fetchRun(selectedRunId)
      setDetail(runDetail)
      onRunUpdate(runDetail)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to create PR')
    } finally {
      setCreatingPr(false)
    }
  }, [selectedRunId, onRunUpdate])

  const handleMerge = useCallback(async () => {
    if (!selectedRunId) return
    setMerging(true)
    try {
      await mergeRunBranch(selectedRunId)
      toast.success('Branch merged')
      const runDetail = await fetchRun(selectedRunId)
      setDetail(runDetail)
      onRunUpdate(runDetail)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to merge')
    } finally {
      setMerging(false)
    }
  }, [selectedRunId, onRunUpdate])

  const handleResolveConflicts = useCallback(() => {
    if (!detail) return
    const prompt = `The PR for this branch has merge conflicts. Please resolve them:

1. Rebase this branch onto ${detail.source_branch || 'main'}
2. For each conflict, understand the intent of both changes and merge them intelligently
3. After resolving all conflicts, force-push the rebased branch

Focus on preserving the functionality from both sides where possible.`
    setResumePrompt(prompt)
    setTimeout(() => resumeTextareaRef.current?.focus(), 100)
  }, [detail])

  // Evict opened viewers for archived/removed runs
  useEffect(() => {
    const runIds = new Set(runs.map((r) => r.id))
    setOpenedRunIds((prev) => {
      const next = new Set<string>()
      for (const id of prev) {
        if (runIds.has(id)) next.add(id)
      }
      return next.size === prev.size ? prev : next
    })
    // Drop cached messages for runs that no longer exist.
    setMessagesByRun((prev) => {
      let changed = false
      const next = new Map(prev)
      for (const id of prev.keys()) {
        if (!runIds.has(id)) {
          next.delete(id)
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [runs])

  // ---- Keyboard navigation -----------------------------------------------

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Ignore when typing in an input or contenteditable.
      const tgt = e.target as HTMLElement | null
      const tag = tgt?.tagName
      const inField =
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'SELECT' ||
        (tgt && (tgt as HTMLElement).isContentEditable)

      if (inField && e.key !== 'Escape') return

      // Allow '/' to focus search even outside fields.
      if (e.key === '/' && !inField) {
        e.preventDefault()
        searchInputRef.current?.focus()
        searchInputRef.current?.select()
        return
      }

      if (e.key === 'Escape' && inField) {
        if (tgt === searchInputRef.current && searchInputRef.current) {
          searchInputRef.current.blur()
          setSearchQuery('')
        }
        return
      }

      if (flatVisibleRuns.length === 0) return
      const currentIdx = flatVisibleRuns.findIndex((r) => r.id === selectedRunId)

      if (e.key === 'j' || e.key === 'ArrowDown') {
        e.preventDefault()
        const next = Math.min(currentIdx + 1, flatVisibleRuns.length - 1)
        setSelectedRunId(flatVisibleRuns[Math.max(next, 0)].id)
      } else if (e.key === 'k' || e.key === 'ArrowUp') {
        e.preventDefault()
        const next = Math.max(currentIdx - 1, 0)
        setSelectedRunId(flatVisibleRuns[next].id)
      } else if (e.key === 'x' && selectedRunId) {
        e.preventDefault()
        const run = flatVisibleRuns[currentIdx]
        if (run && run.status !== 'running' && run.status !== 'pending') {
          void handleArchive(selectedRunId)
        }
      } else if (e.key === 'f' && selectedRun) {
        e.preventDefault()
        if (canFork) setForkDialogFor(selectedRun)
      } else if (e.key === 'h' && selectedRun) {
        e.preventDefault()
        // Anchor to selected row if we can find it; otherwise center of viewport.
        const el = document.querySelector(`[data-run-id="${selectedRun.id}"]`)
        if (el) {
          const rect = el.getBoundingClientRect()
          setSnoozeAnchor(rect)
          setSnoozeForRunId(selectedRun.id)
        }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [flatVisibleRuns, selectedRunId, selectedRun, canFork, handleArchive])

  // ---- Render -----------------------------------------------------------

  const totalAttention = attention?.total ?? 0

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      {/* Left sidebar — full width on mobile (single-pane master-detail),
          fixed width alongside the detail pane from md up. */}
      <div
        className={cn(
          'shrink-0 border-r border-[rgba(163,163,163,0.1)] flex-col overflow-hidden',
          'w-full md:w-64 lg:w-72 xl:w-80',
          // Mobile: hide the list once a run is selected so the detail pane
          // takes over the full width; always visible from md up.
          selectedRunId ? 'hidden md:flex' : 'flex'
        )}
      >
        {/* Sidebar header — search + sort + density */}
        <div className="shrink-0 border-b border-[rgba(163,163,163,0.06)] px-2 py-1.5 flex items-center gap-1.5">
          <div className="flex-1 relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-[var(--color-stone)]/40 pointer-events-none" />
            <input
              ref={searchInputRef}
              type="text"
              placeholder="Search   /"
              className={cn(
                'w-full bg-[var(--color-ink)] border border-[rgba(163,163,163,0.08)] rounded-sm',
                'pl-7 pr-7 py-1.5 text-body text-[var(--color-paper)] focus:outline-none',
                'focus:border-[rgba(163,163,163,0.2)] placeholder:text-[var(--color-stone)]/40'
              )}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                type="button"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 p-0.5 text-[var(--color-stone)]/40 hover:text-[var(--color-stone)]"
                onClick={() => setSearchQuery('')}
              >
                <X className="w-3 h-3" />
              </button>
            )}
          </div>
          <select
            className={cn(
              'bg-[var(--color-ink)] border border-[rgba(163,163,163,0.08)] rounded-sm',
              'px-1.5 py-1.5 text-caption uppercase tracking-widest text-[var(--color-stone)]',
              'focus:outline-none cursor-pointer'
            )}
            value={sortMode}
            onChange={(e) => setSortMode(e.target.value as SortMode)}
            title="Sort"
          >
            <option value="activity">Activity</option>
            <option value="created">Created</option>
            <option value="project">Project</option>
          </select>
        </div>

        {/* Section list */}
        <div ref={sidebarRef} className="overflow-y-auto flex-1 py-1">
          {sections.length === 0 && (
            <EmptyState hasSearch={Boolean(searchQuery)} totalRuns={runs.length} />
          )}
          {sections.map((section) => {
            const isCollapsed = collapsedSections.has(section.key)
            const Icon = section.icon
            const isSmart = ['needs', 'running'].includes(section.key)
            const isQuiet = ['snoozed', 'archived'].includes(section.key)
            const projectAttention =
              section.key.startsWith('project:') && attention?.by_project
                ? sumProjectAttention(section, attention)
                : 0
            return (
              <div key={section.key}>
                <div className="group/section flex items-center">
                  <button
                    className={cn(
                      'flex-1 flex items-center gap-1.5 px-3 py-1.5 text-caption uppercase tracking-widest',
                      'hover:text-[var(--color-paper)] transition-colors',
                      isSmart && section.key === 'needs'
                        ? 'text-[var(--color-harvest)]'
                        : 'text-[var(--color-stone)]/60'
                    )}
                    onClick={() => toggleSection(section.key)}
                  >
                    {isCollapsed ? (
                      <ChevronRight className="w-3 h-3 shrink-0" />
                    ) : (
                      <ChevronDown className="w-3 h-3 shrink-0" />
                    )}
                    <Icon
                      className={cn(
                        'w-3 h-3 shrink-0',
                        section.key === 'needs' && totalAttention > 0 && 'animate-pulse'
                      )}
                    />
                    <span className="truncate flex-1 text-left">{section.title}</span>
                    <span className="text-[var(--color-stone)]/40 tabular-nums">
                      {section.runs.length}
                    </span>
                    {projectAttention > 0 && (
                      <span
                        className="ml-1 px-1 rounded-sm bg-[var(--color-harvest)]/20 text-[var(--color-harvest)] tabular-nums"
                        title={`${projectAttention} need${projectAttention === 1 ? 's' : ''} your attention`}
                      >
                        {projectAttention}!
                      </span>
                    )}
                  </button>
                  {section.key.startsWith('project:') && onNewTaskForProject && (
                    <button
                      type="button"
                      className={cn(
                        'shrink-0 mr-2 p-0.5 rounded-sm transition-all',
                        'text-[var(--color-stone)]/30 hover:text-[var(--color-paper)] hover:bg-[var(--color-paper)]/8',
                        'opacity-0 group-hover/section:opacity-100'
                      )}
                      onClick={(e) => {
                        e.stopPropagation()
                        onNewTaskForProject(section.title)
                      }}
                      title={`New task in ${section.title}`}
                    >
                      <Plus className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
                {!isCollapsed &&
                  section.runs.map((run) => {
                    const isPending = pendingQuestionByRun.has(run.id)
                    const isPinned = pinnedRuns.has(run.id)
                    const isSelected = run.id === selectedRunId
                    const kind = (run.kind ?? null) as RunKind | null
                    const kindMeta = kind && kind in KIND_META ? KIND_META[kind] : null
                    const KindIcon = kindMeta?.icon
                    return (
                      <div
                        key={run.id}
                        data-run-id={run.id}
                        className={cn(
                          'group relative flex items-start gap-2 px-3 cursor-pointer transition-colors',
                          compactMode ? 'py-1' : 'py-2',
                          isSelected
                            ? 'bg-[var(--color-paper)]/12 shadow-[inset_3px_0_0_var(--color-paper)]'
                            : 'hover:bg-[var(--color-paper)]/4',
                          isQuiet && !isSelected && 'opacity-70'
                        )}
                        onClick={() => setSelectedRunId(run.id)}
                      >
                        {/* Status dot */}
                        <div className="mt-1.5 shrink-0">
                          <div
                            className={cn(
                              'w-2 h-2 rounded-full',
                              getStatusDotClass(run, isPending)
                            )}
                            title={getStatusDotTitle(run, isPending)}
                          />
                        </div>

                        {/* Kind glyph */}
                        {KindIcon && (
                          <div className="mt-1 shrink-0" title={kindMeta?.label}>
                            <KindIcon className={cn('w-3 h-3', kindMeta?.tone)} />
                          </div>
                        )}

                        {/* Content */}
                        <div className="flex-1 min-w-0">
                          <p
                            className={cn(
                              'text-body text-[var(--color-paper)] leading-snug truncate',
                              isPinned && 'font-medium'
                            )}
                            title={run.custom_title || run.prompt}
                          >
                            {run.forked_from_run_id && (
                              <span
                                className="text-[var(--color-stone)]/50 mr-1"
                                title="Forked from another session"
                              >
                                ↳
                              </span>
                            )}
                            {run.custom_title?.trim() || run.prompt}
                          </p>
                          {!compactMode && (
                            <div className="flex items-center gap-2 mt-0.5">
                              <span className="text-caption text-[var(--color-stone)]/50">
                                {formatRelativeTime(run.last_activity_at || run.created_at)}
                              </span>
                              {section.key !== 'needs' &&
                                section.key !== 'running' &&
                                !section.key.startsWith('project:') && (
                                  <span className="text-caption text-[var(--color-stone)]/40 truncate">
                                    {run.project_name}
                                  </span>
                                )}
                              {run.branch_name && (
                                <span
                                  className="flex items-center gap-0.5 text-caption text-[var(--color-orchid)]/70"
                                  title={run.branch_name}
                                >
                                  <GitBranch className="w-2.5 h-2.5" />
                                </span>
                              )}
                              {run.pr_number && run.pr_url && (
                                <a
                                  href={run.pr_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className={cn(
                                    'flex items-center gap-0.5 px-1.5 py-0.5 rounded-sm text-micro transition-colors',
                                    run.pr_status === 'merged' &&
                                      'bg-[rgba(168,85,247,0.15)] text-[var(--color-orchid)]',
                                    run.pr_status === 'closed' &&
                                      'bg-[rgba(239,68,68,0.15)] text-[var(--color-vermillion)]',
                                    run.pr_status === 'open' &&
                                      run.ci_status === 'success' &&
                                      'bg-[rgba(34,197,94,0.15)] text-[var(--color-jade)] hover:bg-[rgba(34,197,94,0.25)]',
                                    run.pr_status === 'open' &&
                                      run.ci_status === 'failure' &&
                                      'bg-[rgba(239,68,68,0.15)] text-[var(--color-vermillion)] hover:bg-[rgba(239,68,68,0.25)]',
                                    run.pr_status === 'open' &&
                                      run.ci_status === 'pending' &&
                                      'bg-[rgba(234,179,8,0.15)] text-[var(--color-harvest)] hover:bg-[rgba(234,179,8,0.25)]',
                                    run.pr_status === 'open' &&
                                      !run.ci_status &&
                                      'bg-[rgba(34,197,94,0.15)] text-[var(--color-jade)] hover:bg-[rgba(34,197,94,0.25)]'
                                  )}
                                  onClick={(e) => e.stopPropagation()}
                                  title={`PR #${run.pr_number}${run.ci_status ? ` · CI: ${run.ci_status}` : ''}`}
                                >
                                  {run.pr_status === 'merged' && (
                                    <GitPullRequest className="w-2.5 h-2.5" />
                                  )}
                                  {run.pr_status === 'closed' && (
                                    <XCircle className="w-2.5 h-2.5" />
                                  )}
                                  {run.pr_status === 'open' && run.ci_status === 'success' && (
                                    <CheckCircle2 className="w-2.5 h-2.5" />
                                  )}
                                  {run.pr_status === 'open' && run.ci_status === 'failure' && (
                                    <XCircle className="w-2.5 h-2.5" />
                                  )}
                                  {run.pr_status === 'open' && run.ci_status === 'pending' && (
                                    <Circle className="w-2.5 h-2.5 animate-pulse" />
                                  )}
                                  {run.pr_status === 'open' && !run.ci_status && (
                                    <GitPullRequest className="w-2.5 h-2.5" />
                                  )}
                                  {!run.pr_status && <GitPullRequest className="w-2.5 h-2.5" />}
                                  <span>#{run.pr_number}</span>
                                </a>
                              )}
                              {isSnoozedRun(run) && (
                                <span
                                  className="flex items-center gap-0.5 text-caption text-[var(--color-stone)]/50"
                                  title={`Snoozed until ${new Date(
                                    run.snoozed_until || ''
                                  ).toLocaleString()}`}
                                >
                                  <Clock className="w-2.5 h-2.5" />
                                </span>
                              )}
                            </div>
                          )}
                        </div>

                        {/* Always-visible ⋯ menu trigger + pin indicator */}
                        <div className="flex items-center gap-0.5 shrink-0">
                          {isPinned && (
                            <Pin
                              className="w-2.5 h-2.5 text-[var(--color-harvest)]/80"
                              aria-label="Pinned"
                            />
                          )}
                          <button
                            type="button"
                            className={cn(
                              'p-0.5 rounded-sm transition-opacity',
                              'text-[var(--color-stone)]/30 hover:text-[var(--color-stone)]',
                              'opacity-50 group-hover:opacity-100'
                            )}
                            onClick={(e) => {
                              e.stopPropagation()
                              const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
                              setMenuRunId(run.id)
                              setMenuAnchor(rect)
                            }}
                            title="More actions"
                          >
                            <MoreHorizontal className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    )
                  })}
              </div>
            )
          })}
        </div>

        {/* Sidebar footer: density toggle */}
        <div className="shrink-0 border-t border-[rgba(163,163,163,0.06)] px-3 py-1 flex items-center justify-between text-caption text-[var(--color-stone)]/50">
          <button
            type="button"
            className="hover:text-[var(--color-stone)] transition-colors uppercase tracking-widest"
            onClick={() => setCompactMode((v) => !v)}
            title="Toggle density"
          >
            {compactMode ? 'Compact' : 'Comfortable'}
          </button>
          <span className="tabular-nums">{flatVisibleRuns.length} visible</span>
        </div>
      </div>

      {/* Right panel — messages + action bar + footer. On mobile it only
          appears once a run is selected (single-pane); always shown from md up. */}
      <div
        className={cn(
          'flex-1 flex-col overflow-hidden min-w-0',
          selectedRunId ? 'flex' : 'hidden md:flex'
        )}
      >
        {!selectedRun ? (
          <div className="flex items-center justify-center h-full text-[var(--color-stone)]/40 text-body">
            Select a session to view
          </div>
        ) : loadingDetail && !detail ? (
          <div className="flex items-center justify-center h-full">
            <RefreshCw className="w-5 h-5 text-[var(--color-stone)]/40 animate-spin" />
          </div>
        ) : (
          <>
            {/* Header: title editor + parent fork link */}
            {selectedRun && (
              <div className="shrink-0 px-3 py-1.5 border-b border-[rgba(163,163,163,0.06)] flex items-center gap-3 min-w-0">
                {/* Mobile: return to the list (single-pane navigation). */}
                <button
                  type="button"
                  className="md:hidden shrink-0 -ml-1 p-1 text-[var(--color-stone)]/60 hover:text-[var(--color-paper)] transition-colors"
                  onClick={() => setSelectedRunId(null)}
                  title="Back to list"
                  aria-label="Back to list"
                >
                  <ArrowLeft className="w-4 h-4" />
                </button>
                <InlineTitleEditor
                  value={selectedRun.custom_title ?? null}
                  placeholder={selectedRun.prompt.slice(0, 80)}
                  className="text-display text-[var(--color-paper)] min-w-0"
                  onSave={(next) => handleSetTitle(selectedRun.id, next)}
                />
                {selectedRun.forked_from_run_id && (
                  <button
                    type="button"
                    className="flex items-center gap-1 text-caption text-[var(--color-stone)]/50 hover:text-[var(--color-stone)] uppercase tracking-widest"
                    onClick={() =>
                      selectedRun.forked_from_run_id &&
                      setSelectedRunId(selectedRun.forked_from_run_id)
                    }
                    title="Jump to parent session"
                  >
                    <GitBranch className="w-3 h-3" />
                    <span>Parent</span>
                  </button>
                )}
              </div>
            )}

            {/* Messages area */}
            <div className="flex-1 overflow-hidden relative">
              {[...openedRunIds].map((openedId) => {
                const run = runs.find((r) => r.id === openedId)
                if (!run) return null
                const isVisible = openedId === selectedRunId
                return (
                  <div
                    key={openedId}
                    className="absolute inset-0"
                    style={{ display: isVisible ? 'block' : 'none' }}
                  >
                    <StreamingLogViewer
                      runId={openedId}
                      runStatus={(run.status ?? 'pending') as RunStatus}
                      initialMessages={messagesByRun.get(openedId) || []}
                    />
                  </div>
                )
              })}
            </div>

            {/* Action bar — chat input + git info + buttons */}
            <div className="shrink-0 border-t border-[rgba(163,163,163,0.1)]">
              {detail && (
                <div className="flex items-center justify-between px-3 py-1.5 border-b border-[rgba(163,163,163,0.06)] bg-[var(--color-void)]/50">
                  <div className="flex items-center gap-3 min-w-0">
                    {detail.branch_name && (
                      <span className="flex items-center gap-1.5 text-body text-[var(--color-orchid)]/80">
                        <GitBranch className="w-3 h-3" />
                        <span className="truncate max-w-[140px]">{detail.branch_name}</span>
                      </span>
                    )}
                    {filesData && filesData.total_additions + filesData.total_deletions > 0 && (
                      <span className="text-body tabular-nums">
                        <span className="text-[var(--color-jade)]">
                          +{filesData.total_additions}
                        </span>{' '}
                        <span className="text-[var(--color-vermillion)]">
                          -{filesData.total_deletions}
                        </span>
                      </span>
                    )}
                    {detail.pr_number && detail.pr_url && (
                      <a
                        href={detail.pr_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className={cn(
                          'flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-caption transition-colors',
                          detail.pr_mergeable === 'CONFLICTING'
                            ? 'bg-[rgba(239,68,68,0.15)] text-[var(--color-vermillion)]'
                            : detail.pr_status === 'open'
                              ? 'bg-[rgba(34,197,94,0.1)] text-[var(--color-jade)]'
                              : detail.pr_status === 'merged'
                                ? 'bg-[rgba(168,85,247,0.1)] text-[var(--color-orchid)]'
                                : 'bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]'
                        )}
                      >
                        <GitPullRequest className="w-3 h-3" />
                        <span>#{detail.pr_number}</span>
                        <ExternalLink className="w-2.5 h-2.5 opacity-60" />
                      </a>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {canFork && (
                      <button
                        onClick={() => setForkDialogFor(selectedRun)}
                        className={cn(
                          'flex items-center gap-1.5 px-2.5 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors',
                          'bg-[rgba(168,85,247,0.12)] border border-[rgba(168,85,247,0.25)] text-[var(--color-orchid)] hover:bg-[rgba(168,85,247,0.2)]'
                        )}
                        title="Fork this session (F)"
                      >
                        <GitBranch className="w-3 h-3" />
                        Fork
                      </button>
                    )}
                    {detail.use_worktree &&
                      detail.branch_name &&
                      detail.has_remote &&
                      !detail.pr_url &&
                      !isActive && (
                        <button
                          onClick={handleCreatePr}
                          disabled={creatingPr}
                          className={cn(
                            'flex items-center gap-1.5 px-2.5 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors',
                            creatingPr
                              ? 'bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/50 cursor-wait'
                              : 'bg-[rgba(102,178,255,0.15)] border border-[rgba(102,178,255,0.3)] text-[var(--color-sky)] hover:bg-[rgba(102,178,255,0.25)]'
                          )}
                        >
                          <GitPullRequest className="w-3 h-3" />
                          {creatingPr ? 'Creating...' : 'Create PR'}
                        </button>
                      )}
                    {detail.pr_status === 'open' &&
                      detail.pr_mergeable !== 'CONFLICTING' &&
                      detail.branch_name &&
                      !isActive && (
                        <button
                          onClick={handleMerge}
                          disabled={merging}
                          className={cn(
                            'flex items-center gap-1.5 px-2.5 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors',
                            merging
                              ? 'bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/50 cursor-wait'
                              : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-[var(--color-jade)] hover:bg-[rgba(34,197,94,0.25)]'
                          )}
                        >
                          <GitMerge className="w-3 h-3" />
                          {merging ? 'Merging...' : 'Merge'}
                        </button>
                      )}
                    {detail.use_worktree &&
                      detail.branch_name &&
                      !detail.pr_url &&
                      detail.pr_status !== 'merged' &&
                      !isActive &&
                      !(detail.has_remote && !detail.pr_url) && (
                        <button
                          onClick={handleMerge}
                          disabled={merging}
                          className={cn(
                            'flex items-center gap-1.5 px-2.5 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors',
                            merging
                              ? 'bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/50 cursor-wait'
                              : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-[var(--color-jade)] hover:bg-[rgba(34,197,94,0.25)]'
                          )}
                        >
                          <GitMerge className="w-3 h-3" />
                          {merging ? 'Merging...' : 'Merge'}
                        </button>
                      )}
                    {detail.pr_mergeable === 'CONFLICTING' && isResumable && !isActive && (
                      <button
                        onClick={handleResolveConflicts}
                        className="flex items-center gap-1.5 px-2.5 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors bg-[rgba(168,85,247,0.15)] border border-[rgba(168,85,247,0.3)] text-[var(--color-orchid)] hover:bg-[rgba(168,85,247,0.25)]"
                      >
                        <Sparkles className="w-3 h-3" />
                        Resolve
                      </button>
                    )}
                  </div>
                </div>
              )}

              {/* Chat input */}
              {selectedRun && (
                <div className="px-3 py-2 bg-[var(--color-void)]">
                  <div className="flex gap-2">
                    <textarea
                      ref={resumeTextareaRef}
                      className="flex-1 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.1)] rounded-sm px-3 py-2 text-input text-[var(--color-paper)] placeholder:text-[var(--color-stone)]/40 focus:outline-none focus:border-[rgba(163,163,163,0.2)] resize-none min-h-[38px] max-h-32"
                      placeholder={
                        isActive ? 'Send follow-up message...' : 'Continue with follow-up...'
                      }
                      value={resumePrompt}
                      onChange={(e) => setResumePrompt(e.target.value)}
                      onKeyDown={(e) => {
                        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                          e.preventDefault()
                          if (resumePrompt.trim() && !resuming && !queuing) {
                            if (isActive) handleQueueFollowup()
                            else handleResume()
                          }
                        }
                      }}
                      disabled={resuming || queuing}
                      rows={1}
                      onInput={(e) => {
                        const target = e.target as HTMLTextAreaElement
                        target.style.height = 'auto'
                        target.style.height = `${Math.min(target.scrollHeight, 128)}px`
                      }}
                    />
                    {isActive ? (
                      <div className="flex gap-1.5 shrink-0 self-start">
                        <button
                          className={cn(
                            'flex items-center justify-center rounded-sm text-body uppercase tracking-widest transition-colors px-3 py-2 gap-1.5',
                            resumePrompt.trim() && !queuing && !resuming
                              ? 'bg-[var(--color-stone)]/20 text-[var(--color-paper)] hover:bg-[var(--color-stone)]/30'
                              : 'bg-[var(--color-stone)]/10 text-[var(--color-stone)]/40 cursor-not-allowed'
                          )}
                          onClick={handleQueueFollowup}
                          disabled={!resumePrompt.trim() || queuing || resuming}
                          title="Add to queue"
                        >
                          <Clock className="w-3 h-3" />
                          <span>{queuing ? 'Queueing...' : 'Queue'}</span>
                        </button>
                        <button
                          className={cn(
                            'flex items-center justify-center rounded-sm text-body uppercase tracking-widest transition-colors px-3 py-2 gap-1.5',
                            resumePrompt.trim() && !resuming && !queuing
                              ? 'bg-[var(--color-paper)] text-[var(--color-void)] hover:opacity-90'
                              : 'bg-[var(--color-stone)]/20 text-[var(--color-stone)]/50 cursor-not-allowed'
                          )}
                          onClick={handleResume}
                          disabled={!resumePrompt.trim() || resuming || queuing}
                          title="Cancel current task and send immediately"
                        >
                          <Play className="w-3 h-3" />
                          <span>{resuming ? 'Sending...' : 'Send Now'}</span>
                        </button>
                      </div>
                    ) : (
                      <button
                        className={cn(
                          'flex items-center justify-center rounded-sm text-body uppercase tracking-widest transition-colors shrink-0 self-start px-4 py-2 gap-2',
                          resumePrompt.trim() && !resuming
                            ? 'bg-[var(--color-paper)] text-[var(--color-void)] hover:opacity-90'
                            : 'bg-[var(--color-stone)]/20 text-[var(--color-stone)]/50 cursor-not-allowed'
                        )}
                        onClick={handleResume}
                        disabled={!resumePrompt.trim() || resuming}
                        title={resuming ? 'Resuming...' : 'Resume'}
                      >
                        <Play className="w-3 h-3" />
                        <span>{resuming ? 'Resuming...' : 'Resume'}</span>
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Footer status — model, tokens, cost */}
            {detail && (
              <div className="shrink-0 px-3 py-1.5 border-t border-[rgba(163,163,163,0.06)] bg-[var(--color-void)]/50 flex items-center justify-between">
                <span className="text-caption text-[var(--color-stone)]/40 uppercase tracking-widest">
                  {selectedRun?.status}
                  {detail.duration_seconds != null && (
                    <span className="ml-2">
                      {detail.duration_seconds < 60
                        ? `${Math.round(detail.duration_seconds)}s`
                        : `${Math.floor(detail.duration_seconds / 60)}m ${Math.round(detail.duration_seconds % 60)}s`}
                    </span>
                  )}
                </span>
                <div className="flex items-center gap-4 text-caption text-[var(--color-stone)]/40">
                  {(detail.input_tokens || detail.output_tokens) && (
                    <span className="tabular-nums">
                      {formatTokens(detail.input_tokens)} in / {formatTokens(detail.output_tokens)}{' '}
                      out
                    </span>
                  )}
                  {detail.cost_usd != null && detail.cost_usd > 0 && (
                    <span className="text-[var(--color-harvest)]/60 tabular-nums">
                      ${detail.cost_usd.toFixed(3)}
                    </span>
                  )}
                  {detail.model_used && <span>{detail.model_used}</span>}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Per-row context menu */}
      {menuRunId &&
        (() => {
          const run = runs.find((r) => r.id === menuRunId)
          if (!run) return null
          return (
            <RowMenu
              open
              anchorRect={menuAnchor}
              run={run}
              isPinned={pinnedRuns.has(run.id)}
              onClose={() => setMenuRunId(null)}
              onTogglePin={() => togglePin(run.id)}
              onSnooze={(rect) => {
                setSnoozeForRunId(run.id)
                setSnoozeAnchor(rect)
              }}
              onUnsnooze={() => void handleSnooze(run.id, null)}
              onSetKind={(k) => void handleSetKind(run.id, k)}
              onFork={() => setForkDialogFor(run)}
              onArchive={() => void handleArchive(run.id)}
              onUnarchive={() => void handleUnarchive(run.id)}
              onCancel={() => void handleCancel(run.id)}
            />
          )
        })()}

      {/* Snooze popover */}
      <SnoozePopover
        open={Boolean(snoozeForRunId)}
        anchorRect={snoozeAnchor}
        onPick={(until) => {
          if (snoozeForRunId) void handleSnooze(snoozeForRunId, until)
          setSnoozeForRunId(null)
          setSnoozeAnchor(null)
        }}
        onClose={() => {
          setSnoozeForRunId(null)
          setSnoozeAnchor(null)
        }}
      />

      {/* Fork dialog */}
      <ForkRunDialog
        open={Boolean(forkDialogFor)}
        parent={forkDialogFor}
        onClose={() => setForkDialogFor(null)}
        onForked={(child) => {
          setSelectedRunId(child.id)
          onRefresh()
        }}
      />
    </div>
  )
}

function sumProjectAttention(section: SectionEntry, attn: AttentionCountsResponse): number {
  // section.runs share project_id; we just look up the first one.
  const first = section.runs[0]
  if (!first) return 0
  return attn.by_project[first.project_id] ?? 0
}

function EmptyState({ hasSearch, totalRuns }: { hasSearch: boolean; totalRuns: number }) {
  if (hasSearch) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-4">
        <Search className="w-5 h-5 text-[var(--color-stone)]/30 mb-2" />
        <p className="text-body text-[var(--color-stone)]/60">No matches</p>
        <p className="text-caption text-[var(--color-stone)]/40 mt-1">
          Try a different keyword or clear search.
        </p>
      </div>
    )
  }
  if (totalRuns === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6 gap-2">
        <Inbox className="w-6 h-6 text-[var(--color-stone)]/30" />
        <p className="text-body text-[var(--color-paper)]">No sessions yet</p>
        <p className="text-caption text-[var(--color-stone)]/50">
          Press <kbd className="px-1 rounded-sm bg-[var(--color-paper)]/10">N</kbd> to start a new
          task. Pin a thread (⋯ → Pin) to keep it at the top.
        </p>
      </div>
    )
  }
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-4">
      <p className="text-body text-[var(--color-stone)]/60">All caught up.</p>
    </div>
  )
}
