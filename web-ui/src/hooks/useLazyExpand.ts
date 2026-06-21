import { useCallback } from 'react'

export interface UseLazyExpandOptions<T> {
  /** The currently-expanded key (or null). */
  expanded: string | null
  setExpanded: (key: string | null) => void
  /** Cache of already-loaded detail, keyed by the expand key. */
  cache: Record<string, T>
  setCache: (updater: (prev: Record<string, T>) => Record<string, T>) => void
  /** Loads the detail for a key (called once, when first expanded). */
  load: (key: string) => Promise<T>
  /** Optional loading indicator setter (key while loading, null when done). */
  setLoading?: (key: string | null) => void
  /** When false, toggle expansion but skip loading (mirrors the `&& run` guard). */
  enabled?: boolean
  /** Optional error handler (the call sites log their own specific message). */
  onError?: (err: unknown) => void
}

/**
 * Toggle-and-lazy-load handler shared by the commit / file / history expanders
 * in RunDetailDialog and RunDetailPage (#165). Clicking an already-expanded key
 * collapses it; expanding a new key loads its detail into the cache exactly
 * once. The per-expander differences (which state, which fetcher, whether a
 * loading indicator / `run` guard applies) are passed as options, so each call
 * site keeps its exact current behavior.
 */
export function useLazyExpand<T>(opts: UseLazyExpandOptions<T>): (key: string) => Promise<void> {
  const { expanded, setExpanded, cache, setCache, load, setLoading, enabled = true, onError } = opts
  return useCallback(
    async (key: string) => {
      if (expanded === key) {
        setExpanded(null)
        return
      }
      setExpanded(key)
      if (!enabled || cache[key]) return
      setLoading?.(key)
      try {
        const value = await load(key)
        setCache((prev) => ({ ...prev, [key]: value }))
      } catch (err) {
        onError?.(err)
      } finally {
        setLoading?.(null)
      }
    },
    [expanded, setExpanded, cache, setCache, load, setLoading, enabled, onError]
  )
}
