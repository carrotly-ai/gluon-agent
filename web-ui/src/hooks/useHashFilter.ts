import { useState, useEffect, useCallback } from 'react'

export type FilterType = 'all' | 'workspace' | 'project' | 'archived'
export type ViewMode = 'board' | 'usage' | 'settings'

export interface HashFilter {
  type: FilterType
  value: string | null
}

interface HashState {
  view: ViewMode
  filter: HashFilter
}

function parseHash(): HashState {
  const hash = window.location.hash.slice(1) // Remove #

  // Check for view-only hashes first
  if (hash === 'usage') {
    return { view: 'usage', filter: { type: 'all', value: null } }
  }
  if (hash === 'settings') {
    return { view: 'settings', filter: { type: 'all', value: null } }
  }

  // Board view with filters
  if (!hash) {
    return { view: 'board', filter: { type: 'all', value: null } }
  }
  if (hash.startsWith('workspace:')) {
    return { view: 'board', filter: { type: 'workspace', value: hash.slice('workspace:'.length) } }
  }
  if (hash.startsWith('project:')) {
    return { view: 'board', filter: { type: 'project', value: hash.slice('project:'.length) } }
  }
  if (hash === 'archived') {
    return { view: 'board', filter: { type: 'archived', value: null } }
  }

  return { view: 'board', filter: { type: 'all', value: null } }
}

function buildHash(state: HashState): string {
  // View-only hashes
  if (state.view === 'usage') return 'usage'
  if (state.view === 'settings') return 'settings'

  // Board view with filters
  if (state.filter.type === 'all') return ''
  if (state.filter.type === 'archived') return 'archived'
  return `${state.filter.type}:${state.filter.value}`
}

export function useHashFilter() {
  const [hashState, setHashState] = useState<HashState>(parseHash)

  // Listen for hash changes (back/forward navigation)
  useEffect(() => {
    const handleHashChange = () => {
      setHashState(parseHash())
    }

    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [])

  const setFilter = useCallback((newFilter: HashFilter) => {
    const newState: HashState = { view: 'board', filter: newFilter }
    const hash = buildHash(newState)
    if (hash) {
      window.location.hash = hash
    } else {
      // Remove hash without triggering page jump
      history.pushState('', document.title, window.location.pathname + window.location.search)
    }
    setHashState(newState)
  }, [])

  const setViewMode = useCallback((view: ViewMode) => {
    // When switching to board view, preserve current filter
    // When switching away from board, reset filter to all
    const newState: HashState = {
      view,
      filter: view === 'board' ? hashState.filter : { type: 'all', value: null }
    }
    const hash = buildHash(newState)
    if (hash) {
      window.location.hash = hash
    } else {
      history.pushState('', document.title, window.location.pathname + window.location.search)
    }
    setHashState(newState)
  }, [hashState.filter])

  return {
    filter: hashState.filter,
    setFilter,
    viewMode: hashState.view,
    setViewMode
  }
}
