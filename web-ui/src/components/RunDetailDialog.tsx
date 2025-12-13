import { useEffect, useState, useRef, useCallback } from 'react'
import {
  Dialog,
  DialogContent,
} from '@/components/ui/dialog'
import { RotateCw, ChevronLeft, Copy, Check, Play, ChevronDown, ChevronRight, Clock, GitBranch, GitCommit, ExternalLink, GitPullRequest, FileCode, Plus, Minus, GitMerge, Image as ImageIcon, Download, Wrench, MessageSquare, AlertCircle, CheckCircle2, Settings2, Filter, Sparkles } from 'lucide-react'
import type { Run, RunDetail, RunCommitsResponse, RunFilesResponse, ImageAttachment, CommitDetail, FileDiff } from '@/lib/types'
import { formatFileSize } from '@/lib/types'
import { fetchRun, fetchLogs, cancelRun, resumeRun, fetchSessionHistory, createPrForRun, fetchRunCommits, fetchRunFiles, mergeRunBranch, fetchRunAttachments, getImageFileUrl, uploadAndAttachImage, fetchCommitDetail, fetchFileDiff } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatDateWithContext, formatMessageTime, formatRelativeTime } from '@/lib/timestamps'
import ReactMarkdown from 'react-markdown'

type TabType = 'output' | 'errors' | 'messages' | 'history' | 'commits' | 'files' | 'attachments'

