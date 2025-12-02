import { useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { XCircle, RefreshCw, Clock, Timer, User, Folder } from 'lucide-react'
import type { Run, RunDetail, RunStatus } from '@/lib/types'
import { fetchRun, fetchLogs, cancelRun } from '@/lib/api'
import { cn } from '@/lib/utils'

interface RunDetailDialogProps {
  run: Run | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onRunUpdated: (run: Run) => void
}

const STATUS_STYLES: Record<RunStatus, string> = {
  pending: 'bg-yellow-500/10 text-yellow-600 border-yellow-500/20',
  running: 'bg-blue-500/10 text-blue-600 border-blue-500/20',
  completed: 'bg-green-500/10 text-green-600 border-green-500/20',
  failed: 'bg-red-500/10 text-red-600 border-red-500/20',
  cancelled: 'bg-gray-500/10 text-gray-600 border-gray-500/20',
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <DialogTitle className="text-base font-medium truncate">
                {run?.prompt || 'Run Details'}
              </DialogTitle>
              <DialogDescription className="flex items-center gap-2 mt-1">
                <span className="font-mono text-xs">{run?.id.slice(0, 8)}</span>
                {run && (
                  <Badge variant="outline" className={cn('text-xs', STATUS_STYLES[run.status])}>
                    {run.status}
                  </Badge>
                )}
              </DialogDescription>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <Button
                variant="outline"
                size="sm"
                onClick={handleRefresh}
                disabled={loading}
              >
                <RefreshCw className={cn('h-4 w-4 mr-1', loading && 'animate-spin')} />
                Refresh
              </Button>
              {isActive && (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={handleCancel}
                  disabled={cancelling}
                >
                  <XCircle className="h-4 w-4 mr-1" />
                  Cancel
                </Button>
              )}
            </div>
          </div>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
          <TabsList className="shrink-0">
            <TabsTrigger value="details">Details</TabsTrigger>
            <TabsTrigger value="stdout">Stdout</TabsTrigger>
            <TabsTrigger value="stderr">Stderr</TabsTrigger>
          </TabsList>

          <TabsContent value="details" className="flex-1 overflow-auto">
            <div className="space-y-4 py-4">
              {/* Metadata grid */}
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div className="flex items-center gap-2">
                  <Folder className="h-4 w-4 text-muted-foreground" />
                  <span className="text-muted-foreground">Project:</span>
                  <span className="font-medium">{run?.project_name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <User className="h-4 w-4 text-muted-foreground" />
                  <span className="text-muted-foreground">Initiator:</span>
                  <span className="font-medium">{run?.initiator || 'CLI'}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Clock className="h-4 w-4 text-muted-foreground" />
                  <span className="text-muted-foreground">Created:</span>
                  <span className="font-medium">{formatDateTime(run?.created_at ?? null)}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Timer className="h-4 w-4 text-muted-foreground" />
                  <span className="text-muted-foreground">Duration:</span>
                  <span className="font-medium">{formatDuration(run?.duration_seconds ?? null)}</span>
                </div>
              </div>

              {/* Full prompt */}
              <div>
                <h4 className="text-sm font-medium mb-2">Prompt</h4>
                <pre className="text-sm bg-zinc-100 dark:bg-zinc-900 p-3 rounded-md whitespace-pre-wrap">
                  {run?.prompt}
                </pre>
              </div>

              {/* Error message */}
              {run?.error_message && (
                <div>
                  <h4 className="text-sm font-medium mb-2 text-red-600">Error</h4>
                  <pre className="text-sm bg-red-50 dark:bg-red-950/20 text-red-600 p-3 rounded-md whitespace-pre-wrap">
                    {run.error_message}
                  </pre>
                </div>
              )}

              {/* Additional details */}
              {detail && (
                <div className="text-xs text-muted-foreground space-y-1">
                  {detail.session_id && <p>Session: {detail.session_id}</p>}
                  {detail.exit_code !== null && <p>Exit code: {detail.exit_code}</p>}
                </div>
              )}
            </div>
          </TabsContent>

          <TabsContent value="stdout" className="flex-1 min-h-0">
            <ScrollArea className="h-[400px] rounded-md border">
              <pre className="p-4 text-xs font-mono whitespace-pre-wrap">
                {logs.stdout || <span className="text-muted-foreground">No output</span>}
              </pre>
            </ScrollArea>
          </TabsContent>

          <TabsContent value="stderr" className="flex-1 min-h-0">
            <ScrollArea className="h-[400px] rounded-md border">
              <pre className="p-4 text-xs font-mono whitespace-pre-wrap text-red-600">
                {logs.stderr || <span className="text-muted-foreground">No errors</span>}
              </pre>
            </ScrollArea>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
