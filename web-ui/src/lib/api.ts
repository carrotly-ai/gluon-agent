import type {
  // Activity Log types
  ActivityEvent,
  // Agent Loops (loop-engineering Phase 2)
  AgentLoop,
  AgentLoopListResponse,
  AttentionCountsResponse,
  AuthProvidersResponse,
  ChangePasswordRequest,
  CloneResultResponse,
  CommitDetail,
  CreateAgentLoopRequest,
  // Advanced Git Operations types
  CreateRunRequest,
  CreateTaskScheduleRequest,
  // Auth types (D5 Phase 2)
  CreateUserRequest,
  CreateWorkspaceRequest,
  DailyUsage,
  FileDiff,
  ForkRunRequest,
  // Formula types
  FormulaTemplate,
  GitSyncResponse,
  // Notification types
  GluonNotification,
  ImageAttachment,
  LinkCodeResponse,
  LinkStatusResponse,
  LinkTransport,
  LoginResponse,
  LogResponse,
  LoopEffectiveness,
  MeResponse,
  // Merge Queue types
  MergeQueueEntry,
  NotificationsListResponse,
  // AskUserQuestion types
  PendingQuestion,
  PendingQuestionsResponse,
  Project,
  ProjectFile,
  ProjectFilesResponse,
  ProjectUsage,
  QueuedMessage,
  // Ralph Loop types
  RalphIterationsResponse,
  RecoverRunResponse,
  ResumeRunResponse,
  Run,
  RunCommitsResponse,
  RunDetail,
  RunFilesResponse,
  RunImagesResponse,
  RunStatus,
  // Todo Tracking types
  RunTodosResponse,
  RunUsageItem,
  ScanResultResponse,
  SchedulePreviewRequest,
  SchedulePreviewResponse,
  SDKSession,
  SessionDetail,
  SessionHistoryResponse,
  // Slash Command types
  SlashCommand,
  SlashCommandsResponse,
  StopLoopResponse,
  TaskSchedule,
  TaskScheduleListResponse,
  UpdateRunRequest,
  UpdateStatusResponse,
  UpdateTaskScheduleRequest,
  UpdateUserRequest,
  UsageSummary,
  User,
  UserListResponse,
  // Witness types
  WitnessDecision,
  // Work Queue types
  WorkQueueItem,
  Workspace,
  WorkspaceSettingsData,
} from './types'

const API_BASE = '/api'

/** Error thrown by `fetchJson` when the server returns a non-2xx response. */
export class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${url}`, {
    ...options,
    // Always send the session cookie. Critical for auth — same-origin in
    // production (FastAPI serves the SPA), and the Vite dev proxy preserves
    // cookies too. Explicit `include` makes this work even if a future
    // deployment runs the API on a different origin.
    credentials: options?.credentials ?? 'include',
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }))
    throw new ApiError(response.status, error.detail || 'API request failed')
  }

  return response.json()
}

/** Fetch all runs with optional filters */
export async function fetchRuns(params?: {
  project_id?: string
  status?: string[]
  limit?: number
  archived?: boolean
}): Promise<Run[]> {
  const searchParams = new URLSearchParams()
  if (params?.project_id) searchParams.set('project_id', params.project_id)
  if (params?.status) params.status.forEach((s) => searchParams.append('status', s))
  if (params?.limit) searchParams.set('limit', String(params.limit))
  if (params?.archived !== undefined) searchParams.set('archived', String(params.archived))

  const query = searchParams.toString()
  return fetchJson<Run[]>(`/runs${query ? `?${query}` : ''}`)
}

/** Fetch a single run by ID */
export async function fetchRun(runId: string): Promise<RunDetail> {
  return fetchJson<RunDetail>(`/runs/${runId}`)
}

/** Create a new run */
export async function createRun(request: CreateRunRequest): Promise<Run> {
  return fetchJson<Run>('/runs', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

/** Cancel a running task */
export async function cancelRun(runId: string): Promise<Run> {
  return fetchJson<Run>(`/runs/${runId}/cancel`, {
    method: 'POST',
  })
}

/** Archive a run (hide from board) */
export async function archiveRun(runId: string): Promise<Run> {
  return fetchJson<Run>(`/runs/${runId}/archive`, {
    method: 'POST',
  })
}

/** Unarchive a run */
export async function unarchiveRun(runId: string): Promise<Run> {
  return fetchJson<Run>(`/runs/${runId}/unarchive`, {
    method: 'POST',
  })
}

/** PATCH a run's user-editable fields (title, kind). */
export async function updateRun(runId: string, patch: UpdateRunRequest): Promise<Run> {
  return fetchJson<Run>(`/runs/${runId}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

