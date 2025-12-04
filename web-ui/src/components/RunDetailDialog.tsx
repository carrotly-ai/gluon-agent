import { useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
} from '@/components/ui/dialog'
import { RotateCw, ChevronLeft, Copy, Check, Play, ChevronDown, Clock, GitBranch, GitCommit, ExternalLink, Archive, GitPullRequest } from 'lucide-react'
import type { Run, RunDetail } from '@/lib/types'
import { fetchRun, fetchLogs, cancelRun, resumeRun, fetchSessionHistory, archiveRun, createPrForRun } from '@/lib/api'
import { cn } from '@/lib/utils'

interface RunDetailDialogProps {
  run: Run | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onRunUpdated: (run: Run) => void
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

function formatTime(dateStr: string | null): string {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  const today = new Date()
  const isToday = date.toDateString() === today.toDateString()
  if (isToday) return `Today, ${formatTime(dateStr)}`
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ', ' + formatTime(dateStr)
}

function formatTokens(tokens: number | null): string {
  if (tokens === null || tokens === undefined) return '-'
  if (tokens < 1000) return `${tokens}`
  if (tokens < 1000000) return `${(tokens / 1000).toFixed(1)}k`
  return `${(tokens / 1000000).toFixed(2)}M`
}

interface AgentMessage {
  timestamp: string
  type: 'text' | 'tool_use' | 'system' | 'error' | 'result'
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

function formatToolInput(input: unknown): string {
  const inputStr = typeof input === 'string' ? input : JSON.stringify(input, null, 2)
  return inputStr.length > 200 ? inputStr.slice(0, 200) + '...' : inputStr
}

function MessageItem({ msg }: { msg: AgentMessage }) {
  const typeColors: Record<string, string> = {
    text: 'text-[var(--color-paper)]/70',
    tool_use: 'text-[var(--color-sky)]',
    system: 'text-[var(--color-stone)]/60',
    error: 'text-[var(--color-vermillion)]',
    result: 'text-[var(--color-jade)]',
  }

  const typeLabels: Record<string, string> = {
    text: 'TEXT',
    tool_use: 'TOOL',
    system: 'SYS',
    error: 'ERR',
    result: 'DONE',
  }

  return (
    <div className="flex gap-2 py-1 border-b border-[rgba(163,163,163,0.05)] last:border-0">
      <span className={cn('text-[0.5rem] uppercase tracking-widest w-10 shrink-0 pt-0.5', typeColors[msg.type] || 'text-[var(--color-stone)]')}>
        {typeLabels[msg.type] || msg.type}
      </span>
      <div className="flex-1 min-w-0">
        {msg.type === 'tool_use' && msg.metadata?.tool ? (
          <div>
            <span className="text-[var(--color-sky)] text-[0.6875rem] font-medium">{msg.metadata.tool}</span>
            {msg.metadata.input !== undefined && msg.metadata.input !== null ? (
              <pre className="text-[0.625rem] text-[var(--color-stone)]/50 mt-1 whitespace-pre-wrap break-all">
                {formatToolInput(msg.metadata.input)}
              </pre>
            ) : null}
          </div>
        ) : (
          <span className={cn('text-[0.6875rem] break-words', typeColors[msg.type] || 'text-[var(--color-stone)]')}>
            {msg.content}
          </span>
        )}
      </div>
    </div>
  )
}

export function RunDetailDialog({ run, open, onOpenChange, onRunUpdated }: RunDetailDialogProps) {
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [logs, setLogs] = useState<{ stdout: string; stderr: string; messages: string }>({ stdout: '', stderr: '', messages: '' })
  const [activeTab, setActiveTab] = useState<'output' | 'errors' | 'messages' | 'history' | 'continue'>('output')
  const [loading, setLoading] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [logsCopied, setLogsCopied] = useState(false)
  const [resumePrompt, setResumePrompt] = useState('')
  const [resuming, setResuming] = useState(false)
  const [resumeError, setResumeError] = useState<string | null>(null)
  const [sessionHistory, setSessionHistory] = useState<Run[]>([])
  const [expandedHistoryRun, setExpandedHistoryRun] = useState<string | null>(null)
  const [historyLogs, setHistoryLogs] = useState<Record<string, { stdout: string; stderr: string }>>({})
  const [archiving, setArchiving] = useState(false)
  const [creatingPr, setCreatingPr] = useState(false)
  const [prError, setPrError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !run) {
      setDetail(null)
      setLogs({ stdout: '', stderr: '', messages: '' })
      setActiveTab('output')
      setResumePrompt('')
      setResumeError(null)
      setSessionHistory([])
      setExpandedHistoryRun(null)
      setHistoryLogs({})
      setPrError(null)
      return
    }

    const runId = run.id

    async function load() {
      setLoading(true)
      try {
        const [runDetail, stdoutLogs, stderrLogs, messagesLogs] = await Promise.all([
          fetchRun(runId),
          fetchLogs(runId, 'stdout').catch(() => ({ content: '' })),
          fetchLogs(runId, 'stderr').catch(() => ({ content: '' })),
          fetchLogs(runId, 'messages').catch(() => ({ content: '' })),
        ])
        setDetail(runDetail)
        setLogs({ stdout: stdoutLogs.content || '', stderr: stderrLogs.content || '', messages: messagesLogs.content || '' })

        // Fetch session history if there's a session
        if (runDetail.session_id) {
          try {
            const history = await fetchSessionHistory(runId)
            // Filter out the current run and only show previous runs
            const previousRuns = history.runs.filter(r => r.id !== runId)
            setSessionHistory(previousRuns)
          } catch {
            // Session history is optional, don't fail if it errors
            setSessionHistory([])
          }
        }
      } catch (err) {
        console.error('Failed to load run details:', err)
      } finally {
        setLoading(false)
      }
    }

    load()
  }, [open, run])

