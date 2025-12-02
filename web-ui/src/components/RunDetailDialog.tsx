import { useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  XCircle, RefreshCw, Clock, Timer, User, Folder,
  Terminal, AlertTriangle, CheckCircle2, Loader2, Ban,
  Hash, ExternalLink
} from 'lucide-react'
import type { Run, RunDetail, RunStatus } from '@/lib/types'
import { fetchRun, fetchLogs, cancelRun } from '@/lib/api'
import { cn } from '@/lib/utils'

interface RunDetailDialogProps {
  run: Run | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onRunUpdated: (run: Run) => void
}

const STATUS_CONFIG: Record<RunStatus, {
  color: string
  bgColor: string
  borderColor: string
  icon: React.ElementType
  label: string
}> = {
  pending: {
    color: 'text-[#ffbe0b]',
    bgColor: 'bg-[#ffbe0b]/10',
    borderColor: 'border-[#ffbe0b]/30',
    icon: Clock,
    label: 'QUEUED'
  },
  running: {
    color: 'text-[#00f5ff]',
    bgColor: 'bg-[#00f5ff]/10',
    borderColor: 'border-[#00f5ff]/30',
    icon: Loader2,
    label: 'ACTIVE'
  },
  completed: {
    color: 'text-[#39ff14]',
    bgColor: 'bg-[#39ff14]/10',
    borderColor: 'border-[#39ff14]/30',
    icon: CheckCircle2,
    label: 'COMPLETE'
  },
  failed: {
    color: 'text-[#ff3366]',
    bgColor: 'bg-[#ff3366]/10',
    borderColor: 'border-[#ff3366]/30',
    icon: AlertTriangle,
    label: 'FAILED'
  },
  cancelled: {
    color: 'text-[#6b7280]',
    bgColor: 'bg-[#6b7280]/10',
    borderColor: 'border-[#6b7280]/30',
    icon: Ban,
    label: 'ABORTED'
  },
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
  const [activeTab, setActiveTab] = useState<'details' | 'stdout' | 'stderr'>('details')
  const [loading, setLoading] = useState(false)
  const [cancelling, setCancelling] = useState(false)

  // Load run details and logs when dialog opens
  useEffect(() => {
    if (!open || !run) {
      setDetail(null)
      setLogs({ stdout: '', stderr: '' })
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
        setLogs({
          stdout: stdoutLogs.content || '',
          stderr: stderrLogs.content || '',
        })
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
      setLogs({
        stdout: stdoutLogs.content || '',
        stderr: stderrLogs.content || '',
      })
      onRunUpdated(runDetail)
    } catch (err) {
      console.error('Failed to refresh:', err)
    } finally {
      setLoading(false)
    }
  }

  const isActive = run?.status === 'running' || run?.status === 'pending'
  const config = run ? STATUS_CONFIG[run.status] : null
  const StatusIcon = config?.icon || Terminal

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="mission-dialog max-w-4xl max-h-[90vh] flex flex-col p-0 gap-0 overflow-hidden">
        {/* Header */}
        <DialogHeader className="p-4 border-b border-[#2a2a3a] bg-[#0a0a0f]">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-3 mb-2">
                {config && (
                  <div className={cn(
                    'flex items-center gap-2 px-3 py-1.5 rounded-md border',
                    config.bgColor,
                    config.borderColor,
                    config.color
                  )}>
                    <StatusIcon className={cn('w-4 h-4', run?.status === 'running' && 'animate-spin')} />
                    <span className="font-mono text-xs font-semibold">{config.label}</span>
                  </div>
                )}
                <span className="font-mono text-xs text-[#666]">
                  <Hash className="w-3 h-3 inline mr-1" />
                  {run?.id.slice(0, 8)}
                </span>
              </div>
              <DialogTitle className="text-base font-medium text-[#e4e4e7]">
                {run?.prompt || 'Mission Details'}
              </DialogTitle>
              <DialogDescription className="text-[#666] text-sm mt-1">
                {run?.project_name}
              </DialogDescription>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                className="mission-button"
                onClick={handleRefresh}
                disabled={loading}
              >
                <RefreshCw className={cn('h-4 w-4 mr-2', loading && 'animate-spin')} />
                REFRESH
              </button>
              {isActive && (
                <button
                  className="mission-button danger"
                  onClick={handleCancel}
                  disabled={cancelling}
                >
                  <XCircle className="h-4 w-4 mr-2" />
                  ABORT
                </button>
              )}
            </div>
          </div>
        </DialogHeader>

        {/* Tabs */}
        <div className="flex border-b border-[#2a2a3a] bg-[#12121a]">
          {(['details', 'stdout', 'stderr'] as const).map((tab) => (
            <button
              key={tab}
              className={cn(
                'mission-tab',
                activeTab === tab && 'active'
              )}
              onClick={() => setActiveTab(tab)}
            >
              {tab === 'details' && <Terminal className="w-3 h-3 mr-2 inline" />}
              {tab.toUpperCase()}
              {tab === 'stderr' && logs.stderr && (
                <span className="ml-2 w-2 h-2 rounded-full bg-[#ff3366] inline-block" />
              )}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden bg-[#0a0a0f]">
          {activeTab === 'details' && (
            <ScrollArea className="h-full">
              <div className="p-6 space-y-6">
                {/* Metadata grid */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-[#12121a] border border-[#2a2a3a] rounded-lg p-4">
                    <div className="flex items-center gap-2 text-[#666] text-xs font-mono mb-2">
                      <Folder className="h-4 w-4" />
                      PROJECT
                    </div>
                    <p className="text-[#e4e4e7] font-medium">{run?.project_name}</p>
                  </div>
                  <div className="bg-[#12121a] border border-[#2a2a3a] rounded-lg p-4">
                    <div className="flex items-center gap-2 text-[#666] text-xs font-mono mb-2">
                      <User className="h-4 w-4" />
                      INITIATOR
                    </div>
                    <p className="text-[#e4e4e7] font-medium">{run?.initiator || 'CLI'}</p>
                  </div>
                  <div className="bg-[#12121a] border border-[#2a2a3a] rounded-lg p-4">
                    <div className="flex items-center gap-2 text-[#666] text-xs font-mono mb-2">
                      <Clock className="h-4 w-4" />
                      CREATED
                    </div>
                    <p className="text-[#e4e4e7] font-medium font-mono text-sm">
                      {formatDateTime(run?.created_at ?? null)}
                    </p>
                  </div>
                  <div className="bg-[#12121a] border border-[#2a2a3a] rounded-lg p-4">
                    <div className="flex items-center gap-2 text-[#666] text-xs font-mono mb-2">
                      <Timer className="h-4 w-4" />
                      DURATION
                    </div>
                    <p className="text-[#e4e4e7] font-medium font-mono text-sm">
                      {formatDuration(run?.duration_seconds ?? null)}
                    </p>
                  </div>
                </div>

                {/* Full prompt */}
                <div>
                  <h4 className="text-xs font-mono text-[#666] mb-3 flex items-center gap-2">
                    <Terminal className="w-4 h-4" />
                    MISSION PROMPT
                  </h4>
                  <div className="log-viewer">
                    <pre>{run?.prompt}</pre>
                  </div>
                </div>

                {/* Error message */}
                {run?.error_message && (
                  <div>
                    <h4 className="text-xs font-mono text-[#ff3366] mb-3 flex items-center gap-2">
                      <AlertTriangle className="w-4 h-4" />
                      ERROR
                    </h4>
                    <div className="log-viewer border-[#ff3366]/30 bg-[#ff3366]/5">
                      <pre className="text-[#ff3366]">{run.error_message}</pre>
                    </div>
                  </div>
                )}

                {/* Additional details */}
                {detail && (detail.session_id || detail.exit_code !== null) && (
                  <div className="border-t border-[#2a2a3a] pt-4">
                    <h4 className="text-xs font-mono text-[#666] mb-3">SYSTEM INFO</h4>
                    <div className="flex items-center gap-4 text-xs font-mono text-[#888]">
                      {detail.session_id && (
                        <span className="flex items-center gap-1">
                          <ExternalLink className="w-3 h-3" />
                          Session: {detail.session_id.slice(0, 12)}...
                        </span>
                      )}
                      {detail.exit_code !== null && (
                        <span className={cn(
                          'px-2 py-0.5 rounded',
                          detail.exit_code === 0
                            ? 'bg-[#39ff14]/10 text-[#39ff14]'
                            : 'bg-[#ff3366]/10 text-[#ff3366]'
                        )}>
                          Exit: {detail.exit_code}
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </ScrollArea>
          )}

          {activeTab === 'stdout' && (
            <ScrollArea className="h-[500px]">
              <div className="p-4">
                <div className="log-viewer min-h-[400px]">
                  <pre>
                    {logs.stdout || <span className="text-[#444]">// No output captured</span>}
                  </pre>
                </div>
              </div>
            </ScrollArea>
          )}

          {activeTab === 'stderr' && (
            <ScrollArea className="h-[500px]">
              <div className="p-4">
                <div className="log-viewer min-h-[400px] border-[#ff3366]/20">
                  <pre className={logs.stderr ? 'text-[#ff3366]' : ''}>
                    {logs.stderr || <span className="text-[#444]">// No errors captured</span>}
                  </pre>
                </div>
              </div>
            </ScrollArea>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
