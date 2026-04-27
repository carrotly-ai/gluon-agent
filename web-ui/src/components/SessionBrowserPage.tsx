import {
  ChevronLeft,
  Clock,
  ExternalLink,
  FolderOpen,
  GitBranch,
  HardDrive,
  MessageSquare,
  RefreshCw,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { fetchSDKSessionDetail, fetchSDKSessions } from '@/lib/api'
import { formatRelativeTime } from '@/lib/timestamps'
import type { SDKSession, SessionDetail, SessionMessage } from '@/lib/types'
import { formatFileSize } from '@/lib/types'
import { cn } from '@/lib/utils'

function truncate(text: string, max: number): string {
  if (text.length <= max) return text
  return `${text.slice(0, max)}...`
}

function SessionDetailDialog({
  session,
  open,
  onOpenChange,
}: {
  session: SDKSession | null
  open: boolean
  onOpenChange: (open: boolean) => void
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
            className="md:hidden p-1 -ml-1 rounded-sm hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/60"
            onClick={() => onOpenChange(false)}
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
                  key={runId}
                  onClick={() => {
                    onOpenChange(false)
                    navigate(`/board/${runId}`)
                  }}
                  className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-mono bg-[var(--color-sky)]/10 text-[var(--color-sky)] hover:bg-[var(--color-sky)]/20 transition-colors"
                >
                  {runId.slice(0, 8)}
                  <ExternalLink className="w-2.5 h-2.5" />
                </button>
              ))}
            </div>
          )}

          <button
            className="hidden md:flex p-1 rounded-sm hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/50 hover:text-[var(--color-stone)]"
            onClick={() => onOpenChange(false)}
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
        <div className="shrink-0 px-4 py-2 border-t border-[rgba(163,163,163,0.06)] text-[10px] font-mono text-[var(--color-stone)]/30 select-all">
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
            'text-[10px] uppercase tracking-widest mb-1 font-medium',
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
        <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-[var(--color-paper)]/85 overflow-x-auto">
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
    const interval = setInterval(load, 30000)
    return () => clearInterval(interval)
  }, [load])

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

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[rgba(163,163,163,0.08)]">
        <div className="flex items-center gap-3">
          <h2 className="text-body font-medium text-[var(--color-paper)]">SDK Sessions</h2>
          <span className="text-caption text-[var(--color-stone)]/50">
            {filtered.length} session{filtered.length !== 1 ? 's' : ''}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="Search sessions..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="px-2 py-1 text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded text-[var(--color-paper)] placeholder:text-[var(--color-stone)]/30 focus:outline-none focus:border-[var(--color-sky)]/40 w-48"
          />
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded-sm hover:bg-[rgba(163,163,163,0.1)] transition-colors text-[var(--color-stone)]/60"
            title="Refresh"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {loading && sessions.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <div className="mark mark-running w-2 h-2" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-caption text-[var(--color-stone)]/40">
            {search ? 'No sessions match your search' : 'No SDK sessions found'}
          </div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-[rgba(163,163,163,0.08)] text-caption text-[var(--color-stone)]/50">
                <th className="text-left px-4 py-2 font-normal">Title / Summary</th>
                <th className="text-left px-4 py-2 font-normal">First Prompt</th>
                <th className="text-left px-4 py-2 font-normal">Branch</th>
                <th className="text-left px-4 py-2 font-normal">Directory</th>
                <th className="text-left px-4 py-2 font-normal">Last Modified</th>
                <th className="text-right px-4 py-2 font-normal">Size</th>
                <th className="text-left px-4 py-2 font-normal">Linked Runs</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((session) => (
                <tr
                  key={session.session_id}
                  className="border-b border-[rgba(163,163,163,0.04)] hover:bg-[rgba(163,163,163,0.03)] cursor-pointer transition-colors"
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
                  <td className="px-4 py-2 text-caption text-[var(--color-stone)]/50 text-right">
                    {formatFileSize(session.file_size)}
                  </td>
                  <td className="px-4 py-2">
                    <div className="flex gap-1 flex-wrap">
                      {session.linked_run_ids.length > 0 ? (
                        session.linked_run_ids.map((runId) => (
                          <button
                            key={runId}
                            onClick={(e) => {
                              e.stopPropagation()
                              navigate(`/board/${runId}`)
                            }}
                            className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-mono bg-[var(--color-sky)]/10 text-[var(--color-sky)] hover:bg-[var(--color-sky)]/20 transition-colors"
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
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <SessionDetailDialog
        session={selectedSession}
        open={!!selectedSession}
        onOpenChange={(open) => {
          if (!open) setSelectedSession(null)
        }}
      />
    </div>
  )
}
