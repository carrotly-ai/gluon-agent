import { useState, useCallback } from 'react'
import { KanbanBoard } from './components/KanbanBoard'
import { RunDetailDialog } from './components/RunDetailDialog'
import { useRunsWithWebSocket } from './hooks/useWebSocket'
import { cancelRun } from './lib/api'
import type { Run } from './lib/types'
import { Activity, Wifi, WifiOff, Zap } from 'lucide-react'

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
      {/* Mission Control Header */}
      <header className="mission-header sticky top-0 z-10">
        <div className="flex items-center justify-between px-6 py-4">
          {/* Brand */}
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-[#00f5ff] to-[#bf5af2] flex items-center justify-center">
                <Zap className="w-5 h-5 text-[#0a0a0f]" />
              </div>
              <div>
                <h1 className="brand-logo">GLUON</h1>
                <p className="brand-subtitle">Mission Control</p>
              </div>
            </div>

            {/* Stats */}
            <div className="hidden md:flex items-center gap-4 ml-8 pl-8 border-l border-[#2a2a3a]">
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-[#00f5ff]" />
                <span className="font-mono text-xs text-[#888]">
                  <span className="text-[#00f5ff] font-semibold">{activeRuns}</span> active
                </span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-[#ffbe0b]" />
                <span className="font-mono text-xs text-[#888]">
                  <span className="text-[#ffbe0b] font-semibold">{pendingRuns}</span> queued
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-[#888]">
                  <span className="text-[#e4e4e7] font-semibold">{runs.length}</span> total
                </span>
              </div>
            </div>
          </div>

          {/* Right side */}
          <div className="flex items-center gap-4">
            {/* Connection status */}
            <div className={`connection-badge ${connected ? 'connected' : 'disconnected'}`}>
              {connected ? (
                <>
                  <Wifi className="w-3 h-3" />
                  <span>LIVE</span>
                  <span className="w-2 h-2 rounded-full bg-current animate-pulse" />
                </>
              ) : (
                <>
                  <WifiOff className="w-3 h-3" />
                  <span>OFFLINE</span>
                </>
              )}
            </div>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">
        {loading ? (
          <div className="flex flex-col items-center justify-center h-full gap-4">
            <div className="mission-loader" />
            <p className="font-mono text-sm text-[#666]">Initializing systems...</p>
          </div>
        ) : error ? (
          <div className="flex items-center justify-center h-full">
            <div className="text-center p-8 bg-[#12121a] border border-[#ff3366]/30 rounded-lg max-w-md">
              <div className="w-12 h-12 rounded-full bg-[#ff3366]/10 flex items-center justify-center mx-auto mb-4">
                <span className="text-2xl">⚠</span>
              </div>
              <p className="text-[#ff3366] font-mono text-sm mb-2">SYSTEM ERROR</p>
              <p className="text-[#888] text-sm">{error}</p>
            </div>
          </div>
        ) : (
          <KanbanBoard
            runs={runs}
            onRunClick={handleRunClick}
            onCancelRun={handleCancelRun}
          />
        )}
      </main>

      {/* Run detail dialog */}
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
