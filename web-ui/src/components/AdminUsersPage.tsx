import {
  AlertCircle,
  Check,
  Key,
  Loader2,
  Plus,
  RefreshCw,
  Shield,
  Trash2,
  UserCog,
  X,
} from 'lucide-react'
import { type FormEvent, useCallback, useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import {
  ApiError,
  changePassword,
  createUser as createUserApi,
  disableUser,
  listUsers,
  updateUser,
} from '@/lib/api'
import type { User, UserRole } from '@/lib/types'
import { cn } from '@/lib/utils'

const ROLE_OPTIONS: UserRole[] = ['admin', 'operator', 'viewer']

const ROLE_BADGE: Record<UserRole, string> = {
  admin: 'bg-[var(--color-harvest)]/15 text-[var(--color-harvest)]',
  operator: 'bg-[var(--color-sky)]/15 text-[var(--color-sky)]',
  viewer: 'bg-[var(--color-stone)]/15 text-[var(--color-stone)]',
}

const ROLE_DESCRIPTION: Record<UserRole, string> = {
  admin: 'Full access — can manage users and all projects.',
  operator: 'Can create runs, decide approvals, and use the dashboard.',
  viewer: 'Read-only access to runs and projects.',
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleString()
  } catch {
    return iso
  }
}

/**
 * Admin-only page for managing users — list, create, edit role/email/
 * display name, disable/enable, and reset passwords.
 *
 * Shown via the `admin-users` view-mode in App.tsx, gated by `role === 'admin'`.
 */
