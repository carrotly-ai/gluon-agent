import {
  Activity,
  BarChart3,
  Database,
  GitMerge,
  LayoutGrid,
  ListTodo,
  Loader2,
  Menu,
  Moon,
  Plus,
  Settings,
  Sun,
  WifiOff,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ActivityPage } from './components/ActivityPage'
import { AdminUsersPage } from './components/AdminUsersPage'
import { CreateTaskDialog } from './components/CreateTaskDialog'
import { KanbanBoard } from './components/KanbanBoard'
import { LoginPage } from './components/LoginPage'
import { MergeQueuePage } from './components/MergeQueuePage'
import { NotificationBell } from './components/NotificationBell'
import { OfflineOverlay } from './components/OfflineOverlay'
import { ProjectFilter } from './components/ProjectFilter'
import { QuestionModal } from './components/QuestionModal'
import { RunDetailDialog } from './components/RunDetailDialog'
import { SessionBrowserPage } from './components/SessionBrowserPage'
import { SettingsPage } from './components/SettingsPage'
import { UpdateBanner } from './components/UpdateBanner'
import { UsagePage } from './components/UsagePage'
import { UserMenu } from './components/UserMenu'
import { WorkQueuePage } from './components/WorkQueuePage'
import { useConnectivity } from './hooks/useConnectivity'
import { useCurrentUser } from './hooks/useCurrentUser'
import {
  NotificationCenterContext,
  useNotificationCenterProvider,
} from './hooks/useNotificationCenter'
import { useNotifications } from './hooks/useNotifications'
import { useOnline } from './hooks/useOnline'
import { type RunDetailTab, useRouteSync } from './hooks/useRouteSync'
import { useTheme } from './hooks/useTheme'
import { useRunsWithWebSocket } from './hooks/useWebSocket'
import {
  answerQuestion,
  archiveRun,
  cancelRun,
  fetchProjects,
  fetchRun,
  fetchRuns,
  fetchUsageSummary,
  stopLoop,
} from './lib/api'
import type { Project, Run, UsageSummary } from './lib/types'
import { getWorkspaceFromPath } from './lib/types'
import { cn } from './lib/utils'
import { fetchServerVersion } from './lib/version'

type ViewMode =
  | 'board'
  | 'activity'
  | 'queue'
  | 'merge'
  | 'sessions'
  | 'usage'
  | 'settings'
  | 'admin-users'

const SECONDARY_NAV_ITEMS: { mode: ViewMode; icon: typeof Activity; label: string }[] = [
  { mode: 'activity', icon: Activity, label: 'Activity' },
  { mode: 'queue', icon: ListTodo, label: 'Work Queue' },
  { mode: 'merge', icon: GitMerge, label: 'Merge Queue' },
  { mode: 'sessions', icon: Database, label: 'Sessions' },
  { mode: 'usage', icon: BarChart3, label: 'Usage' },
]

