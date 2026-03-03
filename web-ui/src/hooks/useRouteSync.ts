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

// Valid tabs for SettingsPage
export type SettingsTab = 'workspaces' | 'projects' | 'preferences' | 'formulas'

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

  // Settings tab from route params (for /settings/:tab)
  const settingsTab = useMemo((): SettingsTab => {
    if (viewMode !== 'settings') return 'workspaces'
    const path = location.pathname
    if (path === '/settings/projects') return 'projects'
    if (path === '/settings/preferences') return 'preferences'
    if (path === '/settings/formulas') return 'formulas'
    return 'workspaces'
  }, [viewMode, location.pathname])

  // Navigation functions
  const setViewMode = useCallback(
    (view: 'board' | 'activity' | 'queue' | 'merge' | 'usage' | 'settings') => {
      switch (view) {
        case 'board':
          navigate(`/board${location.search}`)
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
        case 'settings':
          navigate('/settings')
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
  }
}
