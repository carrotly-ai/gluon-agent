/**
 * StreamingLogViewer - Real-time agent log streaming component
 *
 * Combines initial HTTP-fetched logs with WebSocket streamed updates.
 * Shows progress indicators and token usage during active runs.
 */

import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  DollarSign,
  Filter,
  MessageSquare,
  Settings2,
  Wrench,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { type RunProgress, type RunTokens, useRunLogStream } from '@/hooks/useRunLogStream'
import { formatMessageTime } from '@/lib/timestamps'
import type { RunStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

// Message type configuration
const MESSAGE_CONFIG: Record<
  string,
  {
    icon: typeof Wrench
    color: string
    bg: string
    border: string
    label: string
  }
> = {
  tool_use: {
    icon: Wrench,
    color: 'text-[var(--color-stone)]',
    bg: '',
    border: 'border-l-2 border-l-[var(--color-stone)]/30',
    label: 'Tool',
  },
  text: {
    icon: MessageSquare,
    color: 'text-[var(--color-paper)]/70',
    bg: '',
    border: '',
    label: 'Text',
  },
  system: {
    icon: Settings2,
    color: 'text-[var(--color-stone)]/60',
    bg: '',
    border: '',
    label: 'System',
  },
  error: {
    icon: AlertCircle,
    color: 'text-[var(--color-vermillion)]',
    bg: 'bg-[rgba(199,62,58,0.06)]',
    border: 'border-l-2 border-l-[var(--color-vermillion)]',
    label: 'Error',
  },
  result: {
    icon: CheckCircle2,
    color: 'text-[var(--color-jade)]',
    bg: 'bg-[rgba(45,212,191,0.06)]',
    border: 'border-l-2 border-l-[var(--color-jade)]',
    label: 'Done',
  },
}

// Helper to get primary parameter from tool input
function getToolPrimaryParam(input: unknown): { key: string; value: string } | null {
  if (!input || typeof input !== 'object') return null
  const obj = input as Record<string, unknown>

  const priorityKeys = [
    'file_path',
    'command',
    'pattern',
    'query',
    'url',
    'path',
    'content',
    'prompt',
  ]
  for (const key of priorityKeys) {
    if (obj[key] && typeof obj[key] === 'string') {
      let val = obj[key] as string
      // Strip worktree prefix
      if (key === 'file_path' || key === 'path') {
        val = val.replace(/^\/tmp\/gluon-worktrees\/wt-[a-f0-9]+\//, '')
      }
      return { key, value: val.length > 80 ? `${val.slice(0, 77)}...` : val }
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

// Message filter types
type MessageFilter = 'all' | 'tool_use' | 'text' | 'error'

// Convert AgentMessageData to the format MessagesPanel expects
interface AgentMessage {
  timestamp: string
  type: 'text' | 'tool_use' | 'system' | 'error' | 'result'
  content: string
  metadata?: {
    tool?: string
    tool_id?: string
    input?: unknown
  }
}

// TodoWrite specialized renderer
interface TodoItem {
  content: string
  status: 'pending' | 'in_progress' | 'completed'
  activeForm?: string
}

function TodoWriteMessage({
  msg,
  isExpanded,
  onToggle,
}: {
  msg: AgentMessage
  isExpanded: boolean
  onToggle: () => void
}) {
  const time = formatMessageTime(msg.timestamp)
  const input = msg.metadata?.input as { todos?: TodoItem[] } | undefined
  const todos = input?.todos || []

  const completed = todos.filter((t) => t.status === 'completed').length
  const inProgress = todos.filter((t) => t.status === 'in_progress').length
  const currentTask = todos.find((t) => t.status === 'in_progress')

  return (
    <div className="group">
      <div
        className={cn(
          'flex items-center gap-2 py-1.5 px-3 cursor-pointer transition-colors',
          'border-l-2 border-l-[var(--color-jade)]/50 hover:border-l-[var(--color-jade)]/70 hover:bg-[var(--color-paper)]/[0.02]',
          isExpanded && 'border-l-[var(--color-jade)]/70'
        )}
        onClick={onToggle}
      >
        <ChevronRight
          className={cn(
            'w-3 h-3 text-[var(--color-stone)]/30 transition-transform',
            isExpanded && 'rotate-90'
          )}
        />
        <CheckCircle2 className="w-2.5 h-2.5 shrink-0 text-[var(--color-jade)]/80" />
        <span className="text-[0.6875rem] font-medium font-mono text-[var(--color-jade)]/90">
          TodoWrite
        </span>
        <span className="text-[0.6875rem] text-[var(--color-paper)]/60 truncate flex-1 min-w-0">
          {currentTask ? (
            <span className="text-[var(--color-sky)]">{currentTask.content}</span>
          ) : todos.length > 0 ? (
            <span className="text-[var(--color-jade)]/70">
              {completed}/{todos.length} done
              {inProgress > 0 && (
                <span className="text-[var(--color-sky)]"> - {inProgress} active</span>
              )}
            </span>
          ) : (
            <span className="text-[var(--color-stone)]/50">cleared</span>
          )}
        </span>
        <span className="text-[0.625rem] text-[var(--color-stone)]/40 font-mono shrink-0">
          {time}
        </span>
      </div>

      {isExpanded && todos.length > 0 && (
        <div className="border-l-2 border-l-[var(--color-jade)]/70 ml-0 pl-4 py-2 bg-[var(--color-paper)]/[0.02]">
          <div className="space-y-1">
            {todos.map((todo, idx) => (
              <div key={idx} className="flex items-start gap-2 text-[0.6875rem]">
                {todo.status === 'completed' ? (
                  <span className="text-[var(--color-jade)] shrink-0">done</span>
                ) : todo.status === 'in_progress' ? (
                  <span className="text-[var(--color-sky)] shrink-0">...</span>
                ) : (
                  <span className="text-[var(--color-stone)]/40 shrink-0">o</span>
                )}
                <span
                  className={cn(
                    todo.status === 'completed' && 'text-[var(--color-jade)]/70 line-through',
                    todo.status === 'in_progress' && 'text-[var(--color-paper)]/90',
                    todo.status === 'pending' && 'text-[var(--color-paper)]/60'
                  )}
                >
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

function ToolCallMessage({
  msg,
  isExpanded,
  onToggle,
}: {
  msg: AgentMessage
  isExpanded: boolean
  onToggle: () => void
}) {
  const toolName = msg.metadata?.tool || 'Unknown'

  if (toolName === 'TodoWrite') {
    return <TodoWriteMessage msg={msg} isExpanded={isExpanded} onToggle={onToggle} />
  }

  const primaryParam = getToolPrimaryParam(msg.metadata?.input)
  const fullParams = formatToolInputFull(msg.metadata?.input)
  const hasMultipleParams = fullParams.length > 1
  const time = formatMessageTime(msg.timestamp)

  return (
    <div className="group">
      <div
        className={cn(
          'flex items-center gap-2 py-1.5 px-3 cursor-pointer transition-colors',
          'border-l-2 border-l-[var(--color-stone)]/20 hover:border-l-[var(--color-stone)]/40 hover:bg-[var(--color-paper)]/[0.02]',
          isExpanded && 'border-l-[var(--color-stone)]/40'
        )}
        onClick={onToggle}
      >
        {hasMultipleParams ? (
          <ChevronRight
            className={cn(
              'w-3 h-3 text-[var(--color-stone)]/30 transition-transform',
              isExpanded && 'rotate-90'
            )}
          />
        ) : (
          <div className="w-3" />
        )}
        <Wrench className="w-2.5 h-2.5 text-[var(--color-stone)]/50 shrink-0" />
        <span className="text-[0.6875rem] font-medium text-[var(--color-stone)]/80 font-mono">
          {toolName}
        </span>
        {primaryParam && (
          <span className="text-[0.6875rem] text-[var(--color-paper)]/60 font-mono truncate flex-1 min-w-0">
            <span className="text-[var(--color-stone)]/50">{primaryParam.key}=</span>
            <span className="text-[var(--color-paper)]/70">"{primaryParam.value}"</span>
          </span>
        )}
        <span className="text-[0.625rem] text-[var(--color-stone)]/40 font-mono shrink-0">
          {time}
        </span>
      </div>

      {isExpanded && fullParams.length > 0 && (
        <div className="border-l-2 border-l-[var(--color-stone)]/40 ml-0 pl-6 py-1.5 bg-[var(--color-paper)]/[0.02]">
          <div className="space-y-1">
            {fullParams.map((param, idx) => (
              <div key={idx} className="flex gap-2 text-[0.625rem] font-mono">
                <span className="text-[var(--color-stone)]/50 shrink-0 min-w-[70px]">
                  {param.key}
                </span>
                <span className="text-[var(--color-paper)]/70 whitespace-pre-wrap break-all">
                  {param.value}
                </span>
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
        <div
          className={cn(
            'text-[0.75rem] text-[var(--color-paper)]/90 leading-relaxed',
            !isExpanded && 'line-clamp-2'
          )}
        >
          <ReactMarkdown
            components={{
              p: ({ children }) => <span>{children} </span>,
              code: ({ children }) => (
                <code className="text-[var(--color-paper)]/70 bg-[var(--color-ink)] px-1 py-0.5 rounded text-[0.6875rem]">
                  {children}
                </code>
              ),
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
      <span className="text-[0.625rem] text-[var(--color-stone)]/40 font-mono shrink-0">
        {time}
      </span>
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
      <span className="text-[0.625rem] text-[var(--color-stone)]/40 font-mono shrink-0">
        {time}
      </span>
    </div>
  )
}

// Progress indicator component
function ProgressIndicator({
  progress,
  tokens,
}: {
  progress: RunProgress | null
  tokens: RunTokens | null
}) {
  if (!progress && !tokens) return null

  return (
    <div className="flex items-center gap-4 px-3 py-2 border-b border-[rgba(163,163,163,0.08)] bg-[var(--color-void)] text-[0.625rem]">
      {progress && (
        <>
          <div className="flex items-center gap-1.5 text-[var(--color-paper)]/70">
            <MessageSquare className="w-3 h-3 text-[var(--color-sky)]/70" />
            <span>{progress.turns} turns</span>
          </div>
          <div className="flex items-center gap-1.5 text-[var(--color-paper)]/70">
            <Wrench className="w-3 h-3 text-[var(--color-stone)]/70" />
            <span>{progress.tool_calls} tools</span>
          </div>
          <div className="flex items-center gap-1.5 text-[var(--color-stone)]/60">
            <Clock className="w-3 h-3" />
            <span>{Math.round(progress.elapsed_seconds)}s</span>
          </div>
        </>
      )}
      {tokens && (
        <div className="flex items-center gap-1.5 text-[var(--color-harvest)]/80 ml-auto">
          <DollarSign className="w-3 h-3" />
          <span>${tokens.estimated_cost_usd.toFixed(4)}</span>
          <span className="text-[var(--color-stone)]/50">
            ({Math.round(tokens.input_tokens / 1000)}k in /{' '}
            {Math.round(tokens.output_tokens / 1000)}k out)
          </span>
        </div>
      )}
    </div>
  )
}

// Streaming indicator when connected
function StreamingIndicator({
  connected,
  subscribed,
}: {
  connected: boolean
  subscribed: boolean
}) {
  if (!connected) return null

  return (
    <div className="flex items-center gap-1.5 text-[0.5rem] text-[var(--color-jade)]/70 uppercase tracking-wider">
      <Zap className="w-2.5 h-2.5" />
      {subscribed ? 'Live' : 'Connecting...'}
    </div>
  )
}

export interface StreamingLogViewerProps {
  runId: string | null
  runStatus: RunStatus
  /** Initial messages loaded via HTTP (for history when opening completed runs) */
  initialMessages: AgentMessage[]
  /** Callback when initial messages should be refreshed (e.g., after resume) */
  onRefreshInitial?: () => void
}

export function StreamingLogViewer({ runId, runStatus, initialMessages }: StreamingLogViewerProps) {
  const isActive = runStatus === 'running' || runStatus === 'pending'

  // Only subscribe when run is active
  const {
    connected,
    subscribed,
    messages: streamedMessages,
    progress,
    tokens,
    clear,
  } = useRunLogStream(isActive ? runId : null)

  const [filter, setFilter] = useState<MessageFilter>('all')
  const [expandedTools, setExpandedTools] = useState<Set<number>>(new Set())
  const [showScrollButton, setShowScrollButton] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // Clear streamed messages when run changes or completes
  useEffect(() => {
    if (!isActive) {
      clear()
    }
  }, [isActive, clear])

  // Combine initial messages with streamed messages
  // For active runs, we start streaming from where we are, so don't duplicate
  const allMessages: AgentMessage[] = [
    ...initialMessages,
    ...streamedMessages.map(
      (msg): AgentMessage => ({
        timestamp: msg.timestamp || new Date().toISOString(),
        type: msg.type,
        content: msg.content,
        metadata: msg.metadata as AgentMessage['metadata'],
      })
    ),
  ]

  // Handle scroll position tracking
  const handleScroll = useCallback(() => {
    const container = containerRef.current
    if (!container) return
    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100
    setShowScrollButton(!isNearBottom)
  }, [])

  const scrollToBottom = useCallback(() => {
    containerRef.current?.scrollTo({ top: containerRef.current.scrollHeight, behavior: 'smooth' })
  }, [])

  // Auto-scroll when new messages arrive (if already at bottom)
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 150
    if (isNearBottom) {
      container.scrollTo({ top: container.scrollHeight })
    }
  }, [])

  const toggleToolExpanded = (idx: number) => {
    setExpandedTools((prev) => {
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
    tool_use: allMessages.filter((m) => m.type === 'tool_use').length,
    text: allMessages.filter((m) => m.type === 'text').length,
    error: allMessages.filter((m) => m.type === 'error').length,
  }

  const filteredMessages =
    filter === 'all' ? allMessages : allMessages.filter((m) => m.type === filter)

  return (
    <div className="flex flex-col h-full">
      {/* Progress bar for active runs */}
      {isActive && <ProgressIndicator progress={progress} tokens={tokens} />}

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

        {/* Streaming indicator */}
        <div className="ml-auto">
          {isActive && <StreamingIndicator connected={connected} subscribed={subscribed} />}
        </div>
      </div>

      {/* Messages list */}
      <div className="relative flex-1">
        <div
          ref={containerRef}
          className="absolute inset-0 overflow-y-auto px-2 py-2 space-y-1"
          onScroll={handleScroll}
        >
          {filteredMessages.length === 0 ? (
            <div className="flex items-center justify-center h-32 text-[var(--color-stone)]/50 text-[0.75rem]">
              {isActive ? 'Waiting for messages...' : 'No messages'}
            </div>
          ) : (
            filteredMessages.map((msg, idx) => {
              if (msg.type === 'tool_use') {
                return (
                  <ToolCallMessage
                    key={idx}
                    msg={msg}
                    isExpanded={expandedTools.has(idx)}
                    onToggle={() => toggleToolExpanded(idx)}
                  />
                )
              }
              if (msg.type === 'text') {
                return <TextMessage key={idx} msg={msg} />
              }
              return <SystemMessage key={idx} msg={msg} />
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
