import type {
  Run,
  RunDetail,
  RunStatus,
  CreateRunRequest,
  LogResponse,
  Project,
  ProjectDetail,
  CreateProjectRequest,
  Workspace,
  CreateWorkspaceRequest,
  ScanResultResponse,
  SystemStatus,
  ResumeRunResponse,
  SessionHistoryResponse,
  UpdateStatusResponse,
  UsageSummary,
  ProjectUsage,
  DailyUsage,
  RunUsageItem,
  RunCommitsResponse,
  RunFilesResponse,
} from './types'

const API_BASE = '/api'

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${url}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(error.detail || 'API request failed')
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
  if (params?.status) params.status.forEach(s => searchParams.append('status', s))
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

/** Update PR status (e.g., mark as merged to move from REVIEW to DONE) */
export async function updatePrStatus(runId: string, prStatus: 'open' | 'merged' | 'closed' | 'draft'): Promise<Run> {
  const params = new URLSearchParams({ pr_status: prStatus })
  return fetchJson<Run>(`/runs/${runId}/pr-status?${params}`, {
    method: 'POST',
  })
}

/** Resume a completed/failed run with a follow-up prompt */
export async function resumeRun(runId: string, prompt: string): Promise<ResumeRunResponse> {
  return fetchJson<ResumeRunResponse>(`/runs/${runId}/resume`, {
    method: 'POST',
    body: JSON.stringify({ prompt }),
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

/** Fetch all projects */
export async function fetchProjects(): Promise<Project[]> {
  return fetchJson<Project[]>('/projects')
}

/** Fetch system status */
export async function fetchStatus(): Promise<SystemStatus> {
  return fetchJson<SystemStatus>('/status')
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

/** Fetch a single project by ID */
export async function fetchProject(projectId: string): Promise<ProjectDetail> {
  return fetchJson<ProjectDetail>(`/projects/${projectId}`)
}

/** Create a new project */
export async function createProject(request: CreateProjectRequest): Promise<Project> {
  return fetchJson<Project>('/projects', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

/** Delete a project */
export async function deleteProject(projectId: string): Promise<{ deleted: boolean; project_id: string }> {
  return fetchJson<{ deleted: boolean; project_id: string }>(`/projects/${projectId}`, {
    method: 'DELETE',
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
export async function deleteWorkspace(workspaceId: string): Promise<{ deleted: boolean; workspace_id: string }> {
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

// ========== Usage Dashboard API (Phase 8) ==========

/** Fetch usage summary for header display */
export async function fetchUsageSummary(): Promise<UsageSummary> {
  return fetchJson<UsageSummary>('/usage/summary')
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
export async function updateSetting(key: string, value: string): Promise<{ key: string; value: string }> {
  return fetchJson<{ key: string; value: string }>(`/settings/${key}`, {
    method: 'PUT',
    body: JSON.stringify({ value }),
  })
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

// ========== Merge Branch API ==========

export interface MergeResponse {
  success: boolean
  message?: string
  merged_commit_sha?: string
  error?: string
}

/** Merge a run's branch locally and push (GitHub will auto-close the PR) */
export async function mergeRunBranch(runId: string): Promise<MergeResponse> {
  return fetchJson<MergeResponse>(`/runs/${runId}/merge`, {
    method: 'POST',
  })
}
