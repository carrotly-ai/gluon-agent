import type { LucideIcon } from 'lucide-react'
import {
  AlertCircle,
  CheckCircle2,
  Copy,
  Link as LinkIcon,
  Loader2,
  MessageCircle,
  Send,
  Unlink,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { ApiError, createLinkCode, fetchMe, unlinkMyChat } from '@/lib/api'
import type { LinkTransport } from '@/lib/types'

/**
 * Self-serve "Connected accounts" panel (D5 Phase 4).
 *
 * Lives inside `UserMenu`. Lets the logged-in user generate a one-time
 * code, paste it into their Telegram/Discord bot, and bind that chat
 * identity to their Gluon account — all without admin intervention.
 *
 * UX flow:
 *  1. Click "Link Telegram" → generate code via POST /auth/link-codes
 *  2. Show the code with a copy button + 10-min countdown + how-to hint
 *  3. Poll /auth/me every 3s to detect when the bot consumes it
 *  4. On detection, flip to "linked" state automatically
 *  5. "Unlink" calls DELETE /auth/links/{transport}
 */

type TransportConfig = {
  transport: LinkTransport
  label: string
  // Lucide glyph — monochrome, matches the surrounding aesthetic (no emoji colour pops).
  Icon: LucideIcon
  // The bot command pattern — used in the "how to redeem" hint.
  command: string
}

const TRANSPORTS: TransportConfig[] = [
  { transport: 'telegram', label: 'Telegram', Icon: Send, command: '/link' },
  { transport: 'discord', label: 'Discord', Icon: MessageCircle, command: 'link-account' },
]

interface ActiveCode {
  transport: LinkTransport
  code: string
  expiresAt: Date
}

export function ConnectedAccountsSection() {
  const { user, refresh: refreshMe } = useCurrentUser()
  const [activeCode, setActiveCode] = useState<ActiveCode | null>(null)
  const [generating, setGenerating] = useState<LinkTransport | null>(null)
  const [unlinking, setUnlinking] = useState<LinkTransport | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Poll /auth/me while a code is active so the UI flips automatically when
  // the bot consumes it. 3s cadence is responsive without being chatty —
  // the user is actively waiting.
  useEffect(() => {
    if (!activeCode) return
    const interval = setInterval(() => {
      void fetchMe().then(() => refreshMe())
    }, 3000)
    return () => clearInterval(interval)
  }, [activeCode, refreshMe])

  // Whenever the user's bound IDs change, drop the active code if its
  // transport just got linked.
  useEffect(() => {
    if (!activeCode || !user) return
    const justLinked =
      (activeCode.transport === 'telegram' && user.telegram_user_id !== null) ||
      (activeCode.transport === 'discord' && user.discord_user_id !== null)
    if (justLinked) {
      setActiveCode(null)
    }
  }, [activeCode, user])

  // Auto-clear expired codes (visual cue).
  useEffect(() => {
    if (!activeCode) return
    const remaining = activeCode.expiresAt.getTime() - Date.now()
    if (remaining <= 0) {
      setActiveCode(null)
      return
    }
    const t = setTimeout(() => setActiveCode(null), remaining)
    return () => clearTimeout(t)
  }, [activeCode])

  const handleGenerate = useCallback(async (transport: LinkTransport) => {
    setError(null)
    setGenerating(transport)
    try {
      const resp = await createLinkCode(transport)
      setActiveCode({
        transport,
        code: resp.code,
        expiresAt: new Date(resp.expires_at),
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to generate code.')
    } finally {
      setGenerating(null)
    }
  }, [])

  const handleUnlink = useCallback(
    async (transport: LinkTransport) => {
      setError(null)
      setUnlinking(transport)
      try {
        await unlinkMyChat(transport)
        await refreshMe()
        // If we were waiting on a code for this transport, drop it.
        if (activeCode?.transport === transport) setActiveCode(null)
      } catch (err) {
        setError(err instanceof ApiError ? err.detail : 'Failed to unlink.')
      } finally {
        setUnlinking(null)
      }
    },
    [activeCode, refreshMe]
  )

  if (!user) return null

  return (
    <div className="px-3 py-3 border-t border-[rgba(163,163,163,0.08)]">
      <p className="text-caption uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
        Connected accounts
      </p>

      <div className="flex flex-col gap-1.5">
        {TRANSPORTS.map((tc) => {
          const linked = tc.transport === 'telegram' ? user.telegram_user_id : user.discord_user_id
          const isActiveTransport = activeCode?.transport === tc.transport

          return (
            <div key={tc.transport} className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <span className="w-5 flex justify-center shrink-0" aria-hidden>
                  <tc.Icon className="w-4 h-4 text-[var(--color-stone)]" />
                </span>
                <span className="flex-1 text-body text-[var(--color-paper)]/90">{tc.label}</span>
                {linked !== null ? (
                  <>
                    <span className="text-caption text-[var(--color-jade)] flex items-center gap-1">
                      <CheckCircle2 className="w-3 h-3" />
                      Linked
                    </span>
                    <button
                      type="button"
                      onClick={() => handleUnlink(tc.transport)}
                      disabled={unlinking === tc.transport}
                      className="p-1 rounded-sm hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/70 hover:text-[var(--color-vermillion)] disabled:opacity-50 transition-colors"
                      title={`Unlink ${tc.label}`}
                    >
                      {unlinking === tc.transport ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Unlink className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => handleGenerate(tc.transport)}
                    disabled={generating === tc.transport || isActiveTransport}
                    className="px-2 py-0.5 text-caption uppercase tracking-widest bg-[rgba(163,163,163,0.1)] text-[var(--color-paper)]/90 rounded-sm hover:bg-[rgba(163,163,163,0.2)] disabled:opacity-50 transition-colors flex items-center gap-1"
                  >
                    {generating === tc.transport ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <LinkIcon className="w-3 h-3" />
                    )}
                    Link
                  </button>
                )}
              </div>

              {isActiveTransport && activeCode && (
                <CodeCard
                  code={activeCode.code}
                  expiresAt={activeCode.expiresAt}
                  hint={`Send \`${tc.command} ${activeCode.code}\` to the bot on ${tc.label}.`}
                  onCancel={() => setActiveCode(null)}
                />
              )}
            </div>
          )
        })}
      </div>

      {error && (
        <div className="mt-2 flex items-start gap-1.5 px-2 py-1.5 bg-[var(--color-vermillion)]/10 border border-[var(--color-vermillion)]/20 rounded-sm">
          <AlertCircle className="w-3.5 h-3.5 text-[var(--color-vermillion)] shrink-0 mt-0.5" />
          <span className="text-caption text-[var(--color-vermillion)]">{error}</span>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------

function CodeCard({
  code,
  expiresAt,
  hint,
  onCancel,
}: {
  code: string
  expiresAt: Date
  hint: string
  onCancel: () => void
}) {
  const [now, setNow] = useState(Date.now())
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  const remainingSec = Math.max(0, Math.floor((expiresAt.getTime() - now) / 1000))
  const mm = Math.floor(remainingSec / 60)
  const ss = String(remainingSec % 60).padStart(2, '0')

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard may be unavailable in non-https contexts; user can still type the code.
    }
  }

  return (
    <div className="flex flex-col gap-1.5 ml-7 p-2 rounded-sm bg-[rgba(163,163,163,0.05)] border border-[rgba(163,163,163,0.1)]">
      <div className="flex items-center gap-2">
        <code className="flex-1 px-2 py-1 text-body font-mono text-[var(--color-paper)] bg-[var(--color-void)] rounded-sm tracking-widest text-center">
          {code}
        </code>
        <button
          type="button"
          onClick={handleCopy}
          className="p-1 rounded-sm hover:bg-[rgba(163,163,163,0.15)] text-[var(--color-stone)]/80 hover:text-[var(--color-paper)] transition-colors"
          title={copied ? 'Copied' : 'Copy'}
        >
          {copied ? (
            <CheckCircle2 className="w-3.5 h-3.5 text-[var(--color-jade)]" />
          ) : (
            <Copy className="w-3.5 h-3.5" />
          )}
        </button>
      </div>
      <p className="text-caption text-[var(--color-stone)]/70">{hint}</p>
      <div className="flex items-center justify-between text-caption text-[var(--color-stone)]/50">
        <span>
          Expires in {mm}:{ss}
        </span>
        <button
          type="button"
          onClick={onCancel}
          className="hover:text-[var(--color-stone)]/80 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
