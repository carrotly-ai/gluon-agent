import {
  createContext,
  createElement,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'
import { ApiError, fetchMe, login as loginApi, logout as logoutApi } from '@/lib/api'
import type { User } from '@/lib/types'

/**
 * Auth state shared across the app.
 *
 * `user` is null only during the very first load (before /auth/me responds).
 * After that, `user` is *always* set — to the SYSTEM_USER when auth is off,
 * or to the SYSTEM_USER + `auth_enabled=true` when auth is on but no session
 * is present (the App shell uses that combination to gate the login screen).
 */
export interface CurrentUserState {
  user: User | null
  authEnabled: boolean
  /** True until the first /auth/me response lands. */
  loading: boolean
  /** True when no session exists and `authEnabled` is true. */
  needsLogin: boolean

  login(username: string, password: string): Promise<void>
  logout(): Promise<void>
  /** Re-fetch /auth/me — call after admin actions that may rotate sessions. */
  refresh(): Promise<void>
  /** Local update — used after PATCH /api/users/{me} to reflect changes immediately. */
  updateLocalUser(user: User): void
}

const defaultState: CurrentUserState = {
  user: null,
  authEnabled: false,
  loading: true,
  needsLogin: false,
  login: async () => {},
  logout: async () => {},
  refresh: async () => {},
  updateLocalUser: () => {},
}

const SYSTEM_USER_ID = '00000000-0000-0000-0000-000000000000'

const CurrentUserContext = createContext<CurrentUserState>(defaultState)

export function useCurrentUser(): CurrentUserState {
  return useContext(CurrentUserContext)
}

/**
 * Hook used by the top-level provider — call once at the app root.
 *
 * Spreads to a value that satisfies `CurrentUserState` and a `Provider`
 * convenience. Pattern matches `useNotificationCenterProvider`.
 */
export function useCurrentUserProvider(): CurrentUserState {
  const [user, setUser] = useState<User | null>(null)
  const [authEnabled, setAuthEnabled] = useState(false)
  const [loading, setLoading] = useState(true)
  const inflight = useRef<Promise<void> | null>(null)

  const refresh = useCallback(async () => {
    if (inflight.current) return inflight.current
    const p = (async () => {
      try {
        const me = await fetchMe()
        setUser(me.user)
        setAuthEnabled(me.auth_enabled)
      } catch (err) {
        // /auth/me should never 401 (it falls back to SYSTEM_USER), but if
        // the backend is unreachable we set a sane default and keep going.
        console.error('[useCurrentUser] failed to fetch /auth/me', err)
        setUser(null)
        setAuthEnabled(false)
      } finally {
        setLoading(false)
      }
    })()
    inflight.current = p
    try {
      await p
    } finally {
      inflight.current = null
    }
  }, [])

  // Initial fetch.
  useEffect(() => {
    void refresh()
  }, [refresh])

  const login = useCallback(
    async (username: string, password: string) => {
      try {
        await loginApi(username, password)
        await refresh()
      } catch (err) {
        if (err instanceof ApiError) throw err
        throw new ApiError(0, err instanceof Error ? err.message : 'login failed')
      }
    },
    [refresh]
  )

  const logout = useCallback(async () => {
    try {
      await logoutApi()
    } catch {
      // Even if the server logout fails (e.g. session already expired),
      // we want to clear local state so the user lands on the login screen.
    }
    await refresh()
  }, [refresh])

  const updateLocalUser = useCallback((u: User) => {
    setUser(u)
  }, [])

  // When auth is on and the user is the SYSTEM_USER fallback, the client
  // needs to show the login screen. Auth-off uses SYSTEM_USER as a real
  // user (single-user mode) and never shows login.
  const needsLogin = authEnabled && (user === null || user.id === SYSTEM_USER_ID)

  return {
    user,
    authEnabled,
    loading,
    needsLogin,
    login,
    logout,
    refresh,
    updateLocalUser,
  }
}

export const CurrentUserContextProvider = CurrentUserContext.Provider

/**
 * Top-level provider — wrap the app once with this to make `useCurrentUser`
 * available everywhere. Internally calls `useCurrentUserProvider` and
 * publishes the value through context.
 */
export function CurrentUserProvider({ children }: { children: ReactNode }) {
  const value = useCurrentUserProvider()
  return createElement(CurrentUserContextProvider, { value }, children)
}
