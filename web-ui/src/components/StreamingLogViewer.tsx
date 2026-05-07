/**
 * StreamingLogViewer - Real-time agent log streaming component
 *
 * Combines initial HTTP-fetched logs with WebSocket streamed updates.
 * Shows progress indicators and token usage during active runs.
 */

import { useVirtualizer } from '@tanstack/react-virtual'
import {
  AlertCircle,
  Bell,
  Camera,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Copy,
  DollarSign,
  FileCode,
  Filter,
  Flag,
  Lightbulb,
  ListChecks,
  Loader2,
  MessageSquare,
  PlayCircle,
  Settings2,
  TestTube,
  Wrench,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ImageLightbox } from '@/components/ImageLightbox'
import { useNotifications } from '@/hooks/useNotifications'
import { type RunProgress, type RunTokens, useRunLogStream } from '@/hooks/useRunLogStream'
import { getImageFileUrl } from '@/lib/api'
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
  user: {
    icon: MessageSquare,
    color: 'text-[var(--color-sky)]',
    bg: 'bg-[var(--color-sky)]/10',
    border: 'border-l-2 border-l-[var(--color-sky)]/60',
    label: 'You',
  },
  screenshot: {
    icon: Camera,
    color: 'text-[var(--color-harvest)]',
    bg: 'bg-[rgba(245,166,35,0.06)]',
    border: 'border-l-2 border-l-[var(--color-harvest)]/50',
    label: 'Screenshot',
  },
  mcp_status: {
    icon: Zap,
    color: 'text-[var(--color-sky)]',
    bg: '',
    border: 'border-l-2 border-l-[var(--color-sky)]/30',
    label: 'MCP',
  },
  notification: {
    icon: Flag,
    color: 'text-[var(--color-sky)]',
    bg: 'bg-[rgba(56,189,248,0.06)]',
    border: 'border-l-2 border-l-[var(--color-sky)]/50',
    label: 'Notification',
  },
  task_started: {
    icon: PlayCircle,
    color: 'text-[var(--color-sky)]',
    bg: 'bg-[rgba(56,189,248,0.06)]',
    border: 'border-l-2 border-l-[var(--color-sky)]/30',
    label: 'Task Started',
  },
  task_progress: {
    icon: Loader2,
    color: 'text-[var(--color-stone)]/60',
    bg: '',
    border: '',
    label: 'Progress',
  },
  task_notification: {
    icon: Bell,
    color: 'text-[var(--color-jade)]',
    bg: 'bg-[rgba(45,212,191,0.06)]',
    border: 'border-l-2 border-l-[var(--color-jade)]/30',
    label: 'Task Notification',
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
  type:
    | 'text'
    | 'tool_use'
    | 'system'
    | 'error'
    | 'result'
    | 'user'
    | 'screenshot'
    | 'mcp_status'
    | 'notification'
    | 'thinking'
    | 'tool_result'
    | 'task_started'
    | 'task_progress'
    | 'task_notification'
    | 'usage'
  content: string
  metadata?: {
    tool?: string
    tool_id?: string
    input?: unknown
    image_id?: string
    original_name?: string
    size_bytes?: number
    reasoning?: string
    // Usage message fields (emitted with type="usage")
    final?: boolean
    input_tokens?: number
    output_tokens?: number
    cache_read?: number
    cache_create?: number
    context_window?: number | null
    model?: string
    [key: string]: unknown
  }
}

// RALPH_STATUS block parser and renderer
interface RalphStatusData {
  status?: 'COMPLETE' | 'IN_PROGRESS' | 'BLOCKED'
  tasksCompleted?: number
  filesModified?: number
  testsStatus?: 'PASSING' | 'FAILING' | 'NOT_RUN'
  workType?: 'IMPLEMENTATION' | 'TESTING' | 'DOCUMENTATION' | 'REFACTORING'
  exitSignal?: boolean
  recommendation?: string
}

function parseRalphStatus(content: string): {
  before: string
  status: RalphStatusData | null
  after: string
} {
  const pattern = /---RALPH_STATUS---([\s\S]*?)---END_RALPH_STATUS---/
  const match = content.match(pattern)

  if (!match) {
    return { before: content, status: null, after: '' }
  }

  const beforeMatch = content.slice(0, match.index).trim()
  const afterMatch = content.slice((match.index || 0) + match[0].length).trim()
  const blockContent = match[1]

  const data: RalphStatusData = {}

  // Parse each line
  for (const line of blockContent.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed || !trimmed.includes(':')) continue

    const [key, ...valueParts] = trimmed.split(':')
    const value = valueParts.join(':').trim()

    switch (key.trim().toUpperCase()) {
      case 'STATUS':
        if (['COMPLETE', 'IN_PROGRESS', 'BLOCKED'].includes(value.toUpperCase())) {
          data.status = value.toUpperCase() as RalphStatusData['status']
        }
        break
      case 'TASKS_COMPLETED_THIS_LOOP':
        data.tasksCompleted = Number.parseInt(value, 10) || 0
        break
      case 'FILES_MODIFIED':
        data.filesModified = Number.parseInt(value, 10) || 0
        break
      case 'TESTS_STATUS':
        if (['PASSING', 'FAILING', 'NOT_RUN'].includes(value.toUpperCase())) {
          data.testsStatus = value.toUpperCase() as RalphStatusData['testsStatus']
        }
        break
      case 'WORK_TYPE':
        if (
          ['IMPLEMENTATION', 'TESTING', 'DOCUMENTATION', 'REFACTORING'].includes(
            value.toUpperCase()
          )
        ) {
          data.workType = value.toUpperCase() as RalphStatusData['workType']
        }
        break
      case 'EXIT_SIGNAL':
        data.exitSignal = value.toLowerCase() === 'true'
        break
      case 'RECOMMENDATION':
        data.recommendation = value
        break
    }
  }

  return { before: beforeMatch, status: data, after: afterMatch }
}