interface RunDetailDialogProps {
  run: Run | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onRunUpdated: (run: Run) => void
  initialTab?: TabType
  onTabChange?: (tab: TabType) => void
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


// Strip worktree prefix from paths for cleaner display
// e.g., /tmp/gluon-worktrees/wt-358a086b/src/foo.ts → src/foo.ts
function stripWorktreePrefix(path: string): string {
  return path.replace(/^\/tmp\/gluon-worktrees\/wt-[a-f0-9]+\//, '')
}

// Get primary value from tool input (for compact display)
function getToolPrimaryParam(input: unknown): { key: string; value: string } | null {
  if (!input || typeof input !== 'object') return null
  const obj = input as Record<string, unknown>

  // Priority order for common tool params
  const priorityKeys = ['file_path', 'command', 'pattern', 'query', 'url', 'path', 'content', 'prompt']
  for (const key of priorityKeys) {
    if (obj[key] && typeof obj[key] === 'string') {
      let val = obj[key] as string
      // Strip worktree prefix for file paths in compact display
      if (key === 'file_path' || key === 'path') {
        val = stripWorktreePrefix(val)
      }
      return { key, value: val.length > 80 ? val.slice(0, 77) + '...' : val }
    }
  }

  // Fall back to first string param
  const entries = Object.entries(obj)
  for (const [key, val] of entries) {
    if (typeof val === 'string' && val.length > 0) {
      return { key, value: val.length > 80 ? val.slice(0, 77) + '...' : val }
    }
  }
  return null
}

// Format full tool input for expanded view
function formatToolInputFull(input: unknown): { key: string; value: string }[] {
  if (!input || typeof input !== 'object') {
    if (typeof input === 'string') return [{ key: '', value: input }]
    return []
  }
  const obj = input as Record<string, unknown>
  return Object.entries(obj).map(([key, val]) => {
    let valStr: string
    if (typeof val === 'string') {
      valStr = val
    } else if (typeof val === 'number' || typeof val === 'boolean') {
      valStr = String(val)
    } else if (val === null) {
      valStr = 'null'
    } else {
      valStr = JSON.stringify(val, null, 2)
    }
    return { key, value: valStr }
  })
}

// Message type configuration
const MESSAGE_CONFIG: Record<string, {
  icon: typeof Wrench
  color: string
  bg: string
  border: string
  label: string
}> = {
  tool_use: { icon: Wrench, color: 'text-[var(--color-stone)]', bg: '', border: 'border-l-2 border-l-[var(--color-stone)]/30', label: 'Tool' },
  text: { icon: MessageSquare, color: 'text-[var(--color-paper)]/70', bg: '', border: '', label: 'Text' },
  system: { icon: Settings2, color: 'text-[var(--color-stone)]/60', bg: '', border: '', label: 'System' },
  error: { icon: AlertCircle, color: 'text-[var(--color-vermillion)]', bg: 'bg-[rgba(199,62,58,0.06)]', border: 'border-l-2 border-l-[var(--color-vermillion)]', label: 'Error' },
  result: { icon: CheckCircle2, color: 'text-[var(--color-jade)]', bg: 'bg-[rgba(45,212,191,0.06)]', border: 'border-l-2 border-l-[var(--color-jade)]', label: 'Done' },
}

// Special renderer for TodoWrite tool
interface TodoItem {
  content: string
  status: 'pending' | 'in_progress' | 'completed'
  activeForm?: string
}

function TodoWriteMessage({ msg, isExpanded, onToggle }: { msg: AgentMessage; isExpanded: boolean; onToggle: () => void }) {
  const time = formatMessageTime(msg.timestamp)
  const input = msg.metadata?.input as { todos?: TodoItem[] } | undefined
  const todos = input?.todos || []

  const completed = todos.filter(t => t.status === 'completed').length
  const inProgress = todos.filter(t => t.status === 'in_progress').length

  // Find the current in-progress task for the header
  const currentTask = todos.find(t => t.status === 'in_progress')

  return (
    <div className="group">
      {/* Compact header */}
      <div
        className={cn(
          'flex items-center gap-2 py-1.5 px-3 cursor-pointer transition-colors',
          'border-l-2 border-l-[var(--color-harvest)]/40 hover:border-l-[var(--color-harvest)]/60 hover:bg-[var(--color-paper)]/[0.02]',
          isExpanded && 'border-l-[var(--color-harvest)]/60'
        )}
        onClick={onToggle}
      >
        <ChevronRight className={cn('w-3 h-3 text-[var(--color-stone)]/30 transition-transform', isExpanded && 'rotate-90')} />

        {/* Icon */}
        <CheckCircle2 className="w-2.5 h-2.5 text-[var(--color-harvest)]/70 shrink-0" />

        {/* Tool name + summary */}
        <span className="text-[0.6875rem] font-medium text-[var(--color-harvest)]/80 font-mono">TodoWrite</span>

        {/* Current task or summary */}
        <span className="text-[0.6875rem] text-[var(--color-paper)]/60 truncate flex-1 min-w-0">
          {currentTask ? (
            <span className="text-[var(--color-sky)]">{currentTask.content}</span>
          ) : todos.length > 0 ? (
            <span className="text-[var(--color-stone)]/60">
              {completed}/{todos.length} done
              {inProgress > 0 && <span className="text-[var(--color-sky)]"> · {inProgress} active</span>}
            </span>
          ) : (
            <span className="text-[var(--color-stone)]/50">cleared</span>
          )}
        </span>

        {/* Timestamp */}
        <span className="text-[0.625rem] text-[var(--color-stone)]/40 font-mono shrink-0">{time}</span>
      </div>

      {/* Expanded todo list */}
      {isExpanded && todos.length > 0 && (
        <div className="border-l-2 border-l-[var(--color-harvest)]/60 ml-0 pl-4 py-2 bg-[var(--color-paper)]/[0.02]">
          <div className="space-y-1">
            {todos.map((todo, idx) => (
              <div key={idx} className="flex items-start gap-2 text-[0.6875rem]">
                {/* Status indicator */}
                {todo.status === 'completed' ? (
                  <span className="text-[var(--color-jade)] shrink-0">✓</span>
                ) : todo.status === 'in_progress' ? (
                  <span className="text-[var(--color-sky)] shrink-0">●</span>
                ) : (
                  <span className="text-[var(--color-stone)]/40 shrink-0">○</span>
                )}
                {/* Content */}
                <span className={cn(
                  todo.status === 'completed' && 'text-[var(--color-stone)]/50 line-through',
                  todo.status === 'in_progress' && 'text-[var(--color-paper)]/90',
                  todo.status === 'pending' && 'text-[var(--color-paper)]/60'
                )}>
                  {todo.content}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function ToolCallMessage({ msg, isExpanded, onToggle }: { msg: AgentMessage; isExpanded: boolean; onToggle: () => void }) {
  const toolName = msg.metadata?.tool || 'Unknown'

  // Use specialized renderer for TodoWrite
  if (toolName === 'TodoWrite') {
    return <TodoWriteMessage msg={msg} isExpanded={isExpanded} onToggle={onToggle} />
  }

  const primaryParam = getToolPrimaryParam(msg.metadata?.input)
  const fullParams = formatToolInputFull(msg.metadata?.input)
  const hasMultipleParams = fullParams.length > 1
  const time = formatMessageTime(msg.timestamp)

  return (
    <div className="group">
      {/* Compact header - always visible */}
      <div
        className={cn(
          'flex items-center gap-2 py-1.5 px-3 cursor-pointer transition-colors',
          'border-l-2 border-l-[var(--color-stone)]/20 hover:border-l-[var(--color-stone)]/40 hover:bg-[var(--color-paper)]/[0.02]',
          isExpanded && 'border-l-[var(--color-stone)]/40'
        )}
        onClick={onToggle}
      >
        {/* Expand/collapse indicator */}
        {hasMultipleParams ? (
          <ChevronRight className={cn('w-3 h-3 text-[var(--color-stone)]/30 transition-transform', isExpanded && 'rotate-90')} />
        ) : (
          <div className="w-3" />
        )}

        {/* Icon */}
        <Wrench className="w-2.5 h-2.5 text-[var(--color-stone)]/50 shrink-0" />

        {/* Tool name */}
        <span className="text-[0.6875rem] font-medium text-[var(--color-stone)]/80 font-mono">{toolName}</span>

        {/* Primary param preview */}
        {primaryParam && (
          <span className="text-[0.6875rem] text-[var(--color-paper)]/60 font-mono truncate flex-1 min-w-0">
            <span className="text-[var(--color-stone)]/50">{primaryParam.key}=</span>
            <span className="text-[var(--color-paper)]/70">"{primaryParam.value}"</span>
          </span>
        )}

        {/* Timestamp */}
        <span className="text-[0.625rem] text-[var(--color-stone)]/40 font-mono shrink-0">{time}</span>
      </div>

      {/* Expanded params */}
      {isExpanded && fullParams.length > 0 && (
        <div className="border-l-2 border-l-[var(--color-stone)]/40 ml-0 pl-6 py-1.5 bg-[var(--color-paper)]/[0.02]">
          <div className="space-y-1">
            {fullParams.map((param, idx) => (
              <div key={idx} className="flex gap-2 text-[0.625rem] font-mono">
                <span className="text-[var(--color-stone)]/50 shrink-0 min-w-[70px]">{param.key}</span>
                <span className="text-[var(--color-paper)]/70 whitespace-pre-wrap break-all">{param.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function TextMessage({ msg }: { msg: AgentMessage }) {
  const time = formatMessageTime(msg.timestamp)
  const isLong = msg.content.length > 200
  const [isExpanded, setIsExpanded] = useState(!isLong)

  return (
    <div className="flex items-start gap-2 py-1.5 px-3">
      <MessageSquare className="w-2.5 h-2.5 text-[var(--color-paper)]/40 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className={cn(
          'text-[0.75rem] text-[var(--color-paper)]/90 leading-relaxed',
          !isExpanded && 'line-clamp-2'
        )}>
          <ReactMarkdown
            components={{
              p: ({ children }) => <span>{children} </span>,
              code: ({ children }) => <code className="text-[var(--color-paper)]/70 bg-[var(--color-ink)] px-1 py-0.5 rounded text-[0.6875rem]">{children}</code>,
            }}
          >
            {msg.content}
          </ReactMarkdown>
        </div>
        {isLong && (
          <button
            className="text-[0.625rem] text-[var(--color-stone)]/60 hover:text-[var(--color-paper)] mt-1"
            onClick={() => setIsExpanded(!isExpanded)}
          >
            {isExpanded ? 'Show less' : 'Show more'}
          </button>
        )}
      </div>
      <span className="text-[0.625rem] text-[var(--color-stone)]/40 font-mono shrink-0">{time}</span>
    </div>
  )
}

function SystemMessage({ msg }: { msg: AgentMessage }) {
  const time = formatMessageTime(msg.timestamp)
  const config = MESSAGE_CONFIG[msg.type] || MESSAGE_CONFIG.system
  const Icon = config.icon

  return (
    <div className={cn('flex items-center gap-2 py-1.5 px-3', config.bg, config.border)}>
      <Icon className={cn('w-2.5 h-2.5 shrink-0', config.color)} />
      <span className={cn('text-[0.6875rem] flex-1', config.color)}>{msg.content}</span>
      <span className="text-[0.625rem] text-[var(--color-stone)]/40 font-mono shrink-0">{time}</span>
    </div>
  )
}

// Message filter types
type MessageFilter = 'all' | 'tool_use' | 'text' | 'error'

function MessagesPanel({ messages, scrollRef }: { messages: AgentMessage[]; scrollRef?: React.RefObject<HTMLDivElement | null> }) {
  const [filter, setFilter] = useState<MessageFilter>('all')
  const [expandedTools, setExpandedTools] = useState<Set<number>>(new Set())
  const [showScrollButton, setShowScrollButton] = useState(false)
  const internalScrollRef = useRef<HTMLDivElement>(null)
  const containerRef = scrollRef || internalScrollRef

  // Track scroll position to show/hide scroll-to-bottom button
  const handleScroll = useCallback(() => {
    const container = containerRef.current
    if (!container) return
    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100
    setShowScrollButton(!isNearBottom)
  }, [containerRef])

  const scrollToBottom = useCallback(() => {
    const container = containerRef.current
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' })
    }
  }, [containerRef])

  const toggleToolExpanded = (idx: number) => {
    setExpandedTools(prev => {
      const next = new Set(prev)
      if (next.has(idx)) {
        next.delete(idx)
      } else {
        next.add(idx)
      }
      return next
    })
  }

  // Count message types for filter badges
  const counts = {
    tool_use: messages.filter(m => m.type === 'tool_use').length,
    text: messages.filter(m => m.type === 'text').length,
    error: messages.filter(m => m.type === 'error').length,
  }

  const filteredMessages = filter === 'all'
    ? messages
    : messages.filter(m => m.type === filter)

  return (
    <div className="flex flex-col h-full">
      {/* Filter bar */}
      <div className="flex items-center gap-1 px-3 py-2 border-b border-[rgba(163,163,163,0.08)] shrink-0">
        <Filter className="w-3 h-3 text-[var(--color-stone)]/40 mr-1" />
        <button
          className={cn(
            'px-2 py-1 text-[0.625rem] rounded-sm transition-colors',
            filter === 'all'
              ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
              : 'text-[var(--color-stone)]/60 hover:text-[var(--color-paper)]'
          )}
          onClick={() => setFilter('all')}
        >
          All
        </button>
        <button
          className={cn(
            'px-2 py-1 text-[0.625rem] rounded-sm transition-colors flex items-center gap-1',
            filter === 'tool_use'
              ? 'bg-[rgba(102,178,255,0.15)] text-[var(--color-sky)]'
              : 'text-[var(--color-stone)]/60 hover:text-[var(--color-sky)]'
          )}
          onClick={() => setFilter('tool_use')}
        >
          <Wrench className="w-3 h-3" />
          Tools
          <span className="text-[0.5rem] opacity-60">{counts.tool_use}</span>
        </button>
        <button
          className={cn(
            'px-2 py-1 text-[0.625rem] rounded-sm transition-colors flex items-center gap-1',
            filter === 'text'
              ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
              : 'text-[var(--color-stone)]/60 hover:text-[var(--color-paper)]'
          )}
          onClick={() => setFilter('text')}
        >
          <MessageSquare className="w-3 h-3" />
          Text
          <span className="text-[0.5rem] opacity-60">{counts.text}</span>
        </button>
        {counts.error > 0 && (
          <button
            className={cn(
              'px-2 py-1 text-[0.625rem] rounded-sm transition-colors flex items-center gap-1',
              filter === 'error'
                ? 'bg-[rgba(199,62,58,0.15)] text-[var(--color-vermillion)]'
                : 'text-[var(--color-stone)]/60 hover:text-[var(--color-vermillion)]'
            )}
            onClick={() => setFilter('error')}
          >
            <AlertCircle className="w-3 h-3" />
            Errors
            <span className="text-[0.5rem] opacity-60">{counts.error}</span>
          </button>
        )}
      </div>

      {/* Messages list */}
      <div className="relative flex-1">
        <div
          ref={containerRef as React.RefObject<HTMLDivElement>}
          className="absolute inset-0 overflow-y-auto px-2 py-2 space-y-1"
          onScroll={handleScroll}
        >
          {filteredMessages.length === 0 ? (
            <div className="flex items-center justify-center h-32 text-[var(--color-stone)]/50 text-[0.75rem]">
              No messages
            </div>
          ) : (
            filteredMessages.map((msg) => {
              const originalIdx = messages.indexOf(msg)
              if (msg.type === 'tool_use') {
                return (
                  <ToolCallMessage
                    key={originalIdx}
                    msg={msg}
                    isExpanded={expandedTools.has(originalIdx)}
                    onToggle={() => toggleToolExpanded(originalIdx)}
                  />
                )
              }
              if (msg.type === 'text') {
                return <TextMessage key={originalIdx} msg={msg} />
              }
              return <SystemMessage key={originalIdx} msg={msg} />
            })
          )}
        </div>

        {/* Scroll to bottom button */}
        {showScrollButton && (
          <button
            onClick={scrollToBottom}
            className="absolute bottom-3 right-3 p-1.5 rounded-full bg-[var(--color-ink)] border border-[var(--color-stone)]/20 text-[var(--color-stone)]/60 hover:text-[var(--color-paper)] hover:border-[var(--color-stone)]/40 transition-all shadow-lg"
            title="Scroll to bottom"
          >
            <ChevronDown className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}

export function RunDetailDialog({ run, open, onOpenChange, onRunUpdated, initialTab, onTabChange }: RunDetailDialogProps) {
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [logs, setLogs] = useState<{ stdout: string; stderr: string; messages: string }>({ stdout: '', stderr: '', messages: '' })
  const [activeTab, setActiveTabInternal] = useState<TabType>(initialTab || 'messages')

  // Wrap setActiveTab to notify parent
  const setActiveTab = useCallback((tab: TabType) => {
    setActiveTabInternal(tab)
    onTabChange?.(tab)
  }, [onTabChange])

  // Sync with initialTab when it changes (URL navigation)
  useEffect(() => {
    if (initialTab && initialTab !== activeTab) {
      setActiveTabInternal(initialTab)
    }
  }, [initialTab])
  const [loading, setLoading] = useState(false)
  const [commitsData, setCommitsData] = useState<RunCommitsResponse | null>(null)
  const [filesData, setFilesData] = useState<RunFilesResponse | null>(null)
  const [loadingCommits, setLoadingCommits] = useState(false)
  const [loadingFiles, setLoadingFiles] = useState(false)
  // Expanded commit/file state for lazy loading details
  const [expandedCommit, setExpandedCommit] = useState<string | null>(null)
  const [commitDetails, setCommitDetails] = useState<Record<string, CommitDetail>>({})
  const [loadingCommitDetail, setLoadingCommitDetail] = useState<string | null>(null)
  const [expandedFile, setExpandedFile] = useState<string | null>(null)
  const [fileDiffs, setFileDiffs] = useState<Record<string, FileDiff>>({})
  const [loadingFileDiff, setLoadingFileDiff] = useState<string | null>(null)
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
  // Archive state removed - Archive button currently disabled
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
      setActiveTabInternal(initialTab || 'messages')
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
      setExpandedCommit(null)
      setCommitDetails({})
      setExpandedFile(null)
      setFileDiffs({})
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
        const [runDetail, stdoutLogs, stderrLogs, messagesLogs, newCommitsData, newFilesData] = await Promise.all([
          fetchRun(run.id),
          fetchLogs(run.id, 'stdout').catch(() => ({ content: '' })),
          fetchLogs(run.id, 'stderr').catch(() => ({ content: '' })),
          fetchLogs(run.id, 'messages').catch(() => ({ content: '' })),
          // Also refresh commits and files during active runs
          fetchRunCommits(run.id).catch(() => null),
          fetchRunFiles(run.id).catch(() => null),
        ])
        setDetail(runDetail)
        setLogs({ stdout: stdoutLogs.content || '', stderr: stderrLogs.content || '', messages: messagesLogs.content || '' })
        // Update commits and files if fetched successfully
        if (newCommitsData) setCommitsData(newCommitsData)
        if (newFilesData) setFilesData(newFilesData)
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
      // Fetch run details (which refreshes PR status from GitHub if PR is open),
      // logs, commits, and files all in parallel
      const [runDetail, stdoutLogs, stderrLogs, messagesLogs, newCommitsData, newFilesData] = await Promise.all([
        fetchRun(run.id),
        fetchLogs(run.id, 'stdout').catch(() => ({ content: '' })),
        fetchLogs(run.id, 'stderr').catch(() => ({ content: '' })),
        fetchLogs(run.id, 'messages').catch(() => ({ content: '' })),
        // Also refresh commits and files data
        fetchRunCommits(run.id).catch(() => null),
        fetchRunFiles(run.id).catch(() => null),
      ])
      setDetail(runDetail)
      setLogs({ stdout: stdoutLogs.content || '', stderr: stderrLogs.content || '', messages: messagesLogs.content || '' })
      // Update commits and files if fetched successfully
      if (newCommitsData) {
        setCommitsData(newCommitsData)
        // Clear cached commit details since files may have changed
        setCommitDetails({})
        setExpandedCommit(null)
      }
      if (newFilesData) {
        setFilesData(newFilesData)
        // Clear cached file diffs since content may have changed
        setFileDiffs({})
        setExpandedFile(null)
      }
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
      // Resume continues the same run in-place
      const result = await resumeRun(run.id, resumePrompt.trim())

      // Upload images to the run if any (same run_id)
      if (resumePendingImages.length > 0 && result.run_id) {
        const uploadPromises = resumePendingImages.map(img =>
          uploadAndAttachImage(result.run_id, img.file).catch(err => {
            console.error(`Failed to upload image ${img.file.name}:`, err)
            return null
          })
        )
        await Promise.all(uploadPromises)
      }

      // Cleanup images and prompt, but DON'T close dialog
      // The run is now RUNNING again - stay open to watch progress
      resumePendingImages.forEach(img => URL.revokeObjectURL(img.preview))
      setResumePendingImages([])
      setResumePrompt('')

      // Refresh the run data to show new status
      handleRefresh()
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

  // Handler for expanding a commit to see full message + files
  const handleExpandCommit = async (sha: string) => {
    if (expandedCommit === sha) {
      setExpandedCommit(null)
      return
    }
    setExpandedCommit(sha)
    // Load commit details if not already cached
    if (!commitDetails[sha] && run) {
      setLoadingCommitDetail(sha)
      try {
        const detail = await fetchCommitDetail(run.id, sha)
        setCommitDetails(prev => ({ ...prev, [sha]: detail }))
      } catch (err) {
        console.error('Failed to load commit details:', err)
      } finally {
        setLoadingCommitDetail(null)
      }
    }
  }

  // Handler for expanding a file to see diff
  const handleExpandFile = async (filePath: string) => {
    if (expandedFile === filePath) {
      setExpandedFile(null)
      return
    }
    setExpandedFile(filePath)
    // Load file diff if not already cached
    if (!fileDiffs[filePath] && run) {
      setLoadingFileDiff(filePath)
      try {
        const diff = await fetchFileDiff(run.id, filePath)
        setFileDiffs(prev => ({ ...prev, [filePath]: diff }))
      } catch (err) {
        console.error('Failed to load file diff:', err)
      } finally {
        setLoadingFileDiff(null)
      }
    }
  }

  // handleArchive removed - Archive button currently disabled

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
      } else if (result.has_conflicts && result.conflicting_files && result.conflicting_files.length > 0) {
        // Merge conflicts detected - prompt user to resolve via agent resume
        const filesStr = result.conflicting_files.slice(0, 10).join('\n- ')
        const moreCount = result.conflicting_files.length > 10 ? result.conflicting_files.length - 10 : 0
        const conflictPrompt = `The merge has conflicts that need to be resolved. Please fix these merge conflicts:

Conflicting files:
- ${filesStr}${moreCount > 0 ? `\n- ... and ${moreCount} more files` : ''}

Steps to resolve:
1. In the worktree, run: git merge ${detail?.source_branch || 'main'}
2. Resolve each conflict by understanding both changes and merging them appropriately
3. After resolving all conflicts, commit the merge
4. Push the changes

Focus on preserving functionality from both sides where possible.`

        setResumePrompt(conflictPrompt)
        setMergeError(`Merge conflicts in ${result.conflicting_files.length} file(s). Use the resume prompt below to have Claude resolve them.`)

        // Scroll to resume section
        setTimeout(() => {
          document.querySelector('textarea[placeholder*="Continue with follow-up"]')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }, 100)
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
        <div className="flex items-center justify-between px-4 sm:px-5 py-2.5 border-b border-[rgba(163,163,163,0.1)] bg-[var(--color-void)]">
          {/* Left: Back (mobile) + Run Identity */}
          <div className="flex items-center gap-2 min-w-0">
            <button
              className="md:hidden p-1 -ml-1 text-[var(--color-stone)] hover:text-[var(--color-paper)] transition-colors shrink-0"
              onClick={() => onOpenChange(false)}
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            {/* Run status and ID */}
            <div className="flex items-center gap-2 shrink-0">
              <div className={cn('mark', `mark-${run?.status}`)} />
              <span className="text-mono text-[var(--color-stone)]/60 text-[0.625rem]">{run?.id.slice(0, 8)}</span>
              <span className="text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/55">{run?.status}</span>
            </div>
            {/* Git context - branch and commit */}
            {detail?.branch_name && (
              <div className="hidden sm:flex items-center gap-1.5 ml-1 text-[0.625rem] text-[var(--color-stone)]/50">
                <span className="text-[var(--color-stone)]/30">on</span>
                <GitBranch className="w-2.5 h-2.5 text-purple-400/70" />
                <span className="text-purple-300/80 truncate max-w-[100px]">{detail.branch_name}</span>
                {detail.git_commit_sha && (
                  <>
                    <span className="text-[var(--color-stone)]/30">@</span>
                    <span className="text-mono text-[var(--color-stone)]/50">{detail.git_commit_sha.slice(0, 7)}</span>
                  </>
                )}
              </div>
            )}
          </div>

          {/* Right: PR Status + Actions */}
          <div className="flex items-center gap-2 shrink-0">
            {/* PR badge - informational, links to GitHub */}
            {detail?.pr_number && detail?.pr_url && (
              <a
                href={detail.pr_url}
                target="_blank"
                rel="noopener noreferrer"
                className={cn(
                  'hidden sm:flex items-center gap-1.5 px-2 py-1 rounded-sm text-[0.625rem] transition-colors',
                  detail.pr_mergeable === 'CONFLICTING' && 'bg-[rgba(239,68,68,0.15)] border border-[rgba(239,68,68,0.3)] text-red-400 hover:bg-[rgba(239,68,68,0.2)]',
                  detail.pr_mergeable !== 'CONFLICTING' && detail.pr_status === 'open' && 'bg-[rgba(34,197,94,0.1)] border border-[rgba(34,197,94,0.2)] text-green-400 hover:bg-[rgba(34,197,94,0.15)]',
                  detail.pr_status === 'merged' && 'bg-[rgba(168,85,247,0.1)] border border-[rgba(168,85,247,0.2)] text-purple-400',
                  detail.pr_status === 'closed' && 'bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.2)] text-red-400',
                  detail.pr_status === 'draft' && 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]'
                )}
                title={detail.pr_mergeable === 'CONFLICTING' ? 'PR has merge conflicts - click to view on GitHub' : `View PR #${detail.pr_number} on GitHub`}
              >
                <GitPullRequest className="w-3 h-3" />
                <span>#{detail.pr_number}</span>
                {detail.pr_mergeable === 'CONFLICTING' ? (
                  <span className="uppercase font-medium">Conflicts</span>
                ) : (
                  <span className="uppercase">{detail.pr_status}</span>
                )}
                <ExternalLink className="w-2.5 h-2.5 opacity-60" />
              </a>
            )}

            {/* Divider between info and actions */}
            {(detail?.pr_number || detail?.branch_name) && (
              <div className="hidden sm:block w-px h-4 bg-[var(--color-stone)]/20" />
            )}

            {/* Action buttons */}
            <div className="flex items-center gap-1 pr-8">
              {/* Merge - only show when PR is open AND mergeable (no conflicts) */}
              {detail?.pr_status === 'open' && detail?.pr_mergeable !== 'CONFLICTING' && detail?.branch_name && !isActive && (
                <button
                  onClick={handleMerge}
                  disabled={merging}
                  className={cn(
                    'flex items-center gap-1.5 px-2.5 py-1 text-[0.625rem] uppercase tracking-widest rounded-sm transition-colors',
                    merging
                      ? 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                      : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]'
                  )}
                  title="Merge branch locally and push to remote"
                >
                  <GitMerge className="w-3 h-3" />
                  <span>{merging ? 'Merging...' : 'Merge'}</span>
                </button>
              )}
              {/* Resolve Conflicts - show when PR has conflicts and run is resumable */}
              {detail?.pr_mergeable === 'CONFLICTING' && isResumable && !isActive && (
                <button
                  onClick={() => {
                    const conflictPrompt = `The PR for this branch has merge conflicts. Please resolve them:

1. Rebase this branch onto ${detail?.source_branch || 'main'}
2. For each conflict, understand the intent of both changes and merge them intelligently
3. After resolving all conflicts, force-push the rebased branch
4. The PR should become mergeable after this

Focus on preserving the functionality from both sides where possible.`
                    setResumePrompt(conflictPrompt)
                    // Scroll to resume section
                    setTimeout(() => {
                      document.querySelector('textarea[placeholder*="Continue with follow-up"]')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                    }, 100)
                  }}
                  className="flex items-center gap-1.5 px-2.5 py-1 text-[0.625rem] uppercase tracking-widest rounded-sm transition-colors bg-[rgba(168,85,247,0.15)] border border-[rgba(168,85,247,0.3)] text-purple-400 hover:bg-[rgba(168,85,247,0.25)]"
                  title="Use Claude to resolve merge conflicts"
                >
                  <Sparkles className="w-3 h-3" />
                  <span>Resolve</span>
                </button>
              )}
              {/* Create PR - show for worktree runs with branch, has remote, but no PR */}
              {detail?.use_worktree && detail?.branch_name && detail?.has_remote && !detail?.pr_url && !isActive && (
                <button
                  onClick={handleCreatePr}
                  disabled={creatingPr}
                  className={cn(
                    'flex items-center gap-1.5 px-2.5 py-1 text-[0.625rem] uppercase tracking-widest rounded-sm transition-colors',
                    creatingPr
                      ? 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                      : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]'
                  )}
                >
                  <GitPullRequest className="w-3 h-3" />
                  <span>{creatingPr ? 'Creating...' : 'Create PR'}</span>
                </button>
              )}
              {/* Merge (local) - show for worktree runs with branch but NO remote, hide if already merged */}
              {detail?.use_worktree && detail?.branch_name && !detail?.has_remote && detail?.pr_status !== 'merged' && !isActive && (
                <button
                  onClick={handleMerge}
                  disabled={merging}
                  className={cn(
                    'flex items-center gap-1.5 px-2.5 py-1 text-[0.625rem] uppercase tracking-widest rounded-sm transition-colors',
                    merging
                      ? 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                      : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]'
                  )}
                  title="Merge branch locally"
                >
                  <GitMerge className="w-3 h-3" />
                  <span>{merging ? 'Merging...' : 'Merge'}</span>
                </button>
              )}
              {/* Cancel - for active runs */}
              {isActive && (
                <button
                  className="flex items-center gap-1.5 px-2.5 py-1 text-[0.625rem] uppercase tracking-widest text-[var(--color-vermillion)] hover:text-[var(--color-vermillion)] border border-[var(--color-vermillion)]/30 hover:border-[var(--color-vermillion)]/50 hover:bg-[rgba(199,62,58,0.1)] rounded-sm transition-colors"
                  onClick={handleCancel}
                  disabled={cancelling}
                >
                  {cancelling ? 'Cancelling...' : 'Cancel'}
                </button>
              )}
              {/* Archive - for completed runs */}
              {/* {!isActive && (
                <button
                  className="flex items-center gap-1.5 px-2 py-1 text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/60 hover:text-[var(--color-stone)] border border-[var(--color-stone)]/15 hover:border-[var(--color-stone)]/30 rounded-sm transition-colors"
                  onClick={handleArchive}
                  disabled={archiving}
                  title="Archive this run"
                >
                  <Archive className="w-3 h-3" />
                  <span>{archiving ? '...' : 'Archive'}</span>
                </button>
              )} */}
              {/* Refresh */}
              <button
                className="p-1.5 text-[var(--color-stone)]/50 hover:text-[var(--color-paper)] transition-colors rounded-sm hover:bg-[var(--color-paper)]/5"
                onClick={handleRefresh}
                disabled={loading}
                title="Refresh"
              >
                <RotateCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
              </button>
            </div>
          </div>
        </div>

        {/* Main Content */}
        <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
          <div className="p-4 sm:p-5 flex flex-col flex-1 min-h-0">
            {/* Project + Meta Row */}
            <div className="flex items-center gap-4 text-[0.6875rem] text-[var(--color-stone)]/60 mb-4 shrink-0 flex-wrap">
              <span className="text-[var(--color-paper)]/80">{run?.project_name}</span>
              <span className="hidden sm:inline">{formatDateWithContext(run?.created_at ?? null)}</span>
              {run?.duration_seconds !== null && (
                <span className="text-mono">{formatDuration(run?.duration_seconds ?? null)}</span>
              )}
              {detail?.exit_code !== null && detail?.exit_code !== undefined && (
                <span className="text-mono">exit {detail?.exit_code}</span>
              )}
              {detail?.cost_usd != null && detail.cost_usd > 0 && (
                <span className="text-mono text-[var(--color-harvest)]">${detail.cost_usd.toFixed(4)}</span>
              )}
              {/* Tool count - calculated from messages */}
              {(() => {
                const toolCount = parseMessages(logs.messages).filter(m => m.type === 'tool_use').length
                return toolCount > 0 ? (
                  <span className="text-mono text-[var(--color-sky)]">{toolCount} tools</span>
                ) : null
              })()}
            </div>

            {/* Git Info Row - show on mobile only (desktop shows in header) */}
            {(detail?.branch_name || detail?.pr_number) && (
              <div className="flex sm:hidden items-center gap-3 text-[0.6875rem] mb-4 shrink-0 flex-wrap">
                {detail.branch_name && (
                  <div className="flex items-center gap-1.5 px-2 py-1 bg-[rgba(168,85,247,0.1)] border border-[rgba(168,85,247,0.2)] rounded-sm">
                    <GitBranch className="w-3 h-3 text-purple-400" />
                    <span className="text-purple-300">{detail.branch_name}</span>
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
                        {(() => {
                          const count = commitsData?.commit_count ?? detail?.commit_count
                          return count && count > 0 ? (
                            <span className="text-[0.5rem] text-[var(--color-stone)]/50">({count})</span>
                          ) : null
                        })()}
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
                        {(() => {
                          const count = filesData?.file_count ?? detail?.file_count
                          return count && count > 0 ? (
                            <span className="text-[0.5rem] text-[var(--color-stone)]/50">({count})</span>
                          ) : null
                        })()}
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
                  <div className="h-full overflow-hidden">
                    <MessagesPanel messages={parseMessages(logs.messages)} scrollRef={messagesContainerRef} />
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
                                  {formatDateWithContext(historyRun.created_at)} · {formatDuration(historyRun.duration_seconds)}
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
                        {/* Commits list - compact expandable (newest first) */}
                        {commitsData.commits.slice().reverse().map((commit, idx) => {
                          const isExpanded = expandedCommit === commit.sha
                          const detail = commitDetails[commit.sha]
                          const isLoading = loadingCommitDetail === commit.sha

                          return (
                            <div
                              key={commit.sha}
                              className={cn(
                                'border-b border-[rgba(163,163,163,0.05)]',
                                idx === commitsData.commits.length - 1 && 'border-b-0'
                              )}
                            >
                              {/* Commit header - compact single line */}
                              <button
                                className="w-full flex items-center gap-2 py-1.5 text-left hover:bg-[var(--color-paper)]/5 transition-colors px-1 -mx-1 rounded"
                                onClick={() => handleExpandCommit(commit.sha)}
                              >
                                {/* Expand chevron */}
                                <ChevronRight className={cn(
                                  'w-3 h-3 text-[var(--color-stone)]/40 transition-transform shrink-0',
                                  isExpanded && 'rotate-90'
                                )} />
                                {/* Commit message */}
                                <span className="text-[0.6875rem] text-[var(--color-paper)]/90 truncate flex-1 min-w-0">
                                  {commit.message}
                                </span>
                                {/* Relative time */}
                                <span className="text-[0.625rem] text-[var(--color-stone)]/50 shrink-0">
                                  {formatRelativeTime(commit.date)}
                                </span>
                                {/* Short hash */}
                                <span className="text-mono text-[0.625rem] text-[var(--color-stone)]/40 shrink-0">
                                  {commit.sha.slice(0, 7)}
                                </span>
                              </button>
                              {/* Expanded content */}
                              {isExpanded && (
                                <div className="ml-5 pl-3 border-l border-[rgba(163,163,163,0.15)] mb-2">
                                  {isLoading ? (
                                    <div className="py-2 flex items-center gap-2">
                                      <RotateCw className="w-3 h-3 animate-spin text-[var(--color-stone)]/50" />
                                      <span className="text-[0.625rem] text-[var(--color-stone)]/50">Loading...</span>
                                    </div>
                                  ) : detail ? (
                                    <div className="py-2 space-y-2">
                                      {/* Full commit message */}
                                      {detail.message && detail.message !== commit.message && (
                                        <pre className="text-[0.6875rem] text-[var(--color-paper)]/70 whitespace-pre-wrap font-sans leading-relaxed">
                                          {detail.message}
                                        </pre>
                                      )}
                                      {/* Files changed in this commit */}
                                      {detail.files && detail.files.length > 0 && (
                                        <div className="space-y-1">
                                          <p className="text-[0.625rem] text-[var(--color-stone)]/60 font-medium">
                                            {detail.files.length} file{detail.files.length !== 1 ? 's' : ''} changed
                                          </p>
                                          <div className="space-y-0.5">
                                            {detail.files.map((file) => (
                                              <div key={file.file_path} className="flex items-center gap-2 text-[0.625rem]">
                                                <span className={cn(
                                                  'uppercase px-1 py-0.5 rounded font-medium text-[0.5rem]',
                                                  file.change_type === 'added' && 'bg-[rgba(45,212,191,0.15)] text-[var(--color-jade)]',
                                                  file.change_type === 'modified' && 'bg-[rgba(102,178,255,0.15)] text-[var(--color-sky)]',
                                                  file.change_type === 'deleted' && 'bg-[rgba(199,62,58,0.15)] text-[var(--color-vermillion)]',
                                                  file.change_type === 'renamed' && 'bg-[rgba(168,85,247,0.15)] text-purple-400'
                                                )}>
                                                  {file.change_type === 'added' ? 'A' : file.change_type === 'modified' ? 'M' : file.change_type === 'deleted' ? 'D' : 'R'}
                                                </span>
                                                <span className="text-[var(--color-paper)]/70 font-mono truncate">{file.file_path}</span>
                                                <span className="text-[var(--color-jade)] shrink-0">+{file.additions}</span>
                                                <span className="text-[var(--color-vermillion)] shrink-0">-{file.deletions}</span>
                                              </div>
                                            ))}
                                          </div>
                                        </div>
                                      )}
                                    </div>
                                  ) : null}
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    ) : (
                      <div className="flex flex-col items-center justify-center h-32 text-[var(--color-stone)]/50">
                        <GitCommit className="w-6 h-6 mb-2 opacity-50" />
                        {detail?.pr_status === 'merged' ? (
                          <>
                            <span className="text-[0.6875rem]">Branch merged into {detail?.source_branch || 'main'}</span>
                            <span className="text-[0.625rem] mt-1 opacity-70">Commit history no longer available</span>
                          </>
                        ) : (
                          <span className="text-[0.6875rem]">No commits on this branch</span>
                        )}
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
                        {/* Files list - expandable */}
                        {filesData.files.map((file, idx) => {
                          const totalChanges = file.additions + file.deletions
                          const maxBarWidth = 100
                          const additionWidth = totalChanges > 0 ? Math.max((file.additions / totalChanges) * maxBarWidth, file.additions > 0 ? 4 : 0) : 0
                          const deletionWidth = totalChanges > 0 ? Math.max((file.deletions / totalChanges) * maxBarWidth, file.deletions > 0 ? 4 : 0) : 0
                          const isExpanded = expandedFile === file.file_path
                          const diff = fileDiffs[file.file_path]
                          const isLoading = loadingFileDiff === file.file_path

                          return (
                            <div
                              key={file.file_path}
                              className={cn(
                                'border-b border-[rgba(163,163,163,0.05)]',
                                idx === filesData.files.length - 1 && 'border-b-0'
                              )}
                            >
                              {/* File header - clickable */}
                              <button
                                className="w-full flex items-center justify-between py-2 gap-3 text-left hover:bg-[var(--color-paper)]/5 transition-colors px-1 -mx-1 rounded"
                                onClick={() => handleExpandFile(file.file_path)}
                              >
                                {/* File path with change type indicator */}
                                <div className="flex items-center gap-2 min-w-0 flex-1">
                                  <ChevronRight className={cn(
                                    'w-3 h-3 text-[var(--color-stone)]/50 transition-transform shrink-0',
                                    isExpanded && 'rotate-90'
                                  )} />
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
                              </button>
                              {/* Expanded diff content */}
                              {isExpanded && (
                                <div className="ml-4 mb-2 border-l border-[rgba(163,163,163,0.15)] pl-3">
                                  {isLoading ? (
                                    <div className="py-2 flex items-center gap-2">
                                      <RotateCw className="w-3 h-3 animate-spin text-[var(--color-stone)]/50" />
                                      <span className="text-[0.625rem] text-[var(--color-stone)]/50">Loading diff...</span>
                                    </div>
                                  ) : diff && diff.diff ? (
                                    <pre className="text-mono text-[0.625rem] leading-relaxed whitespace-pre-wrap overflow-x-auto max-h-80 overflow-y-auto bg-[var(--color-void)]/50 rounded p-2">
                                      {diff.diff.split('\n').map((line, lineIdx) => {
                                        let lineClass = 'text-[var(--color-paper)]/60'
                                        if (line.startsWith('+') && !line.startsWith('+++')) {
                                          lineClass = 'text-[var(--color-jade)] bg-[rgba(45,212,191,0.08)]'
                                        } else if (line.startsWith('-') && !line.startsWith('---')) {
                                          lineClass = 'text-[var(--color-vermillion)] bg-[rgba(199,62,58,0.08)]'
                                        } else if (line.startsWith('@@')) {
                                          lineClass = 'text-purple-400'
                                        } else if (line.startsWith('diff ') || line.startsWith('index ') || line.startsWith('---') || line.startsWith('+++')) {
                                          lineClass = 'text-[var(--color-stone)]/50'
                                        }
                                        return (
                                          <div key={lineIdx} className={cn('px-1 -mx-1', lineClass)}>
                                            {line || ' '}
                                          </div>
                                        )
                                      })}
                                    </pre>
                                  ) : (
                                    <div className="py-2 text-[0.625rem] text-[var(--color-stone)]/50 italic">
                                      No diff available
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    ) : (
                      <div className="flex flex-col items-center justify-center h-32 text-[var(--color-stone)]/50">
                        <FileCode className="w-6 h-6 mb-2 opacity-50" />
                        {detail?.pr_status === 'merged' ? (
                          <>
                            <span className="text-[0.6875rem]">Branch merged into {detail?.source_branch || 'main'}</span>
                            <span className="text-[0.625rem] mt-1 opacity-70">File changes no longer available</span>
                          </>
                        ) : (
                          <span className="text-[0.6875rem]">No files changed on this branch</span>
                        )}
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
            {(detail?.session_id || detail?.input_tokens || detail?.model_used) && (
              <div className="mt-4 pt-3 border-t border-[rgba(163,163,163,0.06)] shrink-0 flex items-center justify-between">
                <span className="text-mono text-[0.625rem] text-[var(--color-stone)]/50">
                  {detail?.session_id ? `session ${detail.session_id.slice(0, 12)}` : ''}
                </span>
                <div className="flex items-center gap-4 text-mono text-[0.625rem] text-[var(--color-stone)]/50">
                  {(detail?.input_tokens || detail?.output_tokens) && (
                    <span>
                      {formatTokens(detail.input_tokens)} input → {formatTokens(detail.output_tokens)} output
                    </span>
                  )}
                  {detail?.model_used && (
                    <span className="text-[var(--color-stone)]/40">{detail.model_used}</span>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