export function AdminUsersPage() {
  const { user: me, refresh: refreshMe } = useCurrentUser()
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [includeDisabled, setIncludeDisabled] = useState(false)
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [editingUserId, setEditingUserId] = useState<string | null>(null)
  const [resetPasswordForUserId, setResetPasswordForUserId] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const resp = await listUsers(includeDisabled)
      setUsers(resp.users)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load users.')
    } finally {
      setLoading(false)
    }
  }, [includeDisabled])

  useEffect(() => {
    void refresh()
  }, [refresh])

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[rgba(163,163,163,0.1)]">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-[var(--color-stone)]" />
          <h1 className="text-display text-[var(--color-paper)] tracking-tight">User management</h1>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-caption text-[var(--color-stone)] cursor-pointer">
            <input
              type="checkbox"
              checked={includeDisabled}
              onChange={(e) => setIncludeDisabled(e.target.checked)}
              className="accent-[var(--color-paper)]"
            />
            Show disabled
          </label>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            className="p-1.5 rounded-sm hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/80 hover:text-[var(--color-stone)] transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
          </button>
          <button
            type="button"
            onClick={() => setShowCreateForm((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-caption uppercase tracking-widest bg-[var(--color-paper)] text-[var(--color-void)] rounded-sm hover:opacity-90 transition-opacity"
          >
            <Plus className="w-3.5 h-3.5" />
            New user
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto px-6 py-6 flex flex-col gap-4">
          {error && (
            <div className="flex items-start gap-2 p-3 bg-[var(--color-vermillion)]/10 border border-[var(--color-vermillion)]/20 rounded-sm">
              <AlertCircle className="w-4 h-4 text-[var(--color-vermillion)] shrink-0 mt-0.5" />
              <span className="text-body text-[var(--color-vermillion)]">{error}</span>
            </div>
          )}

          {showCreateForm && (
            <CreateUserForm
              onCancel={() => setShowCreateForm(false)}
              onCreated={() => {
                setShowCreateForm(false)
                void refresh()
              }}
            />
          )}

          {loading && users.length === 0 ? (
            <div className="flex items-center justify-center py-20 text-[var(--color-stone)]/40">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
          ) : users.length === 0 ? (
            <div className="text-center py-20 text-body text-[var(--color-stone)]/40">
              No users found.
            </div>
          ) : (
            <div className="rounded-sm border border-[rgba(163,163,163,0.15)] overflow-hidden">
              <table className="w-full">
                <thead>
                  <tr className="bg-[var(--color-ink)] border-b border-[rgba(163,163,163,0.1)]">
                    <th className="text-left px-3 py-2 text-caption uppercase tracking-widest text-[var(--color-stone)] font-normal">
                      User
                    </th>
                    <th className="text-left px-3 py-2 text-caption uppercase tracking-widest text-[var(--color-stone)] font-normal">
                      Role
                    </th>
                    <th className="text-left px-3 py-2 text-caption uppercase tracking-widest text-[var(--color-stone)] font-normal hidden md:table-cell">
                      Created
                    </th>
                    <th className="text-left px-3 py-2 text-caption uppercase tracking-widest text-[var(--color-stone)] font-normal hidden lg:table-cell">
                      Last seen
                    </th>
                    <th className="text-right px-3 py-2 text-caption uppercase tracking-widest text-[var(--color-stone)] font-normal">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <UserRow
                      key={u.id}
                      user={u}
                      isMe={me?.id === u.id}
                      isEditing={editingUserId === u.id}
                      isResettingPassword={resetPasswordForUserId === u.id}
                      onEdit={() => setEditingUserId(u.id)}
                      onCancelEdit={() => setEditingUserId(null)}
                      onSaved={async () => {
                        setEditingUserId(null)
                        await refresh()
                        if (me?.id === u.id) await refreshMe()
                      }}
                      onDisable={async () => {
                        try {
                          await disableUser(u.id)
                          await refresh()
                        } catch (err) {
                          setError(err instanceof Error ? err.message : 'Disable failed.')
                        }
                      }}
                      onStartResetPassword={() => setResetPasswordForUserId(u.id)}
                      onCancelResetPassword={() => setResetPasswordForUserId(null)}
                      onResetPasswordDone={() => setResetPasswordForUserId(null)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p className="text-caption text-[var(--color-stone)]/60 mt-2">
            <strong className="text-[var(--color-stone)]/80">Note:</strong> disabling a user is a
            soft delete — it preserves all their attribution links (runs, tasks, approvals) but
            invalidates their sessions.
          </p>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Row + edit form
// ---------------------------------------------------------------------------

function UserRow({
  user,
  isMe,
  isEditing,
  isResettingPassword,
  onEdit,
  onCancelEdit,
  onSaved,
  onDisable,
  onStartResetPassword,
  onCancelResetPassword,
  onResetPasswordDone,
}: {
  user: User
  isMe: boolean
  isEditing: boolean
  isResettingPassword: boolean
  onEdit: () => void
  onCancelEdit: () => void
  onSaved: () => void | Promise<void>
  onDisable: () => void | Promise<void>
  onStartResetPassword: () => void
  onCancelResetPassword: () => void
  onResetPasswordDone: () => void
}) {
  const [showDisableConfirm, setShowDisableConfirm] = useState(false)

  if (isEditing) {
    return (
      <tr className="border-b border-[rgba(163,163,163,0.06)] bg-[var(--color-ink)]/50">
        <td colSpan={5} className="px-3 py-3">
          <EditUserForm user={user} onCancel={onCancelEdit} onSaved={onSaved} />
        </td>
      </tr>
    )
  }

  if (isResettingPassword) {
    return (
      <tr className="border-b border-[rgba(163,163,163,0.06)] bg-[var(--color-ink)]/50">
        <td colSpan={5} className="px-3 py-3">
          <AdminResetPasswordForm
            user={user}
            onCancel={onCancelResetPassword}
            onDone={onResetPasswordDone}
          />
        </td>
      </tr>
    )
  }

  return (
    <tr
      className={cn(
        'border-b border-[rgba(163,163,163,0.06)] last:border-b-0 hover:bg-[rgba(163,163,163,0.04)] transition-colors',
        user.disabled && 'opacity-50'
      )}
    >
      <td className="px-3 py-2.5">
        <div className="flex flex-col">
          <div className="flex items-center gap-1.5">
            <span className="text-body text-[var(--color-paper)]">
              {user.display_name || user.username}
            </span>
            {isMe && (
              <span className="text-micro uppercase tracking-widest text-[var(--color-stone)]/60 px-1.5 py-0.5 bg-[rgba(163,163,163,0.1)] rounded-sm">
                You
              </span>
            )}
            {user.disabled && (
              <span className="text-micro uppercase tracking-widest text-[var(--color-vermillion)] px-1.5 py-0.5 bg-[var(--color-vermillion)]/10 rounded-sm">
                Disabled
              </span>
            )}
          </div>
          <span className="text-caption text-[var(--color-stone)]/60">
            @{user.username}
            {user.email && ` · ${user.email}`}
          </span>
        </div>
      </td>
      <td className="px-3 py-2.5">
        <span
          className={cn(
            'inline-block px-1.5 py-0.5 rounded-sm text-micro uppercase tracking-widest font-medium',
            ROLE_BADGE[user.role]
          )}
        >
          {user.role}
        </span>
      </td>
      <td className="px-3 py-2.5 text-caption text-[var(--color-stone)]/70 hidden md:table-cell">
        {formatDate(user.created_at)}
      </td>
      <td className="px-3 py-2.5 text-caption text-[var(--color-stone)]/70 hidden lg:table-cell">
        {formatDate(user.last_login_at)}
      </td>
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-1 justify-end">
          <button
            type="button"
            onClick={onEdit}
            className="p-1.5 rounded-sm hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/80 hover:text-[var(--color-stone)] transition-colors"
            title="Edit"
          >
            <UserCog className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={onStartResetPassword}
            className="p-1.5 rounded-sm hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/80 hover:text-[var(--color-stone)] transition-colors"
            aria-label="Reset password"
            title="Reset password"
          >
            <Key className="w-3.5 h-3.5" />
          </button>
          {!user.disabled && !isMe && (
            <button
              type="button"
              onClick={() => setShowDisableConfirm(true)}
              className="p-1.5 rounded-sm transition-colors hover:bg-[var(--color-vermillion)]/10 text-[var(--color-stone)]/80 hover:text-[var(--color-vermillion)]"
              aria-label="Disable user"
              title="Disable user"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </td>

      <DisableUserDialog
        username={user.username}
        open={showDisableConfirm}
        onOpenChange={setShowDisableConfirm}
        onConfirm={async () => {
          setShowDisableConfirm(false)
          await onDisable()
        }}
      />
    </tr>
  )
}

/**
 * Explicit confirmation for disabling a user. Replaces the old
 * click→arm→click-again pattern (which silently disarmed on blur and was
 * easy to mis-fire). A Radix Dialog forces a deliberate Cancel / Disable
 * choice with the consequence spelled out.
 */
function DisableUserDialog({
  username,
  open,
  onOpenChange,
  onConfirm,
}: {
  username: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void | Promise<void>
}) {
  const [submitting, setSubmitting] = useState(false)

  const handleConfirm = async () => {
    setSubmitting(true)
    try {
      await onConfirm()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Disable @{username}?</DialogTitle>
          <DialogDescription>
            They will lose access immediately. Their run history is preserved.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
            className="px-3 py-1.5 text-caption uppercase tracking-widest bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)] rounded-sm hover:bg-[rgba(163,163,163,0.2)] disabled:opacity-50 transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void handleConfirm()}
            disabled={submitting}
            className="px-3 py-1.5 text-caption uppercase tracking-widest bg-[var(--color-vermillion)] text-[var(--color-void)] rounded-sm hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center justify-center gap-1.5"
          >
            {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            Disable
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EditUserForm({
  user,
  onCancel,
  onSaved,
}: {
  user: User
  onCancel: () => void
  onSaved: () => void | Promise<void>
}) {
  const [displayName, setDisplayName] = useState(user.display_name)
  const [email, setEmail] = useState(user.email ?? '')
  const [role, setRole] = useState<UserRole>(user.role)
  const [disabled, setDisabled] = useState(user.disabled)
  // D5 Phase 4 — chat ID inputs are stored as strings to allow the empty
  // string to mean "clear the link". On submit we coerce to int / null.
  const [telegramId, setTelegramId] = useState(
    user.telegram_user_id !== null ? String(user.telegram_user_id) : ''
  )
  const [discordId, setDiscordId] = useState(
    user.discord_user_id !== null ? String(user.discord_user_id) : ''
  )
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const parseChatId = (raw: string, label: string): number | null | string => {
    const trimmed = raw.trim()
    if (trimmed === '') return 0 // 0 = clear (server treats falsy as null)
    if (!/^\d+$/.test(trimmed)) return `${label} must be a positive integer`
    const n = Number(trimmed)
    if (n <= 0) return `${label} must be a positive integer`
    return n
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    const tg = parseChatId(telegramId, 'Telegram user ID')
    if (typeof tg === 'string') {
      setError(tg)
      return
    }
    const dc = parseChatId(discordId, 'Discord user ID')
    if (typeof dc === 'string') {
      setError(dc)
      return
    }
    setSubmitting(true)
    try {
      await updateUser(user.id, {
        display_name: displayName,
        email: email.trim() || null,
        role,
        disabled,
        telegram_user_id: tg,
        discord_user_id: dc,
      })
      await onSaved()
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail)
      else setError(err instanceof Error ? err.message : 'Save failed.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
            Display name
          </span>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            disabled={submitting}
            className="px-3 py-1.5 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
            Email
          </span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={submitting}
            className="px-3 py-1.5 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
          />
        </label>
      </div>

      <div>
        <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
          Role
        </span>
        <div className="flex flex-wrap gap-2 mt-1">
          {ROLE_OPTIONS.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setRole(r)}
              className={cn(
                'px-2.5 py-1 rounded-sm text-caption uppercase tracking-widest transition-colors',
                role === r
                  ? 'bg-[var(--color-paper)] text-[var(--color-void)]'
                  : 'bg-[rgba(163,163,163,0.08)] text-[var(--color-stone)] hover:bg-[rgba(163,163,163,0.15)]'
              )}
            >
              {r}
            </button>
          ))}
        </div>
        <p className="text-caption text-[var(--color-stone)]/60 mt-1.5">{ROLE_DESCRIPTION[role]}</p>
      </div>

      {/* D5 Phase 4 — chat-account binding (admin pre-registration). */}
      <div>
        <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
          Connected chat accounts
        </span>
        <p className="text-caption text-[var(--color-stone)]/60 mt-0.5 mb-2">
          Bind this user's Telegram / Discord numeric ID so approvals and runs from chat are
          attributed correctly. Leave blank to clear.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]/80">
              Telegram user ID
            </span>
            <input
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              value={telegramId}
              onChange={(e) => setTelegramId(e.target.value)}
              disabled={submitting}
              placeholder="e.g. 123456789"
              className="px-3 py-1.5 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]/80">
              Discord user ID
            </span>
            <input
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              value={discordId}
              onChange={(e) => setDiscordId(e.target.value)}
              disabled={submitting}
              placeholder="e.g. 234567890123456789"
              className="px-3 py-1.5 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
            />
          </label>
        </div>
      </div>

      <label className="flex items-center gap-2 text-body text-[var(--color-paper)]/90 cursor-pointer">
        <input
          type="checkbox"
          checked={disabled}
          onChange={(e) => setDisabled(e.target.checked)}
          disabled={submitting}
          className="accent-[var(--color-vermillion)]"
        />
        Disabled — block this user from signing in.
      </label>

      {error && (
        <div className="flex items-start gap-2 p-2 bg-[var(--color-vermillion)]/10 border border-[var(--color-vermillion)]/20 rounded-sm">
          <AlertCircle className="w-3.5 h-3.5 text-[var(--color-vermillion)] shrink-0 mt-0.5" />
          <span className="text-caption text-[var(--color-vermillion)]">{error}</span>
        </div>
      )}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="px-3 py-1.5 text-caption uppercase tracking-widest bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)] rounded-sm hover:bg-[rgba(163,163,163,0.2)] disabled:opacity-50 transition-colors flex items-center gap-1.5"
        >
          <X className="w-3.5 h-3.5" />
          Cancel
        </button>
        <button
          type="submit"
          disabled={submitting}
          className="px-3 py-1.5 text-caption uppercase tracking-widest bg-[var(--color-paper)] text-[var(--color-void)] rounded-sm hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-1.5"
        >
          {submitting ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Check className="w-3.5 h-3.5" />
          )}
          Save
        </button>
      </div>
    </form>
  )
}

function AdminResetPasswordForm({
  user,
  onCancel,
  onDone,
}: {
  user: User
  onCancel: () => void
  onDone: () => void
}) {
  const [newPassword, setNewPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (newPassword.length < 12) {
      setError('Password must be at least 12 characters.')
      return
    }
    setSubmitting(true)
    try {
      // Admin path: omit current_password.
      await changePassword(user.id, newPassword)
      onDone()
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail)
      else setError(err instanceof Error ? err.message : 'Reset failed.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-2 max-w-sm">
      <p className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
        Reset password — {user.username}
      </p>
      <p className="text-caption text-[var(--color-stone)]/70">
        The user&apos;s active sessions will be invalidated. Share the new password securely.
      </p>
      <input
        type="password"
        autoComplete="new-password"
        placeholder="New password (12+ chars)"
        value={newPassword}
        onChange={(e) => setNewPassword(e.target.value)}
        disabled={submitting}
        // biome-ignore lint/a11y/noAutofocus: form is opened via explicit admin action; first field is the obvious focus target.
        autoFocus
        className="px-3 py-1.5 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
      />
      {error && (
        <div className="flex items-start gap-2 p-2 bg-[var(--color-vermillion)]/10 border border-[var(--color-vermillion)]/20 rounded-sm">
          <AlertCircle className="w-3.5 h-3.5 text-[var(--color-vermillion)] shrink-0 mt-0.5" />
          <span className="text-caption text-[var(--color-vermillion)]">{error}</span>
        </div>
      )}
      <div className="flex justify-end gap-2 mt-1">
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="px-3 py-1.5 text-caption uppercase tracking-widest bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)] rounded-sm hover:bg-[rgba(163,163,163,0.2)] disabled:opacity-50 transition-colors"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={submitting}
          className="px-3 py-1.5 text-caption uppercase tracking-widest bg-[var(--color-paper)] text-[var(--color-void)] rounded-sm hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-1.5"
        >
          {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          Reset
        </button>
      </div>
    </form>
  )
}

// ---------------------------------------------------------------------------
// Create-user form
// ---------------------------------------------------------------------------

function CreateUserForm({ onCancel, onCreated }: { onCancel: () => void; onCreated: () => void }) {
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<UserRole>('operator')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!username.trim()) {
      setError('Username is required.')
      return
    }
    if (password.length < 12) {
      setError('Password must be at least 12 characters.')
      return
    }
    setSubmitting(true)
    try {
      await createUserApi({
        username: username.trim(),
        password,
        display_name: displayName.trim() || null,
        email: email.trim() || null,
        role,
      })
      onCreated()
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail)
      else setError(err instanceof Error ? err.message : 'Create failed.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-3 p-4 rounded-sm border border-[rgba(163,163,163,0.15)] bg-[var(--color-ink)]/50"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
          Create new user
        </h2>
        <button
          type="button"
          onClick={onCancel}
          className="p-1 rounded-sm hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/60"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
            Username <span className="text-[var(--color-vermillion)]">*</span>
          </span>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={submitting}
            // biome-ignore lint/a11y/noAutofocus: dialog-like inline form opened via explicit admin click; first field is the natural focus target.
            autoFocus
            className="px-3 py-1.5 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
            Display name
          </span>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            disabled={submitting}
            placeholder={username || 'Same as username'}
            className="px-3 py-1.5 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
            Email
          </span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={submitting}
            className="px-3 py-1.5 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
            Password <span className="text-[var(--color-vermillion)]">*</span>
          </span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting}
            placeholder="12+ characters"
            className="px-3 py-1.5 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30 disabled:opacity-50"
          />
        </label>
      </div>

      <div>
        <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
          Role
        </span>
        <div className="flex flex-wrap gap-2 mt-1">
          {ROLE_OPTIONS.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setRole(r)}
              className={cn(
                'px-2.5 py-1 rounded-sm text-caption uppercase tracking-widest transition-colors',
                role === r
                  ? 'bg-[var(--color-paper)] text-[var(--color-void)]'
                  : 'bg-[rgba(163,163,163,0.08)] text-[var(--color-stone)] hover:bg-[rgba(163,163,163,0.15)]'
              )}
            >
              {r}
            </button>
          ))}
        </div>
        <p className="text-caption text-[var(--color-stone)]/60 mt-1.5">{ROLE_DESCRIPTION[role]}</p>
      </div>

      {error && (
        <div className="flex items-start gap-2 p-2 bg-[var(--color-vermillion)]/10 border border-[var(--color-vermillion)]/20 rounded-sm">
          <AlertCircle className="w-3.5 h-3.5 text-[var(--color-vermillion)] shrink-0 mt-0.5" />
          <span className="text-caption text-[var(--color-vermillion)]">{error}</span>
        </div>
      )}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="px-3 py-1.5 text-caption uppercase tracking-widest bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)] rounded-sm hover:bg-[rgba(163,163,163,0.2)] disabled:opacity-50 transition-colors"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={submitting}
          className="px-3 py-1.5 text-caption uppercase tracking-widest bg-[var(--color-paper)] text-[var(--color-void)] rounded-sm hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-1.5"
        >
          {submitting ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Plus className="w-3.5 h-3.5" />
          )}
          Create
        </button>
      </div>
    </form>
  )
}