function RalphStatusBlock({ data }: { data: RalphStatusData }) {
  const statusConfig = {
    COMPLETE: {
      color: 'text-[var(--color-jade)]',
      bg: 'bg-[var(--color-jade)]/10',
      label: 'Complete',
    },
    IN_PROGRESS: {
      color: 'text-[var(--color-sky)]',
      bg: 'bg-[var(--color-sky)]/10',
      label: 'In Progress',
    },
    BLOCKED: {
      color: 'text-[var(--color-vermillion)]',
      bg: 'bg-[var(--color-vermillion)]/10',
      label: 'Blocked',
    },
  }

  const testsConfig = {
    PASSING: { color: 'text-[var(--color-jade)]', label: 'Passing' },
    FAILING: { color: 'text-[var(--color-vermillion)]', label: 'Failing' },
    NOT_RUN: { color: 'text-[var(--color-stone)]/60', label: 'Not Run' },
  }

  const workTypeConfig = {
    IMPLEMENTATION: { icon: FileCode, label: 'Implementation' },
    TESTING: { icon: TestTube, label: 'Testing' },
    DOCUMENTATION: { icon: MessageSquare, label: 'Documentation' },
    REFACTORING: { icon: Settings2, label: 'Refactoring' },
  }

  const config = data.status ? statusConfig[data.status] : statusConfig.IN_PROGRESS
  const testsStatusConfig = data.testsStatus ? testsConfig[data.testsStatus] : null
  const workConfig = data.workType ? workTypeConfig[data.workType] : null
  const WorkIcon = workConfig?.icon || FileCode

  return (
    <div className="my-3 rounded-md border border-[var(--color-stone)]/20 bg-[var(--color-void)] overflow-hidden">
      {/* Header */}
      <div
        className={cn(
          'flex items-center gap-2 px-3 py-2 border-b border-[var(--color-stone)]/15',
          config.bg
        )}
      >
        <Flag className={cn('w-3.5 h-3.5', config.color)} />
        <span className={cn('font-medium text-body', config.color)}>Ralph Status</span>
        <span
          className={cn(
            'ml-auto px-2 py-0.5 rounded text-body font-medium',
            config.bg,
            config.color
          )}
        >
          {config.label}
        </span>
        {data.exitSignal && (
          <span className="px-2 py-0.5 rounded text-body font-medium bg-[var(--color-jade)]/20 text-[var(--color-jade)]">
            EXIT
          </span>
        )}
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-[var(--color-stone)]/10">
        {/* Tasks Completed */}
        <div className="bg-[var(--color-void)] px-3 py-2">
          <div className="flex items-center gap-1.5 text-[var(--color-stone)]/60 text-body mb-1">
            <ListChecks className="w-3 h-3" />
            <span>Tasks</span>
          </div>
          <div className="text-[var(--color-paper)] font-medium">{data.tasksCompleted ?? 0}</div>
        </div>

        {/* Files Modified */}
        <div className="bg-[var(--color-void)] px-3 py-2">
          <div className="flex items-center gap-1.5 text-[var(--color-stone)]/60 text-body mb-1">
            <FileCode className="w-3 h-3" />
            <span>Files</span>
          </div>
          <div className="text-[var(--color-paper)] font-medium">{data.filesModified ?? 0}</div>
        </div>

        {/* Tests Status */}
        <div className="bg-[var(--color-void)] px-3 py-2">
          <div className="flex items-center gap-1.5 text-[var(--color-stone)]/60 text-body mb-1">
            <TestTube className="w-3 h-3" />
            <span>Tests</span>
          </div>
          <div
            className={cn(
              'font-medium',
              testsStatusConfig?.color || 'text-[var(--color-stone)]/60'
            )}
          >
            {testsStatusConfig?.label || '-'}
          </div>
        </div>

        {/* Work Type */}
        <div className="bg-[var(--color-void)] px-3 py-2">
          <div className="flex items-center gap-1.5 text-[var(--color-stone)]/60 text-body mb-1">
            <WorkIcon className="w-3 h-3" />
            <span>Type</span>
          </div>
          <div className="text-[var(--color-paper)] font-medium">{workConfig?.label || '-'}</div>
        </div>
      </div>

      {/* Recommendation */}
      {data.recommendation && (
        <div className="px-3 py-2 border-t border-[var(--color-stone)]/15 bg-[var(--color-paper)]/[0.02]">
          <div className="flex items-start gap-2">
            <Lightbulb className="w-3.5 h-3.5 text-[var(--color-harvest)] shrink-0 mt-0.5" />
            <span className="text-body text-[var(--color-paper)]/80">{data.recommendation}</span>
          </div>
        </div>
      )}
    </div>
  )
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
  showTimestamp = true,
}: {
  msg: AgentMessage
  isExpanded: boolean
  onToggle: () => void
  showTimestamp?: boolean
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
        <span className="text-body font-medium font-mono text-[var(--color-jade)]/90">
          TodoWrite
        </span>
        <span className="text-body text-[var(--color-paper)]/60 truncate flex-1 min-w-0">
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
        <span
          className={cn(
            'text-body text-[var(--color-stone)]/40 font-mono shrink-0',
            !showTimestamp && 'hidden sm:inline'
          )}
        >
          {time}
        </span>
      </div>

      {isExpanded && todos.length > 0 && (
        <div className="border-l-2 border-l-[var(--color-jade)]/70 ml-0 pl-4 py-2 bg-[var(--color-paper)]/[0.02]">
          <div className="space-y-1">
            {todos.map((todo) => (
              <div key={todo.content} className="flex items-start gap-2 text-body">
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
  showTimestamp = true,
}: {
  msg: AgentMessage
  isExpanded: boolean
  onToggle: () => void
  showTimestamp?: boolean
}) {
  const toolName = msg.metadata?.tool || 'Unknown'

  if (toolName === 'TodoWrite') {
    return (
      <TodoWriteMessage
        msg={msg}
        isExpanded={isExpanded}
        onToggle={onToggle}
        showTimestamp={showTimestamp}
      />
    )
  }

  const primaryParam = getToolPrimaryParam(msg.metadata?.input)
  const fullParams = formatToolInputFull(msg.metadata?.input)
  const hasMultipleParams = fullParams.length > 1
  const reasoning = msg.metadata?.reasoning?.trim() || null
  const hasReasoning = Boolean(reasoning)
  // Expand affordance shows if params have detail OR if reasoning is available
  // (so users can open the card to see "why did the agent do X").
  const hasExpandable = hasMultipleParams || hasReasoning
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
        {hasExpandable ? (
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
        <span className="text-body font-medium text-[var(--color-stone)]/80 font-mono">
          {toolName}
        </span>
        {hasReasoning && (
          <Lightbulb
            className="w-2.5 h-2.5 text-[var(--color-harvest)]/60 shrink-0"
            aria-label="Agent reasoning available"
          />
        )}
        {primaryParam && (
          <span className="text-body text-[var(--color-paper)]/60 font-mono truncate flex-1 min-w-0">
            <span className="text-[var(--color-stone)]/50">{primaryParam.key}=</span>
            <span className="text-[var(--color-paper)]/70">"{primaryParam.value}"</span>
          </span>
        )}
        <span
          className={cn(
            'text-body text-[var(--color-stone)]/40 font-mono shrink-0',
            !showTimestamp && 'hidden sm:inline'
          )}
        >
          {time}
        </span>
      </div>

      {isExpanded && (hasReasoning || fullParams.length > 0) && (
        <div className="border-l-2 border-l-[var(--color-stone)]/40 ml-0 pl-6 py-1.5 bg-[var(--color-paper)]/[0.02]">
          {hasReasoning && (
            <div className="mb-3 pb-3 border-b border-[rgba(163,163,163,0.08)]">
              <div className="flex items-center gap-1.5 mb-1.5">
                <Lightbulb className="w-3 h-3 text-[var(--color-harvest)]/70" />
                <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60">
                  Reasoning
                </span>
              </div>
              <p className="text-body text-[var(--color-paper)]/70 whitespace-pre-wrap">
                {reasoning}
              </p>
            </div>
          )}
          {fullParams.length > 0 && (
            <div className="space-y-1">
              {fullParams.map((param) => (
                <div key={param.key} className="flex gap-2 text-body font-mono">
                  <span className="text-[var(--color-stone)]/50 shrink-0 min-w-[70px]">
                    {param.key}
                  </span>
                  <span className="text-[var(--color-paper)]/70 whitespace-pre-wrap break-all">
                    {param.value}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// Shared markdown components configuration
const markdownComponents = {
  p: ({ children }: { children?: React.ReactNode }) => <p className="mb-2 last:mb-0">{children}</p>,
  code: ({ children }: { children?: React.ReactNode }) => (
    <code className="text-[var(--color-paper)]/70 bg-[var(--color-ink)] px-1 py-0.5 rounded text-body font-mono">
      {children}
    </code>
  ),
  ul: ({ children }: { children?: React.ReactNode }) => (
    <ul className="list-disc pl-5 mb-2 space-y-1">{children}</ul>
  ),
  ol: ({ children }: { children?: React.ReactNode }) => (
    <ol className="list-decimal pl-5 mb-2 space-y-1">{children}</ol>
  ),
  li: ({ children }: { children?: React.ReactNode }) => (
    <li className="[&>p]:mb-0 [&>p:first-child]:mt-0">{children}</li>
  ),
  strong: ({ children }: { children?: React.ReactNode }) => (
    <strong className="font-medium">{children}</strong>
  ),
  em: ({ children }: { children?: React.ReactNode }) => <em>{children}</em>,
  blockquote: ({ children }: { children?: React.ReactNode }) => (
    <blockquote className="border-l-2 border-[var(--color-stone)]/30 pl-3 my-2 text-[var(--color-stone)]/80">
      {children}
    </blockquote>
  ),
  table: ({ children }: { children?: React.ReactNode }) => (
    <div className="overflow-x-auto mb-2">
      <table className="min-w-full border-collapse text-body">{children}</table>
    </div>
  ),
  thead: ({ children }: { children?: React.ReactNode }) => (
    <thead className="border-b border-[var(--color-stone)]/30">{children}</thead>
  ),
  tbody: ({ children }: { children?: React.ReactNode }) => <tbody>{children}</tbody>,
  tr: ({ children }: { children?: React.ReactNode }) => (
    <tr className="border-b border-[var(--color-stone)]/10">{children}</tr>
  ),
  th: ({ children }: { children?: React.ReactNode }) => (
    <th className="px-2 py-1 text-left font-medium text-[var(--color-paper)]/80">{children}</th>
  ),
  td: ({ children }: { children?: React.ReactNode }) => (
    <td className="px-2 py-1 text-[var(--color-paper)]/70">{children}</td>
  ),
  h1: ({ children }: { children?: React.ReactNode }) => (
    <h1 className="font-semibold text-[var(--color-paper)] mt-3 mb-2 first:mt-0">{children}</h1>
  ),
  h2: ({ children }: { children?: React.ReactNode }) => (
    <h2 className="font-semibold text-[var(--color-paper)] mt-3 mb-2 first:mt-0">{children}</h2>
  ),
  h3: ({ children }: { children?: React.ReactNode }) => (
    <h3 className="font-medium text-[var(--color-paper)] mt-2 mb-1 first:mt-0">{children}</h3>
  ),
  h4: ({ children }: { children?: React.ReactNode }) => (
    <h4 className="font-medium text-[var(--color-paper)]/90 mt-2 mb-1 first:mt-0">{children}</h4>
  ),
  h5: ({ children }: { children?: React.ReactNode }) => (
    <h5 className="font-medium text-[var(--color-paper)]/80 mt-1 mb-1 first:mt-0">{children}</h5>
  ),
  h6: ({ children }: { children?: React.ReactNode }) => (
    <h6 className="font-medium text-[var(--color-paper)]/70 mt-1 mb-1 first:mt-0">{children}</h6>
  ),
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-[var(--color-sky)] hover:text-[var(--color-sky)]/80 underline underline-offset-2"
    >
      {children}
    </a>
  ),
  pre: ({ children }: { children?: React.ReactNode }) => (
    <pre className="bg-[var(--color-void)] border border-[var(--color-stone)]/15 rounded-sm p-3 my-2 overflow-x-auto text-body font-mono">
      {children}
    </pre>
  ),
  hr: () => <hr className="border-0 border-t border-[var(--color-stone)]/20 my-3" />,
  del: ({ children }: { children?: React.ReactNode }) => (
    <del className="text-[var(--color-stone)]/60 line-through">{children}</del>
  ),
}

function TextMessage({
  msg,
  showTimestamp = true,
}: {
  msg: AgentMessage
  showTimestamp?: boolean
}) {
  const time = formatMessageTime(msg.timestamp)

  // Parse RALPH_STATUS block from content
  const {
    before,
    status: ralphStatus,
    after,
  } = useMemo(() => parseRalphStatus(msg.content), [msg.content])

  // Calculate length excluding RALPH_STATUS block for expand logic
  const textLength = before.length + after.length
  const isLong = textLength > 200
  const [isExpanded, setIsExpanded] = useState(!isLong)
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(msg.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="flex items-start gap-2 py-1.5 px-3">
      <MessageSquare className="w-2.5 h-2.5 text-[var(--color-paper)]/40 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div
          className={cn(
            'text-body text-[var(--color-paper)]/90 leading-relaxed',
            !isExpanded && !ralphStatus && 'line-clamp-2'
          )}
        >
          {/* Render text before RALPH_STATUS */}
          {before && (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {before}
            </ReactMarkdown>
          )}

          {/* Render RALPH_STATUS block with special formatting */}
          {ralphStatus && <RalphStatusBlock data={ralphStatus} />}

          {/* Render text after RALPH_STATUS */}
          {after && (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {after}
            </ReactMarkdown>
          )}
        </div>
        {isLong && (
          <div className="flex items-center gap-3 mt-1">
            <button
              className="text-body text-[var(--color-stone)]/60 hover:text-[var(--color-paper)]"
              onClick={() => setIsExpanded(!isExpanded)}
            >
              {isExpanded ? 'Show less' : 'Show more'}
            </button>
            <button
              className="flex items-center gap-1 text-body text-[var(--color-stone)]/60 hover:text-[var(--color-paper)]"
              onClick={handleCopy}
              title="Copy markdown to clipboard"
            >
              {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
              <span>{copied ? 'Copied' : 'Copy'}</span>
            </button>
          </div>
        )}
      </div>
      <span
        className={cn(
          'text-body text-[var(--color-stone)]/40 font-mono shrink-0',
          !showTimestamp && 'hidden sm:inline'
        )}
      >
        {time}
      </span>
    </div>
  )
}

function UserMessage({
  msg,
  showTimestamp = true,
}: {
  msg: AgentMessage
  showTimestamp?: boolean
}) {
  const time = formatMessageTime(msg.timestamp)
  const config = MESSAGE_CONFIG.user
  const Icon = config.icon

  // User prompts can be very long (Ralph Loop prompts with context)
  // Use a higher threshold and show only 3 lines when collapsed
  const isLong = msg.content.length > 300
  const [isExpanded, setIsExpanded] = useState(false)
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(msg.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className={cn('flex items-start gap-2 py-1.5 px-3', config.bg, config.border)}>
      <Icon className={cn('w-2.5 h-2.5 shrink-0 mt-0.5', config.color)} />
      <div className="flex-1 min-w-0">
        <div
          className={cn(
            'text-body leading-relaxed whitespace-pre-wrap break-words',
            config.color,
            !isExpanded && isLong && 'line-clamp-3'
          )}
        >
          {msg.content}
        </div>
        <div className="flex items-center gap-3 mt-1">
          {isLong && (
            <button
              className="text-body text-[var(--color-stone)]/60 hover:text-[var(--color-paper)]"
              onClick={() => setIsExpanded(!isExpanded)}
            >
              {isExpanded ? 'Show less' : 'Show more'}
            </button>
          )}
          <button
            className="flex items-center gap-1 text-body text-[var(--color-stone)]/60 hover:text-[var(--color-paper)]"
            onClick={handleCopy}
            title="Copy to clipboard"
          >
            {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>
        </div>
      </div>
      <span
        className={cn(
          'text-body text-[var(--color-stone)]/40 font-mono shrink-0',
          !showTimestamp && 'hidden sm:inline'
        )}
      >
        {time}
      </span>
    </div>
  )
}

function SystemMessage({
  msg,
  showTimestamp = true,
}: {
  msg: AgentMessage
  showTimestamp?: boolean
}) {
  const time = formatMessageTime(msg.timestamp)
  const config = MESSAGE_CONFIG[msg.type] || MESSAGE_CONFIG.system
  const Icon = config.icon
  const isResult = msg.type === 'result'
  const isThinking = msg.type === 'thinking'
  const hasExpandableContent = isResult || isThinking
  const isLong = hasExpandableContent && msg.content.length > 200
  const [isExpanded, setIsExpanded] = useState(isResult)
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(msg.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div
      className={cn(
        'flex gap-2 py-1.5 px-3',
        hasExpandableContent ? 'items-start' : 'items-center',
        config.bg,
        config.border
      )}
    >
      <Icon className={cn('w-2.5 h-2.5 shrink-0 mt-0.5', config.color)} />
      {hasExpandableContent ? (
        <div className={cn('text-body flex-1 min-w-0 leading-relaxed', config.color)}>
          <div
            className={cn(
              !isResult && 'whitespace-pre-wrap',
              'break-words',
              !isExpanded && 'line-clamp-3'
            )}
          >
            {isResult ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                {msg.content}
              </ReactMarkdown>
            ) : (
              msg.content
            )}
          </div>
          {isLong && (
            <div className="flex items-center gap-3 mt-1">
              <button
                className="text-body text-[var(--color-stone)]/60 hover:text-[var(--color-paper)]"
                onClick={() => setIsExpanded(!isExpanded)}
              >
                {isExpanded ? 'Show less' : 'Show more'}
              </button>
              <button
                className="flex items-center gap-1 text-body text-[var(--color-stone)]/60 hover:text-[var(--color-paper)]"
                onClick={handleCopy}
                title="Copy to clipboard"
              >
                {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                <span>{copied ? 'Copied' : 'Copy'}</span>
              </button>
            </div>
          )}
        </div>
      ) : (
        <span className={cn('text-body flex-1', config.color)}>{msg.content}</span>
      )}
      <span
        className={cn(
          'text-body text-[var(--color-stone)]/40 font-mono shrink-0',
          !showTimestamp && 'hidden sm:inline'
        )}
      >
        {time}
      </span>
    </div>
  )
}

interface AggregatedTask {
  id: string
  name: string
  status: 'running' | 'completed' | 'failed'
  startedAt: string
  completedAt?: string
  durationMs?: number
  totalTokens?: number
  toolUses?: number
}

function TaskChecklist({
  tasks,
  isExpanded,
  onToggle,
}: {
  tasks: AggregatedTask[]
  isExpanded: boolean
  onToggle: () => void
}) {
  const completed = tasks.filter((t) => t.status === 'completed').length
  const failed = tasks.filter((t) => t.status === 'failed').length
  const running = tasks.filter((t) => t.status === 'running').length
  const total = tasks.length

  const summary = [
    `${completed}/${total} done`,
    running > 0 && `${running} running`,
    failed > 0 && `${failed} failed`,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="py-1 px-3">
      <button className="flex items-center gap-2 w-full text-left group" onClick={onToggle}>
        <ListChecks className="w-3 h-3 text-[var(--color-sky)]/70 shrink-0" />
        {isExpanded ? (
          <ChevronDown className="w-3 h-3 text-[var(--color-stone)]/40" />
        ) : (
          <ChevronRight className="w-3 h-3 text-[var(--color-stone)]/40" />
        )}
        <span className="text-body text-[var(--color-paper)]/70">Tasks</span>
        <span className="text-body text-[var(--color-stone)]/50">{summary}</span>
      </button>
      {isExpanded && (
        <div className="ml-5 mt-1 space-y-0.5">
          {tasks.map((task) => (
            <div key={task.id} className="flex items-center gap-2 py-0.5">
              {task.status === 'running' && (
                <Loader2 className="w-3 h-3 text-[var(--color-sky)] animate-spin shrink-0" />
              )}
              {task.status === 'completed' && (
                <CheckCircle2 className="w-3 h-3 text-[var(--color-jade)] shrink-0" />
              )}
              {task.status === 'failed' && (
                <AlertCircle className="w-3 h-3 text-[var(--color-vermillion)] shrink-0" />
              )}
              <span
                className={cn(
                  'text-body flex-1 min-w-0 truncate',
                  task.status === 'running'
                    ? 'text-[var(--color-paper)]/70'
                    : 'text-[var(--color-stone)]/60'
                )}
              >
                {task.name}
              </span>
              {task.durationMs != null && (
                <span className="text-body text-[var(--color-stone)]/40 tabular-nums shrink-0">
                  {formatDurationMs(task.durationMs)}
                </span>
              )}
              {task.totalTokens != null && (
                <span className="text-body text-[var(--color-stone)]/30 tabular-nums shrink-0">
                  {formatTokenCount(task.totalTokens)}t
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function formatDurationMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const secs = ms / 1000
  if (secs < 60) return `${secs.toFixed(1)}s`
  const mins = Math.floor(secs / 60)
  const remainSecs = Math.round(secs % 60)
  return `${mins}m${remainSecs}s`
}

function ScreenshotMessage({
  msg,
  showTimestamp = true,
}: {
  msg: AgentMessage
  showTimestamp?: boolean
}) {
  const imageId = msg.metadata?.image_id
  const time = formatMessageTime(msg.timestamp)
  const config = MESSAGE_CONFIG.screenshot

  return (
    <div className={cn('flex items-start gap-3 py-2 px-3', config.bg, config.border)}>
      <Camera className={cn('w-3 h-3 shrink-0 mt-0.5', config.color)} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-body text-[var(--color-harvest)] uppercase tracking-wider">
            Screenshot
          </span>
          <span className="text-body text-[var(--color-paper)]/60">{msg.content}</span>
        </div>
        {imageId && (
          <ImageLightbox src={getImageFileUrl(imageId)} alt={msg.content}>
            <div className="block w-48 aspect-[4/3] rounded-sm overflow-hidden border border-[rgba(163,163,163,0.15)] hover:border-[var(--color-harvest)]/50 transition-colors cursor-pointer">
              <img
                src={getImageFileUrl(imageId)}
                alt={msg.content}
                className="w-full h-full object-cover"
              />
            </div>
          </ImageLightbox>
        )}
      </div>
      {showTimestamp && (
        <span className="text-body text-[var(--color-stone)]/40 font-mono shrink-0">{time}</span>
      )}
    </div>
  )
}

function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

interface ContextUsageData {
  input_tokens: number
  output_tokens: number
  cache_read: number
  cache_create: number
  model: string | null
}

function SessionTokensFooter({ data }: { data: ContextUsageData }) {
  const { input_tokens, output_tokens, cache_read, cache_create, model } = data

  const totalTokens = input_tokens + output_tokens
  const freshInput = Math.max(0, input_tokens - cache_read - cache_create)
  const cacheHitRate = input_tokens > 0 ? (cache_read / input_tokens) * 100 : 0
  const shortModel = model?.replace(/^(global\.)?anthropic\./, '').replace(/-v\d+.*$/, '') ?? null

  return (
    <div className="shrink-0 border-t border-[rgba(163,163,163,0.08)] bg-[var(--color-void)]/80 px-3 py-1.5">
      <div className="flex items-center gap-3 flex-wrap text-body">
        <span className="text-[var(--color-stone)]/40">Session</span>
        {shortModel && <span className="text-[var(--color-stone)]/50">{shortModel}</span>}
        <span
          className="text-[var(--color-stone)]/60 tabular-nums"
          title="Total tokens (input + output)"
        >
          {formatTokenCount(totalTokens)} total
        </span>
        <span
          className="text-[var(--color-sky)]/70 tabular-nums"
          title="Fresh input tokens (non-cached)"
        >
          ↓{formatTokenCount(freshInput)}
        </span>
        <span className="text-purple-400/70 tabular-nums" title="Output tokens">
          ↑{formatTokenCount(output_tokens)}
        </span>
        {cache_read > 0 && (
          <span
            className="text-[var(--color-jade)]/70 tabular-nums"
            title="Tokens served from cache"
          >
            ⚡{formatTokenCount(cache_read)}
          </span>
        )}
        {cacheHitRate > 0 && (
          <span
            className="text-[var(--color-jade)]/60 tabular-nums"
            title="% of input served from cache"
          >
            {Math.round(cacheHitRate)}% cached
          </span>
        )}
      </div>
    </div>
  )
}

function ProgressIndicator({
  progress,
  tokens,
}: {
  progress: RunProgress | null
  tokens: RunTokens | null
}) {
  if (!progress && !tokens) return null

  return (
    <div className="flex flex-col border-b border-[rgba(163,163,163,0.08)] bg-[var(--color-void)] text-body">
      <div className="flex items-center gap-4 px-3 py-2">
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
    <div className="flex items-center gap-1.5 text-body text-[var(--color-jade)]/70 uppercase tracking-wider">
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

  // Browser notifications for SDK notification events
  const { show: showNotification, requestPermission } = useNotifications()

  // Request notification permission on mount
  useEffect(() => {
    requestPermission()
  }, [requestPermission])

  // Fire browser notification when notification messages arrive
  const prevNotificationCountRef = useRef(0)
  useEffect(() => {
    if (streamedMessages.length <= prevNotificationCountRef.current) {
      prevNotificationCountRef.current = streamedMessages.length
      return
    }
    // Check new messages for notifications
    const newMessages = streamedMessages.slice(prevNotificationCountRef.current)
    prevNotificationCountRef.current = streamedMessages.length
    for (const msg of newMessages) {
      if (msg.type === 'notification') {
        const title = (msg.metadata?.title as string) || 'Gluon Agent'
        showNotification(title, msg.content)
      }
    }
  }, [streamedMessages, showNotification])

  const [filter, setFilter] = useState<MessageFilter>('all')
  const [expandedTools, setExpandedTools] = useState<Set<number>>(new Set())
  const [taskChecklistExpanded, setTaskChecklistExpanded] = useState(true)
  const [showScrollButton, setShowScrollButton] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // Track if we should auto-scroll (user hasn't scrolled up manually)
  const shouldAutoScrollRef = useRef(true)
  const prevMessageCountRef = useRef(0)

  // Clear streamed messages when run changes or completes
  useEffect(() => {
    if (!isActive) {
      clear()
    }
  }, [isActive, clear])

  // Reset scroll state when runId changes (new run opened)
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentionally reset refs when runId changes
  useEffect(() => {
    shouldAutoScrollRef.current = true
    prevMessageCountRef.current = 0
  }, [runId])

  // Combine initial messages with streamed messages, deduplicating by unique key
  // This prevents duplicate messages when resuming runs (HTTP fetch + WebSocket stream overlap)
  const allMessages = useMemo((): AgentMessage[] => {
    const seen = new Set<string>()
    const combined: AgentMessage[] = [
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
    const deduped = combined.filter((msg) => {
      // Filter out task_progress heartbeats (low signal, high volume)
      // Handles both new format (type="task_progress") and old format (type="system", content="task_progress")
      if (msg.type === 'task_progress') return false
      if (msg.type === 'system' && msg.content === 'task_progress') return false
      // Filter out task_updated noise (system messages with no useful content)
      if (msg.type === 'system' && msg.content === 'task_updated') return false
      // Create unique key from timestamp + type + content preview
      // This handles the race condition between HTTP fetch and WebSocket streaming
      const contentPreview = msg.content?.slice(0, 100) || ''
      const key = `${msg.timestamp}-${msg.type}-${contentPreview}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })

    // Collapse task_started/task_notification into a single placeholder message.
    // The first task event becomes a synthetic "task_checklist" marker; the rest are removed.
    let insertedChecklist = false
    const withChecklist = deduped.reduce<AgentMessage[]>((acc, msg) => {
      if (msg.type === 'task_started' || msg.type === 'task_notification') {
        if (!insertedChecklist) {
          insertedChecklist = true
          acc.push({ timestamp: msg.timestamp, type: 'system', content: '__task_checklist__' })
        }
        return acc
      }
      acc.push(msg)
      return acc
    }, [])

    // Theme C2 — attach preceding assistant reasoning to each tool_use so the
    // tool card can show "why did the agent do X?". We walk forward once and
    // track the most recent reasoning/text block; each tool_use adopts it.
    // A new user message resets the rolling reasoning (new turn starts fresh).
    // Thinking messages consumed by a tool_use are marked for removal to avoid
    // duplicate display (reasoning shows inside the tool card instead).
    let currentReasoning: string | null = null
    let currentReasoningIdx: number | null = null
    const consumedIndices = new Set<number>()
    const withReasoning = withChecklist.map((msg, idx) => {
      if (msg.type === 'user') {
        currentReasoning = null
        currentReasoningIdx = null
        return msg
      }
      if (msg.type === 'thinking') {
        const text = (msg.content || '').trim()
        if (text) {
          currentReasoning = text
          currentReasoningIdx = idx
        }
        return msg
      }
      if (msg.type === 'text') {
        const text = (msg.content || '').trim()
        if (text) {
          currentReasoning = text
          currentReasoningIdx = null
        }
        return msg
      }
      if (msg.type === 'tool_use' && currentReasoning && !msg.metadata?.reasoning) {
        if (currentReasoningIdx != null) consumedIndices.add(currentReasoningIdx)
        const reasoning = currentReasoning
        currentReasoning = null
        currentReasoningIdx = null
        return {
          ...msg,
          metadata: { ...(msg.metadata ?? {}), reasoning },
        }
      }
      return msg
    })
    // Remove consumed thinking messages and empty thinking/system lines
    return withReasoning.filter((msg, idx) => {
      if (consumedIndices.has(idx)) return false
      if (msg.type === 'thinking' && !(msg.content || '').trim()) return false
      return true
    })
  }, [initialMessages, streamedMessages])

  // Aggregate task events into a compact checklist model
  const aggregatedTasks = useMemo((): AggregatedTask[] => {
    const taskMap = new Map<string, AggregatedTask>()
    const allRaw = [...initialMessages, ...streamedMessages]
    for (const msg of allRaw) {
      if (msg.type === 'task_started') {
        const id = (msg.metadata as Record<string, unknown>)?.task_id as string
        if (!id) continue
        const name = (msg.content || '').replace(/^Task started:\s*/i, '')
        taskMap.set(id, { id, name, status: 'running', startedAt: msg.timestamp || '' })
      }
      if (msg.type === 'task_notification') {
        const meta = msg.metadata as Record<string, unknown> | undefined
        const id = meta?.task_id as string
        if (!id) continue
        const existing = taskMap.get(id)
        const status = meta?.status === 'failed' ? ('failed' as const) : ('completed' as const)
        const usage = meta?.usage as Record<string, number> | null | undefined
        if (existing) {
          existing.status = status
          existing.completedAt = msg.timestamp || ''
          existing.durationMs = usage?.duration_ms
          existing.totalTokens = usage?.total_tokens
          existing.toolUses = usage?.tool_uses
        } else {
          const name = (msg.content || '').replace(/^Task (completed|failed):\s*/i, '')
          taskMap.set(id, {
            id,
            name,
            status,
            startedAt: msg.timestamp || '',
            completedAt: msg.timestamp || '',
            durationMs: usage?.duration_ms,
            totalTokens: usage?.total_tokens,
            toolUses: usage?.tool_uses,
          })
        }
      }
    }
    return Array.from(taskMap.values())
  }, [initialMessages, streamedMessages])

  // Handle scroll position tracking
  const handleScroll = useCallback(() => {
    const container = containerRef.current
    if (!container) return
    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100
    setShowScrollButton(!isNearBottom)
  }, [])

  const toggleToolExpanded = useCallback((idx: number) => {
    setExpandedTools((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) {
        next.delete(idx)
      } else {
        next.add(idx)
      }
      return next
    })
  }, [])

  // Extract session token totals from streaming tokens (active) or final usage message (completed)
  const sessionTokens = useMemo((): ContextUsageData | null => {
    // During active runs, use live WebSocket cumulative totals
    if (tokens && (tokens.input_tokens > 0 || tokens.output_tokens > 0)) {
      return {
        input_tokens: tokens.input_tokens,
        output_tokens: tokens.output_tokens,
        cache_read: tokens.cache_read,
        cache_create: tokens.cache_create,
        model: tokens.model,
      }
    }
    // For completed runs, find the final usage message in initial messages
    for (let i = initialMessages.length - 1; i >= 0; i--) {
      const msg = initialMessages[i]
      if (msg.type === 'usage' && msg.metadata?.final) {
        return {
          input_tokens: msg.metadata.input_tokens || 0,
          output_tokens: msg.metadata.output_tokens || 0,
          cache_read: msg.metadata.cache_read || 0,
          cache_create: msg.metadata.cache_create || 0,
          model: msg.metadata.model || null,
        }
      }
    }
    return null
  }, [tokens, initialMessages])

  // Count message types for filter badges
  const counts = {
    tool_use: allMessages.filter((m) => m.type === 'tool_use').length,
    text: allMessages.filter((m) => m.type === 'text').length,
    error: allMessages.filter((m) => m.type === 'error').length,
  }

  const filteredMessages =
    filter === 'all' ? allMessages : allMessages.filter((m) => m.type === filter)

  // Virtual scrolling for performance with large message lists
  const virtualizer = useVirtualizer({
    count: filteredMessages.length,
    getScrollElement: () => containerRef.current,
    estimateSize: () => 36, // Estimated row height in pixels
    overscan: 10, // Render extra items above/below viewport
  })

  // Render individual message based on type
  const renderMessage = useCallback(
    (msg: AgentMessage, idx: number) => {
      const isFirstOrLast = idx === 0 || idx === filteredMessages.length - 1
      const msgKey = `${msg.timestamp}-${msg.type}-${idx}`
      if (msg.type === 'tool_use') {
        return (
          <ToolCallMessage
            key={msgKey}
            msg={msg}
            isExpanded={expandedTools.has(idx)}
            onToggle={() => toggleToolExpanded(idx)}
            showTimestamp={isFirstOrLast}
          />
        )
      }
      if (msg.type === 'text') {
        return <TextMessage key={msgKey} msg={msg} showTimestamp={isFirstOrLast} />
      }
      if (msg.type === 'user') {
        return <UserMessage key={msgKey} msg={msg} showTimestamp={isFirstOrLast} />
      }
      if (msg.type === 'screenshot') {
        return <ScreenshotMessage key={msgKey} msg={msg} showTimestamp={isFirstOrLast} />
      }
      if (msg.content === '__task_checklist__' && aggregatedTasks.length > 0) {
        return (
          <TaskChecklist
            key="task-checklist"
            tasks={aggregatedTasks}
            isExpanded={taskChecklistExpanded}
            onToggle={() => setTaskChecklistExpanded((v) => !v)}
          />
        )
      }
      return <SystemMessage key={msgKey} msg={msg} showTimestamp={isFirstOrLast} />
    },
    [
      expandedTools,
      filteredMessages.length,
      toggleToolExpanded,
      aggregatedTasks,
      taskChecklistExpanded,
    ]
  )

  // Auto-scroll to bottom when new messages arrive (using virtualizer)
  useEffect(() => {
    if (!containerRef.current) return

    const isInitialLoad = prevMessageCountRef.current === 0 && filteredMessages.length > 0
    const hasNewMessages = filteredMessages.length > prevMessageCountRef.current

    if (isInitialLoad) {
      // Always scroll to bottom on initial load
      virtualizer.scrollToIndex(filteredMessages.length - 1, { align: 'end' })
      shouldAutoScrollRef.current = true
    } else if (hasNewMessages && shouldAutoScrollRef.current) {
      // Check if still near bottom before auto-scrolling
      const container = containerRef.current
      const isNearBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight < 400
      if (isNearBottom) {
        virtualizer.scrollToIndex(filteredMessages.length - 1, { align: 'end' })
      } else {
        shouldAutoScrollRef.current = false
      }
    }

    prevMessageCountRef.current = filteredMessages.length
  }, [filteredMessages.length, virtualizer])

  // Reset auto-scroll when user clicks scroll-to-bottom button (updated for virtualizer)
  const scrollToBottomAndResetVirtual = useCallback(() => {
    virtualizer.scrollToIndex(filteredMessages.length - 1, { align: 'end', behavior: 'smooth' })
    shouldAutoScrollRef.current = true
  }, [virtualizer, filteredMessages.length])

  return (
    <div className="flex flex-col h-full">
      {/* Progress bar for active runs */}
      {isActive && <ProgressIndicator progress={progress} tokens={tokens} />}

      {/* Filter bar */}
      <div className="flex items-center gap-1 px-3 py-2 border-b border-[rgba(163,163,163,0.08)] shrink-0">
        <Filter className="w-3 h-3 text-[var(--color-stone)]/40 mr-1" />
        <button
          className={cn(
            'px-2 py-1 text-body rounded-sm transition-colors',
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
            'px-2 py-1 text-body rounded-sm transition-colors flex items-center gap-1',
            filter === 'tool_use'
              ? 'bg-[rgba(102,178,255,0.15)] text-[var(--color-sky)]'
              : 'text-[var(--color-stone)]/60 hover:text-[var(--color-sky)]'
          )}
          onClick={() => setFilter('tool_use')}
        >
          <Wrench className="w-3 h-3" />
          Tools
          <span className="text-body opacity-60">{counts.tool_use}</span>
        </button>
        <button
          className={cn(
            'px-2 py-1 text-body rounded-sm transition-colors flex items-center gap-1',
            filter === 'text'
              ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
              : 'text-[var(--color-stone)]/60 hover:text-[var(--color-paper)]'
          )}
          onClick={() => setFilter('text')}
        >
          <MessageSquare className="w-3 h-3" />
          Text
          <span className="text-body opacity-60">{counts.text}</span>
        </button>
        {counts.error > 0 && (
          <button
            className={cn(
              'px-2 py-1 text-body rounded-sm transition-colors flex items-center gap-1',
              filter === 'error'
                ? 'bg-[rgba(199,62,58,0.15)] text-[var(--color-vermillion)]'
                : 'text-[var(--color-stone)]/60 hover:text-[var(--color-vermillion)]'
            )}
            onClick={() => setFilter('error')}
          >
            <AlertCircle className="w-3 h-3" />
            Errors
            <span className="text-body opacity-60">{counts.error}</span>
          </button>
        )}

        {/* Streaming indicator */}
        <div className="ml-auto">
          {isActive && <StreamingIndicator connected={connected} subscribed={subscribed} />}
        </div>
      </div>

      {/* Messages list with virtual scrolling */}
      <div className="relative flex-1">
        <div
          ref={containerRef}
          className="absolute inset-0 overflow-y-auto px-2 pb-4"
          onScroll={handleScroll}
        >
          {filteredMessages.length === 0 ? (
            <div className="flex items-center justify-center h-32 text-[var(--color-stone)]/50 text-body">
              {isActive ? 'Waiting for messages...' : 'No messages'}
            </div>
          ) : (
            <div
              style={{
                height: `${virtualizer.getTotalSize()}px`,
                width: '100%',
                position: 'relative',
              }}
            >
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const msg = filteredMessages[virtualRow.index]
                return (
                  <div
                    key={virtualRow.key}
                    data-index={virtualRow.index}
                    ref={virtualizer.measureElement}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                  >
                    {renderMessage(msg, virtualRow.index)}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Scroll to bottom button */}
        {showScrollButton && (
          <button
            onClick={scrollToBottomAndResetVirtual}
            className="absolute bottom-3 right-3 p-1.5 rounded-full bg-[var(--color-ink)] border border-[var(--color-stone)]/20 text-[var(--color-stone)]/60 hover:text-[var(--color-paper)] hover:border-[var(--color-stone)]/40 transition-all shadow-lg"
            title="Scroll to bottom"
          >
            <ChevronDown className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Context usage footer — visible for active and completed runs */}
      {sessionTokens && <SessionTokensFooter data={sessionTokens} />}
    </div>
  )
}
