/**
 * Canonical `AgentMessage` shape + JSONL parser for the run message stream.
 *
 * Consolidated here to stop the four-way drift of the `AgentMessage` interface
 * (was redeclared in StreamingLogViewer, RunDetailDialog, RunDetailPage, and
 * ListViewPage) and the three identical `parseMessages` copies. This is the
 * superset type — StreamingLogViewer relies on the typed `metadata` fields, and
 * the list/detail views only read `type`/`content`/`timestamp`, so widening
 * them to this type is a no-op for their call sites.
 *
 * Note: this differs from `AgentMessageData` in `types.ts`, which is the
 * over-the-wire WS payload (`metadata?: Record<string, unknown>`). This type is
 * the UI-side view model with typed metadata for rendering.
 */

export interface AgentMessage {
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
    | 'server_tool_use'
    | 'server_tool_result'
    | 'todos_updated'
    | 'task_started'
    | 'task_progress'
    | 'task_notification'
    | 'task_updated'
    | 'usage'
    | 'hook_event'
    | 'rate_limit'
  content: string
  metadata?: {
    tool?: string
    tool_id?: string
    input?: unknown
    image_id?: string
    original_name?: string
    size_bytes?: number
    reasoning?: string
    /** Set on tool_result messages when the tool call failed */
    is_error?: boolean
    // Usage message fields (emitted with type="usage")
    final?: boolean
    input_tokens?: number
    output_tokens?: number
    cache_read?: number
    cache_create?: number
    context_used?: number | null
    context_window?: number | null
    model?: string
    [key: string]: unknown
  }
}

/**
 * Parse a `messages.jsonl` blob into `AgentMessage[]`, one JSON object per line.
 * Blank and malformed lines are skipped silently (the stream can be truncated
 * mid-write while a run is live).
 */
export function parseMessages(messagesContent: string): AgentMessage[] {
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
