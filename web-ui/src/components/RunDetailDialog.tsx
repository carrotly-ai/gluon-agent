import { useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { X, RefreshCw, Clock, Timer, User, Folder, AlertCircle } from 'lucide-react'
import type { Run, RunDetail, RunStatus } from '@/lib/types'
import { fetchRun, fetchLogs, cancelRun } from '@/lib/api'
import { cn } from '@/lib/utils'

interface RunDetailDialogProps {
  run: Run | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onRunUpdated: (run: Run) => void
}

const STATUS_BADGE: Record<RunStatus, string> = {
  pending: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  running: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  completed: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20',
  failed: 'bg-red-500/10 text-red-500 border-red-500/20',
  cancelled: 'bg-zinc-500/10 text-zinc-500 border-zinc-500/20',
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
  const [activeTab, setActiveTab] = useState('details')
  const [loading, setLoading] = useState(false)
  const [cancelling, setCancelling] = useState(false)

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
      <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <code className="text-xs text-zinc-500">{run?.id.slice(0, 8)}</code>
                {run && (
                  <Badge variant="outline" className={cn('text-xs', STATUS_BADGE[run.status])}>
                    {run.status}
                  </Badge>
                )}
              </div>
              <DialogTitle className="text-sm font-medium truncate">
                {run?.prompt || 'Run Details'}
              </DialogTitle>
              <DialogDescription className="text-xs">
                {run?.project_name}
              </DialogDescription>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <Button variant="outline" size="sm" onClick={handleRefresh} disabled={loading}>
                <RefreshCw className={cn('w-3 h-3 mr-1', loading && 'animate-spin')} />
                Refresh
              </Button>
              {isActive && (
                <Button variant="destructive" size="sm" onClick={handleCancel} disabled={cancelling}>
                  <X className="w-3 h-3 mr-1" />
                  Cancel
                </Button>
              )}
            </div>
          </div>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
          <TabsList className="shrink-0">
            <TabsTrigger value="details" className="text-xs">Details</TabsTrigger>
            <TabsTrigger value="stdout" className="text-xs">Output</TabsTrigger>
            <TabsTrigger value="stderr" className="text-xs">
              Errors
              {logs.stderr && <span className="ml-1 w-1.5 h-1.5 rounded-full bg-red-500" />}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="details" className="flex-1 overflow-auto mt-4">
            <div className="space-y-4 text-sm">
              {/* Metadata */}
              <div className="grid grid-cols-2 gap-3">
                <div className="flex items-center gap-2 text-zinc-400">
                  <Folder className="w-4 h-4" />
                  <span className="text-zinc-500">Project:</span>
                  <span className="text-zinc-200">{run?.project_name}</span>
                </div>
                <div className="flex items-center gap-2 text-zinc-400">
                  <User className="w-4 h-4" />
                  <span className="text-zinc-500">Initiator:</span>
                  <span className="text-zinc-200">{run?.initiator || 'CLI'}</span>
                </div>
                <div className="flex items-center gap-2 text-zinc-400">
                  <Clock className="w-4 h-4" />
                  <span className="text-zinc-500">Created:</span>
                  <span className="text-zinc-200">{formatDateTime(run?.created_at ?? null)}</span>
                </div>
                <div className="flex items-center gap-2 text-zinc-400">
                  <Timer className="w-4 h-4" />
                  <span className="text-zinc-500">Duration:</span>
                  <span className="text-zinc-200">{formatDuration(run?.duration_seconds ?? null)}</span>
                </div>
              </div>

              {/* Prompt */}
              <div>
                <h4 className="text-xs font-medium text-zinc-500 mb-2">Prompt</h4>
                <pre className="text-xs bg-zinc-900 border border-zinc-800 rounded-md p-3 whitespace-pre-wrap">
                  {run?.prompt}
                </pre>
              </div>

              {/* Error */}
              {run?.error_message && (
                <div>
                  <h4 className="text-xs font-medium text-red-400 mb-2 flex items-center gap-1">
                    <AlertCircle className="w-3 h-3" /> Error
                  </h4>
                  <pre className="text-xs bg-red-500/5 border border-red-500/20 text-red-400 rounded-md p-3 whitespace-pre-wrap">
                    {run.error_message}
                  </pre>
                </div>
              )}

              {/* System info */}
              {detail && (detail.session_id || detail.exit_code !== null) && (
                <div className="text-xs text-zinc-500 pt-2 border-t border-zinc-800">
                  {detail.session_id && <span>Session: {detail.session_id.slice(0, 12)}...</span>}
                  {detail.exit_code !== null && <span className="ml-4">Exit: {detail.exit_code}</span>}
                </div>
              )}
            </div>
          </TabsContent>

          <TabsContent value="stdout" className="flex-1 min-h-0 mt-4">
            <ScrollArea className="h-80 rounded-md border border-zinc-800">
              <pre className="p-3 text-xs font-mono whitespace-pre-wrap text-zinc-300">
                {logs.stdout || <span className="text-zinc-600">No output</span>}
              </pre>
            </ScrollArea>
          </TabsContent>

          <TabsContent value="stderr" className="flex-1 min-h-0 mt-4">
            <ScrollArea className="h-80 rounded-md border border-zinc-800">
              <pre className="p-3 text-xs font-mono whitespace-pre-wrap text-red-400">
                {logs.stderr || <span className="text-zinc-600">No errors</span>}
              </pre>
            </ScrollArea>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
