import { useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
} from '@/components/ui/dialog'
import { X, RotateCw, ChevronLeft, Copy, Check, Play } from 'lucide-react'
import type { Run, RunDetail } from '@/lib/types'
import { fetchRun, fetchLogs, cancelRun, resumeRun } from '@/lib/api'
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

export function RunDetailDialog({ run, open, onOpenChange, onRunUpdated }: RunDetailDialogProps) {
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [logs, setLogs] = useState<{ stdout: string; stderr: string }>({ stdout: '', stderr: '' })
  const [activeTab, setActiveTab] = useState<'output' | 'errors' | 'continue'>('output')
  const [loading, setLoading] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [copied, setCopied] = useState(false)
  const [logsCopied, setLogsCopied] = useState(false)
  const [resumePrompt, setResumePrompt] = useState('')
  const [resuming, setResuming] = useState(false)
  const [resumeError, setResumeError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !run) {
      setDetail(null)
      setLogs({ stdout: '', stderr: '' })
      setActiveTab('output')
      setResumePrompt('')
      setResumeError(null)
      return
    }

    const runId = run.id

    async function load() {
      setLoading(true)
      try {
        const [runDetail, stdoutLogs, stderrLogs] = await Promise.all([
          fetchRun(runId),
          fetchLogs(runId, 'stdout').catch(() => ({ content: '' })),
          fetchLogs(runId, 'stderr').catch(() => ({ content: '' })),
        ])
        setDetail(runDetail)
        setLogs({ stdout: stdoutLogs.content || '', stderr: stderrLogs.content || '' })
      } catch (err) {
        console.error('Failed to load run details:', err)
      } finally {
        setLoading(false)
      }
    }

    load()
  }, [open, run])

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
      const [runDetail, stdoutLogs, stderrLogs] = await Promise.all([
        fetchRun(run.id),
        fetchLogs(run.id, 'stdout').catch(() => ({ content: '' })),
        fetchLogs(run.id, 'stderr').catch(() => ({ content: '' })),
      ])
      setDetail(runDetail)
      setLogs({ stdout: stdoutLogs.content || '', stderr: stderrLogs.content || '' })
      onRunUpdated(runDetail)
    } catch (err) {
      console.error('Failed to refresh:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleCopyPrompt = async () => {
    if (!run?.prompt) return
    await navigator.clipboard.writeText(run.prompt)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
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

  const isActive = run?.status === 'running' || run?.status === 'pending'
  const hasErrors = !!logs.stderr
  const isResumable = (run?.status === 'completed' || run?.status === 'failed') && detail?.session_id

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
                className="p-2 text-[var(--color-stone)]/60 hover:text-[var(--color-vermillion)] transition-colors"
                onClick={handleCancel}
                disabled={cancelling}
                title="Cancel run"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>

        {/* Main Content */}
        <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
          <div className="p-4 sm:p-5 flex flex-col flex-1 min-h-0">
            {/* Project + Meta Row */}
            <div className="flex items-center gap-4 text-[0.6875rem] text-[var(--color-stone)]/60 mb-4 shrink-0">
              <span className="text-[var(--color-paper)]/80">{run?.project_name}</span>
              <span className="hidden sm:inline">{formatDate(run?.created_at ?? null)}</span>
              {run?.duration_seconds !== null && (
                <span className="text-mono">{formatDuration(run?.duration_seconds ?? null)}</span>
              )}
              {detail?.exit_code !== null && detail?.exit_code !== undefined && (
                <span className="text-mono">exit {detail?.exit_code}</span>
              )}
            </div>

            {/* Prompt - Constrained height with scroll */}
            <div className="mb-4 shrink-0">
              <div className="flex items-start justify-between gap-3">
                <div className="max-h-24 overflow-y-auto flex-1 pr-2 scrollbar-thin">
                  <p className="text-[0.8125rem] text-[var(--color-paper)] leading-relaxed font-light">
                    {run?.prompt}
                  </p>
                </div>
                <button
                  className="p-1.5 text-[var(--color-stone)]/55 hover:text-[var(--color-paper)] transition-colors shrink-0"
                  onClick={handleCopyPrompt}
                  title="Copy prompt"
                >
                  {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
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
                  {isResumable && (
                    <button
                      className={cn(
                        'px-3 py-1.5 text-[0.625rem] uppercase tracking-widest transition-colors rounded-sm',
                        activeTab === 'continue'
                          ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                          : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                      )}
                      onClick={() => setActiveTab('continue')}
                    >
                      Continue
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
                  <pre className="p-3 text-mono text-[var(--color-paper)]/70 whitespace-pre-wrap break-words text-[0.6875rem] leading-relaxed">
                    {logs.stdout || <span className="text-[var(--color-stone)]/50 italic">No output</span>}
                  </pre>
                )}
                {activeTab === 'errors' && (
                  <pre className={cn(
                    'p-3 text-mono whitespace-pre-wrap break-words text-[0.6875rem] leading-relaxed',
                    logs.stderr ? 'text-[var(--color-vermillion)]/90' : 'text-[var(--color-stone)]/50 italic'
                  )}>
                    {logs.stderr || 'No errors'}
                  </pre>
                )}
                {activeTab === 'continue' && (
                  <div className="p-4 flex flex-col h-full">
                    <p className="text-[0.6875rem] text-[var(--color-stone)]/70 mb-3">
                      Continue this session with a follow-up prompt. The agent will resume from where it left off.
                    </p>
                    <textarea
                      className="flex-1 min-h-[120px] bg-[var(--color-ink)] border border-[rgba(163,163,163,0.1)] rounded-sm p-3 text-[0.8125rem] text-[var(--color-paper)] placeholder:text-[var(--color-stone)]/40 focus:outline-none focus:border-[rgba(163,163,163,0.2)] resize-none"
                      placeholder="Enter follow-up prompt..."
                      value={resumePrompt}
                      onChange={(e) => setResumePrompt(e.target.value)}
                      disabled={resuming}
                    />
                    {resumeError && (
                      <div className="mt-3 p-2 bg-[rgba(199,62,58,0.08)] border border-[rgba(199,62,58,0.2)] rounded-sm">
                        <p className="text-[0.6875rem] text-[var(--color-vermillion)]">{resumeError}</p>
                      </div>
                    )}
                    <div className="mt-3 flex items-center justify-between">
                      <span className="text-[0.625rem] text-[var(--color-stone)]/50">
                        Session: {detail?.session_id?.slice(0, 12)}...
                      </span>
                      <button
                        className={cn(
                          'flex items-center gap-2 px-4 py-2 rounded-sm text-[0.6875rem] uppercase tracking-widest transition-colors',
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
