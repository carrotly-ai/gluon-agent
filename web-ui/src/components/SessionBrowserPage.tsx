import { ChevronDown, ChevronRight, ExternalLink, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchSDKSessionDetail, fetchSDKSessions } from '@/lib/api'
import { formatRelativeTime } from '@/lib/timestamps'
import type { SDKSession, SessionDetail, SessionMessage } from '@/lib/types'
import { formatFileSize } from '@/lib/types'
import { cn } from '@/lib/utils'

function truncate(text: string, max: number): string {
  if (text.length <= max) return text
  return `${text.slice(0, max)}...`
}

function MessageBubble({ msg }: { msg: SessionMessage }) {
  const isUser = msg.type === 'user'
  const content =
    typeof msg.message === 'string' ? msg.message : JSON.stringify(msg.message, null, 2)

  return (
    <div
      className={cn(
        'px-3 py-2 rounded text-body',
        isUser
          ? 'bg-[var(--color-sky)]/10 border border-[var(--color-sky)]/20'
          : 'bg-[var(--color-stone)]/5 border border-[var(--color-stone)]/10'
      )}
    >
      <div className="text-caption text-[var(--color-stone)]/50 mb-1">
        {isUser ? 'User' : 'Assistant'}
      </div>
      <pre className="whitespace-pre-wrap text-body font-mono text-[11px] max-h-[300px] overflow-auto">
        {truncate(content, 2000)}
      </pre>
    </div>
  )
}

export function SessionBrowserPage() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState<SDKSession[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [search, setSearch] = useState('')

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

  const handleExpand = useCallback(
    async (sessionId: string) => {
      if (expandedId === sessionId) {
        setExpandedId(null)
        setDetail(null)
        return
      }
      setExpandedId(sessionId)
      setDetailLoading(true)
      try {
        const d = await fetchSDKSessionDetail(sessionId, { limit: 50 })
        setDetail(d)
      } catch (err) {
        console.error('Failed to load session detail:', err)
        setDetail(null)
      } finally {
        setDetailLoading(false)
      }
    },
    [expandedId]
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

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[rgba(163,163,163,0.08)]">
        <div className="flex items-center gap-3">
          <h2 className="text-body font-medium text-[var(--color-ink)]">SDK Sessions</h2>
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
            className="px-2 py-1 text-caption bg-transparent border border-[rgba(163,163,163,0.15)] rounded text-[var(--color-ink)] placeholder:text-[var(--color-stone)]/30 focus:outline-none focus:border-[var(--color-sky)]/40 w-48"
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
                <th className="text-left px-4 py-2 font-normal w-8" />
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
                <>
                  <tr
                    key={session.session_id}
                    className="border-b border-[rgba(163,163,163,0.04)] hover:bg-[rgba(163,163,163,0.03)] cursor-pointer transition-colors"
                    onClick={() => handleExpand(session.session_id)}
                  >
                    <td className="px-4 py-2 text-[var(--color-stone)]/40">
                      {expandedId === session.session_id ? (
                        <ChevronDown className="w-3.5 h-3.5" />
                      ) : (
                        <ChevronRight className="w-3.5 h-3.5" />
                      )}
                    </td>
                    <td className="px-4 py-2 text-body text-[var(--color-ink)]">
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
                  {expandedId === session.session_id && (
                    <tr key={`${session.session_id}-detail`}>
                      <td colSpan={8} className="px-4 py-3 bg-[rgba(163,163,163,0.02)]">
                        {detailLoading ? (
                          <div className="flex items-center justify-center h-16">
                            <div className="mark mark-running w-2 h-2" />
                          </div>
                        ) : detail ? (
                          <div className="space-y-2 max-h-[500px] overflow-auto">
                            <div className="text-caption text-[var(--color-stone)]/50 mb-2">
                              {detail.total_messages} message
                              {detail.total_messages !== 1 ? 's' : ''} &middot; Session{' '}
                              {session.session_id.slice(0, 12)}
                            </div>
                            {detail.messages.map((msg) => (
                              <MessageBubble key={msg.uuid} msg={msg} />
                            ))}
                            {detail.total_messages > detail.messages.length && (
                              <div className="text-caption text-[var(--color-stone)]/40 text-center py-2">
                                Showing {detail.messages.length} of {detail.total_messages} messages
                              </div>
                            )}
                          </div>
                        ) : (
                          <div className="text-caption text-[var(--color-stone)]/40 text-center py-4">
                            Failed to load session detail
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
