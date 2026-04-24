import { AlertCircle, Loader2, LogIn } from 'lucide-react'
import { type FormEvent, useState } from 'react'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { ApiError } from '@/lib/api'

/**
 * Full-screen login page rendered when `auth_enabled=true` and no session
 * is present. Submits to /api/auth/login and lets `useCurrentUser` re-fetch
 * /auth/me on success.
 */
export function LoginPage() {
  const { login } = useCurrentUser()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!username.trim() || !password) {
      setError('Username and password are required.')
      return
    }
    setSubmitting(true)
    try {
      await login(username.trim(), password)
      // useCurrentUser.refresh() flips needsLogin to false; the App shell
      // will swap us out automatically.
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('Invalid username or password.')
      } else if (err instanceof ApiError && err.status === 403) {
        setError('This account has been disabled.')
      } else if (err instanceof ApiError && err.status === 400) {
        setError(err.detail)
      } else {
        setError(err instanceof Error ? err.message : 'Login failed.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--color-void)] px-6">
      <div className="w-full max-w-sm">
        {/* Brand header */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-12 h-12 rounded-full bg-[var(--color-paper)]/10 flex items-center justify-center mb-3">
            <LogIn className="w-5 h-5 text-[var(--color-paper)]" />
          </div>
          <h1 className="text-display text-[var(--color-paper)] tracking-tight">Gluon</h1>
          <p className="text-caption text-[var(--color-stone)] mt-1 uppercase tracking-widest">
            Sign in to continue
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="flex flex-col gap-4 p-6 rounded-sm border border-[rgba(163,163,163,0.15)] bg-[var(--color-ink)]"
          aria-label="Login form"
        >
          <label className="flex flex-col gap-1.5">
            <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
              Username
            </span>
            <input
              type="text"
              autoComplete="username"
              // biome-ignore lint/a11y/noAutofocus: dedicated full-screen login route; the first field is the natural focus target.
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={submitting}
              className="w-full px-3 py-2 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
              Password
            </span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
              className="w-full px-3 py-2 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
            />
          </label>

          {error && (
            <div
              role="alert"
              className="flex items-start gap-2 p-3 bg-[var(--color-vermillion)]/10 border border-[var(--color-vermillion)]/20 rounded-sm"
            >
              <AlertCircle className="w-4 h-4 text-[var(--color-vermillion)] shrink-0 mt-0.5" />
              <span className="text-body text-[var(--color-vermillion)]">{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full mt-2 px-3 py-2.5 text-caption uppercase tracking-widest bg-[var(--color-paper)] text-[var(--color-void)] rounded-sm hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center justify-center gap-2"
          >
            {submitting ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                Signing in...
              </>
            ) : (
              'Sign in'
            )}
          </button>
        </form>

        <p className="text-caption text-[var(--color-stone)]/60 text-center mt-6">
          Forgot your password? Ask an admin to reset it for you.
        </p>
      </div>
    </div>
  )
}
