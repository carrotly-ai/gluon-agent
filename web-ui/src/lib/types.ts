/** Run status enum matching backend RunStatus */
export type RunStatus = 'pending' | 'running' | 'review' | 'completed' | 'failed' | 'cancelled'

/** Circuit breaker state enum matching backend CircuitState */
export type CircuitState = 'CLOSED' | 'HALF_OPEN' | 'OPEN'

/** Queued follow-up message */
export interface QueuedMessage {
  id: string
  message: string
  queued_at: string
}

/** API response for execution runs */
export interface Run {
  id: string
  project_id: string
  project_name: string
  status: RunStatus
  prompt: string
  original_prompt: string | null // Original task prompt (preserved across resumes)
  initiator: string | null
  user_id: string | null // FK to users(id) — who submitted the run (null pre-auth / SYSTEM_USER)
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
  ci_status?: 'pending' | 'success' | 'failure' | null
  // Archive tracking
  archived?: boolean
  // Recovery progress UI
  is_recovering?: boolean
  recovery_item_count?: number
  // Ralph Loop fields (autonomous execution mode)
  ralph_enabled?: boolean
  loop_count?: number
  max_loops?: number
  circuit_state?: CircuitState
  completion_confidence?: number
  completion_reason?: string | null
  calls_this_hour?: number
  max_calls_per_hour?: number
  // Queued follow-up messages (for message injection while task is running)
  queued_messages?: QueuedMessage[]
  // Witness health classification (for running runs)
  health_classification?: HealthClassification | null
  // Chain/formula step progress
  chain_id?: string | null
  chain_step_name?: string | null
  chain_step_index?: number | null
  chain_total_steps?: number | null
  // SDK stop reason (surfaced in run lists/cards)
  stop_reason?: string | null
  // List-view cockpit fields (see tmp/list-view-plan.md)
  custom_title?: string | null
  kind?: RunKind | null
  snoozed_until?: string | null
  last_activity_at?: string | null
  forked_from_run_id?: string | null
  // Schedule that spawned this run (null for ad-hoc runs)
  schedule_id?: string | null
}

// ========== Task Schedules (user-defined recurring tasks) ==========

export type ConcurrencyPolicy = 'skip' | 'cancel_replace' | 'allow_overlap'

/** A user-defined recurring task. */
export interface TaskSchedule {
  id: string
  name: string
  project_id: string
  project_name: string
  prompt: string
  profile: string
  model: string | null
  use_worktree: boolean
  timezone: string
  /** ISO weekday numbers, Mon=0..Sun=6. Null when using Advanced cron. */
  recurrence_days: number[] | null
  /** Wall-clock HH:MM in `timezone`. Null when using Advanced cron. */
  recurrence_time: string | null
  schedule_cron: string
  concurrency_policy: ConcurrencyPolicy
  is_enabled: boolean
  last_fired_at: string | null
  next_fire_at: string | null
  description: string | null
  created_by_user_id: string | null
  created_at: string
  updated_at: string
  /** Friendly one-line summary, e.g. "Weekdays at 9:00 AM (Asia/Singapore)" */
  summary: string
  run_count: number
  active_run_count: number
}

export interface TaskScheduleListResponse {
  schedules: TaskSchedule[]
  total: number
}

export interface CreateTaskScheduleRequest {
  name: string
  project_name: string
  prompt: string
  profile?: string
  model?: string | null
  use_worktree?: boolean
  timezone: string
  recurrence_days?: number[] | null
  recurrence_time?: string | null
  schedule_cron?: string | null
  concurrency_policy?: ConcurrencyPolicy
  is_enabled?: boolean
  description?: string | null
}

export interface UpdateTaskScheduleRequest {
  name?: string
  project_name?: string
  prompt?: string
  profile?: string
  model?: string | null
  use_worktree?: boolean
  timezone?: string
  recurrence_days?: number[] | null
  recurrence_time?: string | null
  schedule_cron?: string | null
  concurrency_policy?: ConcurrencyPolicy
  is_enabled?: boolean
  description?: string | null
}

