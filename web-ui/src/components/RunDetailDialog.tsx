import { useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { X, RotateCw } from 'lucide-react'
import type { Run, RunDetail } from '@/lib/types'
import { fetchRun, fetchLogs, cancelRun } from '@/lib/api'
import { cn } from '@/lib/utils'

interface RunDetailDialogProps {
  run: Run | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onRunUpdated: (run: Run) => void
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleString()
}

export function RunDetailDialog({ run, open, onOpenChange, onRunUpdated }: RunDetailDialogProps) {
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [logs, setLogs] = useState<{ stdout: string; stderr: string }>({ stdout: '', stderr: '' })
  const [activeTab, setActiveTab] = useState<'info' | 'output' | 'errors'>('info')
  const [loading, setLoading] = useState(false)
  const [cancelling, setCancelling] = useState(false)

  useEffect(() => {
    if (!open || !run) {
      setDetail(null)
      setLogs({ stdout: '', stderr: '' })
      setActiveTab('info')
      return
    }

    const runId = run.id

    async function load() {
      setLoading(true)
      try {
        const [runDetail, stdoutLogs, stderrLogs] = await Promise.all([
          fetchRun(runId),
          fetchLogs(runId, 'stdout').catch(() => ({ content: '' })),
          fetchLogs(runId, 'stderr').catch(() => ({ content: '' })),
        ])
        setDetail(runDetail)
        setLogs({ stdout: stdoutLogs.content || '', stderr: stderrLogs.content || '' })
      } catch (err) {
        console.error('Failed to load run details:', err)
      } finally {
        setLoading(false)
      }
    }

    load()
  }, [open, run])

  const handleCancel = async () => {
    if (!run) return
    setCancelling(true)
    try {
      const updated = await cancelRun(run.id)
      onRunUpdated(updated)
    } catch (err) {
      console.error('Failed to cancel run:', err)
    } finally {
      setCancelling(false)
    }
  }

  const handleRefresh = async () => {
    if (!run) return
    setLoading(true)
    try {
      const [runDetail, stdoutLogs, stderrLogs] = await Promise.all([
        fetchRun(run.id),
        fetchLogs(run.id, 'stdout').catch(() => ({ content: '' })),
        fetchLogs(run.id, 'stderr').catch(() => ({ content: '' })),
      ])
      setDetail(runDetail)
      setLogs({ stdout: stdoutLogs.content || '', stderr: stderrLogs.content || '' })
      onRunUpdated(runDetail)
    } catch (err) {
      console.error('Failed to refresh:', err)
    } finally {
      setLoading(false)
    }
  }

  const isActive = run?.status === 'running' || run?.status === 'pending'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="dialog-content max-w-2xl max-h-[85vh] flex flex-col p-0 gap-0">
        {/* Header */}
        <DialogHeader className="p-6 pb-4 border-b border-[rgba(163,163,163,0.1)]">
          <div className="flex items-start gap-4">
            <div className={cn('mark mt-1', `mark-${run?.status}`)} />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-3 mb-2">
                <span className="text-mono text-[#a3a3a3]/50">{run?.id.slice(0, 8)}</span>
                <span className="text-caption uppercase tracking-widest">{run?.status}</span>
              </div>
              <DialogTitle className="text-title text-[#fafaf9] font-normal">
                {run?.prompt}
              </DialogTitle>
              <p className="text-caption mt-1">{run?.project_name}</p>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3 mt-4">
            <button
              className="text-caption hover:text-[#fafaf9] transition-colors flex items-center gap-1.5"
              onClick={handleRefresh}
              disabled={loading}
            >
              <RotateCw className={cn('w-3 h-3', loading && 'animate-spin')} />
              Refresh
            </button>
            {isActive && (
              <button
                className="text-caption hover:text-[#c73e3a] transition-colors flex items-center gap-1.5"
                onClick={handleCancel}
                disabled={cancelling}
              >
                <X className="w-3 h-3" />
                Cancel
              </button>
            )}
          </div>
        </DialogHeader>

        {/* Tabs - minimal horizontal line */}
        <div className="flex border-b border-[rgba(163,163,163,0.1)]">
          {(['info', 'output', 'errors'] as const).map((tab) => (
            <button
              key={tab}
              className={cn(
                'px-6 py-3 text-caption uppercase tracking-widest transition-colors',
                activeTab === tab
                  ? 'text-[#fafaf9] border-b border-[#fafaf9]'
                  : 'text-[#a3a3a3]/50 hover:text-[#a3a3a3]'
              )}
              onClick={() => setActiveTab(tab)}
            >
              {tab}
              {tab === 'errors' && logs.stderr && (
                <span className="ml-2 w-1 h-1 rounded-full bg-[#c73e3a] inline-block" />
              )}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden">
          {activeTab === 'info' && (
            <ScrollArea className="h-full">
              <div className="p-6 space-y-6">
                {/* Metadata - clean grid */}
                <div className="grid grid-cols-2 gap-y-4 gap-x-8">
                  <div>
                    <p className="text-caption mb-1">Project</p>
                    <p className="text-body text-[#fafaf9]">{run?.project_name}</p>
                  </div>
                  <div>
                    <p className="text-caption mb-1">Initiator</p>
                    <p className="text-body text-[#fafaf9]">{run?.initiator || 'CLI'}</p>
                  </div>
                  <div>
                    <p className="text-caption mb-1">Created</p>
                    <p className="text-mono text-[#fafaf9]">{formatDateTime(run?.created_at ?? null)}</p>
                  </div>
                  <div>
                    <p className="text-caption mb-1">Duration</p>
                    <p className="text-mono text-[#fafaf9]">{formatDuration(run?.duration_seconds ?? null)}</p>
                  </div>
                </div>

                {/* Prompt */}
                <div>
                  <p className="text-caption mb-2">Prompt</p>
                  <pre className="text-body text-[#fafaf9] bg-[#0c0c0c] p-4 whitespace-pre-wrap border border-[rgba(163,163,163,0.08)]">
                    {run?.prompt}
                  </pre>
                </div>

                {/* Error */}
                {run?.error_message && (
                  <div>
                    <p className="text-caption mb-2 accent-vermillion">Error</p>
                    <pre className="text-body accent-vermillion bg-[rgba(199,62,58,0.05)] p-4 whitespace-pre-wrap border border-[rgba(199,62,58,0.15)]">
                      {run.error_message}
                    </pre>
                  </div>
                )}

                {/* Session info */}
                {detail && (detail.session_id || detail.exit_code !== null) && (
                  <div className="pt-4 border-t border-[rgba(163,163,163,0.08)]">
                    <div className="flex items-center gap-6 text-mono text-[#a3a3a3]/40">
                      {detail.session_id && <span>session {detail.session_id.slice(0, 8)}</span>}
                      {detail.exit_code !== null && <span>exit {detail.exit_code}</span>}
                    </div>
                  </div>
                )}
              </div>
            </ScrollArea>
          )}

          {activeTab === 'output' && (
            <ScrollArea className="h-80">
              <pre className="p-6 text-mono text-[#fafaf9]/80 whitespace-pre-wrap">
                {logs.stdout || <span className="text-[#a3a3a3]/30">No output</span>}
              </pre>
            </ScrollArea>
          )}

          {activeTab === 'errors' && (
            <ScrollArea className="h-80">
              <pre className={cn(
                'p-6 text-mono whitespace-pre-wrap',
                logs.stderr ? 'accent-vermillion' : 'text-[#a3a3a3]/30'
              )}>
                {logs.stderr || 'No errors'}
              </pre>
            </ScrollArea>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
