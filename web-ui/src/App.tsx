import {
  Activity,
  BarChart3,
  CalendarClock,
  ChevronRight,
  Database,
  GitMerge,
  LayoutGrid,
  List,
  ListTodo,
  Loader2,
  Menu,
  MoreVertical,
  Plus,
  Settings,
  WifiOff,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ActivityPage } from './components/ActivityPage'
import { AdminUsersPage } from './components/AdminUsersPage'
import { CreateTaskDialog } from './components/CreateTaskDialog'
import { KanbanBoard } from './components/KanbanBoard'
import { ListViewPage } from './components/ListViewPage'
import { LoginPage } from './components/LoginPage'
import { MergeQueuePage } from './components/MergeQueuePage'
import { NotificationBell } from './components/NotificationBell'
import { OfflineOverlay } from './components/OfflineOverlay'
import { ProjectFilter } from './components/ProjectFilter'
import { QuestionModal } from './components/QuestionModal'
import { RunDetailDialog } from './components/RunDetailDialog'
import { SchedulesPage } from './components/SchedulesPage'
import { SessionBrowserPage } from './components/SessionBrowserPage'
import { SettingsPage } from './components/SettingsPage'
import { UpdateBanner } from './components/UpdateBanner'
import { UsagePage } from './components/UsagePage'
import { UserMenu } from './components/UserMenu'
import { StatusDot } from './components/ui/StatusDot'
import { WorkQueuePage } from './components/WorkQueuePage'
import { useConnectivity } from './hooks/useConnectivity'
import { useCurrentUser } from './hooks/useCurrentUser'
import { KeyboardHelpDialog, useKeyboardShortcuts } from './hooks/useKeyboardShortcuts'
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
  | 'list'
  | 'activity'
  | 'queue'
  | 'merge'
  | 'sessions'
  | 'schedules'
  | 'usage'
  | 'settings'
  | 'admin-users'

// Items shown in the desktop "More" overflow + the mobile hamburger. The
// daily-use views (Board, List, Queue, Merge, Usage) live in the visible
// primary nav row; everything else collapses into here.
const SECONDARY_NAV_ITEMS: { mode: ViewMode; icon: typeof Activity; label: string }[] = [
  { mode: 'activity', icon: Activity, label: 'Activity' },
  { mode: 'sessions', icon: Database, label: 'Sessions' },
  { mode: 'schedules', icon: CalendarClock, label: 'Schedules' },
]

