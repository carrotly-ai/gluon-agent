import { useEffect, useState, useRef, useCallback } from 'react'
import {
  Dialog,
  DialogContent,
} from '@/components/ui/dialog'
import { RotateCw, ChevronLeft, Copy, Check, Play, ChevronDown, Clock, GitBranch, GitCommit, ExternalLink, Archive, GitPullRequest, FileCode, Plus, Minus, GitMerge, Image as ImageIcon, Download } from 'lucide-react'
import type { Run, RunDetail, RunCommitsResponse, RunFilesResponse, ImageAttachment } from '@/lib/types'
import { formatFileSize } from '@/lib/types'
import { fetchRun, fetchLogs, cancelRun, resumeRun, fetchSessionHistory, archiveRun, createPrForRun, fetchRunCommits, fetchRunFiles, mergeRunBranch, fetchRunAttachments, getImageFileUrl, uploadAndAttachImage } from '@/lib/api'
import { cn } from '@/lib/utils'
import ReactMarkdown from 'react-markdown'

interface RunDetailDialogProps {
  run: Run | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onRunUpdated: (run: Run) => void
}

// Pending image for resume feature
interface ResumePendingImage {
  file: File
  preview: string
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

function formatToolInputCompact(input: unknown): string {
  if (input === null || input === undefined) return ''
  if (typeof input === 'string') {
    // For strings, show truncated path or value
    const truncated = input.length > 80 ? input.slice(0, 77) + '...' : input
    return `"${truncated}"`
  }
  if (typeof input === 'object') {
    // For objects, format as key=value pairs on one line
    const entries = Object.entries(input as Record<string, unknown>)
    const parts: string[] = []
    let totalLen = 0
    for (const [key, val] of entries) {
      let valStr: string
      if (typeof val === 'string') {
        // Truncate long strings (like file paths or content)
        valStr = val.length > 50 ? `"${val.slice(0, 47)}..."` : `"${val}"`
      } else if (typeof val === 'number' || typeof val === 'boolean') {
        valStr = String(val)
      } else if (val === null) {
        valStr = 'null'
      } else {
        valStr = '{...}'
      }
      const part = `${key}=${valStr}`
      if (totalLen + part.length > 120 && parts.length > 0) {
        parts.push('...')
        break
      }
      parts.push(part)
      totalLen += part.length + 1
    }
    return parts.join(' ')
  }
  return String(input)
}

interface LiveStats {
  totalCost: number
  totalTokensIn: number
  totalTokensOut: number
  toolCalls: number
}

function aggregateLiveStats(messages: AgentMessage[]): LiveStats {
  let totalCost = 0
  let totalTokensIn = 0
  let totalTokensOut = 0
  let toolCalls = 0

  for (const msg of messages) {
    if (msg.metadata?.cost) totalCost += msg.metadata.cost
    if (msg.metadata?.tokens_in) totalTokensIn += msg.metadata.tokens_in
    if (msg.metadata?.tokens_out) totalTokensOut += msg.metadata.tokens_out
    if (msg.type === 'tool_use') toolCalls++
  }

  return { totalCost, totalTokensIn, totalTokensOut, toolCalls }
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
    <div className="flex gap-2 py-1 border-b border-[rgba(163,163,163,0.05)] last:border-0 items-start">
      <span className={cn('text-[0.5rem] uppercase tracking-widest w-8 shrink-0 pt-0.5', typeColors[msg.type] || 'text-[var(--color-stone)]')}>
        {typeLabels[msg.type] || msg.type}
      </span>
      <div className="flex-1 min-w-0 overflow-hidden">
        {msg.type === 'tool_use' && msg.metadata?.tool ? (
          <span className="text-[0.6875rem] font-mono">
            <span className="text-[var(--color-sky)] font-medium">{msg.metadata.tool}</span>
            {msg.metadata.input !== undefined && msg.metadata.input !== null && (
              <span className="text-[var(--color-stone)]/50 ml-1.5">{formatToolInputCompact(msg.metadata.input)}</span>
            )}
          </span>
        ) : msg.type === 'text' ? (
          <div className="text-[0.6875rem] prose prose-sm prose-invert max-w-none prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-pre:my-2 prose-code:text-[var(--color-sky)] prose-code:bg-[var(--color-void)] prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-pre:bg-[var(--color-void)] prose-pre:p-2 prose-pre:rounded">
            <ReactMarkdown>{msg.content}</ReactMarkdown>
          </div>
        ) : (
          <span className={cn('text-[0.6875rem]', typeColors[msg.type] || 'text-[var(--color-stone)]')}>
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
  const [activeTab, setActiveTab] = useState<'output' | 'errors' | 'messages' | 'history' | 'commits' | 'files' | 'attachments'>('messages')
  const [loading, setLoading] = useState(false)
  const [commitsData, setCommitsData] = useState<RunCommitsResponse | null>(null)
  const [filesData, setFilesData] = useState<RunFilesResponse | null>(null)
  const [loadingCommits, setLoadingCommits] = useState(false)
  const [loadingFiles, setLoadingFiles] = useState(false)
  const [attachments, setAttachments] = useState<ImageAttachment[]>([])
  const [loadingAttachments, setLoadingAttachments] = useState(false)
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
  const [merging, setMerging] = useState(false)
  const [mergeError, setMergeError] = useState<string | null>(null)

  // Resume image paste support
  const [resumePendingImages, setResumePendingImages] = useState<ResumePendingImage[]>([])

  // Refs for auto-scroll
  const messagesContainerRef = useRef<HTMLDivElement>(null)
  const outputContainerRef = useRef<HTMLPreElement>(null)
  const prevMessagesRef = useRef<string>('')
  const prevOutputRef = useRef<string>('')

  useEffect(() => {
    if (!open || !run) {
      setDetail(null)
      setLogs({ stdout: '', stderr: '', messages: '' })
      setActiveTab('messages')
      setResumePrompt('')
      setResumeError(null)
      // Cleanup resume image previews
      resumePendingImages.forEach(img => URL.revokeObjectURL(img.preview))
      setResumePendingImages([])
      setSessionHistory([])
      setExpandedHistoryRun(null)
      setHistoryLogs({})
      setPrError(null)
      setMergeError(null)
      setCommitsData(null)
      setFilesData(null)
      setAttachments([])
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

  // Poll PR status when dialog is open with an open PR
  // This catches when user merges PR on GitHub
  useEffect(() => {
    if (!open || !detail) return
    // Only poll if we have an open PR and run is not active
    const hasOpenPr = detail.pr_status === 'open' && detail.pr_url
    const isRunActive = detail.status === 'running' || detail.status === 'pending'
    if (!hasOpenPr || isRunActive) return

    const pollInterval = setInterval(async () => {
      try {
        const updatedDetail = await fetchRun(detail.id)
        // Check if PR status changed (e.g., merged on GitHub)
        if (updatedDetail.pr_status !== detail.pr_status) {
          setDetail(updatedDetail)
          onRunUpdated(updatedDetail)
        }
      } catch (err) {
        console.error('PR status poll failed:', err)
      }
    }, 7000) // Poll every 7 seconds for PR status changes

    return () => clearInterval(pollInterval)
  }, [open, detail?.id, detail?.pr_status, detail?.status, onRunUpdated])

  // Auto-scroll to bottom when content changes
  useEffect(() => {
    // Only scroll if content actually changed
    if (activeTab === 'messages' && logs.messages !== prevMessagesRef.current) {
      prevMessagesRef.current = logs.messages
      if (messagesContainerRef.current) {
        messagesContainerRef.current.scrollTop = messagesContainerRef.current.scrollHeight
      }
    }
    if (activeTab === 'output' && logs.stdout !== prevOutputRef.current) {
      prevOutputRef.current = logs.stdout
      if (outputContainerRef.current) {
        outputContainerRef.current.scrollTop = outputContainerRef.current.scrollHeight
      }
    }
  }, [logs.messages, logs.stdout, activeTab])

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

  // Handle paste for resume textarea
  const handleResumePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items
    if (!items) return

    const imageFiles: File[] = []
    for (const item of Array.from(items)) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) {
          const ext = file.type.split('/')[1] || 'png'
          const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
          const namedFile = new File([file], `pasted-image-${timestamp}.${ext}`, { type: file.type })
          imageFiles.push(namedFile)
        }
      }
    }

    if (imageFiles.length > 0) {
      const validTypes = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
      const maxSize = 50 * 1024 * 1024

      const newImages: ResumePendingImage[] = []
      for (const file of imageFiles) {
        if (!validTypes.includes(file.type)) continue
        if (file.size > maxSize) continue
        newImages.push({
          file,
          preview: URL.createObjectURL(file),
        })
      }
      setResumePendingImages(prev => [...prev, ...newImages])
    }
  }, [])

  const removeResumeImage = useCallback((index: number) => {
    setResumePendingImages(prev => {
      const updated = [...prev]
      URL.revokeObjectURL(updated[index].preview)
      updated.splice(index, 1)
      return updated
    })
  }, [])

  const handleResume = async () => {
    if (!run || !resumePrompt.trim()) return
    setResuming(true)
    setResumeError(null)
    try {
      // Resume creates a new run
      const result = await resumeRun(run.id, resumePrompt.trim())

      // Upload images to the new run if any
      if (resumePendingImages.length > 0 && result.new_run_id) {
        const uploadPromises = resumePendingImages.map(img =>
          uploadAndAttachImage(result.new_run_id, img.file).catch(err => {
            console.error(`Failed to upload image ${img.file.name}:`, err)
            return null
          })
        )
        await Promise.all(uploadPromises)
      }

      // Cleanup and close
      resumePendingImages.forEach(img => URL.revokeObjectURL(img.preview))
      setResumePendingImages([])
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

  const handleMerge = async () => {
    if (!run) return
    setMerging(true)
    setMergeError(null)
    try {
      const result = await mergeRunBranch(run.id)
      if (result.success) {
        // Refresh the run details to get updated PR status (will show as merged)
        const updatedDetail = await fetchRun(run.id)
        setDetail(updatedDetail)
        onRunUpdated(updatedDetail)
      } else {
        setMergeError(result.error || 'Failed to merge branch')
      }
    } catch (err) {
      setMergeError(err instanceof Error ? err.message : 'Failed to merge branch')
    } finally {
      setMerging(false)
    }
  }

  // Lazy load commits when switching to commits tab
  const loadCommits = async () => {
    if (!run || commitsData || loadingCommits) return
    setLoadingCommits(true)
    try {
      const data = await fetchRunCommits(run.id)
      setCommitsData(data)
    } catch (err) {
      console.error('Failed to load commits:', err)
    } finally {
      setLoadingCommits(false)
    }
  }

  // Lazy load files when switching to files tab
  const loadFiles = async () => {
    if (!run || filesData || loadingFiles) return
    setLoadingFiles(true)
    try {
      const data = await fetchRunFiles(run.id)
      setFilesData(data)
    } catch (err) {
      console.error('Failed to load files:', err)
    } finally {
      setLoadingFiles(false)
    }
  }

  // Lazy load attachments when switching to attachments tab
  const loadAttachments = async () => {
    if (!run || attachments.length > 0 || loadingAttachments) return
    setLoadingAttachments(true)
    try {
      const data = await fetchRunAttachments(run.id)
      setAttachments(data.images)
    } catch (err) {
      console.error('Failed to load attachments:', err)
    } finally {
      setLoadingAttachments(false)
    }
  }

  // Load data when tab changes
  useEffect(() => {
    if (activeTab === 'commits' && !commitsData && !loadingCommits) {
      loadCommits()
    } else if (activeTab === 'files' && !filesData && !loadingFiles) {
      loadFiles()
    } else if (activeTab === 'attachments' && attachments.length === 0 && !loadingAttachments) {
      loadAttachments()
    }
  }, [activeTab, run?.id])

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
                      detail.pr_mergeable === 'CONFLICTING' && 'bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.2)] text-red-400 hover:bg-[rgba(239,68,68,0.15)]',
                      detail.pr_mergeable !== 'CONFLICTING' && detail.pr_status === 'open' && 'bg-[rgba(34,197,94,0.1)] border border-[rgba(34,197,94,0.2)] text-green-400 hover:bg-[rgba(34,197,94,0.15)]',
                      detail.pr_status === 'merged' && 'bg-[rgba(168,85,247,0.1)] border border-[rgba(168,85,247,0.2)] text-purple-400',
                      detail.pr_status === 'closed' && 'bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.2)] text-red-400',
                      detail.pr_status === 'draft' && 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]'
                    )}
                  >
                    <span>PR #{detail.pr_number}</span>
                    {detail.pr_mergeable === 'CONFLICTING' ? (
                      <span className="text-[0.5rem] uppercase text-red-400">CONFLICTS</span>
                    ) : (
                      <span className="text-[0.5rem] uppercase">{detail.pr_status}</span>
                    )}
                    <ExternalLink className="w-2.5 h-2.5" />
                  </a>
                )}
                {/* Merge button for open PRs */}
                {detail.pr_status === 'open' && detail.branch_name && !isActive && (
                  <button
                    onClick={handleMerge}
                    disabled={merging}
                    className={cn(
                      'flex items-center gap-1.5 px-2 py-1 rounded-sm transition-colors',
                      merging
                        ? 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                        : 'bg-[rgba(168,85,247,0.1)] border border-[rgba(168,85,247,0.2)] text-purple-400 hover:bg-[rgba(168,85,247,0.15)]'
                    )}
                    title="Merge branch locally and push to remote"
                  >
                    <GitMerge className="w-3 h-3" />
                    <span>{merging ? 'Merging...' : 'Merge'}</span>
                  </button>
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
            {/* Merge error */}
            {mergeError && (
              <div className="mb-4 p-2 bg-[rgba(199,62,58,0.08)] border border-[rgba(199,62,58,0.2)] rounded-sm shrink-0">
                <p className="text-[0.625rem] text-[var(--color-vermillion)]">{mergeError}</p>
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

            {/* Live Stats Bar - Show for active runs */}
            {isActive && (() => {
              const liveStats = aggregateLiveStats(parseMessages(logs.messages))
              return (
                <div className="mb-4 p-3 bg-[rgba(102,178,255,0.08)] border border-[rgba(102,178,255,0.2)] rounded-sm shrink-0">
                  <div className="flex items-center gap-4 flex-wrap text-[0.6875rem]">
                    {/* Running indicator */}
                    <div className="flex items-center gap-2">
                      <div className="mark mark-running" />
                      <span className="text-[var(--color-sky)] uppercase tracking-widest text-[0.5rem]">Live</span>
                    </div>
                    {/* Branch */}
                    {detail?.branch_name && (
                      <div className="flex items-center gap-1.5 text-purple-400">
                        <GitBranch className="w-3 h-3" />
                        <span>{detail.branch_name}</span>
                      </div>
                    )}
                    {/* Tokens */}
                    {(liveStats.totalTokensIn > 0 || liveStats.totalTokensOut > 0) && (
                      <div className="flex items-center gap-1 text-[var(--color-stone)]/80">
                        <span className="text-[0.5rem] uppercase tracking-widest text-[var(--color-stone)]/60">Tokens</span>
                        <span className="text-mono">{formatTokens(liveStats.totalTokensIn)} → {formatTokens(liveStats.totalTokensOut)}</span>
                      </div>
                    )}
                    {/* Cost */}
                    {liveStats.totalCost > 0 && (
                      <div className="flex items-center gap-1 text-[var(--color-harvest)]">
                        <span className="text-[0.5rem] uppercase tracking-widest text-[var(--color-harvest)]/60">Cost</span>
                        <span className="text-mono">${liveStats.totalCost.toFixed(4)}</span>
                      </div>
                    )}
                    {/* Tool calls */}
                    {liveStats.toolCalls > 0 && (
                      <div className="flex items-center gap-1 text-[var(--color-sky)]">
                        <span className="text-[0.5rem] uppercase tracking-widest text-[var(--color-sky)]/60">Tools</span>
                        <span className="text-mono">{liveStats.toolCalls}</span>
                      </div>
                    )}
                  </div>
                </div>
              )
            })()}

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
                      activeTab === 'messages'
                        ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                        : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                    )}
                    onClick={() => setActiveTab('messages')}
                  >
                    Messages
                  </button>
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
                  {detail?.branch_name && (
                    <>
                      <button
                        className={cn(
                          'px-3 py-1.5 text-[0.625rem] uppercase tracking-widest transition-colors rounded-sm flex items-center gap-1.5',
                          activeTab === 'commits'
                            ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                            : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                        )}
                        onClick={() => setActiveTab('commits')}
                      >
                        <GitCommit className="w-3 h-3" />
                        Commits
                        {commitsData && commitsData.commit_count > 0 && (
                          <span className="text-[0.5rem] text-[var(--color-stone)]/50">({commitsData.commit_count})</span>
                        )}
                      </button>
                      <button
                        className={cn(
                          'px-3 py-1.5 text-[0.625rem] uppercase tracking-widest transition-colors rounded-sm flex items-center gap-1.5',
                          activeTab === 'files'
                            ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                            : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                        )}
                        onClick={() => setActiveTab('files')}
                      >
                        <FileCode className="w-3 h-3" />
                        Files
                        {filesData && filesData.file_count > 0 && (
                          <span className="text-[0.5rem] text-[var(--color-stone)]/50">({filesData.file_count})</span>
                        )}
                      </button>
                    </>
                  )}
                  <button
                    className={cn(
                      'px-3 py-1.5 text-[0.625rem] uppercase tracking-widest transition-colors rounded-sm flex items-center gap-1.5',
                      activeTab === 'attachments'
                        ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                        : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                    )}
                    onClick={() => setActiveTab('attachments')}
                  >
                    <ImageIcon className="w-3 h-3" />
                    Images
                    {attachments.length > 0 && (
                      <span className="text-[0.5rem] text-[var(--color-stone)]/50">({attachments.length})</span>
                    )}
                  </button>
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
                  <pre ref={outputContainerRef} className="p-3 text-mono text-[var(--color-paper)]/70 whitespace-pre-wrap break-words text-[0.6875rem] leading-relaxed h-full overflow-auto">
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
                {activeTab === 'messages' && (
                  <div ref={messagesContainerRef} className="p-3 overflow-y-auto h-full">
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
                {activeTab === 'commits' && (
                  <div className="p-3 overflow-y-auto h-full">
                    {loadingCommits ? (
                      <div className="flex items-center justify-center h-32">
                        <RotateCw className="w-4 h-4 animate-spin text-[var(--color-stone)]/50" />
                      </div>
                    ) : commitsData && commitsData.commits.length > 0 ? (
                      <div className="space-y-0">
                        {/* Branch info header */}
                        <div className="flex items-center gap-2 mb-3 pb-2 border-b border-[rgba(163,163,163,0.08)]">
                          <GitBranch className="w-3.5 h-3.5 text-purple-400" />
                          <span className="text-[0.6875rem] text-purple-300">{commitsData.branch_name}</span>
                          <span className="text-[0.6875rem] text-[var(--color-stone)]/50">
                            {commitsData.commit_count} commit{commitsData.commit_count !== 1 ? 's' : ''} ahead of {commitsData.base_branch}
                          </span>
                        </div>
                        {/* Commits list */}
                        {commitsData.commits.map((commit, idx) => (
                          <div
                            key={commit.sha}
                            className={cn(
                              'flex items-start gap-3 py-2.5',
                              idx !== commitsData.commits.length - 1 && 'border-b border-[rgba(163,163,163,0.05)]'
                            )}
                          >
                            {/* Timeline dot */}
                            <div className="flex flex-col items-center pt-1.5">
                              <div className="w-2 h-2 rounded-full bg-[var(--color-jade)]" />
                              {idx !== commitsData.commits.length - 1 && (
                                <div className="w-px flex-1 bg-[rgba(163,163,163,0.15)] mt-1" />
                              )}
                            </div>
                            {/* Commit info */}
                            <div className="flex-1 min-w-0">
                              <p className="text-[0.75rem] text-[var(--color-paper)]/90 leading-relaxed">
                                {commit.message}
                              </p>
                              <div className="flex items-center gap-2 mt-1 text-[0.625rem] text-[var(--color-stone)]/50">
                                <span className="text-mono">{commit.sha.slice(0, 7)}</span>
                                <span>·</span>
                                <span>{commit.author}</span>
                                <span>·</span>
                                <span>{new Date(commit.date).toLocaleDateString([], { month: 'short', day: 'numeric' })}</span>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="flex flex-col items-center justify-center h-32 text-[var(--color-stone)]/50">
                        <GitCommit className="w-6 h-6 mb-2 opacity-50" />
                        <span className="text-[0.6875rem]">No commits on this branch</span>
                      </div>
                    )}
                  </div>
                )}
                {activeTab === 'files' && (
                  <div className="p-3 overflow-y-auto h-full">
                    {loadingFiles ? (
                      <div className="flex items-center justify-center h-32">
                        <RotateCw className="w-4 h-4 animate-spin text-[var(--color-stone)]/50" />
                      </div>
                    ) : filesData && filesData.files.length > 0 ? (
                      <div className="space-y-0">
                        {/* Summary header */}
                        <div className="flex items-center justify-between mb-3 pb-2 border-b border-[rgba(163,163,163,0.08)]">
                          <div className="flex items-center gap-2">
                            <FileCode className="w-3.5 h-3.5 text-[var(--color-sky)]" />
                            <span className="text-[0.6875rem] text-[var(--color-paper)]/80">
                              {filesData.file_count} file{filesData.file_count !== 1 ? 's' : ''} changed
                            </span>
                          </div>
                          <div className="flex items-center gap-3 text-[0.625rem]">
                            <span className="flex items-center gap-1 text-[var(--color-jade)]">
                              <Plus className="w-3 h-3" />
                              {filesData.total_additions}
                            </span>
                            <span className="flex items-center gap-1 text-[var(--color-vermillion)]">
                              <Minus className="w-3 h-3" />
                              {filesData.total_deletions}
                            </span>
                          </div>
                        </div>
                        {/* Files list */}
                        {filesData.files.map((file, idx) => {
                          const totalChanges = file.additions + file.deletions
                          const maxBarWidth = 100
                          const additionWidth = totalChanges > 0 ? Math.max((file.additions / totalChanges) * maxBarWidth, file.additions > 0 ? 4 : 0) : 0
                          const deletionWidth = totalChanges > 0 ? Math.max((file.deletions / totalChanges) * maxBarWidth, file.deletions > 0 ? 4 : 0) : 0

                          return (
                            <div
                              key={file.file_path}
                              className={cn(
                                'flex items-center justify-between py-2 gap-3',
                                idx !== filesData.files.length - 1 && 'border-b border-[rgba(163,163,163,0.05)]'
                              )}
                            >
                              {/* File path with change type indicator */}
                              <div className="flex items-center gap-2 min-w-0 flex-1">
                                <span className={cn(
                                  'text-[0.5rem] uppercase px-1 py-0.5 rounded font-medium shrink-0',
                                  file.change_type === 'added' && 'bg-[rgba(45,212,191,0.15)] text-[var(--color-jade)]',
                                  file.change_type === 'modified' && 'bg-[rgba(102,178,255,0.15)] text-[var(--color-sky)]',
                                  file.change_type === 'deleted' && 'bg-[rgba(199,62,58,0.15)] text-[var(--color-vermillion)]',
                                  file.change_type === 'renamed' && 'bg-[rgba(168,85,247,0.15)] text-purple-400'
                                )}>
                                  {file.change_type === 'added' ? 'A' : file.change_type === 'modified' ? 'M' : file.change_type === 'deleted' ? 'D' : 'R'}
                                </span>
                                <span className="text-[0.6875rem] text-[var(--color-paper)]/80 truncate font-mono">
                                  {file.file_path}
                                </span>
                              </div>
                              {/* Changes stats and bar */}
                              <div className="flex items-center gap-3 shrink-0">
                                <div className="flex items-center gap-1.5 text-[0.625rem] min-w-[60px] justify-end">
                                  {file.additions > 0 && (
                                    <span className="text-[var(--color-jade)]">+{file.additions}</span>
                                  )}
                                  {file.deletions > 0 && (
                                    <span className="text-[var(--color-vermillion)]">-{file.deletions}</span>
                                  )}
                                </div>
                                {/* Visual diff bar */}
                                <div className="flex h-2 w-[80px] rounded-sm overflow-hidden bg-[var(--color-void)]">
                                  <div
                                    className="bg-[var(--color-jade)]"
                                    style={{ width: `${additionWidth}%` }}
                                  />
                                  <div
                                    className="bg-[var(--color-vermillion)]"
                                    style={{ width: `${deletionWidth}%` }}
                                  />
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    ) : (
                      <div className="flex flex-col items-center justify-center h-32 text-[var(--color-stone)]/50">
                        <FileCode className="w-6 h-6 mb-2 opacity-50" />
                        <span className="text-[0.6875rem]">No files changed on this branch</span>
                      </div>
                    )}
                  </div>
                )}
                {activeTab === 'attachments' && (
                  <div className="p-3 overflow-y-auto h-full">
                    {loadingAttachments ? (
                      <div className="flex items-center justify-center h-32">
                        <RotateCw className="w-4 h-4 animate-spin text-[var(--color-stone)]/50" />
                      </div>
                    ) : attachments.length > 0 ? (
                      <div className="space-y-0">
                        {/* Summary header */}
                        <div className="flex items-center justify-between mb-3 pb-2 border-b border-[rgba(163,163,163,0.08)]">
                          <div className="flex items-center gap-2">
                            <ImageIcon className="w-3.5 h-3.5 text-[var(--color-harvest)]" />
                            <span className="text-[0.6875rem] text-[var(--color-paper)]/80">
                              {attachments.length} image{attachments.length !== 1 ? 's' : ''} attached
                            </span>
                          </div>
                        </div>
                        {/* Image grid */}
                        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                          {attachments.map((image) => (
                            <div
                              key={image.id}
                              className="group relative rounded-sm overflow-hidden border border-[rgba(163,163,163,0.1)] bg-[var(--color-void)]"
                            >
                              <img
                                src={getImageFileUrl(image.id)}
                                alt={image.original_name}
                                className="w-full h-24 object-cover"
                              />
                              {/* Hover overlay with actions */}
                              <div className="absolute inset-0 bg-[var(--color-void)]/80 opacity-0 group-hover:opacity-100 transition-opacity flex flex-col items-center justify-center gap-2">
                                <a
                                  href={getImageFileUrl(image.id)}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="flex items-center gap-1.5 px-2 py-1 text-[0.5rem] uppercase tracking-widest text-[var(--color-paper)] bg-[var(--color-paper)]/10 rounded-sm hover:bg-[var(--color-paper)]/20 transition-colors"
                                >
                                  <Download className="w-3 h-3" />
                                  View
                                </a>
                              </div>
                              {/* File info footer */}
                              <div className="absolute bottom-0 left-0 right-0 px-2 py-1 bg-[var(--color-void)]/90">
                                <p className="text-[0.5rem] text-[var(--color-paper)]/80 truncate">
                                  {image.original_name}
                                </p>
                                <p className="text-[0.5rem] text-[var(--color-stone)]/60">
                                  {formatFileSize(image.size_bytes)}
                                </p>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <div className="flex flex-col items-center justify-center h-32 text-[var(--color-stone)]/50">
                        <ImageIcon className="w-6 h-6 mb-2 opacity-50" />
                        <span className="text-[0.6875rem]">No images attached</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Resume/Continue Section - Always visible when resumable */}
            {isResumable && (
              <div className="mt-4 p-3 bg-[var(--color-void)] border border-[rgba(163,163,163,0.1)] rounded-sm shrink-0">
                {/* Pasted image previews */}
                {resumePendingImages.length > 0 && (
                  <div className="flex flex-wrap gap-2 mb-2">
                    {resumePendingImages.map((img, idx) => (
                      <div key={idx} className="relative group">
                        <img
                          src={img.preview}
                          alt={img.file.name}
                          className="h-12 w-auto rounded-sm border border-[rgba(163,163,163,0.15)]"
                        />
                        <button
                          type="button"
                          className="absolute -top-1 -right-1 w-4 h-4 bg-[var(--color-vermillion)] rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                          onClick={() => removeResumeImage(idx)}
                        >
                          <span className="text-[0.5rem] text-white font-bold">×</span>
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <div className="flex gap-2">
                  <textarea
                    className="flex-1 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.1)] rounded-sm px-3 py-2 text-[0.8125rem] text-[var(--color-paper)] placeholder:text-[var(--color-stone)]/40 focus:outline-none focus:border-[rgba(163,163,163,0.2)] resize-none min-h-[38px] max-h-32"
                    placeholder="Continue with follow-up... (⌘V to paste images)"
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
                    onPaste={handleResumePaste}
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
