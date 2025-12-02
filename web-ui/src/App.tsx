import { useState, useCallback, useEffect, useMemo } from 'react'
import { Plus, Sun, Moon } from 'lucide-react'
import { KanbanBoard } from './components/KanbanBoard'
import { RunDetailDialog } from './components/RunDetailDialog'
import { ProjectFilter } from './components/ProjectFilter'
import { CreateTaskDialog } from './components/CreateTaskDialog'
import { useRunsWithWebSocket } from './hooks/useWebSocket'
import { useHashFilter } from './hooks/useHashFilter'
import { useTheme } from './hooks/useTheme'
import { cancelRun, fetchProjects } from './lib/api'
import { getWorkspaceFromPath } from './lib/types'
import type { Run, Project } from './lib/types'

function App() {
  const { runs, loading, error, connected, setRuns } = useRunsWithWebSocket()
  const [selectedRun, setSelectedRun] = useState<Run | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const { filter, setFilter } = useHashFilter()
  const { theme, toggleTheme } = useTheme()

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

  // Calculate today's total cost from all runs (not filtered)
  const todayCost = useMemo(() => {
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    return runs
      .filter(run => new Date(run.created_at) >= today)
      .reduce((sum, run) => sum + (run.cost_usd ?? 0), 0)
  }, [runs])

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="border-b border-[rgba(163,163,163,0.1)] shrink-0">
        <div className="flex items-center justify-between px-4 sm:px-6 h-12 sm:h-14">
          {/* Left - wordmark + filter */}
          <div className="flex items-center gap-3 sm:gap-5">
            <span className="text-[0.75rem] sm:text-[0.8125rem] font-normal tracking-[0.1em] text-[var(--color-paper)]">
              GLUON
            </span>
            <ProjectFilter filter={filter} onFilterChange={setFilter} />
            {activeRuns > 0 && (
              <span className="text-caption header-stats">
                {activeRuns} active
              </span>
            )}
            {todayCost > 0 && (
              <span className="text-caption text-[var(--color-harvest)] hidden sm:inline">
                ${todayCost.toFixed(2)} today
              </span>
            )}
          </div>

          {/* Right - theme + create + connection */}
          <div className="flex items-center gap-3 sm:gap-4">
            <button
              className="p-1.5 rounded-sm hover:bg-[rgba(163,163,163,0.1)] transition-colors"
              onClick={toggleTheme}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {theme === 'dark' ? (
                <Sun className="w-4 h-4 text-[var(--color-stone)]" />
              ) : (
                <Moon className="w-4 h-4 text-[var(--color-stone)]" />
              )}
            </button>
            <button
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-[0.625rem] uppercase tracking-widest text-[var(--color-void)] bg-[var(--color-paper)] hover:opacity-90 transition-colors rounded-sm"
              onClick={() => setCreateDialogOpen(true)}
            >
              <Plus className="w-3 h-3" />
              <span className="hidden sm:inline">New</span>
            </button>
            <div className="flex items-center gap-2">
              <div className={`mark ${connected ? 'mark-running' : 'mark-cancelled'}`} />
              <span className="text-caption hidden sm:inline">
                {connected ? 'connected' : 'offline'}
              </span>
            </div>
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

      <CreateTaskDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
        onTaskCreated={() => {}}
        initialProject={filter.type === 'project' ? filter.value || undefined : undefined}
      />
    </div>
  )
}

export default App
