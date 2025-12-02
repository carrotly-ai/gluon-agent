/** Run status enum matching backend RunStatus */
export type RunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

/** API response for execution runs */
export interface Run {
  id: string
  project_id: string
  project_name: string
  status: RunStatus
  prompt: string
  initiator: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  error_message: string | null
}

/** Detailed run response (includes additional fields) */
export interface RunDetail extends Run {
  session_id: string | null
  exit_code: number | null
  log_path: string | null
}

/** Request body for creating a new run */
export interface CreateRunRequest {
  project_name: string
  prompt: string
  model?: string
  use_worktree?: boolean
}

/** API response for log content */
export interface LogResponse {
  run_id: string
  stream: 'stdout' | 'stderr' | 'messages'
  content: string
  line_count: number
}

/** API response for projects */
export interface Project {
  id: string
  name: string
  path: string
  session_count: number
}

/** API response for system status */
export interface SystemStatus {
  total_projects: number
  active_runs: number
  total_runs: number
}

/** WebSocket message types */
export type WebSocketMessageType = 'run_created' | 'run_updated' | 'log_line' | 'subscribed' | 'unsubscribed' | 'pong'

export interface WebSocketMessage {
  type: WebSocketMessageType
}

export interface RunCreatedMessage extends WebSocketMessage {
  type: 'run_created'
  run: Run
}

export interface RunUpdatedMessage extends WebSocketMessage {
  type: 'run_updated'
  run: Run
}

export interface LogLineMessage extends WebSocketMessage {
  type: 'log_line'
  run_id: string
  stream: string
  line: string
}

export interface SubscribedMessage extends WebSocketMessage {
  type: 'subscribed'
  run_id: string
}

export type WSMessage = RunCreatedMessage | RunUpdatedMessage | LogLineMessage | SubscribedMessage | WebSocketMessage

/** Kanban column definitions */
export const KANBAN_COLUMNS = {
  pending: { label: 'Queued', color: 'bg-yellow-500' },
  running: { label: 'Running', color: 'bg-blue-500' },
  completed: { label: 'Completed', color: 'bg-green-500' },
  failed: { label: 'Failed', color: 'bg-red-500' },
  cancelled: { label: 'Cancelled', color: 'bg-gray-500' },
} as const
