import { AlertCircle, Loader2, LogOut, Shield, User as UserIcon } from 'lucide-react'
import { type FormEvent, useCallback, useState } from 'react'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { ApiError, changePassword } from '@/lib/api'
import type { User, UserRole } from '@/lib/types'
import { cn } from '@/lib/utils'

const ROLE_BADGE: Record<UserRole, string> = {
  admin: 'bg-[var(--color-harvest)]/15 text-[var(--color-harvest)]',
  operator: 'bg-[var(--color-sky)]/15 text-[var(--color-sky)]',
  viewer: 'bg-[var(--color-stone)]/15 text-[var(--color-stone)]',
}

function avatarInitials(user: User): string {
  const source = user.display_name || user.username
  const parts = source.split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

/**
 * Inline change-password form rendered inside the menu popover.
 *
 * Used by both the user themselves (with `current_password` required) and
 * by admins changing their own password (admin field is sent regardless —
 * the backend ignores it for admins, but it's harmless to include).
 */
export function ChangePasswordForm({
  user,
  isAdmin,
  onClose,
}: {
  user: User
  isAdmin: boolean
  onClose: () => void
}) {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setSuccess(false)
    if (newPassword.length < 12) {
      setError('Password must be at least 12 characters.')
      return
    }
    if (newPassword !== confirm) {
      setError('New passwords do not match.')
      return
    }
    if (!isAdmin && !currentPassword) {
      setError('Current password is required.')
      return
    }
    setSubmitting(true)
    try {
      await changePassword(user.id, newPassword, isAdmin ? undefined : currentPassword)
      setSuccess(true)
      setCurrentPassword('')
      setNewPassword('')
      setConfirm('')
      // Auto-close after a beat so the success flash is visible.
      setTimeout(onClose, 1200)
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail)
      } else {
        setError(err instanceof Error ? err.message : 'Password change failed.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-2 p-3">
      <p className="text-caption uppercase tracking-widest text-[var(--color-stone)]/70 mb-1">
        Change password
      </p>

      {!isAdmin && (
        <input
          type="password"
          autoComplete="current-password"
          placeholder="Current password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          disabled={submitting}
          className="px-3 py-1.5 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
        />
      )}
      <input
        type="password"
        autoComplete="new-password"
        placeholder="New password (12+ chars)"
        value={newPassword}
        onChange={(e) => setNewPassword(e.target.value)}
        disabled={submitting}
        className="px-3 py-1.5 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
      />
      <input
        type="password"
        autoComplete="new-password"
        placeholder="Confirm new password"
        value={confirm}
        onChange={(e) => setConfirm(e.target.value)}
        disabled={submitting}
        className="px-3 py-1.5 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
      />

      {error && (
        <div className="flex items-start gap-1.5 px-2 py-1.5 bg-[var(--color-vermillion)]/10 border border-[var(--color-vermillion)]/20 rounded-sm">
          <AlertCircle className="w-3.5 h-3.5 text-[var(--color-vermillion)] shrink-0 mt-0.5" />
          <span className="text-caption text-[var(--color-vermillion)]">{error}</span>
        </div>
      )}
      {success && (
        <p className="text-caption text-[var(--color-jade)] px-2 py-1.5 bg-[var(--color-jade)]/10 rounded-sm">
          Password changed.
        </p>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="mt-1 px-3 py-1.5 text-caption uppercase tracking-widest bg-[var(--color-paper)] text-[var(--color-void)] rounded-sm hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center justify-center gap-1.5"
      >
        {submitting && <Loader2 className="w-3 h-3 animate-spin" />}
        {submitting ? 'Saving...' : 'Save'}
      </button>
    </form>
  )
}

/**
 * Header dropdown showing the logged-in user, role, and a logout / change-
 * password / admin shortcut. Only renders when `auth_enabled=true`.
 */
export function UserMenu({
  onOpenAdmin,
  onOpenAccountSettings,
}: {
  onOpenAdmin?: () => void
  onOpenAccountSettings?: () => void
}) {
  const { user, authEnabled, logout } = useCurrentUser()
  const [open, setOpen] = useState(false)

  const handleClose = useCallback(() => {
    setOpen(false)
  }, [])

  if (!authEnabled || !user) return null

  const isAdmin = user.role === 'admin'

  return (
    <div className="relative">
      <button
        type="button"
        className="flex items-center gap-2 px-2 py-1 rounded-sm hover:bg-[rgba(163,163,163,0.1)] transition-colors"
        onClick={() => setOpen((v) => !v)}
        title={`Signed in as ${user.display_name || user.username}`}
      >
        <span className="w-7 h-7 rounded-full bg-[var(--color-paper)]/10 text-[var(--color-paper)] text-caption flex items-center justify-center uppercase tracking-tight font-medium">
          {avatarInitials(user)}
        </span>
        <span className="hidden sm:inline text-body text-[var(--color-paper)]/90 max-w-[120px] truncate">
          {user.display_name || user.username}
        </span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={handleClose} />
          <div className="absolute right-0 top-full mt-1 w-80 z-50 bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-lg overflow-hidden">
            {/* Identity card */}
            <div className="px-3 py-3 border-b border-[rgba(163,163,163,0.1)]">
              <div className="flex items-center gap-2.5">
                <span className="w-9 h-9 rounded-full bg-[var(--color-paper)]/10 text-[var(--color-paper)] text-body flex items-center justify-center uppercase tracking-tight font-medium">
                  {avatarInitials(user)}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <p className="text-body text-[var(--color-paper)] truncate">
                      {user.display_name || user.username}
                    </p>
                    <span
                      className={cn(
                        'px-1.5 py-0.5 rounded-sm text-[10px] uppercase tracking-widest font-medium shrink-0',
                        ROLE_BADGE[user.role]
                      )}
                    >
                      {user.role}
                    </span>
                  </div>
                  <p className="text-caption text-[var(--color-stone)]/60 truncate">
                    {user.email || `@${user.username}`}
                  </p>
                </div>
              </div>
            </div>

            {/* Account controls now live in Settings → Account (D5 Phase 4). */}
            <div className="py-1">
              {onOpenAccountSettings && (
                <button
                  type="button"
                  className="w-full flex items-center gap-2 px-3 py-2 text-body text-[var(--color-paper)]/90 hover:bg-[rgba(163,163,163,0.08)] transition-colors text-left"
                  onClick={() => {
                    onOpenAccountSettings()
                    handleClose()
                  }}
                >
                  <UserIcon className="w-3.5 h-3.5 text-[var(--color-stone)]/70" />
                  Account settings
                </button>
              )}

              {isAdmin && onOpenAdmin && (
                <button
                  type="button"
                  className="w-full flex items-center gap-2 px-3 py-2 text-body text-[var(--color-paper)]/90 hover:bg-[rgba(163,163,163,0.08)] transition-colors text-left"
                  onClick={() => {
                    onOpenAdmin()
                    handleClose()
                  }}
                >
                  <Shield className="w-3.5 h-3.5 text-[var(--color-stone)]/70" />
                  Manage users
                </button>
              )}

              <div className="my-1 mx-3 border-t border-[rgba(163,163,163,0.08)]" />

              <button
                type="button"
                className="w-full flex items-center gap-2 px-3 py-2 text-body text-[var(--color-vermillion)] hover:bg-[var(--color-vermillion)]/10 transition-colors text-left"
                onClick={() => {
                  handleClose()
                  void logout()
                }}
              >
                <LogOut className="w-3.5 h-3.5" />
                Sign out
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