// Mobile-only extras — Merge demoted into the hamburger on small screens to
// keep the primary row tappable. Settings also lives here on mobile (on
// desktop it's a separate gear next to the user menu).
const MOBILE_SECONDARY_NAV_ITEMS: { mode: ViewMode; icon: typeof Activity; label: string }[] = [
  { mode: 'merge', icon: GitMerge, label: 'Merge Queue' },
  ...SECONDARY_NAV_ITEMS,
  { mode: 'settings', icon: Settings, label: 'Settings' },
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

  // Mobile primary: Board / List / Queue / Usage. Everything else
  // (Merge, Activity, Sessions, Schedules, Settings) lives in the hamburger
  // so the always-tappable row stays at 4 icons + menu.
  const MOBILE_PRIMARY: { mode: ViewMode; icon: typeof Activity; title: string }[] = [
    { mode: 'board', icon: LayoutGrid, title: 'Board view' },
    { mode: 'list', icon: List, title: 'List view' },
    { mode: 'queue', icon: ListTodo, title: 'Work Queue' },
    { mode: 'usage', icon: BarChart3, title: 'Usage' },
  ]
  const isSecondaryActive = MOBILE_SECONDARY_NAV_ITEMS.some((item) => item.mode === viewMode)

  return (
    <div className="md:hidden relative" ref={menuRef}>
      <div className="flex items-center gap-0.5 bg-[rgba(163,163,163,0.06)] rounded-sm p-0.5">
        {MOBILE_PRIMARY.map(({ mode, icon: Icon, title }) => (
          <button
            key={mode}
            className={cn(
              'min-w-[44px] min-h-[44px] inline-flex items-center justify-center rounded-sm transition-colors',
              viewMode === mode
                ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
            )}
            onClick={() => {
              onViewChange(mode)
              setOpen(false)
            }}
            title={title}
            aria-label={title}
            aria-current={viewMode === mode ? 'page' : undefined}
          >
            <Icon className="w-3.5 h-3.5" />
          </button>
        ))}
        <button
          className={cn(
            'min-w-[44px] min-h-[44px] inline-flex items-center justify-center rounded-sm transition-colors',
            open || isSecondaryActive
              ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
              : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
          )}
          onClick={() => setOpen((prev) => !prev)}
          title="More views"
          aria-label="More views"
          aria-expanded={open}
          aria-haspopup="menu"
        >
          <Menu className="w-3.5 h-3.5" />
        </button>
      </div>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-50 min-w-[160px] rounded-md border border-[rgba(163,163,163,0.15)] bg-[var(--color-ink)] shadow-xl py-1"
          role="menu"
        >
          {MOBILE_SECONDARY_NAV_ITEMS.map(({ mode, icon: Icon, label }) => (
            <button
              key={mode}
              role="menuitem"
              className={cn(
                'w-full flex items-center gap-2.5 px-3 min-h-[44px] text-body transition-colors',
                viewMode === mode
                  ? 'text-[var(--color-paper)] bg-[var(--color-paper)]/8'
                  : 'text-[var(--color-stone)] hover:text-[var(--color-paper)] hover:bg-[var(--color-paper)]/5'
              )}
              onClick={() => {
                onViewChange(mode)
                setOpen(false)
              }}
              aria-label={label}
              aria-current={viewMode === mode ? 'page' : undefined}
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

function DesktopMoreMenu({
  viewMode,
  onViewChange,
}: {
  viewMode: string
  onViewChange: (mode: ViewMode) => void
}) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // Desktop primary nav now includes Queue + Merge (daily-use). SECONDARY_NAV_ITEMS
  // already excludes those — it's just Activity / Sessions / Schedules.
  const desktopItems = SECONDARY_NAV_ITEMS
  const isSecondaryActive = desktopItems.some((item) => item.mode === viewMode)

  return (
    <div className="relative" ref={menuRef}>
      <button
        className={cn(
          'flex items-center gap-1.5 px-2 py-1.5 rounded-sm transition-colors text-caption uppercase tracking-widest',
          open || isSecondaryActive
            ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
            : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
        )}
        onClick={() => setOpen((prev) => !prev)}
        title="More views"
        aria-label="More views"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <MoreVertical className="w-3.5 h-3.5" />
        <span>More</span>
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-50 min-w-[160px] rounded-md border border-[rgba(163,163,163,0.15)] bg-[var(--color-ink)] shadow-xl py-1"
          role="menu"
        >
          {desktopItems.map(({ mode, icon: Icon, label }) => (
            <button
              key={mode}
              role="menuitem"
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
              aria-label={label}
              aria-current={viewMode === mode ? 'page' : undefined}
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
  const [createDialogProject, setCreateDialogProject] = useState<string | undefined>(undefined)
  const [projects, setProjects] = useState<Project[]>([])
  useTheme()
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

  // The full-screen overlay supersedes the thin banner — when it's up, the
  // banner would render redundantly behind it.
  const offlineOverlayVisible =
    connectivityStatus === 'offline' || connectivityStatus === 'backend-unreachable'

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
    preferencesGroup,
    setPreferencesGroup,
  } = useRouteSync()

  // Keyboard shortcuts
  const { helpOpen, setHelpOpen } = useKeyboardShortcuts(
    useMemo(
      () => ({
        onNewTask: () => setCreateDialogOpen(true),
        onNavigateBoard: () => setViewMode('board'),
        onNavigateList: () => setViewMode('list'),
        onNavigateUsage: () => setViewMode('usage'),
        onNavigateSettings: () => setViewMode('settings'),
        onRefresh: () => {
          refresh()
        },
      }),
      [setViewMode, refresh]
    )
  )

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
                className="text-title font-normal tracking-[0.1em] text-[var(--color-paper)] hover:opacity-80 transition-opacity"
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
              {/* Active-runs counter — desktop. Uses the canonical StatusDot
                  primitive (Stream 2) for the leading pulsing glyph so the
                  styling stays consistent with KanbanBoard / WorkQueue. */}
              {activeRuns > 0 && (
                <button
                  type="button"
                  onClick={() => setViewMode('board')}
                  className="hidden sm:inline-flex items-center gap-1.5 text-caption header-stats hover:opacity-80 transition-opacity"
                  title="View active runs"
                  aria-label={`${activeRuns} active run${activeRuns === 1 ? '' : 's'}`}
                >
                  <StatusDot state="running" size="md" />
                  <span>{activeRuns} active</span>
                </button>
              )}
              {/* Cost-today link — desktop. Subtle underline-on-hover plus
                  a tiny chevron at rest, so it reads as navigable without
                  shouting (Tokyo Minimal: signal not decoration). */}
              <button
                type="button"
                className="hidden sm:inline-flex items-center gap-1 text-caption text-[var(--color-harvest)] hover:opacity-80 transition-opacity group"
                onClick={() => setViewMode('usage')}
                title="View usage details"
              >
                <span className="group-hover:underline underline-offset-2">
                  ${todayCost.toFixed(2)} today
                </span>
                <ChevronRight className="w-3 h-3 opacity-50 group-hover:opacity-100 transition-opacity" />
              </button>
              {/* Mobile status pill — collapses both stats into one tappable
                  affordance so the small-screen header doesn't lose the cost
                  signal. Tap → Usage; pulsing dot if any active. */}
              <button
                type="button"
                onClick={() => setViewMode('usage')}
                className="sm:hidden inline-flex items-center gap-1.5 px-2 py-1 rounded-sm text-caption text-[var(--color-harvest)] bg-[rgba(163,163,163,0.06)] hover:bg-[rgba(163,163,163,0.1)] transition-colors"
                title="View usage details"
                aria-label={
                  activeRuns > 0
                    ? `${activeRuns} active, $${todayCost.toFixed(2)} today`
                    : `$${todayCost.toFixed(2)} today`
                }
              >
                {activeRuns > 0 && <StatusDot state="running" size="sm" />}
                <span>${todayCost.toFixed(2)}</span>
              </button>
            </div>

            {/* Right - view toggle + theme + connection pulse */}
            <div className="flex items-center gap-3 sm:gap-4">
              {/* View Toggle - Desktop: primary daily-use views + overflow dropdown.
                  Settings is intentionally NOT in this group — it lives as a
                  separate gear icon next to the user menu (lower-frequency,
                  config-style action). */}
              <div className="hidden md:flex items-center gap-0.5 bg-[rgba(163,163,163,0.06)] rounded-sm p-0.5">
                {(
                  [
                    { mode: 'board', icon: LayoutGrid, label: 'Board' },
                    { mode: 'list', icon: List, label: 'List' },
                    { mode: 'queue', icon: ListTodo, label: 'Queue' },
                    { mode: 'merge', icon: GitMerge, label: 'Merge' },
                    { mode: 'usage', icon: BarChart3, label: 'Usage' },
                  ] as const
                ).map(({ mode, icon: Icon, label }) => (
                  <button
                    key={mode}
                    className={cn(
                      'flex items-center gap-1.5 px-2 py-1.5 rounded-sm transition-colors text-caption uppercase tracking-widest',
                      viewMode === mode
                        ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                        : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                    )}
                    onClick={() => setViewMode(mode)}
                  >
                    <Icon className="w-3.5 h-3.5" />
                    <span>{label}</span>
                  </button>
                ))}
                <DesktopMoreMenu viewMode={viewMode} onViewChange={setViewMode} />
              </div>
              {/* Mobile: Board + List + Queue + Usage + hamburger */}
              <MobileNavMenu viewMode={viewMode} onViewChange={setViewMode} />
              <NotificationBell onNavigateToRun={(runId) => openRunDetail(runId)} />
              {/* Settings gear — desktop only (mobile users find Settings in the hamburger).
                  Sits at the same hierarchy level as the user menu — both are
                  "account / configuration" affordances rather than primary views. */}
              <button
                type="button"
                className={cn(
                  'hidden md:flex p-1.5 rounded-sm transition-colors',
                  viewMode === 'settings'
                    ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                    : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                )}
                onClick={() => setViewMode('settings')}
                title="Settings"
                aria-label="Settings"
              >
                <Settings className="w-3.5 h-3.5" />
              </button>
              <UserMenu
                onOpenAdmin={() => setViewMode('admin-users')}
                onOpenAccountSettings={() => {
                  setViewMode('settings')
                  setSettingsTab('account')
                }}
              />
              {/* Sonar-style connection indicator. Wrapped in a button so the
                  44×44 hit target is reachable on touch — the visible dot stays
                  8px but the surrounding clickable area lets users retry when
                  the connection is lost. Status is conveyed by colour + aria-label. */}
              <button
                type="button"
                className="min-w-[44px] min-h-[44px] inline-flex items-center justify-center rounded-sm"
                title={connected ? 'WebSocket connected' : 'Connection lost — click to retry'}
                aria-label={connected ? 'WebSocket connected' : 'Connection lost — click to retry'}
                aria-live="polite"
                onClick={() => {
                  if (!connected) checkNow()
                }}
              >
                <span
                  className={cn(
                    'connection-indicator w-2 h-2 rounded-full transition-colors',
                    connected
                      ? 'connected bg-[var(--color-jade)] text-[var(--color-jade)]'
                      : 'bg-[var(--color-vermillion)] text-[var(--color-vermillion)]'
                  )}
                />
              </button>
            </div>
          </div>
        </header>

        {/* Offline Banner — hidden while the full overlay is up to avoid a double render */}
        {!online && !offlineOverlayVisible && (
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
            <SettingsPage
              tab={settingsTab}
              onTabChange={setSettingsTab}
              preferencesGroup={preferencesGroup}
              onPreferencesGroupChange={setPreferencesGroup}
            />
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
          ) : viewMode === 'schedules' ? (
            <SchedulesPage />
          ) : viewMode === 'list' ? (
            <ListViewPage
              runs={filteredRuns}
              onRunUpdate={handleRunUpdated}
              onRefresh={refresh}
              onNewTaskForProject={(projectName) => {
                setCreateDialogProject(projectName)
                setCreateDialogOpen(true)
              }}
            />
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
          onOpenChange={(open) => {
            setCreateDialogOpen(open)
            if (!open) setCreateDialogProject(undefined)
          }}
          onTaskCreated={() => {}}
          initialProject={
            createDialogProject ||
            (filter.type === 'project' ? filter.value || undefined : undefined)
          }
        />

        {/* Keyboard-shortcut reference, toggled with `?` */}
        <KeyboardHelpDialog open={helpOpen} onOpenChange={setHelpOpen} />

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
        {offlineOverlayVisible && (
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
