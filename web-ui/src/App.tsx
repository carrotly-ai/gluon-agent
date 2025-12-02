import { useState, useCallback } from 'react'
import { KanbanBoard } from './components/KanbanBoard'
import { RunDetailDialog } from './components/RunDetailDialog'
import { useRunsWithWebSocket } from './hooks/useWebSocket'
import { cancelRun } from './lib/api'
import type { Run } from './lib/types'
import { Loader2, Wifi, WifiOff } from 'lucide-react'

function App() {
  const { runs, loading, error, connected, setRuns } = useRunsWithWebSocket()
  const [selectedRun, setSelectedRun] = useState<Run | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  const handleRunClick = useCallback((run: Run) => {
    setSelectedRun(run)
    setDialogOpen(true)
  }, [])

  const handleCancelRun = useCallback(async (run: Run) => {
    try {
      const updated = await cancelRun(run.id)
      setRuns(prev => prev.map(r => r.id === updated.id ? updated : r))
    } catch (err) {
      console.error('Failed to cancel run:', err)
    }
  }, [setRuns])

  const handleRunUpdated = useCallback((updatedRun: Run) => {
    setRuns(prev => prev.map(r => r.id === updatedRun.id ? updatedRun : r))
    if (selectedRun?.id === updatedRun.id) {
      setSelectedRun(updatedRun)
    }
  }, [setRuns, selectedRun?.id])

  const activeRuns = runs.filter(r => r.status === 'running').length
  const pendingRuns = runs.filter(r => r.status === 'pending').length

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="sticky top-0 z-10 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-sm">
        <div className="flex items-center justify-between px-4 h-12">
          {/* Left */}
          <div className="flex items-center gap-4">
            <h1 className="text-sm font-semibold">Gluon</h1>
            <div className="flex items-center gap-3 text-xs text-zinc-500">
              <span><span className="text-blue-400">{activeRuns}</span> active</span>
              <span><span className="text-yellow-400">{pendingRuns}</span> queued</span>
              <span className="text-zinc-600">{runs.length} total</span>
            </div>
          </div>

          {/* Right */}
          <div className="flex items-center gap-2 text-xs">
            {connected ? (
              <span className="flex items-center gap-1.5 text-emerald-500">
                <Wifi className="w-3 h-3" />
                Live
              </span>
            ) : (
              <span className="flex items-center gap-1.5 text-zinc-500">
                <WifiOff className="w-3 h-3" />
                Offline
              </span>
            )}
          </div>
        </div>
      </header>

      {/* Main */}
      <main className="flex-1 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-5 h-5 animate-spin text-zinc-500" />
          </div>
        ) : error ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-sm text-red-400">{error}</p>
          </div>
        ) : (
          <KanbanBoard
            runs={runs}
            onRunClick={handleRunClick}
            onCancelRun={handleCancelRun}
          />
        )}
      </main>

      <RunDetailDialog
        run={selectedRun}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onRunUpdated={handleRunUpdated}
      />
    </div>
  )
}

export default App
