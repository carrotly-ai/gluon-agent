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
  // Cost tracking (available in list responses for RunCard display)
  cost_usd: number | null
  // Git indicators (for RunCard display)
  use_worktree?: boolean
  branch_name?: string | null
  // PR info (for Review column routing)
  pr_number?: number | null
  pr_status?: 'open' | 'merged' | 'closed' | 'draft' | null
  // Archive tracking
  archived?: boolean
}

/** Detailed run response (includes additional fields) */
export interface RunDetail extends Run {
  session_id: string | null
  exit_code: number | null
  log_path: string | null
  // Cost tracking
  cost_usd: number | null
  input_tokens: number | null
  output_tokens: number | null
  model_used: string | null
  // Git/worktree tracking (Phase 7.1)
  branch_name: string | null
  source_branch: string | null
  use_worktree: boolean
  git_commit_sha: string | null
  pr_number: number | null
  pr_url: string | null
  pr_status: 'open' | 'merged' | 'closed' | 'draft' | null
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

/** Project with derived workspace info */
export interface ProjectWithWorkspace extends Project {
  workspace: string
}

/** Extract workspace name from project path */
export function getWorkspaceFromPath(path: string): string {
  // Pattern: /Users/.../workspaces/{workspace}/{project}
  const match = path.match(/\/workspaces\/([^/]+)\//)
  return match ? match[1] : 'other'
}

/** Group projects by workspace */
export function groupProjectsByWorkspace(projects: Project[]): Map<string, ProjectWithWorkspace[]> {
  const groups = new Map<string, ProjectWithWorkspace[]>()

  for (const project of projects) {
    const workspace = getWorkspaceFromPath(project.path)
    const projectWithWorkspace = { ...project, workspace }

    if (!groups.has(workspace)) {
      groups.set(workspace, [])
    }
    groups.get(workspace)!.push(projectWithWorkspace)
  }

  // Sort workspaces alphabetically, projects within each workspace alphabetically
  const sortedGroups = new Map<string, ProjectWithWorkspace[]>()
  const sortedKeys = Array.from(groups.keys()).sort()

  for (const key of sortedKeys) {
    sortedGroups.set(key, groups.get(key)!.sort((a, b) => a.name.localeCompare(b.name)))
  }

  return sortedGroups
}

/** API response for system status */
export interface SystemStatus {
  total_projects: number
  active_runs: number
  total_runs: number
}

/** Request body for resuming a run */
export interface ResumeRunRequest {
  prompt: string
}

/** Response from resume operation */
export interface ResumeRunResponse {
  original_run_id: string
  new_run_id: string
  status: string
}

/** Response from session history */
export interface SessionHistoryResponse {
  session_id: string
  runs: Run[]
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
  review: { label: 'Review', color: 'bg-purple-500' },
  completed: { label: 'Completed', color: 'bg-green-500' },
  failed: { label: 'Failed', color: 'bg-red-500' },
  cancelled: { label: 'Cancelled', color: 'bg-gray-500' },
} as const

/** Kanban column type (includes virtual "review" column) */
export type KanbanColumn = keyof typeof KANBAN_COLUMNS

// ========== Status Transition Types (Phase 7.2 Drag-and-Drop) ==========

/** Request to update run status via drag-and-drop */
export interface UpdateStatusRequest {
  status: RunStatus
  reason?: string
}

/** Response from status update */
export interface UpdateStatusResponse {
  run: Run
  previous_status: RunStatus
  new_status: RunStatus
}

/** Allowed status transitions for drag-and-drop */
export const ALLOWED_TRANSITIONS: Record<RunStatus, Set<RunStatus>> = {
  pending: new Set(['cancelled']),
  running: new Set(['cancelled']),
  completed: new Set(['pending']),
  failed: new Set(['pending']),
  cancelled: new Set(['pending']),
}

/** Check if a status transition is allowed */
export function isTransitionAllowed(from: RunStatus, to: RunStatus): boolean {
  return ALLOWED_TRANSITIONS[from]?.has(to) ?? false
}

// ========== Project Management Types (Phase 7.3) ==========

/** Detailed project response */
export interface ProjectDetail extends Project {
  workspace_id: string | null
  workspace_name: string | null
  run_count: number
  last_run_at: string | null
}

/** Request to create a new project */
export interface CreateProjectRequest {
  name: string
  path: string
  workspace_id?: string
}

/** Workspace response */
export interface Workspace {
  id: string
  name: string
  path: string
  project_count: number
  auto_discover: boolean
}

/** Request to create a new workspace */
export interface CreateWorkspaceRequest {
  name: string
  path: string
  auto_scan?: boolean
}

/** Response from workspace scan */
export interface ScanResultResponse {
  workspace_id: string
  projects_found: number
  projects_added: string[]
}

// ========== Usage Dashboard Types (Phase 8) ==========

/** Usage summary for header display */
export interface UsageSummary {
  today_cost_usd: number
  today_runs: number
  week_cost_usd: number
  week_runs: number
  total_cost_usd: number
  total_runs: number
}

/** Project usage breakdown */
export interface ProjectUsage {
  project_id: string
  project_name: string
  cost_usd: number
  run_count: number
  input_tokens: number
  output_tokens: number
}

/** Daily usage data */
export interface DailyUsage {
  date: string
  cost_usd: number
  run_count: number
  input_tokens: number
  output_tokens: number
}

/** Run item for usage table */
export interface RunUsageItem {
  id: string
  project_name: string
  prompt: string
  cost_usd: number | null
  input_tokens: number | null
  output_tokens: number | null
  model_used: string | null
  created_at: string
  status: string
}
