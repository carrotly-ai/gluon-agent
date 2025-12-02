import { useState, useCallback, useEffect, useMemo } from 'react'
import { KanbanBoard } from './components/KanbanBoard'
import { RunDetailDialog } from './components/RunDetailDialog'
import { ProjectFilter } from './components/ProjectFilter'
import { useRunsWithWebSocket } from './hooks/useWebSocket'
import { useHashFilter } from './hooks/useHashFilter'
import { cancelRun, fetchProjects } from './lib/api'
import { getWorkspaceFromPath } from './lib/types'
import type { Run, Project } from './lib/types'

function App() {
  const { runs, loading, error, connected, setRuns } = useRunsWithWebSocket()
  const [selectedRun, setSelectedRun] = useState<Run | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const { filter, setFilter } = useHashFilter()

  // Fetch projects for workspace mapping
  useEffect(() => {
    fetchProjects().then(setProjects).catch(console.error)
  }, [])

  // Build project name -> workspace mapping
  const projectWorkspaceMap = useMemo(() => {
    const map = new Map<string, string>()
    for (const project of projects) {
      map.set(project.name, getWorkspaceFromPath(project.path))
    }
    return map
  }, [projects])

  // Filter runs based on current filter
  const filteredRuns = useMemo(() => {
    if (filter.type === 'all') return runs

    if (filter.type === 'project') {
      return runs.filter(run => run.project_name === filter.value)
    }

    if (filter.type === 'workspace') {
      return runs.filter(run => {
        const workspace = projectWorkspaceMap.get(run.project_name)
        return workspace === filter.value
      })
    }

    return runs
  }, [runs, filter, projectWorkspaceMap])

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

  const activeRuns = filteredRuns.filter(r => r.status === 'running').length

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="border-b border-[rgba(163,163,163,0.1)] shrink-0">
        <div className="flex items-center justify-between px-4 sm:px-6 h-12 sm:h-14">
          {/* Left - wordmark + filter */}
          <div className="flex items-center gap-3 sm:gap-5">
            <span className="text-[0.75rem] sm:text-[0.8125rem] font-normal tracking-[0.1em] text-[#fafaf9]">
              GLUON
            </span>
            <ProjectFilter filter={filter} onFilterChange={setFilter} />
            {activeRuns > 0 && (
              <span className="text-caption header-stats">
                {activeRuns} active
              </span>
            )}
          </div>

          {/* Right - connection */}
          <div className="flex items-center gap-2">
            <div className={`mark ${connected ? 'mark-running' : 'mark-cancelled'}`} />
            <span className="text-caption hidden sm:inline">
              {connected ? 'connected' : 'offline'}
            </span>
          </div>
        </div>
      </header>

      {/* Main */}
      <main className="flex-1 overflow-hidden min-h-0">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <div className="mark mark-running w-2 h-2" />
          </div>
        ) : error ? (
          <div className="flex items-center justify-center h-full p-4">
            <p className="text-caption accent-vermillion text-center">{error}</p>
          </div>
        ) : (
          <KanbanBoard
            runs={filteredRuns}
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
