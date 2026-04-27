import {
  Archive,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock,
  ExternalLink,
  GitBranch,
  GitMerge,
  GitPullRequest,
  Pin,
  PinOff,
  Play,
  RefreshCw,
  Sparkles,
  X,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { useNotificationCenter } from '@/hooks/useNotificationCenter'
import {
  archiveRun,
  cancelRun,
  createPrForRun,
  fetchLogs,
  fetchRun,
  fetchRunFiles,
  mergeRunBranch,
  queueFollowup,
  resumeRun,
} from '@/lib/api'
import { formatRelativeTime } from '@/lib/timestamps'
import type { Run, RunDetail, RunFilesResponse, RunStatus } from '@/lib/types'
import { cn } from '@/lib/utils'
import { StreamingLogViewer } from './StreamingLogViewer'

interface AgentMessage {
  timestamp: string
  type: 'text' | 'tool_use' | 'system' | 'error' | 'result' | 'user'
  content: string
  metadata?: {
    tool?: string
    tool_id?: string
    input?: unknown
    session_id?: string
    cost?: number
    tokens_in?: number
    tokens_out?: number
  }
}

function parseMessages(messagesContent: string): AgentMessage[] {
  if (!messagesContent) return []
  const lines = messagesContent.trim().split('\n')
  const messages: AgentMessage[] = []
  for (const line of lines) {
    if (!line.trim()) continue
    try {
      messages.push(JSON.parse(line))
    } catch {
      // Skip invalid JSON lines
    }
  }
  return messages
}

function formatTokens(tokens: number | null): string {
  if (tokens === null || tokens === undefined) return '-'
  if (tokens < 1000) return `${tokens}`
  if (tokens < 1000000) return `${(tokens / 1000).toFixed(1)}k`
  return `${(tokens / 1000000).toFixed(2)}M`
}

interface ListViewPageProps {
  runs: Run[]
  onRunUpdate: (run: Run) => void
  onRefresh: () => void
}

type PinnedSet = Set<string>

function getStatusDotClass(run: Run, hasPendingQuestion: boolean): string {
  if (hasPendingQuestion) return 'bg-[var(--color-harvest)] animate-pulse'
  switch (run.status) {
    case 'running':
      return 'bg-[var(--color-sky)] animate-pulse'
    case 'pending':
      return 'bg-[var(--color-stone)]/60'
    case 'review':
      return 'bg-purple-400'
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

export function ListViewPage({ runs, onRunUpdate, onRefresh }: ListViewPageProps) {
  const { pendingQuestions } = useNotificationCenter()
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [messages, setMessages] = useState<string>('')
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [filesData, setFilesData] = useState<RunFilesResponse | null>(null)
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(new Set())
  const [pinnedRuns, setPinnedRuns] = useState<PinnedSet>(() => {
    try {
      const stored = localStorage.getItem('gluon-pinned-runs')
      return stored ? new Set(JSON.parse(stored)) : new Set()
    } catch {
      return new Set()
    }
  })

  // Action states
  const [resumePrompt, setResumePrompt] = useState('')
  const [resuming, setResuming] = useState(false)
  const [queuing, setQueuing] = useState(false)
  const [merging, setMerging] = useState(false)
  const [creatingPr, setCreatingPr] = useState(false)
  const resumeTextareaRef = useRef<HTMLTextAreaElement>(null)

  // Persist pins
  useEffect(() => {
    localStorage.setItem('gluon-pinned-runs', JSON.stringify([...pinnedRuns]))
  }, [pinnedRuns])

  // Group runs by project, sorted alphabetically; pinned runs float to top
  const groupedRuns = useMemo(() => {
    const groups = new Map<string, Run[]>()
    for (const run of runs) {
      if (run.archived) continue
      const key = run.project_name
      if (!groups.has(key)) groups.set(key, [])
      groups.get(key)!.push(run)
    }
    // Sort runs within each project: pinned first, then by created_at desc
    for (const [, projectRuns] of groups) {
      projectRuns.sort((a, b) => {
        const aPinned = pinnedRuns.has(a.id)
        const bPinned = pinnedRuns.has(b.id)
        if (aPinned !== bPinned) return aPinned ? -1 : 1
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      })
    }
    // Sort projects alphabetically; projects with pinned runs first
    const sorted = new Map(
      [...groups.entries()].sort(([aKey, aRuns], [bKey, bRuns]) => {
        const aHasPinned = aRuns.some((r) => pinnedRuns.has(r.id))
        const bHasPinned = bRuns.some((r) => pinnedRuns.has(r.id))
        if (aHasPinned !== bHasPinned) return aHasPinned ? -1 : 1
        return aKey.localeCompare(bKey)
      })
    )
    return sorted
  }, [runs, pinnedRuns])

  // Auto-select first run if none selected
  useEffect(() => {
    if (selectedRunId) {
      const exists = runs.some((r) => r.id === selectedRunId)
      if (exists) return
    }
    const first = groupedRuns.values().next().value
    if (first && first.length > 0) {
      setSelectedRunId(first[0].id)
    }
  }, [groupedRuns, selectedRunId, runs])

  // Fetch detail + messages when selection changes
  useEffect(() => {
    if (!selectedRunId) {
      setDetail(null)
      setMessages('')
      setFilesData(null)
      return
    }
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
        setMessages(messagesLog.content || '')
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
        setMessages(messagesLog.content || '')
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

  const togglePin = useCallback((runId: string) => {
    setPinnedRuns((prev) => {
      const next = new Set(prev)
      if (next.has(runId)) next.delete(runId)
      else next.add(runId)
      return next
    })
  }, [])

  const toggleProject = useCallback((project: string) => {
    setCollapsedProjects((prev) => {
      const next = new Set(prev)
      if (next.has(project)) next.delete(project)
      else next.add(project)
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
        console.error('Failed to archive:', err)
      }
    },
    [selectedRunId, onRefresh]
  )

  const handleCancel = useCallback(
    async (runId: string) => {
      try {
        const updated = await cancelRun(runId)
        onRunUpdate(updated)
      } catch (err) {
        console.error('Failed to cancel:', err)
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
      // Refresh detail
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

  const parsedMessages = useMemo(() => parseMessages(messages), [messages])

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      {/* Left sidebar — session list */}
      <div className="w-64 lg:w-72 xl:w-80 shrink-0 border-r border-[rgba(163,163,163,0.1)] flex flex-col overflow-hidden">
        <div className="overflow-y-auto flex-1 py-1">
          {groupedRuns.size === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center px-4">
              <p className="text-body text-[var(--color-stone)]/50">No sessions</p>
            </div>
          )}
          {[...groupedRuns.entries()].map(([project, projectRuns]) => {
            const isCollapsed = collapsedProjects.has(project)
            return (
              <div key={project}>
                {/* Project header */}
                <button
                  className="w-full flex items-center gap-1.5 px-3 py-1.5 text-caption uppercase tracking-widest text-[var(--color-stone)]/60 hover:text-[var(--color-stone)] transition-colors"
                  onClick={() => toggleProject(project)}
                >
                  {isCollapsed ? (
                    <ChevronRight className="w-3 h-3 shrink-0" />
                  ) : (
                    <ChevronDown className="w-3 h-3 shrink-0" />
                  )}
                  <span className="truncate">{project}</span>
                  <span className="ml-auto text-[var(--color-stone)]/40 tabular-nums">
                    {projectRuns.length}
                  </span>
                </button>
                {/* Session list */}
                {!isCollapsed &&
                  projectRuns.map((run) => {
                    const hasPendingQ = pendingQuestions.some((q) => q.run_id === run.id)
                    const isPinned = pinnedRuns.has(run.id)
                    const isSelected = run.id === selectedRunId
                    return (
                      <div
                        key={run.id}
                        className={cn(
                          'group flex items-start gap-2 px-3 py-2 cursor-pointer transition-colors border-l-2',
                          isSelected
                            ? 'bg-[var(--color-paper)]/8 border-l-[var(--color-paper)]/40'
                            : 'border-l-transparent hover:bg-[var(--color-paper)]/4'
                        )}
                        onClick={() => setSelectedRunId(run.id)}
                      >
                        {/* Status dot */}
                        <div className="mt-1.5 shrink-0">
                          <div
                            className={cn(
                              'w-2 h-2 rounded-full',
                              getStatusDotClass(run, hasPendingQ)
                            )}
                            title={getStatusDotTitle(run, hasPendingQ)}
                          />
                        </div>
                        {/* Content */}
                        <div className="flex-1 min-w-0">
                          <p
                            className="text-body text-[var(--color-paper)] leading-snug truncate"
                            title={run.prompt}
                          >
                            {run.prompt}
                          </p>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className="text-caption text-[var(--color-stone)]/50">
                              {formatRelativeTime(run.created_at)}
                            </span>
                            {run.branch_name && (
                              <span className="flex items-center gap-0.5 text-caption text-purple-400/70">
                                <GitBranch className="w-2.5 h-2.5" />
                              </span>
                            )}
                            {run.pr_number && run.pr_url && (
                              <a
                                href={run.pr_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className={cn(
                                  'flex items-center gap-0.5 px-1.5 py-0.5 rounded-sm text-[0.5625rem] transition-colors',
                                  run.pr_status === 'merged' &&
                                    'bg-[rgba(168,85,247,0.15)] text-purple-400',
                                  run.pr_status === 'closed' &&
                                    'bg-[rgba(239,68,68,0.15)] text-red-400',
                                  run.pr_status === 'open' &&
                                    run.ci_status === 'success' &&
                                    'bg-[rgba(34,197,94,0.15)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]',
                                  run.pr_status === 'open' &&
                                    run.ci_status === 'failure' &&
                                    'bg-[rgba(239,68,68,0.15)] text-red-400 hover:bg-[rgba(239,68,68,0.25)]',
                                  run.pr_status === 'open' &&
                                    run.ci_status === 'pending' &&
                                    'bg-[rgba(234,179,8,0.15)] text-yellow-400 hover:bg-[rgba(234,179,8,0.25)]',
                                  run.pr_status === 'open' &&
                                    !run.ci_status &&
                                    'bg-[rgba(34,197,94,0.15)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]'
                                )}
                                onClick={(e) => e.stopPropagation()}
                                title={`PR #${run.pr_number}${run.ci_status ? ` · CI: ${run.ci_status}` : ''}`}
                              >
                                {run.pr_status === 'merged' && (
                                  <GitPullRequest className="w-2.5 h-2.5" />
                                )}
                                {run.pr_status === 'closed' && <XCircle className="w-2.5 h-2.5" />}
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
                          </div>
                        </div>
                        {/* Pin / archive actions */}
                        <div className="flex flex-col gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            className={cn(
                              'p-0.5 rounded-sm transition-colors',
                              isPinned
                                ? 'text-[var(--color-harvest)]'
                                : 'text-[var(--color-stone)]/30 hover:text-[var(--color-stone)]'
                            )}
                            onClick={(e) => {
                              e.stopPropagation()
                              togglePin(run.id)
                            }}
                            title={isPinned ? 'Unpin' : 'Pin to top'}
                          >
                            {isPinned ? (
                              <PinOff className="w-3 h-3" />
                            ) : (
                              <Pin className="w-3 h-3" />
                            )}
                          </button>
                          {run.status !== 'running' && run.status !== 'pending' && (
                            <button
                              className="p-0.5 text-[var(--color-stone)]/30 hover:text-[var(--color-stone)] rounded-sm transition-colors"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleArchive(run.id)
                              }}
                              title="Archive"
                            >
                              <Archive className="w-3 h-3" />
                            </button>
                          )}
                          {(run.status === 'running' || run.status === 'pending') && (
                            <button
                              className="p-0.5 text-[var(--color-stone)]/30 hover:text-[var(--color-vermillion)] rounded-sm transition-colors"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleCancel(run.id)
                              }}
                              title="Cancel"
                            >
                              <X className="w-3 h-3" />
                            </button>
                          )}
                        </div>
                      </div>
                    )
                  })}
              </div>
            )
          })}
        </div>
      </div>

      {/* Right panel — messages + action bar + footer */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
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
            {/* Messages area */}
            <div className="flex-1 overflow-hidden">
              <StreamingLogViewer
                runId={selectedRunId}
                runStatus={(selectedRun.status ?? 'pending') as RunStatus}
                initialMessages={parsedMessages}
              />
            </div>

            {/* Action bar — chat input + git info + buttons */}
            <div className="shrink-0 border-t border-[rgba(163,163,163,0.1)]">
              {/* Git info row + action buttons */}
              {detail && (
                <div className="flex items-center justify-between px-3 py-1.5 border-b border-[rgba(163,163,163,0.06)] bg-[var(--color-void)]/50">
                  <div className="flex items-center gap-3 min-w-0">
                    {detail.branch_name && (
                      <span className="flex items-center gap-1.5 text-body text-purple-400/80">
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
                            ? 'bg-[rgba(239,68,68,0.15)] text-red-400'
                            : detail.pr_status === 'open'
                              ? 'bg-[rgba(34,197,94,0.1)] text-green-400'
                              : detail.pr_status === 'merged'
                                ? 'bg-[rgba(168,85,247,0.1)] text-purple-400'
                                : 'bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]'
                        )}
                      >
                        <GitPullRequest className="w-3 h-3" />
                        <span>#{detail.pr_number}</span>
                        <ExternalLink className="w-2.5 h-2.5 opacity-60" />
                      </a>
                    )}
                  </div>
                  {/* Action buttons */}
                  <div className="flex items-center gap-1.5 shrink-0">
                    {/* Create PR */}
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
                    {/* Merge — PR is open and mergeable */}
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
                              : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]'
                          )}
                        >
                          <GitMerge className="w-3 h-3" />
                          {merging ? 'Merging...' : 'Merge'}
                        </button>
                      )}
                    {/* Merge — worktree branch, no PR yet */}
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
                              : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]'
                          )}
                        >
                          <GitMerge className="w-3 h-3" />
                          {merging ? 'Merging...' : 'Merge'}
                        </button>
                      )}
                    {/* Resolve conflicts */}
                    {detail.pr_mergeable === 'CONFLICTING' && isResumable && !isActive && (
                      <button
                        onClick={handleResolveConflicts}
                        className="flex items-center gap-1.5 px-2.5 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors bg-[rgba(168,85,247,0.15)] border border-[rgba(168,85,247,0.3)] text-purple-400 hover:bg-[rgba(168,85,247,0.25)]"
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
    </div>
  )
}