  // Auto-refresh for active runs
  useEffect(() => {
    if (!open || !run) return
    const isRunActive = run.status === 'running' || run.status === 'pending'
    if (!isRunActive) return

    const intervalId = setInterval(async () => {
      try {
        const [runDetail, stdoutLogs, stderrLogs, messagesLogs] = await Promise.all([
          fetchRun(run.id),
          fetchLogs(run.id, 'stdout').catch(() => ({ content: '' })),
          fetchLogs(run.id, 'stderr').catch(() => ({ content: '' })),
          fetchLogs(run.id, 'messages').catch(() => ({ content: '' })),
        ])
        setDetail(runDetail)
        setLogs({ stdout: stdoutLogs.content || '', stderr: stderrLogs.content || '', messages: messagesLogs.content || '' })
        onRunUpdated(runDetail)
      } catch (err) {
        console.error('Auto-refresh failed:', err)
      }
    }, 3000) // Refresh every 3 seconds

    return () => clearInterval(intervalId)
  }, [open, run?.id, run?.status, onRunUpdated])

  const handleCancel = async () => {
    if (!run) return
    setCancelling(true)
    try {
      const updated = await cancelRun(run.id)
      onRunUpdated(updated)
    } catch (err) {
      console.error('Failed to cancel run:', err)
    } finally {
      setCancelling(false)
    }
  }

