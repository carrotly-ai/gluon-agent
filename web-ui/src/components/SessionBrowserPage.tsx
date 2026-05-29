import {
  Check,
  ChevronLeft,
  Clock,
  ExternalLink,
  FolderOpen,
  GitBranch,
  HardDrive,
  Layers,
  MessageSquare,
  Play,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { fetchSDKSessionDetail, fetchSDKSessions, resumeSdkSession } from '@/lib/api'
import { POLL_SLOW } from '@/lib/polling'
import { formatRelativeTime } from '@/lib/timestamps'
import type { SDKSession, SessionDetail, SessionMessage } from '@/lib/types'
import { formatFileSize } from '@/lib/types'
import { cn } from '@/lib/utils'
import { DataPage } from './ui/DataPage'
import { FilterBar } from './ui/FilterBar'
import { PageHeader } from './ui/PageHeader'

function truncate(text: string, max: number): string {
  if (text.length <= max) return text
  return `${text.slice(0, max)}...`
}

function SessionDetailDialog({
  session,
  open,
  onOpenChange,
  onResume,
  resuming,
}: {
  session: SDKSession | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onResume: (session: SDKSession) => void
  resuming: boolean
}) {
  const navigate = useNavigate()
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!session || !open) {
      setDetail(null)
      return
    }
    setLoading(true)
    fetchSDKSessionDetail(session.session_id, { limit: 200 })
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false))
  }, [session, open])

  useEffect(() => {
    if (detail && scrollRef.current) {
      scrollRef.current.scrollTop = 0
    }
  }, [detail])

  if (!session) return null

  const shortDir = session.cwd?.replace(/^\/Users\/[^/]+\//, '~/') ?? null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="dialog-content sm:max-w-4xl w-[95vw] max-h-[90vh] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden"
        showCloseButton={false}
      >
        {/* Header bar */}
        <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-b border-[rgba(163,163,163,0.08)]">
          <button
            type="button"
            className="md:hidden p-1 -ml-1 rounded-sm hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/60"
            onClick={() => onOpenChange(false)}
            aria-label="Back"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>

          <div className="flex-1 min-w-0">
            <h2 className="text-body font-medium text-[var(--color-paper)] truncate">
              {session.custom_title || truncate(session.summary || 'Untitled session', 80)}
            </h2>
            <div className="flex items-center gap-3 mt-0.5 text-caption text-[var(--color-stone)]/50 flex-wrap">
              {session.git_branch && (
                <span className="flex items-center gap-1">
                  <GitBranch className="w-3 h-3" />
                  <span className="text-[var(--color-sky)]">{session.git_branch}</span>
                </span>
              )}
              {shortDir && (
                <span className="flex items-center gap-1 max-w-[200px] truncate">
                  <FolderOpen className="w-3 h-3 shrink-0" />
                  {shortDir}
                </span>
              )}
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {formatRelativeTime(new Date(session.last_modified * 1000).toISOString())}
              </span>
              <span className="flex items-center gap-1">
                <HardDrive className="w-3 h-3" />
                {formatFileSize(session.file_size)}
              </span>
              {detail && (
                <span className="flex items-center gap-1">
                  <MessageSquare className="w-3 h-3" />
                  {detail.total_messages} message{detail.total_messages !== 1 ? 's' : ''}
                </span>
              )}
            </div>
          </div>

          {/* Linked runs */}
          {session.linked_run_ids.length > 0 && (
            <div className="hidden sm:flex items-center gap-1 shrink-0">
              {session.linked_run_ids.map((runId) => (
                <button
                  type="button"
                  key={runId}
                  onClick={() => {
                    onOpenChange(false)
                    navigate(`/board/${runId}`)
                  }}
                  className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-mono bg-[var(--color-sky)]/10 text-[var(--color-sky)] hover:bg-[var(--color-sky)]/20 transition-colors"
                >
                  {runId.slice(0, 8)}
                  <ExternalLink className="w-2.5 h-2.5" />
                </button>
              ))}
            </div>
          )}

          {/* Resume — primary affordance */}
          <button
            type="button"
            onClick={() => onResume(session)}
            disabled={resuming}
            className="hidden md:inline-flex items-center gap-1.5 px-2.5 py-1 text-caption uppercase tracking-widest text-[var(--color-void)] bg-[var(--color-paper)] hover:opacity-90 disabled:opacity-40 rounded-sm transition-opacity"
            aria-label="Resume session"
          >
            <Play className="w-3 h-3" />
            Resume
          </button>

          <button
            type="button"
            className="hidden md:flex p-1 rounded-sm hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/50 hover:text-[var(--color-stone)]"
            onClick={() => onOpenChange(false)}
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Message transcript */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto min-h-0">
          {loading ? (
            <div className="flex items-center justify-center h-32">
              <div className="mark mark-running w-2 h-2" />
            </div>
          ) : !detail ? (
            <div className="flex items-center justify-center h-32 text-caption text-[var(--color-stone)]/40">
              Failed to load session messages
            </div>
          ) : detail.messages.length === 0 ? (
            <div className="flex items-center justify-center h-32 text-caption text-[var(--color-stone)]/40">
              No messages in this session
            </div>
          ) : (
            <div className="px-4 sm:px-6 py-4 space-y-1">
              {detail.messages.map((msg, idx) => (
                <TranscriptMessage
                  key={msg.uuid}
                  msg={msg}
                  isFirst={idx === 0}
                  prevType={idx > 0 ? detail.messages[idx - 1].type : null}
                />
              ))}
              {detail.total_messages > detail.messages.length && (
                <div className="text-caption text-[var(--color-stone)]/40 text-center pt-4 pb-2">
                  Showing {detail.messages.length} of {detail.total_messages} messages
                </div>
              )}
            </div>
          )}
        </div>

        {/* Session ID footer */}
        <div className="shrink-0 px-4 py-2 border-t border-[rgba(163,163,163,0.06)] text-mono text-[var(--color-stone)]/30 select-all">
          {session.session_id}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function TranscriptMessage({
  msg,
  isFirst,
  prevType,
}: {
  msg: SessionMessage
  isFirst: boolean
  prevType: string | null
}) {
  const isUser = msg.type === 'user'
  const content =
    typeof msg.message === 'string' ? msg.message : JSON.stringify(msg.message, null, 2)
  const showLabel = isFirst || prevType !== msg.type

  return (
    <div className={cn('group', showLabel ? 'pt-3' : 'pt-0.5', isUser ? 'pr-8 sm:pr-16' : 'pl-0')}>
      {showLabel && (
        <div
          className={cn(
            'text-micro uppercase tracking-widest mb-1 font-medium',
            isUser ? 'text-[var(--color-sky)]/60' : 'text-[var(--color-stone)]/40'
          )}
        >
          {isUser ? 'User' : 'Assistant'}
        </div>
      )}
      <div
        className={cn(
          'rounded-sm px-3 py-2 text-body',
          isUser
            ? 'bg-[var(--color-sky)]/8 border-l-2 border-[var(--color-sky)]/30'
            : 'bg-[rgba(163,163,163,0.04)]'
        )}
      >
        <pre className="whitespace-pre-wrap text-mono leading-relaxed text-[var(--color-paper)]/85 overflow-x-auto">
          {content}
        </pre>
      </div>
    </div>
  )
}

export function SessionBrowserPage() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState<SDKSession[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [selectedSession, setSelectedSession] = useState<SDKSession | null>(null)
  const [resumingId, setResumingId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchSDKSessions({ limit: 100 })
      setSessions(data)
    } catch (err) {
      console.error('Failed to load SDK sessions:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const interval = setInterval(load, POLL_SLOW)
    return () => clearInterval(interval)
  }, [load])

  const handleResume = useCallback(
    async (session: SDKSession) => {
      setResumingId(session.session_id)
      try {
        const run = await resumeSdkSession(session.session_id)
        // Navigate to the new run; resumeSdkSession is currently a stub that
        // will throw — the catch below shows a useful message.
        navigate(`/board/${run.id}`)
      } catch (err) {
        console.error('Failed to resume session:', err)
        // Best-effort surface — keep it terse, the API stub explains the gap.
        window.alert(
          err instanceof Error
            ? `Resume not available yet: ${err.message}`
            : 'Resume not available yet (backend endpoint pending).'
        )
      } finally {
        setResumingId(null)
      }
    },
    [navigate]
  )

  const filtered = sessions.filter((s) => {
    if (!search) return true
    const q = search.toLowerCase()
    return (
      s.first_prompt?.toLowerCase().includes(q) ||
      s.custom_title?.toLowerCase().includes(q) ||
      s.summary?.toLowerCase().includes(q) ||
      s.cwd?.toLowerCase().includes(q) ||
      s.git_branch?.toLowerCase().includes(q)
    )
  })

  const isEmpty = !loading && filtered.length === 0
  const initialLoading = loading && sessions.length === 0

  return (
    <DataPage>
      <PageHeader title="SDK Sessions" icon={Layers} count={filtered.length} countLabel="session" />

      <FilterBar
        search={{
          value: search,
          onChange: setSearch,
          placeholder: 'Search sessions…',
          ariaLabel: 'Search sessions',
        }}
        refresh={load}
        refreshing={loading}
      />

      <DataPage.Body>
        {initialLoading ? (
          <div className="flex items-center justify-center h-32">
            <div className="mark mark-running w-2 h-2" />
          </div>
        ) : isEmpty ? (
          <SessionsEmptyState searching={search.length > 0} onClear={() => setSearch('')} />
        ) : (
          <table className="w-full">
            <thead className="sticky top-0 bg-[var(--color-void)] z-10">
              <tr className="border-b border-[rgba(163,163,163,0.08)] text-caption text-[var(--color-stone)]/50">
                <th className="text-left px-4 py-2 font-normal">Title / Summary</th>
                <th className="text-left px-4 py-2 font-normal">First Prompt</th>
                <th className="text-left px-4 py-2 font-normal">Branch</th>
                <th className="text-left px-4 py-2 font-normal">Directory</th>
                <th className="text-left px-4 py-2 font-normal">Last Modified</th>
                <th className="text-left px-4 py-2 font-normal">Linked Runs</th>
                <th className="text-right px-4 py-2 font-normal w-20">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((session) => (
                <tr
                  key={session.session_id}
                  className="border-b border-[rgba(163,163,163,0.04)] hover:bg-[rgba(163,163,163,0.03)] cursor-pointer transition-colors group"
                  onClick={() => setSelectedSession(session)}
                >
                  <td className="px-4 py-2 text-body text-[var(--color-paper)]">
                    {session.custom_title || truncate(session.summary, 60)}
                  </td>
                  <td className="px-4 py-2 text-caption text-[var(--color-stone)]/60 max-w-[200px]">
                    {session.first_prompt ? truncate(session.first_prompt, 50) : '—'}
                  </td>
                  <td className="px-4 py-2 text-caption">
                    {session.git_branch ? (
                      <span className="text-mono text-[var(--color-sky)]">
                        {session.git_branch}
                      </span>
                    ) : (
                      <span className="text-[var(--color-stone)]/30">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-caption text-[var(--color-stone)]/50 max-w-[180px] truncate">
                    {session.cwd ? session.cwd.replace(/^\/Users\/[^/]+\//, '~/') : '—'}
                  </td>
                  <td className="px-4 py-2 text-caption text-[var(--color-stone)]/50">
                    {formatRelativeTime(new Date(session.last_modified * 1000).toISOString())}
                  </td>
                  <td className="px-4 py-2">
                    <div className="flex gap-1 flex-wrap">
                      {session.linked_run_ids.length > 0 ? (
                        session.linked_run_ids.map((runId) => (
                          <button
                            type="button"
                            key={runId}
                            onClick={(e) => {
                              e.stopPropagation()
                              navigate(`/board/${runId}`)
                            }}
                            className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-mono bg-[var(--color-sky)]/10 text-[var(--color-sky)] hover:bg-[var(--color-sky)]/20 transition-colors"
                          >
                            {runId.slice(0, 8)}
                            <ExternalLink className="w-2.5 h-2.5" />
                          </button>
                        ))
                      ) : (
                        <span className="text-caption text-[var(--color-stone)]/30">—</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-2 text-right">
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        void handleResume(session)
                      }}
                      disabled={resumingId === session.session_id}
                      aria-label="Resume session"
                      title="Resume session"
                      className={cn(
                        'p-1.5 rounded-sm transition-all',
                        'text-[var(--color-stone)]/50 hover:text-[var(--color-paper)] hover:bg-[var(--color-paper)]/5',
                        // Visible always on touch / coarse pointers; only on hover on fine.
                        'opacity-100 md:opacity-0 md:group-hover:opacity-100 focus:opacity-100',
                        resumingId === session.session_id && 'opacity-40 cursor-wait'
                      )}
                    >
                      <Play className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </DataPage.Body>

      <SessionDetailDialog
        session={selectedSession}
        open={!!selectedSession}
        onOpenChange={(open) => {
          if (!open) setSelectedSession(null)
        }}
        onResume={(s) => {
          setSelectedSession(null)
          void handleResume(s)
        }}
        resuming={resumingId !== null}
      />
    </DataPage>
  )
}

function SessionsEmptyState({ searching, onClear }: { searching: boolean; onClear: () => void }) {
  if (searching) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6 gap-3 max-w-md mx-auto">
        <Layers className="w-8 h-8 text-[var(--color-stone)]/30" />
        <div>
          <p className="text-display text-[var(--color-paper)] mb-1">No matching sessions</p>
          <p className="text-body text-[var(--color-stone)]/60">
            Try clearing filters or widening the date range. Search runs over title, summary,
            prompt, branch, and working directory.
          </p>
        </div>
        <button
          type="button"
          onClick={onClear}
          className="px-3 py-1.5 text-caption uppercase tracking-widest text-[var(--color-paper)] border border-[rgba(163,163,163,0.2)] hover:bg-[rgba(163,163,163,0.06)] rounded-sm"
        >
          Clear search
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-6 gap-3 max-w-md mx-auto">
      <Layers className="w-8 h-8 text-[var(--color-stone)]/30" />
      <div>
        <p className="text-display text-[var(--color-paper)] mb-1">No SDK sessions yet</p>
        <p className="text-body text-[var(--color-stone)]/60">
          Sessions appear here once you've run agents through the Claude CLI. Each run leaves a
          local transcript you can revisit or resume from.
        </p>
      </div>
      <ul className="text-caption text-[var(--color-stone)]/50 text-left mt-2 flex flex-col gap-1">
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Full transcript with user +
          assistant turns
        </li>
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Linked back to the runs they
          spawned
        </li>
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Search across title, prompt,
          branch, and cwd
        </li>
        <li className="flex items-center gap-2">
          <Check className="w-3 h-3 text-[var(--color-jade)]/60" /> Resume any session as a new run
          (when backend support lands)
        </li>
      </ul>
    </div>
  )
}
