import type { Run, RunDetail, CreateRunRequest, LogResponse, Project, SystemStatus } from './types'

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
}): Promise<Run[]> {
  const searchParams = new URLSearchParams()
  if (params?.project_id) searchParams.set('project_id', params.project_id)
  if (params?.status) params.status.forEach(s => searchParams.append('status', s))
  if (params?.limit) searchParams.set('limit', String(params.limit))

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

/** Fetch all projects */
export async function fetchProjects(): Promise<Project[]> {
  return fetchJson<Project[]>('/projects')
}

/** Fetch system status */
export async function fetchStatus(): Promise<SystemStatus> {
  return fetchJson<SystemStatus>('/status')
}
