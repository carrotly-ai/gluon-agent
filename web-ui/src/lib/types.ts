/** Run status enum matching backend RunStatus */
export type RunStatus = 'pending' | 'running' | 'review' | 'completed' | 'failed' | 'cancelled'

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
  pr_url?: string | null
  pr_status?: 'open' | 'merged' | 'closed' | 'draft' | null
  pr_mergeable?: 'MERGEABLE' | 'CONFLICTING' | 'UNKNOWN' | null
  // Archive tracking
  archived?: boolean
  // Recovery progress UI
  is_recovering?: boolean
  recovery_item_count?: number
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
  pr_mergeable: 'MERGEABLE' | 'CONFLICTING' | 'UNKNOWN' | null
  has_remote: boolean // Whether the project has a git remote configured
  // Resume tracking
  resume_count: number
  last_resumed_at: string | null
  // Precomputed counts for tab badges
  commit_count: number | null
  file_count: number | null
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
  workspace_id?: string | null
  git_branch?: string | null
  git_ahead?: number | null
  git_behind?: number | null
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
    sortedGroups.set(
      key,
      groups.get(key)!.sort((a, b) => a.name.localeCompare(b.name))
    )
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

/** Response from resume operation (in-place resume) */
export interface ResumeRunResponse {
  run_id: string // Same run continues
  status: string
  resume_count: number
  // Backward compatibility (deprecated, same as run_id)
  original_run_id?: string
  new_run_id?: string
}

/** Request body for recovering a run from context overflow */
export interface RecoverRunRequest {
  fresh?: boolean
}

/** Response from recover operation */
export interface RecoverRunResponse {
  run_id: string
  status: string
  recovery_count: number
  is_fresh: boolean
  completed_work: string[]
}

/** Response from session history */
export interface SessionHistoryResponse {
  session_id: string
  runs: Run[]
}

/** WebSocket message types */
export type WebSocketMessageType =
  | 'run_created'
  | 'run_updated'
  | 'log_line'
  | 'agent_message'
  | 'progress'
  | 'token_update'
  | 'subscribed'
  | 'unsubscribed'
  | 'pong'

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

/** Agent message from messages.jsonl - text, tool_use, error, result, etc. */
export interface AgentMessageData {
  type: 'text' | 'tool_use' | 'system' | 'error' | 'result'
  content: string
  metadata?: Record<string, unknown>
  timestamp?: string
}

export interface AgentMessageWSMessage extends WebSocketMessage {
  type: 'agent_message'
  run_id: string
  message: AgentMessageData
}

/** Progress update during task execution */
export interface ProgressMessage extends WebSocketMessage {
  type: 'progress'
  run_id: string
  turns: number
  tool_calls: number
  elapsed_seconds: number
}

/** Token/cost update during task execution */
export interface TokenUpdateMessage extends WebSocketMessage {
  type: 'token_update'
  run_id: string
  input_tokens: number
  output_tokens: number
  estimated_cost_usd: number
}

export interface SubscribedMessage extends WebSocketMessage {
  type: 'subscribed'
  run_id: string
}

export type WSMessage =
  | RunCreatedMessage
  | RunUpdatedMessage
  | LogLineMessage
  | AgentMessageWSMessage
  | ProgressMessage
  | TokenUpdateMessage
  | SubscribedMessage
  | WebSocketMessage

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
  review: new Set(['completed', 'pending', 'failed', 'cancelled']),
  completed: new Set(['pending', 'review']), // Back to review if PR still open
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
  projects_removed: string[]
}

// ========== Usage Dashboard Types (Phase 8) ==========

/** Usage summary for header display */
export interface UsageSummary {
  today_cost_usd: number
  today_runs: number
  week_cost_usd: number
  week_runs: number
  month_cost_usd: number
  month_runs: number
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

// ========== Git Commits and Files Types ==========

/** Commit response */
export interface Commit {
  sha: string
  message: string
  author: string
  author_email: string
  date: string
}

/** Response for run commits */
export interface RunCommitsResponse {
  run_id: string
  branch_name: string | null
  base_branch: string
  commit_count: number
  commits: Commit[]
}

/** Detailed commit with files - for expanded view */
export interface CommitDetail {
  sha: string
  message: string // Full message (subject + body)
  author: string
  author_email: string
  date: string
  files: FileChange[]
}

/** File change response */
export interface FileChange {
  file_path: string
  additions: number
  deletions: number
  change_type: 'added' | 'modified' | 'deleted' | 'renamed'
}

/** Response for run files */
export interface RunFilesResponse {
  run_id: string
  branch_name: string | null
  base_branch: string
  file_count: number
  total_additions: number
  total_deletions: number
  files: FileChange[]
}

/** File diff response - for expanded view */
export interface FileDiff {
  file_path: string
  diff: string
  additions: number
  deletions: number
}

// ========== Image Attachment Types (Phase 10.1) ==========

/** Image attachment metadata */
export interface ImageAttachment {
  id: string
  file_path: string
  original_name: string
  mime_type: string | null
  size_bytes: number
  hash: string
  created_at: string
}

/** Response for images attached to a run */
export interface RunImagesResponse {
  run_id: string
  image_count: number
  images: ImageAttachment[]
}

/** Helper to format file size */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Get image URL for serving */
export function getImageUrl(imageId: string): string {
  return `/api/images/${imageId}/file`
}

// ========== Advanced Git Operations Types (Phase 5) ==========

/** Conflicted file info */
export interface ConflictFile {
  file_path: string
  conflict_markers_count: number
}

/** Conflict detection response */
export interface ConflictDetectionResponse {
  has_conflicts: boolean
  is_rebase_in_progress: boolean
  is_merge_in_progress: boolean
  conflict_operation: 'rebase' | 'merge' | 'cherry_pick' | null
  rebase_current_step: number | null
  rebase_total_steps: number | null
  conflicted_files: ConflictFile[]
}

/** 3-way diff for a conflicted file */
export interface ConflictDiff {
  file_path: string
  base: string | null
  ours: string | null
  theirs: string | null
  merged: string | null
}

/** Request to resolve a conflict */
export interface ResolveConflictRequest {
  file_path: string
  resolution: 'ours' | 'theirs' | 'resolved'
}

/** Response from conflict resolution */
export interface ResolveConflictResponse {
  success: boolean
  message: string
}

/** Request to start a rebase */
export interface RebaseRequest {
  onto_branch: string
}

/** Response from rebase operations */
export interface RebaseResponse {
  success: boolean
  message: string
  conflicts: string[]
}

/** Response from force push check */
export interface ForcePushCheckResponse {
  needed: boolean
  commits_to_delete: number
  reason: string
}

/** Request for force push */
export interface ForcePushRequest {
  branch?: string
  force_with_lease?: boolean
}

/** Response from force push */
export interface ForcePushResponse {
  success: boolean
  message: string
}

/** Branch info */
export interface Branch {
  name: string
  is_current: boolean
  upstream: string | null
  ahead: number
  behind: number
}

/** Response for branch list */
export interface BranchListResponse {
  branches: Branch[]
  current_branch: string | null
}

/** Request to rename a branch */
export interface RenameBranchRequest {
  old_name: string
  new_name: string
}

/** Request to change branch base */
export interface ChangeBaseBranchRequest {
  feature_branch: string
  new_base: string
}

/** Generic branch operation response */
export interface BranchOperationResponse {
  success: boolean
  message: string
  conflicts: string[]
}