export interface SchedulePreviewRequest {
  timezone: string
  recurrence_days?: number[] | null
  recurrence_time?: string | null
  schedule_cron?: string | null
}

export interface SchedulePreviewResponse {
  schedule_cron: string
  summary: string
  next_fires: string[]
}

/** Run "kind" — low-cardinality category surfaced as a leading glyph in the list. */
export type RunKind = 'research' | 'build' | 'docs' | 'bug' | 'review' | 'chore'

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
  ci_status: 'pending' | 'success' | 'failure' | null
  has_remote: boolean // Whether the project has a git remote configured
  // Resume tracking
  resume_count: number
  last_resumed_at: string | null
  // Precomputed counts for tab badges
  commit_count: number | null
  file_count: number | null
  // Ralph Loop detail fields (additional to Run fields)
  consecutive_no_progress?: number
  consecutive_same_error?: number
  test_only_loops?: number
  max_cost_usd?: number | null
  // Hard caps (Theme D3) — present on RunDetailResponse
  max_tool_calls?: number | null
  max_duration_minutes?: number | null
  tool_call_count?: number
  // SDK stop reason
  stop_reason?: string | null
}

/** Task profile options */
export type TaskProfile = 'quick' | 'standard' | 'deep' | 'planning'

/** Thinking budget options */
export type ThinkingBudget = 'none' | 'low' | 'medium' | 'high' | 'ultrathink' | 'adaptive'

/** Effort level options */
export type EffortLevel = 'low' | 'medium' | 'high' | 'max'