/** Set or clear a run's snooze deadline. Pass null to unsnooze. */
export async function snoozeRun(runId: string, until: string | null): Promise<Run> {
  return fetchJson<Run>(`/runs/${runId}/snooze`, {
    method: 'POST',
    body: JSON.stringify({ until }),
  })
}

/** Fork an existing run's Claude session into a new child run. */
export async function forkRun(runId: string, body: ForkRunRequest): Promise<Run> {
  return fetchJson<Run>(`/runs/${runId}/fork`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** Fetch aggregate attention counts (for sidebar / project badges). */
export async function fetchAttentionCounts(): Promise<AttentionCountsResponse> {
  return fetchJson<AttentionCountsResponse>('/attention-counts')
}

/** Resume a completed/failed run with a follow-up prompt */
export async function resumeRun(runId: string, prompt: string): Promise<ResumeRunResponse> {
  return fetchJson<ResumeRunResponse>(`/runs/${runId}/resume`, {
    method: 'POST',
    body: JSON.stringify({ prompt }),
  })
}

/** Response from queue follow-up operation */
export interface QueueFollowupResponse {
  run_id: string
  action: 'queued' | 'resume_now'
  message: string | null
  /** ID of the queued message (present when action === 'queued') */
  message_id: string | null
}

/** Queue a follow-up message for a running task (will auto-resume after completion) */
export async function queueFollowup(
  runId: string,
  message: string
): Promise<QueueFollowupResponse> {
  return fetchJson<QueueFollowupResponse>(`/runs/${runId}/queue-followup`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  })
}

/** Edit a queued message */
export async function editQueuedMessage(
  runId: string,
  messageId: string,
  message: string
): Promise<QueuedMessage> {
  return fetchJson<QueuedMessage>(`/runs/${runId}/queue/${messageId}`, {
    method: 'PUT',
    body: JSON.stringify({ message }),
  })
}

/** Delete a queued message */
export async function deleteQueuedMessage(
  runId: string,
  messageId: string
): Promise<{ deleted: boolean; message_id: string }> {
  return fetchJson<{ deleted: boolean; message_id: string }>(`/runs/${runId}/queue/${messageId}`, {
    method: 'DELETE',
  })
}

/** Recover a failed run (typically from context overflow) */
export async function recoverRun(
  runId: string,
  fresh: boolean = false
): Promise<RecoverRunResponse> {
  return fetchJson<RecoverRunResponse>(`/runs/${runId}/recover`, {
    method: 'POST',
    body: JSON.stringify({ fresh }),
  })
}

/** Fetch log content for a run */
export async function fetchLogs(
  runId: string,
  stream: 'stdout' | 'stderr' | 'messages' = 'stdout',
  tail?: number
): Promise<LogResponse> {
  const params = new URLSearchParams({ stream })
  if (tail) params.set('tail', String(tail))
  return fetchJson<LogResponse>(`/runs/${runId}/logs?${params}`)
}

/** Fetch session history (all runs in the same session) */
export async function fetchSessionHistory(runId: string): Promise<SessionHistoryResponse> {
  return fetchJson<SessionHistoryResponse>(`/runs/${runId}/session-history`)
}

// ========== Ralph Loop API ==========

/** Fetch iteration history for a ralph-enabled run */
export async function fetchRalphIterations(
  runId: string,
  limit?: number
): Promise<RalphIterationsResponse> {
  const params = limit ? `?limit=${limit}` : ''
  return fetchJson<RalphIterationsResponse>(`/runs/${runId}/iterations${params}`)
}

/** Stop a ralph loop early (graceful termination) */
export async function stopLoop(runId: string): Promise<StopLoopResponse> {
  return fetchJson<StopLoopResponse>(`/runs/${runId}/stop-loop`, {
    method: 'POST',
  })
}

// ========== AskUserQuestion API ==========

/** Fetch pending questions for a run */
export async function fetchRunQuestions(runId: string): Promise<PendingQuestionsResponse> {
  return fetchJson<PendingQuestionsResponse>(`/runs/${runId}/questions`)
}

/** Answer a pending question */
export async function answerQuestion(
  questionId: string,
  selectedLabels: string[]
): Promise<PendingQuestion> {
  return fetchJson<PendingQuestion>(`/questions/${questionId}/answer`, {
    method: 'POST',
    body: JSON.stringify({ selected_labels: selectedLabels }),
  })
}

// ========== Todo Tracking API ==========

/** Fetch the latest todo tracking state for a run */
export async function fetchRunTodos(runId: string): Promise<RunTodosResponse> {
  return fetchJson<RunTodosResponse>(`/runs/${runId}/todos`)
}

/** Fetch all projects */
export async function fetchProjects(): Promise<Project[]> {
  return fetchJson<Project[]>('/projects')
}

// ========== Status Transition API (Phase 7.2 Drag-and-Drop) ==========

/** Update run status via drag-and-drop */
export async function updateRunStatus(
  runId: string,
  status: RunStatus,
  reason?: string
): Promise<UpdateStatusResponse> {
  return fetchJson<UpdateStatusResponse>(`/runs/${runId}/status`, {
    method: 'POST',
    body: JSON.stringify({ status, reason }),
  })
}

// ========== Project Management API (Phase 7.3) ==========

/** Delete a project */
export async function deleteProject(
  projectId: string
): Promise<{ deleted: boolean; project_id: string }> {
  return fetchJson<{ deleted: boolean; project_id: string }>(`/projects/${projectId}`, {
    method: 'DELETE',
  })
}

// ========== Task Schedules (user-defined recurring tasks) ==========

export async function fetchSchedules(opts?: {
  project_id?: string
  include_disabled?: boolean
}): Promise<TaskScheduleListResponse> {
  const params = new URLSearchParams()
  if (opts?.project_id) params.set('project_id', opts.project_id)
  if (opts?.include_disabled !== undefined)
    params.set('include_disabled', String(opts.include_disabled))
  const qs = params.toString()
  return fetchJson<TaskScheduleListResponse>(`/schedules${qs ? `?${qs}` : ''}`)
}

export async function createSchedule(body: CreateTaskScheduleRequest): Promise<TaskSchedule> {
  return fetchJson<TaskSchedule>('/schedules', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function updateSchedule(
  scheduleId: string,
  body: UpdateTaskScheduleRequest
): Promise<TaskSchedule> {
  return fetchJson<TaskSchedule>(`/schedules/${scheduleId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export async function deleteSchedule(scheduleId: string): Promise<void> {
  await fetchJson<unknown>(`/schedules/${scheduleId}`, { method: 'DELETE' })
}

export async function enableSchedule(scheduleId: string): Promise<TaskSchedule> {
  return fetchJson<TaskSchedule>(`/schedules/${scheduleId}/enable`, { method: 'POST' })
}

export async function disableSchedule(scheduleId: string): Promise<TaskSchedule> {
  return fetchJson<TaskSchedule>(`/schedules/${scheduleId}/disable`, { method: 'POST' })
}

export async function fireScheduleNow(scheduleId: string): Promise<Run> {
  return fetchJson<Run>(`/schedules/${scheduleId}/fire`, { method: 'POST' })
}

export async function previewSchedule(
  body: SchedulePreviewRequest
): Promise<SchedulePreviewResponse> {
  return fetchJson<SchedulePreviewResponse>('/schedules/preview', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ========== Workspace Management API (Phase 7.3) ==========

/** Fetch all workspaces */
export async function fetchWorkspaces(): Promise<Workspace[]> {
  return fetchJson<Workspace[]>('/workspaces')
}

/** Create a new workspace */
export async function createWorkspace(request: CreateWorkspaceRequest): Promise<Workspace> {
  return fetchJson<Workspace>('/workspaces', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

/** Delete a workspace */
export async function deleteWorkspace(
  workspaceId: string
): Promise<{ deleted: boolean; workspace_id: string }> {
  return fetchJson<{ deleted: boolean; workspace_id: string }>(`/workspaces/${workspaceId}`, {
    method: 'DELETE',
  })
}

/** Scan a workspace for new projects */
export async function scanWorkspace(workspaceId: string): Promise<ScanResultResponse> {
  return fetchJson<ScanResultResponse>(`/workspaces/${workspaceId}/scan`, {
    method: 'POST',
  })
}

/** Clone a GitHub repository into a workspace */
export async function cloneRepository(
  workspaceId: string,
  githubUrl: string
): Promise<CloneResultResponse> {
  return fetchJson<CloneResultResponse>(`/workspaces/${workspaceId}/clone`, {
    method: 'POST',
    body: JSON.stringify({ github_url: githubUrl }),
  })
}

// ========== Usage Dashboard API (Phase 8) ==========

/** Fetch usage summary for header display */
export async function fetchUsageSummary(): Promise<UsageSummary> {
  return fetchJson<UsageSummary>('/usage/summary')
}

/** Fetch loop-effectiveness (I5): acceptance rate + cost-per-accepted-change */
export async function fetchLoopEffectiveness(): Promise<LoopEffectiveness> {
  return fetchJson<LoopEffectiveness>('/usage/effectiveness')
}

/** Fetch usage breakdown by project */
export async function fetchUsageByProject(params?: {
  since?: string
  until?: string
}): Promise<ProjectUsage[]> {
  const searchParams = new URLSearchParams()
  if (params?.since) searchParams.set('since', params.since)
  if (params?.until) searchParams.set('until', params.until)

  const query = searchParams.toString()
  return fetchJson<ProjectUsage[]>(`/usage/by-project${query ? `?${query}` : ''}`)
}

/** Fetch daily usage for charts */
export async function fetchUsageByDay(params?: {
  since?: string
  until?: string
}): Promise<DailyUsage[]> {
  const searchParams = new URLSearchParams()
  if (params?.since) searchParams.set('since', params.since)
  if (params?.until) searchParams.set('until', params.until)

  const query = searchParams.toString()
  return fetchJson<DailyUsage[]>(`/usage/by-day${query ? `?${query}` : ''}`)
}

/** Fetch runs with cost data for usage dashboard */
export async function fetchUsageRuns(params?: {
  since?: string
  until?: string
  sort_by?: 'cost' | 'date' | 'tokens'
  sort_order?: 'asc' | 'desc'
  limit?: number
}): Promise<RunUsageItem[]> {
  const searchParams = new URLSearchParams()
  if (params?.since) searchParams.set('since', params.since)
  if (params?.until) searchParams.set('until', params.until)
  if (params?.sort_by) searchParams.set('sort_by', params.sort_by)
  if (params?.sort_order) searchParams.set('sort_order', params.sort_order)
  if (params?.limit) searchParams.set('limit', String(params.limit))

  const query = searchParams.toString()
  return fetchJson<RunUsageItem[]>(`/usage/runs${query ? `?${query}` : ''}`)
}

// ========== Settings API (Phase 9) ==========

export type Settings = Record<string, string>

/** Fetch all settings */
export async function fetchSettings(): Promise<Settings> {
  return fetchJson<Settings>('/settings')
}

/** Update a single setting */
export async function updateSetting(
  key: string,
  value: string
): Promise<{ key: string; value: string }> {
  return fetchJson<{ key: string; value: string }>(`/settings/${key}`, {
    method: 'PUT',
    body: JSON.stringify({ value }),
  })
}

export interface SandboxStatus {
  available: boolean
  runtime: string | null
  enabled: boolean
  platform: string
}

/** Fetch sandbox availability and status */
export async function fetchSandboxStatus(): Promise<SandboxStatus> {
  return fetchJson<SandboxStatus>('/sandbox/status')
}

// ========== Manual PR Creation API ==========

export interface CreatePrResponse {
  success: boolean
  pr_url?: string
  pr_number?: number
  pr_status?: string
  error?: string
}

/** Manually create a PR for a completed worktree run */
export async function createPrForRun(runId: string): Promise<CreatePrResponse> {
  return fetchJson<CreatePrResponse>(`/runs/${runId}/create-pr`, {
    method: 'POST',
  })
}

// ========== Git Commits and Files API ==========

/** Fetch commits on a run's branch */
export async function fetchRunCommits(runId: string): Promise<RunCommitsResponse> {
  return fetchJson<RunCommitsResponse>(`/runs/${runId}/commits`)
}

/** Fetch files changed on a run's branch */
export async function fetchRunFiles(runId: string): Promise<RunFilesResponse> {
  return fetchJson<RunFilesResponse>(`/runs/${runId}/files`)
}

/** Fetch detailed commit info (full message + files) - lazy loaded on expand */
export async function fetchCommitDetail(runId: string, sha: string): Promise<CommitDetail> {
  return fetchJson<CommitDetail>(`/runs/${runId}/commits/${sha}`)
}

/** Fetch file diff - lazy loaded on expand */
export async function fetchFileDiff(runId: string, filePath: string): Promise<FileDiff> {
  return fetchJson<FileDiff>(`/runs/${runId}/files/${encodeURIComponent(filePath)}/diff`)
}

// ========== Merge Branch API ==========

export interface MergeResponse {
  success: boolean
  message?: string
  merged_commit_sha?: string
  error?: string
  has_conflicts?: boolean
  conflicting_files?: string[]
}

/** Merge a run's branch locally and push (if remote exists, GitHub will auto-close the PR) */
export async function mergeRunBranch(runId: string): Promise<MergeResponse> {
  return fetchJson<MergeResponse>(`/runs/${runId}/merge`, {
    method: 'POST',
  })
}

// ========== Image Attachments API (Phase 10.1) ==========

/** Get image file URL (for <img> src) */
export function getImageFileUrl(imageId: string): string {
  return `${API_BASE}/images/${imageId}/file`
}

/** Get images attached to a run */
export async function fetchRunAttachments(runId: string): Promise<RunImagesResponse> {
  return fetchJson<RunImagesResponse>(`/runs/${runId}/attachments`)
}

/** Upload and attach an image to a run */
export async function uploadAndAttachImage(runId: string, file: File): Promise<ImageAttachment> {
  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(`${API_BASE}/runs/${runId}/attachments`, {
    method: 'POST',
    body: formData,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(error.detail || 'Failed to attach image')
  }

  return response.json()
}

// ========== Advanced Git Operations API (Phase 5) ==========

// ========== Git Sync API (Settings Page) ==========

/** Perform git sync operation (auto, pull, push, fetch) */
export async function syncProjectGit(
  projectId: string,
  action: 'auto' | 'pull' | 'push' | 'fetch' = 'auto'
): Promise<GitSyncResponse> {
  return fetchJson<GitSyncResponse>(`/projects/${projectId}/git/sync`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
}

/** Refresh git status for all projects */
export async function refreshAllGitStatuses(): Promise<{
  projects_refreshed: number
  errors: string[]
}> {
  return fetchJson<{ projects_refreshed: number; errors: string[] }>('/git/refresh-all', {
    method: 'POST',
  })
}

// ========== Slash Commands ==========

/** Fetch available slash commands and skills.
 * If projectId is provided, includes project-specific commands from <project>/.claude directories.
 * Project commands take precedence over global commands with the same name.
 */
export async function fetchCommands(projectId?: string): Promise<SlashCommand[]> {
  const url = projectId ? `/projects/${projectId}/commands` : '/commands'
  const response = await fetchJson<SlashCommandsResponse>(url)
  return response.commands
}

// ========== Vercel CLI ==========

/** Test a Vercel API token by calling vercel whoami */
export async function testVercelToken(
  token: string
): Promise<{ valid: boolean; account?: string; error?: string }> {
  return fetchJson('/vercel/test', {
    method: 'POST',
    body: JSON.stringify({ token }),
  })
}

// ========== Project Files (Autocomplete) ==========

/** Fetch files in a project for autocomplete */
export async function fetchProjectFiles(
  projectId: string,
  prefix: string = '',
  limit: number = 1000
): Promise<{ files: ProjectFile[]; truncated: boolean }> {
  const params = new URLSearchParams()
  if (prefix) params.set('prefix', prefix)
  params.set('limit', limit.toString())
  const response = await fetchJson<ProjectFilesResponse>(
    `/projects/${projectId}/files?${params.toString()}`
  )
  return { files: response.files, truncated: response.truncated }
}

// ========== Activity Log ==========

/** Fetch activity events with optional filters */
export async function fetchActivity(params?: {
  actor?: string
  action?: string
  since?: string
  limit?: number
}): Promise<{ events: ActivityEvent[]; total: number }> {
  const searchParams = new URLSearchParams()
  if (params?.actor) searchParams.set('actor', params.actor)
  if (params?.action) searchParams.set('action', params.action)
  if (params?.since) searchParams.set('since', params.since)
  if (params?.limit) searchParams.set('limit', String(params.limit))
  const query = searchParams.toString()
  return fetchJson<{ events: ActivityEvent[]; total: number }>(
    `/activity${query ? `?${query}` : ''}`
  )
}

/** Cleanup old activity events */
export async function cleanupActivity(days: number = 90): Promise<{ deleted: number }> {
  return fetchJson<{ deleted: number }>(`/activity/cleanup?days=${days}`, { method: 'POST' })
}

// ========== Work Queue ==========

/** Fetch work queue items with optional filters */
export async function fetchWorkQueue(params?: {
  project_id?: string
  status?: string
  limit?: number
}): Promise<{ items: WorkQueueItem[]; total: number }> {
  const searchParams = new URLSearchParams()
  if (params?.project_id) searchParams.set('project_id', params.project_id)
  if (params?.status) searchParams.set('status', params.status)
  if (params?.limit) searchParams.set('limit', String(params.limit))
  const query = searchParams.toString()
  return fetchJson<{ items: WorkQueueItem[]; total: number }>(`/queue${query ? `?${query}` : ''}`)
}

/** Add item to work queue */
export async function addToQueue(req: {
  project_id: string
  prompt: string
  profile?: string
  priority?: number
}): Promise<WorkQueueItem> {
  return fetchJson<WorkQueueItem>('/queue', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

/** Cancel a work queue item */
export async function cancelQueueItem(itemId: string): Promise<WorkQueueItem> {
  return fetchJson<WorkQueueItem>(`/queue/${itemId}/cancel`, { method: 'POST' })
}

/** Release a claimed work queue item back to pending */
export async function releaseQueueItem(itemId: string): Promise<WorkQueueItem> {
  return fetchJson<WorkQueueItem>(`/queue/${itemId}/release`, { method: 'POST' })
}

// ========== Merge Queue ==========

/** Fetch merge queue entries with optional filters */
export async function fetchMergeQueue(params?: {
  status?: string
  limit?: number
}): Promise<{ entries: MergeQueueEntry[]; total: number }> {
  const searchParams = new URLSearchParams()
  if (params?.status) searchParams.set('status', params.status)
  if (params?.limit) searchParams.set('limit', String(params.limit))
  const query = searchParams.toString()
  return fetchJson<{ entries: MergeQueueEntry[]; total: number }>(
    `/merge-queue${query ? `?${query}` : ''}`
  )
}

/** Retry a failed/conflicted merge */
export async function retryMerge(entryId: string): Promise<MergeQueueEntry> {
  return fetchJson<MergeQueueEntry>(`/merge-queue/${entryId}/retry`, { method: 'POST' })
}

/** Cancel a merge queue entry */
export async function cancelMerge(entryId: string): Promise<MergeQueueEntry> {
  return fetchJson<MergeQueueEntry>(`/merge-queue/${entryId}/cancel`, { method: 'POST' })
}

// ========== Witness ==========

/** Fetch witness health decisions for a run */
export async function fetchWitnessDecisions(
  runId: string
): Promise<{ run_id: string; decisions: WitnessDecision[] }> {
  return fetchJson<{ run_id: string; decisions: WitnessDecision[] }>(`/runs/${runId}/witness`)
}

// ========== Formulas ==========

/** Fetch all available formula templates */
export async function fetchFormulas(): Promise<{ formulas: FormulaTemplate[] }> {
  return fetchJson<{ formulas: FormulaTemplate[] }>('/formulas')
}

/** Run a formula template for a project */
export async function runFormula(
  name: string,
  req: { project_id: string; variables: Record<string, string> }
): Promise<{ chain_id: string; step_count: number }> {
  return fetchJson<{ chain_id: string; step_count: number }>(`/formulas/${name}/run`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

// ========== Notifications ==========

/** Fetch notifications with optional filters */
export async function fetchNotifications(params?: {
  workspace_id?: string
  unread_only?: boolean
  limit?: number
}): Promise<NotificationsListResponse> {
  const searchParams = new URLSearchParams()
  if (params?.workspace_id) searchParams.set('workspace_id', params.workspace_id)
  if (params?.unread_only) searchParams.set('unread_only', 'true')
  if (params?.limit) searchParams.set('limit', String(params.limit))
  const query = searchParams.toString()
  return fetchJson<NotificationsListResponse>(`/notifications${query ? `?${query}` : ''}`)
}

/** Mark a single notification as read */
export async function markNotificationRead(id: string): Promise<GluonNotification> {
  return fetchJson<GluonNotification>(`/notifications/${id}/read`, { method: 'POST' })
}

/** Mark all notifications as read */
export async function markAllNotificationsRead(
  workspaceId?: string
): Promise<{ marked_read: number }> {
  const params = workspaceId ? `?workspace_id=${workspaceId}` : ''
  return fetchJson<{ marked_read: number }>(`/notifications/read-all${params}`, { method: 'POST' })
}

export async function deleteAllNotifications(): Promise<{ deleted: number }> {
  return fetchJson<{ deleted: number }>('/notifications', { method: 'DELETE' })
}

// ========== SDK Session Browser ==========

/** Fetch all SDK sessions from Claude CLI */
export async function fetchSDKSessions(params?: {
  directory?: string
  limit?: number
}): Promise<SDKSession[]> {
  const searchParams = new URLSearchParams()
  if (params?.directory) searchParams.set('directory', params.directory)
  if (params?.limit) searchParams.set('limit', String(params.limit))
  const query = searchParams.toString()
  return fetchJson<SDKSession[]>(`/sdk-sessions${query ? `?${query}` : ''}`)
}

/** Fetch SDK session detail with messages */
export async function fetchSDKSessionDetail(
  sessionId: string,
  params?: { directory?: string; limit?: number; offset?: number }
): Promise<SessionDetail> {
  const searchParams = new URLSearchParams()
  if (params?.directory) searchParams.set('directory', params.directory)
  if (params?.limit) searchParams.set('limit', String(params.limit))
  if (params?.offset) searchParams.set('offset', String(params.offset))
  const query = searchParams.toString()
  return fetchJson<SessionDetail>(`/sdk-sessions/${sessionId}${query ? `?${query}` : ''}`)
}

/**
 * Resume a Claude SDK session as a new run.
 *
 * TODO(backend): no endpoint exists yet. The backend needs to expose
 * `POST /api/sdk-sessions/{session_id}/resume` that:
 *   - Reads the session's `cwd` to determine which project to attach to
 *     (fallback: pick the first project whose path matches).
 *   - Creates a new Run with `resume_session_id = session_id` so the SDK
 *     `ClaudeAgentOptions.resume = session_id` kicks in (see agent.py:resume).
 *   - Returns the new Run shape so the UI can navigate to /board/{run_id}.
 *
 * Until that lands, this throws — the SessionBrowserPage catches the error
 * and surfaces an honest "Resume not available yet" message.
 */
export async function resumeSdkSession(sessionId: string): Promise<Run> {
  return fetchJson<Run>(`/sdk-sessions/${sessionId}/resume`, {
    method: 'POST',
  })
}

// ========== Workspace Settings ==========

/** Fetch workspace settings with global defaults for comparison */
export async function fetchWorkspaceSettings(workspaceId: string): Promise<WorkspaceSettingsData> {
  return fetchJson<WorkspaceSettingsData>(`/workspaces/${workspaceId}/settings`)
}

/** Update one or more workspace setting overrides */
export async function updateWorkspaceSettings(
  workspaceId: string,
  settings: Record<string, string>
): Promise<void> {
  await fetchJson(`/workspaces/${workspaceId}/settings`, {
    method: 'PUT',
    body: JSON.stringify(settings),
  })
}

/** Remove a single workspace setting override (reverts to global) */
export async function deleteWorkspaceSetting(workspaceId: string, key: string): Promise<void> {
  await fetchJson(`/workspaces/${workspaceId}/settings/${encodeURIComponent(key)}`, {
    method: 'DELETE',
  })
}

/** Set workspace environment variables */
export async function updateWorkspaceEnvVars(
  workspaceId: string,
  vars: Record<string, string>
): Promise<void> {
  await fetchJson(`/workspaces/${workspaceId}/env-vars`, {
    method: 'PUT',
    body: JSON.stringify(vars),
  })
}

/** Remove a workspace environment variable */
export async function deleteWorkspaceEnvVar(workspaceId: string, key: string): Promise<void> {
  await fetchJson(`/workspaces/${workspaceId}/env-vars/${encodeURIComponent(key)}`, {
    method: 'DELETE',
  })
}

// ===========================================================================
// Auth API (D5 Phase 2)
// ===========================================================================

/** Get the current user. Always succeeds — returns SYSTEM_USER if no session. */
export async function fetchMe(): Promise<MeResponse> {
  return fetchJson<MeResponse>('/auth/me')
}

/**
 * List the auth methods available on this server (D5 Phase 3 feature
 * detection). Always succeeds — returns `{auth_enabled: false}` for
 * single-user installs, or the union of local + OIDC for multi-user ones.
 */
export async function fetchAuthProviders(): Promise<AuthProvidersResponse> {
  return fetchJson<AuthProvidersResponse>('/auth/providers')
}

/** Log in with username + password. Sets the session cookie on success. */
export async function login(username: string, password: string): Promise<LoginResponse> {
  return fetchJson<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

/** Log out the current session. Clears the session cookie. */
export async function logout(): Promise<void> {
  await fetchJson('/auth/logout', { method: 'POST' })
}

/** List all users (admin-only). */
export async function listUsers(includeDisabled = false): Promise<UserListResponse> {
  const query = includeDisabled ? '?include_disabled=true' : ''
  return fetchJson<UserListResponse>(`/users${query}`)
}

/** Create a new user (admin-only). */
export async function createUser(body: CreateUserRequest): Promise<User> {
  return fetchJson<User>('/users', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** Update an existing user (admin-only). */
export async function updateUser(userId: string, body: UpdateUserRequest): Promise<User> {
  return fetchJson<User>(`/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

/** Disable (soft-delete) a user (admin-only). */
export async function disableUser(userId: string): Promise<User> {
  return fetchJson<User>(`/users/${userId}`, { method: 'DELETE' })
}

/**
 * Change a user's password.
 *
 * - Admins may change anyone's password (omit `currentPassword`).
 * - A non-admin user may only change their own password and must pass
 *   `currentPassword` so it can be verified.
 */
export async function changePassword(
  userId: string,
  newPassword: string,
  currentPassword?: string
): Promise<User> {
  const body: ChangePasswordRequest = {
    new_password: newPassword,
    current_password: currentPassword ?? null,
  }
  return fetchJson<User>(`/users/${userId}/password`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ===========================================================================
// Self-serve account linking (D5 Phase 4)
// ===========================================================================

/**
 * Generate a one-time code that the calling user redeems by sending
 * `link-account <code>` (Discord) or `/link <code>` (Telegram) to the bot.
 *
 * Codes expire after 10 minutes. Generating a new code for the same
 * (user, transport) pair invalidates any prior unconsumed code.
 */
export async function createLinkCode(transport: LinkTransport): Promise<LinkCodeResponse> {
  return fetchJson<LinkCodeResponse>('/auth/link-codes', {
    method: 'POST',
    body: JSON.stringify({ transport }),
  })
}

/** Remove the current user's binding for the given transport. */
export async function unlinkMyChat(transport: LinkTransport): Promise<LinkStatusResponse> {
  return fetchJson<LinkStatusResponse>(`/auth/links/${transport}`, {
    method: 'DELETE',
  })
}

// ========== Agent Loops (loop-engineering Phase 2) ==========

export async function fetchLoops(opts?: {
  project_id?: string
  status?: string
}): Promise<AgentLoopListResponse> {
  const params = new URLSearchParams()
  if (opts?.project_id) params.set('project_id', opts.project_id)
  if (opts?.status) params.set('status', opts.status)
  const qs = params.toString()
  return fetchJson<AgentLoopListResponse>(`/loops${qs ? `?${qs}` : ''}`)
}

export async function fetchLoop(loopId: string): Promise<AgentLoop> {
  return fetchJson<AgentLoop>(`/loops/${loopId}`)
}

export async function createLoop(body: CreateAgentLoopRequest): Promise<AgentLoop> {
  return fetchJson<AgentLoop>('/loops', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function pauseLoop(loopId: string): Promise<AgentLoop> {
  return fetchJson<AgentLoop>(`/loops/${loopId}/pause`, { method: 'POST' })
}

export async function resumeLoop(loopId: string): Promise<AgentLoop> {
  return fetchJson<AgentLoop>(`/loops/${loopId}/resume`, { method: 'POST' })
}

export async function cancelLoop(loopId: string): Promise<AgentLoop> {
  return fetchJson<AgentLoop>(`/loops/${loopId}/cancel`, { method: 'POST' })
}