function MobileNavMenu({
  viewMode,
  onViewChange,
}: {
  viewMode: string
  onViewChange: (mode: ViewMode) => void
}) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const isSecondaryActive = SECONDARY_NAV_ITEMS.some((item) => item.mode === viewMode)

  return (
    <div className="md:hidden relative" ref={menuRef}>
      <div className="flex items-center gap-0.5 bg-[rgba(163,163,163,0.06)] rounded-sm p-0.5">
        <button
          className={cn(
            'p-1.5 rounded-sm transition-colors',
            viewMode === 'board'
              ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
              : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
          )}
          onClick={() => {
            onViewChange('board')
            setOpen(false)
          }}
          title="Board view"
        >
          <LayoutGrid className="w-3.5 h-3.5" />
        </button>
        <button
          className={cn(
            'p-1.5 rounded-sm transition-colors',
            viewMode === 'settings'
              ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
              : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
          )}
          onClick={() => {
            onViewChange('settings')
            setOpen(false)
          }}
          title="Settings"
        >
          <Settings className="w-3.5 h-3.5" />
        </button>
        <button
          className={cn(
            'p-1.5 rounded-sm transition-colors',
            open || isSecondaryActive
              ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
              : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
          )}
          onClick={() => setOpen((prev) => !prev)}
          title="More views"
        >
          <Menu className="w-3.5 h-3.5" />
        </button>
      </div>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 min-w-[160px] rounded-md border border-[rgba(163,163,163,0.15)] bg-[var(--color-ink)] shadow-xl py-1">
          {SECONDARY_NAV_ITEMS.map(({ mode, icon: Icon, label }) => (
            <button
              key={mode}
              className={cn(
                'w-full flex items-center gap-2.5 px-3 py-2 text-body transition-colors',
                viewMode === mode
                  ? 'text-[var(--color-paper)] bg-[var(--color-paper)]/8'
                  : 'text-[var(--color-stone)] hover:text-[var(--color-paper)] hover:bg-[var(--color-paper)]/5'
              )}
              onClick={() => {
                onViewChange(mode)
                setOpen(false)
              }}
            >
              <Icon className="w-3.5 h-3.5" />
              <span className="uppercase tracking-widest">{label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function App() {
  // Auth gate (D5 Phase 2). When auth is enabled and there's no session,
  // show the login screen instead of the main app shell — this avoids
  // mounting any of the heavyweight hooks (WebSocket, runs subscription,
  // etc.) that would 401 anyway.
  const { loading: authLoading, needsLogin } = useCurrentUser()

  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--color-void)]">
        <Loader2 className="w-5 h-5 animate-spin text-[var(--color-stone)]/60" />
      </div>
    )
  }
  if (needsLogin) {
    return <LoginPage />
  }

  return <AuthenticatedApp />
}

function AuthenticatedApp() {
  // Notification center
  const notificationCenter = useNotificationCenterProvider()
  const { show: showBrowserNotification, requestPermission: requestNotifPermission } =
    useNotifications()

  // Request browser notification permission on mount
  useEffect(() => {
    requestNotifPermission()
  }, [requestNotifPermission])

  const { runs, loading, error, connected, setRuns, refresh } = useRunsWithWebSocket({
    onNotificationCreated: notificationCenter.handleNotificationEvent,
    onPendingQuestions: notificationCenter.handleQuestionEvent,
    onQuestionAnswered: notificationCenter.handleQuestionAnswered,
    onQuestionsExpired: notificationCenter.handleQuestionsExpired,
    onBrowserNotification: showBrowserNotification,
  })
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const { theme, toggleTheme } = useTheme()
  const online = useOnline()
  const [archivedRuns, setArchivedRuns] = useState<Run[]>([])
  const [archivedLoading, setArchivedLoading] = useState(false)
  const [usageSummary, setUsageSummary] = useState<UsageSummary | null>(null)
  const [semver, setSemver] = useState('')

  useEffect(() => {
    fetchServerVersion()
      .then((v) => setSemver(v.semver))
      .catch(() => {})
  }, [])

  // Enhanced connectivity detection for offline overlay
  const { status: connectivityStatus, retryIn, lastChecked, checkNow } = useConnectivity()

  // URL-based routing
  const {
    viewMode,
    setViewMode,
    filter,
    setFilter,
    selectedRunId,
    selectedTab,
    openRunDetail,
    closeRunDetail,
    setRunDetailTab,
    settingsTab,
    setSettingsTab,
  } = useRouteSync()

  // Selected run state (loaded from API when URL has runId)
  const [selectedRun, setSelectedRun] = useState<Run | null>(null)
  const [loadingRun, setLoadingRun] = useState(false)

  // Fetch projects for workspace mapping
  useEffect(() => {
    fetchProjects().then(setProjects).catch(console.error)
  }, [])

  // Fetch usage summary for header display (includes archived runs)
  useEffect(() => {
    fetchUsageSummary().then(setUsageSummary).catch(console.error)
    // Refresh every 30 seconds to keep header updated
    const interval = setInterval(() => {
      fetchUsageSummary().then(setUsageSummary).catch(console.error)
    }, 30000)
    return () => clearInterval(interval)
  }, [])

  // Fetch run when URL changes to include a runId
  useEffect(() => {
    if (!selectedRunId) {
      setSelectedRun(null)
      return
    }

    // First check if run is in current runs list
    const runFromList = runs.find((r) => r.id === selectedRunId || r.id.startsWith(selectedRunId))
    if (runFromList) {
      setSelectedRun(runFromList)
      return
    }

    // Otherwise fetch from API (for archived runs or deep links)
    setLoadingRun(true)
    fetchRun(selectedRunId)
      .then(setSelectedRun)
      .catch(() => {
        // Run not found, close modal
        closeRunDetail()
      })
      .finally(() => setLoadingRun(false))
  }, [selectedRunId, runs, closeRunDetail])

  // Fetch archived runs when viewing archived filter
  useEffect(() => {
    if (filter.type === 'archived') {
      setArchivedLoading(true)
      fetchRuns({ archived: true, limit: 100 })
        .then(setArchivedRuns)
        .catch(console.error)
        .finally(() => setArchivedLoading(false))
    }
  }, [filter.type])

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
    // When viewing archived, use archivedRuns
    if (filter.type === 'archived') return archivedRuns

    if (filter.type === 'all') return runs

    if (filter.type === 'project') {
      return runs.filter((run) => run.project_name === filter.value)
    }

    if (filter.type === 'workspace') {
      return runs.filter((run) => {
        const workspace = projectWorkspaceMap.get(run.project_name)
        return workspace === filter.value
      })
    }

    return runs
  }, [runs, filter, projectWorkspaceMap, archivedRuns])

  const handleRunClick = useCallback(
    (run: Run) => {
      openRunDetail(run.id)
    },
    [openRunDetail]
  )

  const handleCancelRun = useCallback(
    async (run: Run) => {
      try {
        const updated = await cancelRun(run.id)
        setRuns((prev) => prev.map((r) => (r.id === updated.id ? updated : r)))
      } catch (err) {
        console.error('Failed to cancel run:', err)
      }
    },
    [setRuns]
  )

  const handleArchiveRun = useCallback(
    async (run: Run) => {
      try {
        // Remove from UI immediately (archived runs filtered on backend via WebSocket)
        setRuns((prev) => prev.filter((r) => r.id !== run.id))
        await archiveRun(run.id)
      } catch (err) {
        console.error('Failed to archive run:', err)
      }
    },
    [setRuns]
  )

  const handleStopLoop = useCallback(
    async (run: Run) => {
      try {
        const response = await stopLoop(run.id)
        if (response.success) {
          // Update will come via WebSocket, but we can optimistically update
          setRuns((prev) =>
            prev.map((r) =>
              r.id === run.id
                ? { ...r, status: 'review' as const, completion_reason: response.message }
                : r
            )
          )
        }
      } catch (err) {
        console.error('Failed to stop loop:', err)
      }
    },
    [setRuns]
  )

  const handleRunUpdated = useCallback(
    (updatedRun: Run) => {
      setRuns((prev) => prev.map((r) => (r.id === updatedRun.id ? updatedRun : r)))
      if (selectedRun?.id === updatedRun.id) {
        setSelectedRun(updatedRun)
      }
    },
    [setRuns, selectedRun?.id]
  )

  const handleDialogOpenChange = useCallback(
    (open: boolean) => {
      if (!open) {
        closeRunDetail()
      }
    },
    [closeRunDetail]
  )

  const handleTabChange = useCallback(
    (tab: string) => {
      setRunDetailTab(tab as RunDetailTab)
    },
    [setRunDetailTab]
  )

  // Global question answer handler
  const handleGlobalAnswerQuestion = useCallback(
    async (questionId: string, selectedLabels: string[]) => {
      await answerQuestion(questionId, selectedLabels)
    },
    []
  )

  const handleDismissQuestions = useCallback(() => {
    // Questions will be auto-dismissed when answered or expired
  }, [])

  const activeRuns = filteredRuns.filter((r) => r.status === 'running').length

  // Get today's cost from usage summary API (includes archived runs)
  const todayCost = usageSummary?.today_cost_usd ?? 0

  return (
    <NotificationCenterContext.Provider value={notificationCenter}>
      <div className="min-h-screen flex flex-col">
        {/* Header */}
        <header className="border-b border-[rgba(163,163,163,0.1)] shrink-0">
          <div className="flex items-center justify-between px-4 sm:px-6 h-12 sm:h-14">
            {/* Left - wordmark + filter + new + stats */}
            <div className="flex items-center gap-3 sm:gap-5">
              <button
                className="text-body sm:text-title font-normal tracking-[0.1em] text-[var(--color-paper)] hover:opacity-80 transition-opacity"
                title={semver ? `Gluon v${semver}` : undefined}
                onClick={() => setViewMode('board')}
              >
                GLUON
              </button>
              <ProjectFilter filter={filter} onFilterChange={setFilter} />
              <button
                className="flex items-center gap-1.5 px-2.5 py-1.5 text-caption uppercase tracking-widest text-[var(--color-void)] bg-[var(--color-paper)] hover:opacity-90 transition-colors rounded-sm"
                onClick={() => setCreateDialogOpen(true)}
              >
                <Plus className="w-3 h-3" />
                <span className="hidden sm:inline">New</span>
              </button>
              {activeRuns > 0 && (
                <span className="text-caption header-stats">{activeRuns} active</span>
              )}
              <button
                className="hidden sm:block text-caption text-[var(--color-harvest)] hover:underline"
                onClick={() => setViewMode('usage')}
                title="View usage details"
              >
                ${todayCost.toFixed(2)} today
              </button>
            </div>

            {/* Right - view toggle + theme + connection pulse */}
            <div className="flex items-center gap-3 sm:gap-4">
              {/* View Toggle - Desktop: all buttons visible */}
              <div className="hidden md:flex items-center gap-0.5 bg-[rgba(163,163,163,0.06)] rounded-sm p-0.5">
                {(
                  [
                    { mode: 'board', icon: LayoutGrid, title: 'Board view' },
                    { mode: 'activity', icon: Activity, title: 'Activity log' },
                    { mode: 'queue', icon: ListTodo, title: 'Work queue' },
                    { mode: 'merge', icon: GitMerge, title: 'Merge queue' },
                    { mode: 'sessions', icon: Database, title: 'SDK Sessions' },
                    { mode: 'usage', icon: BarChart3, title: 'Usage view' },
                    { mode: 'settings', icon: Settings, title: 'Settings' },
                  ] as const
                ).map(({ mode, icon: Icon, title }) => (
                  <button
                    key={mode}
                    className={cn(
                      'p-1.5 rounded-sm transition-colors',
                      viewMode === mode
                        ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                        : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                    )}
                    onClick={() => setViewMode(mode)}
                    title={title}
                  >
                    <Icon className="w-3.5 h-3.5" />
                  </button>
                ))}
              </div>
              {/* View Toggle - Mobile: Board + Settings + overflow menu */}
              <MobileNavMenu viewMode={viewMode} onViewChange={setViewMode} />
              <NotificationBell onNavigateToRun={(runId) => openRunDetail(runId)} />
              <UserMenu onOpenAdmin={() => setViewMode('admin-users')} />
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
              {/* Sonar-style connection indicator */}
              <div
                className={cn(
                  'connection-indicator w-2 h-2 rounded-full transition-colors',
                  connected
                    ? 'connected bg-[var(--color-jade)] text-[var(--color-jade)]'
                    : 'bg-[var(--color-vermillion)] text-[var(--color-vermillion)]'
                )}
                title={connected ? 'WebSocket connected' : 'Connection lost'}
              />
            </div>
          </div>
        </header>

        {/* Offline Banner */}
        {!online && (
          <div className="bg-[var(--color-vermillion)]/10 border-b border-[var(--color-vermillion)]/20 px-4 py-2 flex items-center justify-center gap-2 text-caption text-[var(--color-vermillion)]">
            <WifiOff className="w-3.5 h-3.5" />
            <span>You're offline. Some features may be limited.</span>
          </div>
        )}

        {/* Update Available Banner */}
        {online && <UpdateBanner />}

        {/* Main */}
        <main className="flex-1 flex flex-col overflow-hidden min-h-0">
          {viewMode === 'settings' ? (
            <SettingsPage tab={settingsTab} onTabChange={setSettingsTab} />
          ) : viewMode === 'admin-users' ? (
            <AdminUsersGuard />
          ) : viewMode === 'usage' ? (
            <UsagePage />
          ) : viewMode === 'activity' ? (
            <ActivityPage />
          ) : viewMode === 'queue' ? (
            <WorkQueuePage projects={projects} />
          ) : viewMode === 'merge' ? (
            <MergeQueuePage />
          ) : viewMode === 'sessions' ? (
            <SessionBrowserPage />
          ) : (filter.type === 'archived' ? archivedLoading : loading) ? (
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
              onArchiveRun={handleArchiveRun}
              onStopLoop={handleStopLoop}
              onRunUpdate={handleRunUpdated}
              onRefresh={refresh}
            />
          )}
        </main>

        <RunDetailDialog
          run={selectedRun}
          open={!!selectedRunId && !loadingRun}
          onOpenChange={handleDialogOpenChange}
          onRunUpdated={handleRunUpdated}
          initialTab={selectedTab || undefined}
          onTabChange={handleTabChange}
        />

        <CreateTaskDialog
          open={createDialogOpen}
          onOpenChange={setCreateDialogOpen}
          onTaskCreated={() => {}}
          initialProject={filter.type === 'project' ? filter.value || undefined : undefined}
        />

        {/* Global Question Modal — renders for any run's pending questions */}
        {notificationCenter.pendingQuestions.length > 0 && (
          <QuestionModal
            runId={notificationCenter.pendingQuestions[0].run_id}
            questions={notificationCenter.pendingQuestions.filter(
              (q) => q.run_id === notificationCenter.pendingQuestions[0].run_id
            )}
            onAnswer={handleGlobalAnswerQuestion}
            onClose={handleDismissQuestions}
          />
        )}

        {/* Offline overlay - shows when backend is unreachable */}
        {(connectivityStatus === 'offline' || connectivityStatus === 'backend-unreachable') && (
          <OfflineOverlay
            status={connectivityStatus}
            retryIn={retryIn}
            onRetry={checkNow}
            lastConnected={lastChecked}
          />
        )}
      </div>
    </NotificationCenterContext.Provider>
  )
}

export default App

/**
 * Defense-in-depth wrapper around `AdminUsersPage` — the backend already
 * enforces admin-only on /api/users, but rendering the page shell to a
 * non-admin would just show error toasts. Cleaner to refuse up-front.
 */
function AdminUsersGuard() {
  const { user } = useCurrentUser()
  if (!user || user.role !== 'admin') {
    return (
      <div className="flex flex-col items-center justify-center h-full px-6 text-center gap-2">
        <p className="text-display text-[var(--color-paper)]">Access denied</p>
        <p className="text-body text-[var(--color-stone)] max-w-md">
          User management is only available to admins. Ask an admin to grant you the role if you
          need access.
        </p>
      </div>
    )
  }
  return <AdminUsersPage />
}