/** Request body for creating a new run */
export interface CreateRunRequest {
  project_name: string
  prompt: string
  // Profile-based options
  profile?: TaskProfile
  model?: string
  model_override?: string
  thinking_override?: ThinkingBudget
  effort_override?: EffortLevel
  max_budget_override?: number
  force_planning?: boolean
  task_budget_override?: number
  // Existing options
  use_worktree?: boolean
  // Ralph Loop options (autonomous execution mode)
  ralph_enabled?: boolean
  max_loops?: number
  max_cost_usd?: number
  // Per-task overrides
  agent_teams?: boolean
  model_transition?: string
  // Blueprint orchestration (on by default)
  enable_prehydration?: boolean
  blueprint_enabled?: boolean
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
  // Basic git status fields
  git_branch?: string | null
  git_ahead?: number | null
  git_behind?: number | null
  // Extended git status fields for sync button
  git_uncommitted_count?: number | null
  git_has_remote?: boolean
  git_has_conflicts?: boolean
  git_has_operation_in_progress?: boolean
  // Computed sync state
  can_sync?: boolean
  sync_action?: 'pull' | 'push' | 'commit+push' | 'diverged' | null
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

/** Response from resume operation (in-place resume) */
export interface ResumeRunResponse {
  run_id: string // Same run continues
  status: string
  resume_count: number
  // Backward compatibility (deprecated, same as run_id)
  original_run_id?: string
  new_run_id?: string
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

// ========== List-view Cockpit (tmp/list-view-plan.md) ==========

/** PATCH /api/runs/{id} — partial update of user-editable fields. */
export interface UpdateRunRequest {
  custom_title?: string | null
  kind?: RunKind | null
}

/** POST /api/runs/{id}/fork — fork an existing run's Claude session. */
export interface ForkRunRequest {
  prompt: string
  custom_title?: string | null
}

/** GET /api/attention-counts — aggregate badge counts. */
export interface AttentionCountsResponse {
  total: number
  needs_input: number
  failed: number
  conflicts: number
  by_project: Record<string, number>
}

// ========== Ralph Loop Types ==========

/** Ralph loop iteration history */
export interface RalphIteration {
  id: string
  run_id: string
  loop_number: number
  started_at: string
  ended_at: string | null
  duration_seconds: number | null
  files_changed: number
  progress_detected: boolean
  has_errors: boolean
  error_message: string | null
  has_completion_signal: boolean
  is_test_only: boolean
  confidence_score: number
  cost_usd: number
  input_tokens: number
  output_tokens: number
}

/** Response for iteration list */
export interface RalphIterationsResponse {
  run_id: string
  iteration_count: number
  iterations: RalphIteration[]
}

/** Response from stopping a ralph loop */
export interface StopLoopResponse {
  success: boolean
  run_id: string
  message: string
  final_loop_count: number
}

// ========== Activity Log Types ==========

export interface ActivityEvent {
  id: string
  timestamp: string
  actor: string
  action: string
  result: string | null
  message: string | null
  metadata: Record<string, unknown> | null
}

// ========== Work Queue Types ==========

export type WorkQueueStatus =
  | 'pending'
  | 'claimed'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface WorkQueueItem {
  id: string
  project_id: string
  prompt: string
  profile: string
  priority: number
  status: WorkQueueStatus
  claimed_by: string | null
  created_at: string
  claimed_at: string | null
  completed_at: string | null
  error_message: string | null
}

// ========== Merge Queue Types ==========

export type MergeQueueStatus =
  | 'pending'
  | 'testing'
  | 'merging'
  | 'merged'
  | 'conflict'
  | 'failed'
  | 'cancelled'

export interface MergeQueueEntry {
  id: string
  run_id: string
  project_id: string
  branch_name: string
  pr_number: number | null
  pr_url: string | null
  status: MergeQueueStatus
  priority: number
  conflict_count: number
  max_retries: number
  last_error: string | null
  created_at: string
  completed_at: string | null
}

// ========== Witness Types ==========

export type HealthClassification =
  | 'healthy'
  | 'slow'
  | 'stuck'
  | 'looping'
  | 'needs_context_reset'
  | 'zombie'

export type RecoveryAction = 'none' | 'nudge' | 'escalate' | 'restart'

export interface WitnessDecision {
  id: string
  run_id: string
  timestamp: string
  classification: HealthClassification
  confidence: number
  reasoning: string | null
  action: RecoveryAction
  action_result: string | null
}

// ========== Formula Types ==========

export interface FormulaVariable {
  name: string
  type: string
  required: boolean
  default: string | null
  help: string | null
}

export interface FormulaStep {
  id: string
  name: string
  prompt: string
  depends_on: string[]
  profile: string
}

export interface FormulaTemplate {
  name: string
  description: string | null
  variables: FormulaVariable[]
  steps: FormulaStep[]
  use_worktree: boolean
}

// ========== Notification Types ==========

export type NotificationType = 'question' | 'completion' | 'failure' | 'review' | 'warning' | 'info'
export type NotificationSeverity = 'info' | 'warning' | 'error' | 'success'

export interface GluonNotification {
  id: string
  workspace_id: string | null
  project_id: string | null
  run_id: string | null
  session_id: string | null
  type: NotificationType
  severity: NotificationSeverity
  title: string
  message: string | null
  metadata: Record<string, unknown> | null
  read: boolean
  created_at: string
  read_at: string | null
}

export interface NotificationsListResponse {
  notifications: GluonNotification[]
  unread_count: number
}

// ========== SDK Session Browser Types ==========

export interface SDKSession {
  session_id: string
  summary: string
  last_modified: number
  file_size: number
  custom_title?: string
  first_prompt?: string
  git_branch?: string
  cwd?: string
  linked_run_ids: string[]
}

export interface SessionMessage {
  type: 'user' | 'assistant'
  uuid: string
  session_id: string
  message: unknown
  parent_tool_use_id?: string
}

export interface SessionDetail {
  session: SDKSession
  messages: SessionMessage[]
  total_messages: number
}

// ========== Workspace Settings Types ==========

export interface WorkspaceSettingsData {
  workspace_id: string
  settings: Record<string, string>
  env_var_keys: string[]
  global_defaults: Record<string, string>
}

/** WebSocket message types */
export type WebSocketMessageType =
  | 'run_created'
  | 'run_updated'
  | 'log_line'
  | 'agent_message'
  | 'progress'
  | 'token_update'
  | 'step_progress'
  | 'pending_questions'
  | 'question_answered'
  | 'questions_expired'
  | 'todos_updated'
  | 'witness_decision'
  | 'notification_created'
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

/** Agent message from messages.jsonl - text, tool_use, error, result, user, etc. */
export interface AgentMessageData {
  type:
    | 'text'
    | 'tool_use'
    | 'system'
    | 'error'
    | 'result'
    | 'user'
    | 'mcp_status'
    | 'notification'
    | 'screenshot'
    | 'thinking'
    | 'tool_result'
    | 'task_started'
    | 'task_progress'
    | 'task_notification'
    | 'task_updated'
    // Additional AgentMessage types the backend emits (agent.py)
    | 'hook_event'
    | 'rate_limit'
    | 'server_tool_use'
    | 'server_tool_result'
    | 'usage'
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
  cache_read?: number
  cache_create?: number
  context_used?: number | null
  context_window?: number | null
  model?: string | null
}

export interface SubscribedMessage extends WebSocketMessage {
  type: 'subscribed'
  run_id: string
}

/** WebSocket message for a new notification */
export interface NotificationCreatedMessage extends WebSocketMessage {
  type: 'notification_created'
  notification: {
    id: string
    type: NotificationType
    severity: NotificationSeverity
    title: string
    message: string | null
    run_id: string | null
    created_at: string
    read: boolean
  }
}

export type WSMessage =
  | RunCreatedMessage
  | RunUpdatedMessage
  | LogLineMessage
  | AgentMessageWSMessage
  | ProgressMessage
  | TokenUpdateMessage
  | PendingQuestionsMessage
  | QuestionAnsweredMessage
  | NotificationCreatedMessage
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
  // Rolling budgets / spend (Theme D2) — present on WorkspaceResponse
  daily_budget_usd?: number | null
  monthly_budget_usd?: number | null
  daily_spend_usd?: number
  monthly_spend_usd?: number
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

/** Response from clone operation */
export interface CloneResultResponse {
  workspace_id: string
  repo_name: string
  clone_path: string
  project_registered: boolean
  project_name: string | null
  scan_result: ScanResultResponse
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

/** Loop-effectiveness metrics (I5): acceptance rate + cost-per-accepted-change */
export interface GateabilityBucket {
  runs: number
  pr_producing: number
  accepted: number
  acceptance_rate: number
  cost_usd: number
  cost_per_accepted_usd: number | null
}

export interface LoopEffectivenessKind extends GateabilityBucket {
  kind: string
}

export interface LoopEffectiveness {
  overall: GateabilityBucket
  /** Code-producing kinds (build/bug/chore) — objectively gateable */
  gateable: GateabilityBucket
  /** Judgment-call kinds (research/docs/review) — no objective gate */
  gateless: GateabilityBucket
  by_kind: LoopEffectivenessKind[]
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
  from_snapshot?: boolean // True if data from snapshot (branch may be deleted)
}

/** Detailed commit with files - for expanded view */
export interface CommitDetail {
  sha: string
  message: string // Full message (subject + body)
  author: string
  author_email: string
  date: string
  files: FileChange[]
  from_snapshot?: boolean // True if data from snapshot
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
  from_snapshot?: boolean // True if data from snapshot (branch may be deleted)
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
  source?: 'user' | 'screenshot'
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

/** Branch info */
export interface Branch {
  name: string
  is_current: boolean
  upstream: string | null
  ahead: number
  behind: number
}

// ========== Git Sync Types (Settings Page) ==========

/** Detailed git status for a project */
export interface GitStatusInfo {
  is_git_repo: boolean
  branch: string | null
  remote: string | null
  remote_url: string | null
  has_uncommitted: boolean
  uncommitted_count: number
  commits_ahead: number
  commits_behind: number
  is_diverged: boolean
  needs_pull: boolean
  needs_push: boolean
  has_conflicts: boolean
  has_operation_in_progress: boolean
  operation_type: 'rebase' | 'merge' | 'cherry_pick' | null
  last_fetch_at: string | null
}

/** Response from git sync operation */
export interface GitSyncResponse {
  success: boolean
  action: string
  message: string
  error: string | null
  commits_pulled: number
  commits_pushed: number
  files_committed: number
  updated_status: GitStatusInfo | null
}

// ========== AskUserQuestion Types ==========

/** Question status enum */
export type QuestionStatus = 'pending' | 'answered' | 'auto_answered' | 'expired'

/** Question option */
export interface QuestionOption {
  label: string
  description?: string
}

/** Pending question from AskUserQuestion tool */
export interface PendingQuestion {
  id: string
  run_id: string
  question_index: number
  question_text: string
  header: string
  options: QuestionOption[]
  multi_select: boolean
  status: QuestionStatus
  created_at: string
  expires_at: string | null
  selected_labels: string[] | null
  answer_source: string | null
}

/** Response for pending questions list */
export interface PendingQuestionsResponse {
  run_id: string
  questions: PendingQuestion[]
  has_pending: boolean
}

/** WebSocket message for pending questions */
export interface PendingQuestionsMessage extends WebSocketMessage {
  type: 'pending_questions'
  run_id: string
  questions: {
    id: string
    question: string
    header: string
    options: QuestionOption[]
    multi_select: boolean
  }[]
}

/** WebSocket message for question answered */
export interface QuestionAnsweredMessage extends WebSocketMessage {
  type: 'question_answered'
  run_id: string
  question_id: string
}

/** WebSocket message for questions expired (timeout) */
export interface QuestionsExpiredMessage extends WebSocketMessage {
  type: 'questions_expired'
  run_id: string
  question_ids: string[]
  reason: string
}

// ========== Todo Tracking Types ==========

/** A single todo item from a TodoWrite snapshot */
export interface TodoItem {
  content: string
  status: 'pending' | 'in_progress' | 'completed'
  active_form: string
}

/** Response for todo tracking state of a run */
export interface RunTodosResponse {
  run_id: string
  todos: TodoItem[]
  todo_count: number
  completed_count: number
  in_progress_count: number
  pending_count: number
  captured_at: string | null
}

// ========== Slash Command Types ==========

/** Slash command or skill from ~/.claude directories */
export interface SlashCommand {
  name: string
  type: 'command' | 'skill'
  description: string
  argument_hint: string
}

/** Response for slash commands list */
export interface SlashCommandsResponse {
  commands: SlashCommand[]
}

// ========== Project File Autocomplete Types ==========

/** A file or directory in a project */
export interface ProjectFile {
  path: string
  type: 'file' | 'directory'
}

/** Response for project files list (autocomplete) */
export interface ProjectFilesResponse {
  project_id: string
  files: ProjectFile[]
  truncated: boolean
}

// ===========================================================================
// Auth (D5 Phase 2)
// ===========================================================================

export type UserRole = 'admin' | 'operator' | 'viewer'
export type AuthProvider = 'local' | 'oidc' | 'system'

/** A Gluon user record (no secrets — never includes password hash). */
export interface User {
  id: string
  username: string
  display_name: string
  email: string | null
  role: UserRole
  auth_provider: AuthProvider
  disabled: boolean
  telegram_user_id: number | null
  discord_user_id: number | null
  created_at: string
  last_login_at: string | null
}

/** Response from GET /api/auth/me — always succeeds (returns SYSTEM_USER if no session). */
export interface MeResponse {
  user: User
  /** When false, login UI should be hidden — single-user mode. */
  auth_enabled: boolean
}

/** OIDC provider info exposed by GET /api/auth/providers (D5 Phase 3). */
export interface OIDCProviderInfo {
  /** Display name shown on the button, e.g. "Google" or "Auth0". */
  name: string
  /** Absolute URL to begin the OIDC flow (server 302s to the IdP). */
  login_url: string
}

/** Response from GET /api/auth/providers — drives login-page feature detection. */
export interface AuthProvidersResponse {
  auth_enabled: boolean
  /** Username/password endpoint enabled? */
  local: boolean
  /** Configured OIDC provider, or null if not configured. */
  oidc: OIDCProviderInfo | null
}

/** Response from POST /api/auth/login. */
export interface LoginResponse {
  user: User
}

/** Body for POST /api/users (admin-only). */
export interface CreateUserRequest {
  username: string
  password: string
  display_name?: string | null
  email?: string | null
  role?: UserRole
}

/** Body for PATCH /api/users/{id} — any subset is allowed.
 *
 * For chat-account binding (D5 Phase 4) pass `0` to clear the link or a
 * positive integer to set it. Returning the field as `undefined` leaves
 * it unchanged.
 */
export interface UpdateUserRequest {
  display_name?: string | null
  email?: string | null
  role?: UserRole
  disabled?: boolean
  telegram_user_id?: number | null
  discord_user_id?: number | null
}

/** Body for POST /api/users/{id}/password. */
export interface ChangePasswordRequest {
  new_password: string
  /** Required when a non-admin changes their own password. Ignored for admins. */
  current_password?: string | null
}

/** Response from GET /api/users (admin-only). */
export interface UserListResponse {
  users: User[]
  total: number
}

/** SYSTEM_USER's deterministic ID — used as a sentinel for "no real user".
 * Imported by `useCurrentUser` to detect the auth-on/no-session fallback
 * (server returns SYSTEM_USER as a placeholder so the response shape stays
 * uniform; the client then knows to gate the login screen).
 */
export const SYSTEM_USER_ID = '00000000-0000-0000-0000-000000000000'

/** Transport platforms that support self-serve account linking (D5 Phase 4). */
export type LinkTransport = 'telegram' | 'discord'

/** Response from POST /api/auth/link-codes. */
export interface LinkCodeResponse {
  code: string
  transport: LinkTransport
  expires_at: string // ISO 8601
}

/** Response from GET /api/auth/links — what's bound to *me*. */
export interface LinkStatusResponse {
  telegram_user_id: number | null
  discord_user_id: number | null
}

// ========== Agent Loops (loop-engineering Phase 2) ==========

/** Per-loop effectiveness metrics (I5 scoped to one loop's runs). */
export interface LoopMetrics {
  runs: number
  pr_producing: number
  accepted: number
  acceptance_rate: number
  cost_usd: number
  cost_per_accepted_usd: number | null
}

/** One iteration run in a loop's timeline (detail endpoint only). */
export interface LoopRunSummary {
  id: string
  status: string
  cost_usd: number | null
  title: string
  verifier: boolean
  created_at: string
  completed_at: string | null
}

export interface LoopTaskNode {
  id: string
  status: string
  source: string | null
  prompt: string
  depends_on: string[]
  verify_cmd: string | null
}

export type AgentLoopStatus = 'running' | 'paused' | 'completed' | 'failed' | 'cancelled'

/** An agent loop: a persistent objective iterated across runs. */
export interface AgentLoop {
  id: string
  project_id: string
  project_name: string | null
  metrics: LoopMetrics | null
  recent_runs: LoopRunSummary[]
  graph: LoopTaskNode[]
  objective: string
  verify_cmd: string | null
  agent_verifier: boolean
  readiness: 'gated' | 'gateless'
  profile: string
  model: string | null
  executor_model: string | null
  watch_cmd: string | null
  use_worktree: boolean
  autonomy: 'L1' | 'L2' | 'L3'
  status: AgentLoopStatus
  status_reason: string | null
  iteration_count: number
  max_iterations: number
  total_cost_usd: number
  max_cost_usd: number | null
  stall_count: number
  max_stalls: number
  max_fanout: number
  completion_requested: boolean
  completion_summary: string | null
  pending_tasks: number
  initiator: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface AgentLoopListResponse {
  loops: AgentLoop[]
  total: number
}

/** Request body for POST /api/loops. */
export interface CreateAgentLoopRequest {
  project_name: string
  objective: string
  verify_cmd?: string | null
  agent_verifier?: boolean
  profile?: string
  model?: string | null
  executor_model?: string | null
  watch_cmd?: string | null
  use_worktree?: boolean
  autonomy?: 'L1' | 'L2' | 'L3'
  max_iterations?: number
  max_cost_usd?: number | null
  max_stalls?: number
  max_fanout?: number
}
