import { useCallback, useMemo } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'

export type FilterType = 'all' | 'workspace' | 'project' | 'archived'

export interface RouteFilter {
  type: FilterType
  value: string | null
}

// Valid tabs for RunDetailDialog
export type RunDetailTab =
  | 'messages'
  | 'output'
  | 'errors'
  | 'history'
  | 'commits'
  | 'files'
  | 'attachments'
  | 'health'

// Valid tabs for SettingsPage. `account` was added in the IA restructure —
// it owns Connected Accounts, Change Password, Profile. Sits between
// Workspaces and Preferences in the tab order.
export type SettingsTab = 'workspaces' | 'projects' | 'account' | 'preferences' | 'formulas'

// Preferences left-rail groups. URL: /settings/preferences/<group>.
// Stays optional — bare /settings/preferences defaults to 'agent'.
export type PreferencesGroup = 'agent' | 'integrations' | 'workspace' | 'system'

/**
 * Hook for syncing app state with URL routes
 *
 * Supports:
 * - /board, /board/:runId, /board/:runId/:tab
 * - /cost
 * - /settings, /settings/:tab
 * - Query params: ?workspace=X, ?project=X, ?filter=archived
 */
export function useRouteSync() {
  const params = useParams<{ runId?: string; tab?: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()

  // Derive current view from pathname
  const viewMode = useMemo(() => {
    const path = location.pathname
    if (path.startsWith('/activity')) return 'activity' as const
    if (path.startsWith('/queue')) return 'queue' as const
    if (path.startsWith('/merge')) return 'merge' as const
    if (path.startsWith('/cost')) return 'usage' as const
    if (path.startsWith('/sessions')) return 'sessions' as const
    if (path.startsWith('/schedules')) return 'schedules' as const
    if (path.startsWith('/loops')) return 'loops' as const
    if (path.startsWith('/list')) return 'list' as const
    if (path.startsWith('/admin/users')) return 'admin-users' as const
    if (path.startsWith('/settings')) return 'settings' as const
    return 'board' as const
  }, [location.pathname])

  // Derive filter from search params
  const filter = useMemo((): RouteFilter => {
    const workspace = searchParams.get('workspace')
    const project = searchParams.get('project')
    const filterType = searchParams.get('filter')

    if (workspace) return { type: 'workspace', value: workspace }
    if (project) return { type: 'project', value: project }
    if (filterType === 'archived') return { type: 'archived', value: null }
    return { type: 'all', value: null }
  }, [searchParams])

  // Modal state from route params
  const selectedRunId = params.runId || null
  const selectedTab = (params.tab as RunDetailTab) || null

  // Settings tab from route params (for /settings/:tab[/...]).
  // Match by prefix so /settings/preferences/agent still resolves to the
  // 'preferences' tab; the preferences group is parsed separately below.
  const settingsTab = useMemo((): SettingsTab => {
    if (viewMode !== 'settings') return 'workspaces'
    const path = location.pathname
    if (path.startsWith('/settings/projects')) return 'projects'
    if (path.startsWith('/settings/account')) return 'account'
    if (path.startsWith('/settings/preferences')) return 'preferences'
    if (path.startsWith('/settings/formulas')) return 'formulas'
    return 'workspaces'
  }, [viewMode, location.pathname])

  // Preferences group, derived from /settings/preferences/<group>.
  // Defaults to 'agent' — the most-used cluster (LLM provider, config, tools).
  const preferencesGroup = useMemo((): PreferencesGroup => {
    if (viewMode !== 'settings') return 'agent'
    const path = location.pathname
    if (path === '/settings/preferences/integrations') return 'integrations'
    if (path === '/settings/preferences/workspace') return 'workspace'
    if (path === '/settings/preferences/system') return 'system'
    return 'agent'
  }, [viewMode, location.pathname])

  // Navigation functions
  const setViewMode = useCallback(
    (
      view:
        | 'board'
        | 'list'
        | 'activity'
        | 'queue'
        | 'merge'
        | 'usage'
        | 'sessions'
        | 'schedules'
        | 'loops'
        | 'settings'
        | 'admin-users'
    ) => {
      switch (view) {
        case 'board':
          navigate(`/board${location.search}`)
          break
        case 'list':
          navigate(`/list${location.search}`)
          break
        case 'activity':
          navigate('/activity')
          break
        case 'queue':
          navigate('/queue')
          break
        case 'merge':
          navigate('/merge')
          break
        case 'usage':
          navigate('/cost')
          break
        case 'sessions':
          navigate('/sessions')
          break
        case 'schedules':
          navigate('/schedules')
          break
        case 'loops':
          navigate('/loops')
          break
        case 'settings':
          navigate('/settings')
          break
        case 'admin-users':
          navigate('/admin/users')
          break
      }
    },
    [navigate, location.search]
  )

  const setFilter = useCallback(
    (newFilter: RouteFilter) => {
      const params = new URLSearchParams()

      switch (newFilter.type) {
        case 'workspace':
          if (newFilter.value) params.set('workspace', newFilter.value)
          break
        case 'project':
          if (newFilter.value) params.set('project', newFilter.value)
          break
        case 'archived':
          params.set('filter', 'archived')
          break
        // 'all' - no params needed
      }

      const search = params.toString()
      navigate(`/board${search ? `?${search}` : ''}`)
    },
    [navigate]
  )

  const openRunDetail = useCallback(
    (runId: string, tab?: RunDetailTab) => {
      const tabPath = tab ? `/${tab}` : ''
      navigate(`/board/${runId}${tabPath}${location.search}`)
    },
    [navigate, location.search]
  )

  const closeRunDetail = useCallback(() => {
    navigate(`/board${location.search}`)
  }, [navigate, location.search])

  const setRunDetailTab = useCallback(
    (tab: RunDetailTab) => {
      if (!selectedRunId) return
      navigate(`/board/${selectedRunId}/${tab}${location.search}`)
    },
    [navigate, selectedRunId, location.search]
  )

  const setSettingsTab = useCallback(
    (tab: SettingsTab) => {
      navigate(`/settings/${tab}`)
    },
    [navigate]
  )

  const setPreferencesGroup = useCallback(
    (group: PreferencesGroup) => {
      navigate(`/settings/preferences/${group}`)
    },
    [navigate]
  )

  return {
    // View mode
    viewMode,
    setViewMode,
    // Filtering
    filter,
    setFilter,
    // Modal state
    selectedRunId,
    selectedTab,
    openRunDetail,
    closeRunDetail,
    setRunDetailTab,
    // Settings
    settingsTab,
    setSettingsTab,
    preferencesGroup,
    setPreferencesGroup,
  }
}
