import { useCallback, useEffect, useState } from 'react'

type ThemePreference = 'dark' | 'light' | 'system'
type ResolvedTheme = 'dark' | 'light'

const STORAGE_KEY = 'gluon-theme'

function getSystemTheme(): ResolvedTheme {
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

function resolveTheme(pref: ThemePreference): ResolvedTheme {
  return pref === 'system' ? getSystemTheme() : pref
}

function getStoredPreference(): ThemePreference {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  return 'system'
}

function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement
  if (resolved === 'light') {
    root.classList.add('light')
  } else {
    root.classList.remove('light')
  }
  root.style.colorScheme = resolved
}

export function useTheme() {
  const [preference, setPreference] = useState<ThemePreference>(getStoredPreference)
  const [resolved, setResolved] = useState<ResolvedTheme>(() => resolveTheme(getStoredPreference()))

  useEffect(() => {
    const newResolved = resolveTheme(preference)
    setResolved(newResolved)
    applyTheme(newResolved)
  }, [preference])

  useEffect(() => {
    if (preference !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: light)')
    const handler = () => {
      const newResolved = getSystemTheme()
      setResolved(newResolved)
      applyTheme(newResolved)
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [preference])

  const setTheme = useCallback((pref: ThemePreference) => {
    setPreference(pref)
    localStorage.setItem(STORAGE_KEY, pref)
  }, [])

  // Legacy toggle for backwards compat — cycles dark → light → system
  const toggleTheme = useCallback(() => {
    setTheme(preference === 'dark' ? 'light' : preference === 'light' ? 'system' : 'dark')
  }, [preference, setTheme])

  return { theme: resolved, preference, setTheme, toggleTheme }
}
