import { useState, useEffect, useCallback } from 'react'

export type FilterType = 'all' | 'workspace' | 'project'

export interface HashFilter {
  type: FilterType
  value: string | null
}

function parseHash(): HashFilter {
  const hash = window.location.hash.slice(1) // Remove #
  if (!hash) return { type: 'all', value: null }

  if (hash.startsWith('workspace:')) {
    return { type: 'workspace', value: hash.slice('workspace:'.length) }
  }
  if (hash.startsWith('project:')) {
    return { type: 'project', value: hash.slice('project:'.length) }
  }

  return { type: 'all', value: null }
}

function buildHash(filter: HashFilter): string {
  if (filter.type === 'all') return ''
  return `${filter.type}:${filter.value}`
}

export function useHashFilter() {
  const [filter, setFilterState] = useState<HashFilter>(parseHash)

  // Listen for hash changes (back/forward navigation)
  useEffect(() => {
    const handleHashChange = () => {
      setFilterState(parseHash())
    }

    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [])

  const setFilter = useCallback((newFilter: HashFilter) => {
    const hash = buildHash(newFilter)
    if (hash) {
      window.location.hash = hash
    } else {
      // Remove hash without triggering page jump
      history.pushState('', document.title, window.location.pathname + window.location.search)
    }
    setFilterState(newFilter)
  }, [])

  return { filter, setFilter }
}