  const handleRefresh = async () => {
    if (!run) return
    setLoading(true)
    try {
      const [runDetail, stdoutLogs, stderrLogs, messagesLogs] = await Promise.all([
        fetchRun(run.id),
        fetchLogs(run.id, 'stdout').catch(() => ({ content: '' })),
        fetchLogs(run.id, 'stderr').catch(() => ({ content: '' })),
        fetchLogs(run.id, 'messages').catch(() => ({ content: '' })),
      ])
      setDetail(runDetail)
      setLogs({ stdout: stdoutLogs.content || '', stderr: stderrLogs.content || '', messages: messagesLogs.content || '' })
      onRunUpdated(runDetail)
    } catch (err) {
      console.error('Failed to refresh:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleCopyLogs = async () => {
    const content = activeTab === 'output' ? logs.stdout : logs.stderr
    if (!content) return
    await navigator.clipboard.writeText(content)
    setLogsCopied(true)
    setTimeout(() => setLogsCopied(false), 2000)
  }

  const handleResume = async () => {
    if (!run || !resumePrompt.trim()) return
    setResuming(true)
    setResumeError(null)
    try {
      await resumeRun(run.id, resumePrompt.trim())
      // Success - close dialog and let WebSocket update show new run
      setResumePrompt('')
      onOpenChange(false)
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : 'Failed to resume run')
    } finally {
      setResuming(false)
    }
  }

  const handleExpandHistoryRun = async (historyRunId: string) => {
    if (expandedHistoryRun === historyRunId) {
      setExpandedHistoryRun(null)
      return
    }
    setExpandedHistoryRun(historyRunId)
    // Load logs if not already cached
    if (!historyLogs[historyRunId]) {
      try {
        const [stdout, stderr] = await Promise.all([
          fetchLogs(historyRunId, 'stdout').catch(() => ({ content: '' })),
          fetchLogs(historyRunId, 'stderr').catch(() => ({ content: '' })),
        ])
        setHistoryLogs(prev => ({
          ...prev,
          [historyRunId]: { stdout: stdout.content || '', stderr: stderr.content || '' }
        }))
      } catch {
        setHistoryLogs(prev => ({
          ...prev,
          [historyRunId]: { stdout: '', stderr: '' }
        }))
      }
    }
  }

  const handleArchive = async () => {
    if (!run) return
    setArchiving(true)
    try {
      const updated = await archiveRun(run.id)
      onRunUpdated(updated)
      onOpenChange(false) // Close dialog after archiving
    } catch (err) {
      console.error('Failed to archive run:', err)
    } finally {
      setArchiving(false)
    }
  }

  const handleCreatePr = async () => {
    if (!run) return
    setCreatingPr(true)
    setPrError(null)
    try {
      const result = await createPrForRun(run.id)
      if (result.success && result.pr_url) {
        // Refresh the run details to get updated PR info
        const updatedDetail = await fetchRun(run.id)
        setDetail(updatedDetail)
        onRunUpdated(updatedDetail)
      } else {
        setPrError(result.error || 'Failed to create PR')
      }
    } catch (err) {
      setPrError(err instanceof Error ? err.message : 'Failed to create PR')
    } finally {
      setCreatingPr(false)
    }
  }

  const isActive = run?.status === 'running' || run?.status === 'pending'
  const hasErrors = !!logs.stderr
  const isResumable = (run?.status === 'completed' || run?.status === 'failed') && detail?.session_id
  const hasHistory = sessionHistory.length > 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="dialog-content sm:max-w-6xl w-[95vw] max-h-[90vh] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden">
        {/* Compact Header Bar */}
        <div className="flex items-center justify-between px-4 sm:px-5 py-3 border-b border-[rgba(163,163,163,0.1)] bg-[var(--color-void)]">
          {/* Left: Back (mobile) + Status */}
          <div className="flex items-center gap-3">
            <button
              className="md:hidden p-1 -ml-1 text-[var(--color-stone)] hover:text-[var(--color-paper)] transition-colors"
              onClick={() => onOpenChange(false)}
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <div className="flex items-center gap-2">
              <div className={cn('mark', `mark-${run?.status}`)} />
              <span className="text-mono text-[var(--color-stone)]/60 text-[0.625rem]">{run?.id.slice(0, 8)}</span>
              <span className="text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/55">{run?.status}</span>
            </div>
          </div>

          {/* Right: Actions */}
          <div className="flex items-center gap-1 pr-5">
            <button
              className="p-2 text-[var(--color-stone)]/60 hover:text-[var(--color-paper)] transition-colors"
              onClick={handleRefresh}
              disabled={loading}
              title="Refresh"
            >
              <RotateCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
            </button>
            {isActive && (
              <button
                className="px-2 py-1 text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/70 hover:text-[var(--color-vermillion)] border border-[var(--color-stone)]/20 hover:border-[var(--color-vermillion)]/40 rounded-sm transition-colors"
                onClick={handleCancel}
                disabled={cancelling}
              >
                {cancelling ? 'Cancelling...' : 'Cancel'}
              </button>
            )}
            {!isActive && (
              <button
                className="flex items-center gap-1.5 px-2 py-1 text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/70 hover:text-[var(--color-stone)] border border-[var(--color-stone)]/20 hover:border-[var(--color-stone)]/40 rounded-sm transition-colors"
                onClick={handleArchive}
                disabled={archiving}
                title="Archive this run"
              >
                <Archive className="w-3 h-3" />
                {archiving ? 'Archiving...' : 'Archive'}
              </button>
            )}
          </div>
        </div>

        {/* Main Content */}
        <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
          <div className="p-4 sm:p-5 flex flex-col flex-1 min-h-0">
            {/* Project + Meta Row */}
            <div className="flex items-center gap-4 text-[0.6875rem] text-[var(--color-stone)]/60 mb-4 shrink-0 flex-wrap">
              <span className="text-[var(--color-paper)]/80">{run?.project_name}</span>
              <span className="hidden sm:inline">{formatDate(run?.created_at ?? null)}</span>
              {run?.duration_seconds !== null && (
                <span className="text-mono">{formatDuration(run?.duration_seconds ?? null)}</span>
              )}
              {detail?.exit_code !== null && detail?.exit_code !== undefined && (
                <span className="text-mono">exit {detail?.exit_code}</span>
              )}
              {detail?.cost_usd != null && detail.cost_usd > 0 && (
                <span className="text-mono text-[var(--color-harvest)]">${detail.cost_usd.toFixed(4)}</span>
              )}
              {(detail?.input_tokens || detail?.output_tokens) && (
                <span className="text-mono text-[var(--color-stone)]/60">
                  {formatTokens(detail.input_tokens)} → {formatTokens(detail.output_tokens)}
                </span>
              )}
              {detail?.model_used && (
                <span className="text-mono text-[var(--color-stone)]/50">{detail.model_used}</span>
              )}
            </div>

            {/* Git Info Row - show if branch or PR exists */}
            {(detail?.branch_name || detail?.pr_number) && (
              <div className="flex items-center gap-3 text-[0.6875rem] mb-4 shrink-0 flex-wrap">
                {detail.branch_name && (
                  <div className="flex items-center gap-1.5 px-2 py-1 bg-[rgba(168,85,247,0.1)] border border-[rgba(168,85,247,0.2)] rounded-sm">
                    <GitBranch className="w-3 h-3 text-purple-400" />
                    <span className="text-purple-300">{detail.branch_name}</span>
                    {detail.source_branch && (
                      <span className="text-[var(--color-stone)]/50">from {detail.source_branch}</span>
                    )}
                  </div>
                )}
                {detail.git_commit_sha && (
                  <div className="flex items-center gap-1.5 text-[var(--color-stone)]/60">
                    <GitCommit className="w-3 h-3" />
                    <span className="text-mono">{detail.git_commit_sha.slice(0, 7)}</span>
                  </div>
                )}
                {detail.pr_number && detail.pr_url && (
                  <a
                    href={detail.pr_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={cn(
                      'flex items-center gap-1.5 px-2 py-1 rounded-sm transition-colors',
                      detail.pr_status === 'open' && 'bg-[rgba(34,197,94,0.1)] border border-[rgba(34,197,94,0.2)] text-green-400 hover:bg-[rgba(34,197,94,0.15)]',
                      detail.pr_status === 'merged' && 'bg-[rgba(168,85,247,0.1)] border border-[rgba(168,85,247,0.2)] text-purple-400',
                      detail.pr_status === 'closed' && 'bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.2)] text-red-400',
                      detail.pr_status === 'draft' && 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]'
                    )}
                  >
                    <span>PR #{detail.pr_number}</span>
                    <span className="text-[0.5rem] uppercase">{detail.pr_status}</span>
                    <ExternalLink className="w-2.5 h-2.5" />
                  </a>
                )}
                {/* Create PR button - show for worktree runs with branch but no PR */}
                {detail.use_worktree && detail.branch_name && !detail.pr_url && !isActive && (
                  <button
                    onClick={handleCreatePr}
                    disabled={creatingPr}
                    className={cn(
                      'flex items-center gap-1.5 px-2 py-1 rounded-sm transition-colors',
                      creatingPr
                        ? 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                        : 'bg-[rgba(34,197,94,0.1)] border border-[rgba(34,197,94,0.2)] text-green-400 hover:bg-[rgba(34,197,94,0.15)]'
                    )}
                  >
                    <GitPullRequest className="w-3 h-3" />
                    <span>{creatingPr ? 'Creating...' : 'Create PR'}</span>
                  </button>
                )}
              </div>
            )}
            {/* PR creation error */}
            {prError && (
              <div className="mb-4 p-2 bg-[rgba(199,62,58,0.08)] border border-[rgba(199,62,58,0.2)] rounded-sm shrink-0">
                <p className="text-[0.625rem] text-[var(--color-vermillion)]">{prError}</p>
              </div>
            )}

            {/* Prompt - Constrained height with scroll */}
            <div className="mb-4 shrink-0">
              <div className="flex items-start justify-between gap-3">
                <div className="max-h-24 overflow-y-auto flex-1 pr-2 scrollbar-thin">
                  <p className="text-[0.8125rem] text-[var(--color-paper)] leading-relaxed font-light">
                    {run?.prompt}
                  </p>
                </div>
              </div>
            </div>

            {/* Error Message - Prominent if exists */}
            {run?.error_message && (
              <div className="mb-6 p-3 bg-[rgba(199,62,58,0.08)] border border-[rgba(199,62,58,0.2)] rounded-sm shrink-0">
                <p className="text-[0.625rem] uppercase tracking-widest text-[var(--color-vermillion)]/70 mb-1.5">Error</p>
                <pre className="text-[0.75rem] text-[var(--color-vermillion)] whitespace-pre-wrap break-words font-mono">
                  {run.error_message}
                </pre>
              </div>
            )}

            {/* Logs Section */}
            <div className="flex flex-col flex-1 min-h-0">
              {/* Tab Bar */}
              <div className="flex items-center justify-between mb-3 shrink-0">
                <div className="flex items-center gap-1">
                  <button
                    className={cn(
                      'px-3 py-1.5 text-[0.625rem] uppercase tracking-widest transition-colors rounded-sm',
                      activeTab === 'output'
                        ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                        : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                    )}
                    onClick={() => setActiveTab('output')}
                  >
                    Output
                  </button>
                  <button
                    className={cn(
                      'px-3 py-1.5 text-[0.625rem] uppercase tracking-widest transition-colors rounded-sm flex items-center gap-1.5',
                      activeTab === 'errors'
                        ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                        : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                    )}
                    onClick={() => setActiveTab('errors')}
                  >
                    Errors
                    {hasErrors && (
                      <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-vermillion)]" />
                    )}
                  </button>
                  {logs.messages && (
                    <button
                      className={cn(
                        'px-3 py-1.5 text-[0.625rem] uppercase tracking-widest transition-colors rounded-sm',
                        activeTab === 'messages'
                          ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                          : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                      )}
                      onClick={() => setActiveTab('messages')}
                    >
                      Messages
                    </button>
                  )}
                  {hasHistory && (
                    <button
                      className={cn(
                        'px-3 py-1.5 text-[0.625rem] uppercase tracking-widest transition-colors rounded-sm flex items-center gap-1.5',
                        activeTab === 'history'
                          ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                          : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                      )}
                      onClick={() => setActiveTab('history')}
                    >
                      <Clock className="w-3 h-3" />
                      History
                      <span className="text-[0.5rem] text-[var(--color-stone)]/50">({sessionHistory.length})</span>
                    </button>
                  )}
                </div>
                <button
                  className={cn(
                    'flex items-center gap-1.5 px-2 py-1 text-[0.625rem] uppercase tracking-widest transition-colors rounded-sm',
                    (activeTab === 'output' ? logs.stdout : logs.stderr)
                      ? 'text-[var(--color-stone)]/60 hover:text-[var(--color-paper)]'
                      : 'text-[var(--color-stone)]/40 cursor-not-allowed'
                  )}
                  onClick={handleCopyLogs}
                  disabled={!(activeTab === 'output' ? logs.stdout : logs.stderr)}
                  title={`Copy ${activeTab}`}
                >
                  {logsCopied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                  <span className="hidden sm:inline">{logsCopied ? 'Copied' : 'Copy'}</span>
                </button>
              </div>

              {/* Log Content */}
              <div className="bg-[var(--color-void)] border border-[rgba(163,163,163,0.08)] rounded-sm flex-1 min-h-[200px] overflow-auto">
                {activeTab === 'output' && (
                  <div className="flex flex-col h-full">
                    <pre className="p-3 text-mono text-[var(--color-paper)]/70 whitespace-pre-wrap break-words text-[0.6875rem] leading-relaxed flex-1 overflow-auto">
                      {logs.stdout || <span className="text-[var(--color-stone)]/50 italic">No output</span>}
                    </pre>
                    {isResumable && (
                      <div className="p-3 border-t border-[rgba(163,163,163,0.08)] bg-[var(--color-ink)]/50">
                        <div className="flex gap-2">
                          <textarea
                            className="flex-1 bg-[var(--color-void)] border border-[rgba(163,163,163,0.1)] rounded-sm px-3 py-2 text-[0.8125rem] text-[var(--color-paper)] placeholder:text-[var(--color-stone)]/40 focus:outline-none focus:border-[rgba(163,163,163,0.2)] resize-none min-h-[38px] max-h-32"
                            placeholder="Continue with follow-up prompt... (⌘+Enter to submit)"
                            value={resumePrompt}
                            onChange={(e) => setResumePrompt(e.target.value)}
                            onKeyDown={(e) => {
                              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                                e.preventDefault()
                                if (resumePrompt.trim() && !resuming) {
                                  handleResume()
                                }
                              }
                            }}
                            disabled={resuming}
                            rows={1}
                            onInput={(e) => {
                              // Auto-resize textarea
                              const target = e.target as HTMLTextAreaElement
                              target.style.height = 'auto'
                              target.style.height = Math.min(target.scrollHeight, 128) + 'px'
                            }}
                          />
                          <button
                            className={cn(
                              'flex items-center gap-2 px-4 py-2 rounded-sm text-[0.6875rem] uppercase tracking-widest transition-colors shrink-0 self-start',
                              resumePrompt.trim() && !resuming
                                ? 'bg-[var(--color-paper)] text-[var(--color-void)] hover:opacity-90'
                                : 'bg-[var(--color-stone)]/20 text-[var(--color-stone)]/50 cursor-not-allowed'
                            )}
                            onClick={handleResume}
                            disabled={!resumePrompt.trim() || resuming}
                          >
                            <Play className="w-3 h-3" />
                            {resuming ? 'Resuming...' : 'Resume'}
                          </button>
                        </div>
                        {resumeError && (
                          <p className="text-[0.625rem] text-[var(--color-vermillion)] mt-2">{resumeError}</p>
                        )}
                      </div>
                    )}
                  </div>
                )}
                {activeTab === 'errors' && (
                  <pre className={cn(
                    'p-3 text-mono whitespace-pre-wrap break-words text-[0.6875rem] leading-relaxed',
                    logs.stderr ? 'text-[var(--color-vermillion)]/90' : 'text-[var(--color-stone)]/50 italic'
                  )}>
                    {logs.stderr || 'No errors'}
                  </pre>
                )}
                {activeTab === 'messages' && (
                  <div className="p-3 overflow-y-auto h-full">
                    {logs.messages ? (
                      <div className="space-y-0.5">
                        {parseMessages(logs.messages).map((msg, idx) => (
                          <MessageItem key={idx} msg={msg} />
                        ))}
                      </div>
                    ) : (
                      <span className="text-[var(--color-stone)]/50 italic text-[0.6875rem]">No messages</span>
                    )}
                  </div>
                )}
                {activeTab === 'history' && (
                  <div className="p-3 overflow-y-auto h-full">
                    <p className="text-[0.6875rem] text-[var(--color-stone)]/70 mb-3">
                      Previous runs in this session (oldest first):
                    </p>
                    <div className="space-y-2">
                      {sessionHistory.map((historyRun) => (
                        <div key={historyRun.id} className="border border-[rgba(163,163,163,0.08)] rounded-sm">
                          <button
                            className="w-full p-3 flex items-center justify-between hover:bg-[var(--color-paper)]/5 transition-colors"
                            onClick={() => handleExpandHistoryRun(historyRun.id)}
                          >
                            <div className="flex items-center gap-3 text-left">
                              <div className={cn('mark', `mark-${historyRun.status}`)} />
                              <div>
                                <p className="text-[0.75rem] text-[var(--color-paper)]/80 line-clamp-1">
                                  {historyRun.prompt}
                                </p>
                                <p className="text-[0.625rem] text-[var(--color-stone)]/50 mt-0.5">
                                  {formatDate(historyRun.created_at)} · {formatDuration(historyRun.duration_seconds)}
                                </p>
                              </div>
                            </div>
                            <ChevronDown className={cn(
                              'w-4 h-4 text-[var(--color-stone)]/50 transition-transform',
                              expandedHistoryRun === historyRun.id && 'rotate-180'
                            )} />
                          </button>
                          {expandedHistoryRun === historyRun.id && (
                            <div className="border-t border-[rgba(163,163,163,0.08)] p-3">
                              {historyLogs[historyRun.id] ? (
                                <pre className="text-mono text-[0.625rem] text-[var(--color-paper)]/60 whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                                  {historyLogs[historyRun.id].stdout || <span className="text-[var(--color-stone)]/40 italic">No output</span>}
                                </pre>
                              ) : (
                                <span className="text-[0.625rem] text-[var(--color-stone)]/50">Loading...</span>
                              )}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Footer Meta */}
            {detail?.session_id && (
              <div className="mt-4 pt-3 border-t border-[rgba(163,163,163,0.06)] shrink-0">
                <span className="text-mono text-[0.625rem] text-[var(--color-stone)]/50">
                  session {detail.session_id.slice(0, 12)}
                </span>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
