import {
  AlertCircle,
  Bell,
  Check,
  ChevronDown,
  ChevronRight,
  Download,
  FolderOpen,
  GitBranch,
  Loader2,
  Moon,
  Plus,
  RefreshCw,
  Settings,
  Settings2,
  Sun,
  Trash2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { SaveableSetting } from '@/components/ui/SaveableSetting'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import type { PreferencesGroup } from '@/hooks/useRouteSync'
import { useTheme } from '@/hooks/useTheme'
import {
  cloneRepository,
  createWorkspace,
  deleteProject,
  deleteWorkspace,
  fetchFormulas,
  fetchProjects,
  fetchSandboxStatus,
  fetchSettings,
  fetchWorkspaces,
  refreshAllGitStatuses,
  scanWorkspace,
  testVercelToken,
  updateSetting,
} from '@/lib/api'
import type { FormulaTemplate, Project, ScanResultResponse, Workspace } from '@/lib/types'
import { cn } from '@/lib/utils'
import { fetchServerVersion, type VersionInfo } from '@/lib/version'
import { ConnectedAccountsSection } from './ConnectedAccountsSection'
import { GitSyncButton } from './GitSyncButton'
import { ChangePasswordForm } from './UserMenu'
import { WorkspaceSettingsDialog } from './WorkspaceSettingsDialog'

type Tab = 'workspaces' | 'projects' | 'account' | 'preferences' | 'formulas'

const PREFERENCES_GROUPS: { id: PreferencesGroup; label: string }[] = [
  { id: 'agent', label: 'Agent' },
  { id: 'integrations', label: 'Integrations' },
  { id: 'workspace', label: 'Workspace' },
  { id: 'system', label: 'System' },
]

/**
 * Jump-nav rail for the Preferences tab. The tab is a long stack of setting
 * cards; this lets the user jump to a group and highlights the active one.
 * Driven by the URL-synced `preferencesGroup` so deep links land in place.
 */
function PreferencesSubNav({
  active,
  onChange,
}: {
  active: PreferencesGroup
  onChange: (group: PreferencesGroup) => void
}) {
  return (
    <nav
      aria-label="Preferences sections"
      className="flex flex-wrap gap-0.5 bg-[rgba(163,163,163,0.06)] rounded-sm p-0.5"
    >
      {PREFERENCES_GROUPS.map(({ id, label }) => (
        <button
          key={id}
          type="button"
          aria-current={active === id ? 'true' : undefined}
          className={cn(
            'px-3 py-1.5 text-caption uppercase tracking-widest rounded-sm transition-colors',
            active === id
              ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
              : 'text-[var(--color-stone)]/80 hover:text-[var(--color-stone)]'
          )}
          onClick={() => {
            onChange(id)
            document
              .getElementById(`prefs-${id}`)
              ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }}
        >
          {label}
        </button>
      ))}
    </nav>
  )
}

type LlmProvider = 'bedrock' | 'anthropic' | 'vertex' | 'foundry'

const LLM_PROVIDERS: readonly LlmProvider[] = ['bedrock', 'anthropic', 'vertex', 'foundry'] as const

const LLM_PROVIDER_LABELS: Record<LlmProvider, string> = {
  bedrock: 'AWS Bedrock',
  anthropic: 'Anthropic (Direct)',
  vertex: 'Google Vertex AI',
  foundry: 'Microsoft Foundry',
}

// Per-provider hint shown under the toggle when that provider is active.
const LLM_PROVIDER_HINTS: Record<LlmProvider, React.ReactNode> = {
  bedrock: (
    <>
      Uses your AWS credentials and Bedrock billing. Required env:{' '}
      <code className="text-[var(--color-paper)]/80">AWS_REGION</code> +{' '}
      <code className="text-[var(--color-paper)]/80">AWS_BEARER_TOKEN_BEDROCK</code> (or access
      keys).
    </>
  ),
  anthropic: (
    <>
      Requires <code className="text-[var(--color-paper)]/80">ANTHROPIC_API_KEY</code> env var or{' '}
      <code className="text-[var(--color-paper)]/80">claude login</code>. New agent runs will use
      Anthropic model IDs. Running agents are not affected.
    </>
  ),
  vertex: (
    <>
      Requires <code className="text-[var(--color-paper)]/80">ANTHROPIC_VERTEX_PROJECT_ID</code> +{' '}
      <code className="text-[var(--color-paper)]/80">CLOUD_ML_REGION</code> (global / us / eu /
      us-east5 / ...). Auth via{' '}
      <code className="text-[var(--color-paper)]/80">gcloud auth application-default login</code> on
      the host, or a service account key at{' '}
      <code className="text-[var(--color-paper)]/80">GOOGLE_APPLICATION_CREDENTIALS</code>.
    </>
  ),
  foundry: (
    <>
      Requires <code className="text-[var(--color-paper)]/80">ANTHROPIC_FOUNDRY_RESOURCE</code>{' '}
      (your Azure resource name) and either{' '}
      <code className="text-[var(--color-paper)]/80">ANTHROPIC_FOUNDRY_API_KEY</code> or Entra ID
      via <code className="text-[var(--color-paper)]/80">az login</code> / managed identity.
    </>
  ),
}

interface SettingsPageProps {
  tab?: Tab
  onTabChange?: (tab: Tab) => void
  preferencesGroup?: PreferencesGroup
  onPreferencesGroupChange?: (group: PreferencesGroup) => void
}

export function SettingsPage({
  tab: controlledTab,
  onTabChange,
  preferencesGroup,
  onPreferencesGroupChange,
}: SettingsPageProps) {
  const navigate = useNavigate()
  const { theme, preference, setTheme } = useTheme()
  const { user: currentUser } = useCurrentUser()
  // Use controlled tab if provided, otherwise manage internally
  const tab = controlledTab ?? 'workspaces'
  const setTab = onTabChange ?? (() => {})
  const prefGroup: PreferencesGroup = preferencesGroup ?? 'agent'
  const setPrefGroup = onPreferencesGroupChange ?? (() => {})
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Add workspace form state
  const [showAddForm, setShowAddForm] = useState(false)
  const [newWorkspaceName, setNewWorkspaceName] = useState('')
  const [newWorkspacePath, setNewWorkspacePath] = useState('')
  const [autoScan, setAutoScan] = useState(true)
  const [addingWorkspace, setAddingWorkspace] = useState(false)

  // Scanning state
  const [scanningId, setScanningId] = useState<string | null>(null)
  const [scanResult, setScanResult] = useState<ScanResultResponse | null>(null)

  // Clone repository state
  const [cloneDialogWorkspaceId, setCloneDialogWorkspaceId] = useState<string | null>(null)
  const [cloneUrl, setCloneUrl] = useState('')
  const [cloning, setCloning] = useState(false)

  // Delete confirmation
  const [deleteConfirm, setDeleteConfirm] = useState<{
    type: 'workspace' | 'project'
    id: string
    name: string
  } | null>(null)

  // Workspace settings dialog
  const [settingsWorkspace, setSettingsWorkspace] = useState<{
    id: string
    name: string
  } | null>(null)

  // Formula state
  const [formulas, setFormulas] = useState<FormulaTemplate[]>([])
  const [loadingFormulas, setLoadingFormulas] = useState(false)
  const [expandedFormula, setExpandedFormula] = useState<string | null>(null)

  // Settings state
  const [llmProvider, setLlmProvider] = useState<LlmProvider>('bedrock')
  const [autoCreatePr, setAutoCreatePr] = useState(true)
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [savedKey, setSavedKey] = useState<string | null>(null)
  const [gitUserName, setGitUserName] = useState('')
  const [gitUserEmail, setGitUserEmail] = useState('')
  const [initialGitUserName, setInitialGitUserName] = useState('')
  const [initialGitUserEmail, setInitialGitUserEmail] = useState('')

  // Sandbox settings state
  const [sandboxEnabled, setSandboxEnabled] = useState(true)
  const [sandboxAvailable, setSandboxAvailable] = useState(false)
  const [sandboxRuntime, setSandboxRuntime] = useState<string | null>(null)

  // Experimental features
  const [skillsEnabled, setSkillsEnabled] = useState(false)

  // SDK 0.1.35 feature settings
  const [extendedContextEnabled, setExtendedContextEnabled] = useState(false)
  const [fileCheckpointingEnabled, setFileCheckpointingEnabled] = useState(false)
  const [notificationsEnabled, setNotificationsEnabled] = useState(true)
  const [disallowedTools, setDisallowedTools] = useState<string[]>([])

  // Version info
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null)

  // Vercel CLI integration
  const [vercelCliEnabled, setVercelCliEnabled] = useState(false)
  const [vercelToken, setVercelToken] = useState('')
  const [initialVercelToken, setInitialVercelToken] = useState('')
  const [vercelTokenFromEnv, setVercelTokenFromEnv] = useState(false)
  const [vercelTesting, setVercelTesting] = useState(false)
  const [vercelTestResult, setVercelTestResult] = useState<{
    valid: boolean
    account?: string
    error?: string
  } | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [ws, prj, settings, sandboxStatus] = await Promise.all([
        fetchWorkspaces(),
        fetchProjects(),
        fetchSettings(),
        fetchSandboxStatus(),
      ])
      setWorkspaces(ws)
      setProjects(prj)
      setLlmProvider(
        LLM_PROVIDERS.includes(settings.llm_provider as LlmProvider)
          ? (settings.llm_provider as LlmProvider)
          : 'bedrock'
      )
      setAutoCreatePr(settings.auto_create_pr !== 'false')
      setGitUserName(settings.git_user_name || '')
      setGitUserEmail(settings.git_user_email || '')
      setInitialGitUserName(settings.git_user_name || '')
      setInitialGitUserEmail(settings.git_user_email || '')
      setSandboxEnabled(sandboxStatus.enabled)
      setSandboxAvailable(sandboxStatus.available)
      setSandboxRuntime(sandboxStatus.runtime)
      setSkillsEnabled(settings.skills_enabled !== 'false')
      setExtendedContextEnabled(settings.extended_context_enabled === 'true')
      setFileCheckpointingEnabled(settings.file_checkpointing_enabled === 'true')
      setNotificationsEnabled(settings.notifications_enabled !== 'false') // default true
      try {
        setDisallowedTools(JSON.parse(settings.disallowed_tools || '[]'))
      } catch {
        setDisallowedTools([])
      }
      setVercelCliEnabled(settings.vercel_cli_enabled === 'true')
      setVercelToken(settings.vercel_token || '')
      setInitialVercelToken(settings.vercel_token || '')
      setVercelTokenFromEnv(settings._vercel_token_from_env === 'true')
      fetchServerVersion()
        .then(setVersionInfo)
        .catch(() => {})
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  // Load formulas when tab switches to formulas
  useEffect(() => {
    if (tab === 'formulas' && formulas.length === 0 && !loadingFormulas) {
      setLoadingFormulas(true)
      fetchFormulas()
        .then((data) => setFormulas(data.formulas))
        .catch((err) => console.error('Failed to load formulas:', err))
        .finally(() => setLoadingFormulas(false))
    }
  }, [tab, formulas.length, loadingFormulas])

  const handleAddWorkspace = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newWorkspaceName.trim() || !newWorkspacePath.trim()) return

    setAddingWorkspace(true)
    try {
      const ws = await createWorkspace({
        name: newWorkspaceName.trim(),
        path: newWorkspacePath.trim(),
        auto_scan: autoScan,
      })
      setWorkspaces((prev) => [...prev, ws])
      setShowAddForm(false)
      setNewWorkspaceName('')
      setNewWorkspacePath('')
      // Refresh to get updated project counts
      loadData()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add workspace')
    } finally {
      setAddingWorkspace(false)
    }
  }

  const handleScanWorkspace = async (workspaceId: string) => {
    setScanningId(workspaceId)
    setScanResult(null)
    try {
      const result = await scanWorkspace(workspaceId)
      setScanResult(result)
      // Refresh data to show new projects
      loadData()
      // Also refresh git status for all projects (useful after manual git operations)
      refreshAllGitStatuses().catch((err) => {
        console.warn('Failed to refresh git statuses:', err)
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to scan workspace')
    } finally {
      setScanningId(null)
    }
  }

  const handleCloneRepository = async (workspaceId: string) => {
    if (!cloneUrl.trim()) return

    setCloning(true)
    setError(null)
    try {
      const result = await cloneRepository(workspaceId, cloneUrl.trim())
      setScanResult(result.scan_result)
      setCloneDialogWorkspaceId(null)
      setCloneUrl('')
      // Refresh data to show new projects
      loadData()
      refreshAllGitStatuses().catch((err) => {
        console.warn('Failed to refresh git statuses:', err)
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to clone repository')
    } finally {
      setCloning(false)
    }
  }

  const handleDeleteWorkspace = async (workspaceId: string) => {
    try {
      await deleteWorkspace(workspaceId)
      setWorkspaces((prev) => prev.filter((w) => w.id !== workspaceId))
      setDeleteConfirm(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete workspace')
    }
  }

  const handleDeleteProject = async (projectId: string) => {
    try {
      await deleteProject(projectId)
      setProjects((prev) => prev.filter((p) => p.id !== projectId))
      setDeleteConfirm(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete project')
    }
  }

  const handleChangeLlmProvider = async (value: LlmProvider) => {
    setSavingKey('llm_provider')
    try {
      await updateSetting('llm_provider', value)
      setLlmProvider(value)
      setSavedKey('llm_provider')
      setTimeout(() => setSavedKey(null), 2000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update provider')
    } finally {
      setSavingKey(null)
    }
  }

  const handleToggleAutoCreatePr = async () => {
    const newValue = !autoCreatePr
    setSavingKey('auto_create_pr')
    try {
      await updateSetting('auto_create_pr', newValue ? 'true' : 'false')
      setAutoCreatePr(newValue)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update setting')
    } finally {
      setSavingKey(null)
    }
  }

  const handleSaveGitIdentity = async () => {
    setSavingKey('git_identity')
    try {
      await Promise.all([
        updateSetting('git_user_name', gitUserName),
        updateSetting('git_user_email', gitUserEmail),
      ])
      setInitialGitUserName(gitUserName)
      setInitialGitUserEmail(gitUserEmail)
      setSavedKey('git_identity')
      setTimeout(() => setSavedKey(null), 2000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save git identity')
    } finally {
      setSavingKey(null)
    }
  }

  const handleToggleSandbox = async () => {
    const newValue = !sandboxEnabled
    setSavingKey('sandbox')
    try {
      await updateSetting('sandbox_enabled', newValue ? 'true' : 'false')
      setSandboxEnabled(newValue)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update sandbox setting')
    } finally {
      setSavingKey(null)
    }
  }

  const handleToggleSkills = async () => {
    const newValue = !skillsEnabled
    setSavingKey('skills')
    try {
      await updateSetting('skills_enabled', newValue ? 'true' : 'false')
      setSkillsEnabled(newValue)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update setting')
    } finally {
      setSavingKey(null)
    }
  }

  const handleToggleExtendedContext = async () => {
    const newValue = !extendedContextEnabled
    setSavingKey('extended_context')
    try {
      await updateSetting('extended_context_enabled', newValue ? 'true' : 'false')
      setExtendedContextEnabled(newValue)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update setting')
    } finally {
      setSavingKey(null)
    }
  }

  const handleToggleFileCheckpointing = async () => {
    const newValue = !fileCheckpointingEnabled
    setSavingKey('file_checkpointing')
    try {
      await updateSetting('file_checkpointing_enabled', newValue ? 'true' : 'false')
      setFileCheckpointingEnabled(newValue)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update setting')
    } finally {
      setSavingKey(null)
    }
  }

  const handleToggleNotifications = async () => {
    const newValue = !notificationsEnabled
    setSavingKey('notifications')
    try {
      await updateSetting('notifications_enabled', newValue ? 'true' : 'false')
      setNotificationsEnabled(newValue)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update setting')
    } finally {
      setSavingKey(null)
    }
  }

  const handleToggleDisallowedTool = async (tool: string) => {
    const newList = disallowedTools.includes(tool)
      ? disallowedTools.filter((t) => t !== tool)
      : [...disallowedTools, tool]
    setSavingKey('disallowed_tools')
    try {
      await updateSetting('disallowed_tools', JSON.stringify(newList))
      setDisallowedTools(newList)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update setting')
    } finally {
      setSavingKey(null)
    }
  }

  const handleToggleVercelCli = async () => {
    const newValue = !vercelCliEnabled
    setSavingKey('vercel_cli')
    try {
      await updateSetting('vercel_cli_enabled', newValue ? 'true' : 'false')
      setVercelCliEnabled(newValue)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update setting')
    } finally {
      setSavingKey(null)
    }
  }

  const handleSaveVercelToken = async () => {
    setSavingKey('vercel_token')
    try {
      await updateSetting('vercel_token', vercelToken)
      setInitialVercelToken(vercelToken)
      setSavedKey('vercel_token')
      setTimeout(() => setSavedKey(null), 2000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save Vercel token')
    } finally {
      setSavingKey(null)
    }
  }

  const handleTestVercelToken = async () => {
    setVercelTesting(true)
    setVercelTestResult(null)
    try {
      const result = await testVercelToken(vercelToken)
      setVercelTestResult(result)
    } catch (err) {
      setVercelTestResult({
        valid: false,
        error: err instanceof Error ? err.message : 'Test failed',
      })
    } finally {
      setVercelTesting(false)
    }
  }

  const vercelTokenDirty = vercelToken !== initialVercelToken

  const gitIdentityDirty =
    gitUserName !== initialGitUserName || gitUserEmail !== initialGitUserEmail

  // Group projects by workspace
  const projectsByWorkspace = projects.reduce(
    (acc, project) => {
      const wsId = project.workspace_id || 'standalone'
      if (!acc[wsId]) acc[wsId] = []
      acc[wsId].push(project)
      return acc
    },
    {} as Record<string, Project[]>
  )

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="mark mark-running w-2 h-2" />
      </div>
    )
  }

  return (
    <div className="h-full overflow-auto p-4 sm:p-6">
      <div className="max-w-4xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h2 className="text-title">Settings</h2>
          <div className="flex items-center gap-0.5 bg-[rgba(163,163,163,0.06)] rounded-sm p-0.5">
            <button
              className={cn(
                'px-3 py-1.5 text-caption uppercase tracking-widest rounded-sm transition-colors',
                tab === 'workspaces'
                  ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                  : 'text-[var(--color-stone)]/80 hover:text-[var(--color-stone)]'
              )}
              onClick={() => setTab('workspaces')}
            >
              Workspaces
            </button>
            <button
              className={cn(
                'px-3 py-1.5 text-caption uppercase tracking-widest rounded-sm transition-colors',
                tab === 'projects'
                  ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                  : 'text-[var(--color-stone)]/80 hover:text-[var(--color-stone)]'
              )}
              onClick={() => setTab('projects')}
            >
              Projects
            </button>
            <button
              className={cn(
                'px-3 py-1.5 text-caption uppercase tracking-widest rounded-sm transition-colors',
                tab === 'account'
                  ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                  : 'text-[var(--color-stone)]/80 hover:text-[var(--color-stone)]'
              )}
              onClick={() => setTab('account')}
            >
              Account
            </button>
            <button
              className={cn(
                'px-3 py-1.5 text-caption uppercase tracking-widest rounded-sm transition-colors flex items-center gap-1.5',
                tab === 'preferences'
                  ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                  : 'text-[var(--color-stone)]/80 hover:text-[var(--color-stone)]'
              )}
              onClick={() => setTab('preferences')}
            >
              <Settings className="w-3 h-3" />
              Preferences
            </button>
            <button
              className={cn(
                'px-3 py-1.5 text-caption uppercase tracking-widest rounded-sm transition-colors',
                tab === 'formulas'
                  ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                  : 'text-[var(--color-stone)]/80 hover:text-[var(--color-stone)]'
              )}
              onClick={() => setTab('formulas')}
            >
              Formulas
            </button>
          </div>
        </div>

        {/* Error display */}
        {error && (
          <div className="flex items-center gap-2 p-3 bg-[var(--color-vermillion)]/10 border border-[var(--color-vermillion)]/20 rounded-sm">
            <AlertCircle className="w-4 h-4 text-[var(--color-vermillion)]" />
            <span className="text-body text-[var(--color-vermillion)]">{error}</span>
            <button
              className="ml-auto text-[var(--color-vermillion)] hover:opacity-80"
              onClick={() => setError(null)}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Scan result notification */}
        {scanResult && (
          <div className="flex flex-col gap-2 p-3 bg-[var(--color-jade)]/10 border border-[var(--color-jade)]/20 rounded-sm">
            <div className="flex items-center gap-2">
              <Check className="w-4 h-4 text-[var(--color-jade)]" />
              <span className="text-body text-[var(--color-jade)]">
                Found {scanResult.projects_found} projects
                {scanResult.projects_added?.length > 0 &&
                  `, added ${scanResult.projects_added.length} new`}
                {scanResult.projects_removed?.length > 0 &&
                  `, removed ${scanResult.projects_removed.length}`}
              </span>
              <button
                className="ml-auto text-[var(--color-jade)] hover:opacity-80"
                onClick={() => setScanResult(null)}
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            {scanResult.projects_added?.length > 0 && (
              <div className="text-body text-[var(--color-jade)]/80 pl-6">
                Added: {scanResult.projects_added.join(', ')}
              </div>
            )}
            {scanResult.projects_removed?.length > 0 && (
              <div className="text-body text-[var(--color-vermillion)]/80 pl-6">
                Removed: {scanResult.projects_removed.join(', ')}
              </div>
            )}
          </div>
        )}

        {/* Workspaces Tab */}
        {tab === 'workspaces' && (
          <div className="space-y-4">
            {/* Workspace list */}
            {workspaces.length === 0 ? (
              <div className="text-center py-8 text-[var(--color-stone)]/80 text-body">
                No workspaces configured. Add one to start managing projects.
              </div>
            ) : (
              <div className="space-y-3">
                {workspaces.map((ws) => (
                  <div
                    key={ws.id}
                    className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm"
                  >
                    <div className="flex items-center justify-between gap-4">
                      <div className="flex items-center gap-3 min-w-0">
                        <FolderOpen className="w-4 h-4 text-[var(--color-stone)]/80 shrink-0" />
                        <div className="flex items-center gap-2 min-w-0">
                          <h3 className="text-title font-medium text-[var(--color-paper)] shrink-0">
                            {ws.name}
                          </h3>
                          <span className="text-caption text-[var(--color-stone)]/60 shrink-0">
                            ({ws.project_count} project{ws.project_count !== 1 ? 's' : ''})
                          </span>
                          <span className="text-caption text-[var(--color-stone)]/50 truncate">
                            {ws.path}
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          className={cn(
                            'p-1.5 rounded-sm transition-colors',
                            scanningId === ws.id
                              ? 'bg-[var(--color-sky)]/10 text-[var(--color-sky)]'
                              : 'hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/80 hover:text-[var(--color-stone)]'
                          )}
                          onClick={() => handleScanWorkspace(ws.id)}
                          disabled={scanningId === ws.id}
                          title="Rescan projects and refresh git status"
                        >
                          <RefreshCw
                            className={cn('w-3.5 h-3.5', scanningId === ws.id && 'animate-spin')}
                          />
                        </button>
                        <button
                          className="p-1.5 rounded-sm hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/80 hover:text-[var(--color-stone)] transition-colors"
                          onClick={() => setSettingsWorkspace({ id: ws.id, name: ws.name })}
                          title="Workspace settings"
                        >
                          <Settings2 className="w-3.5 h-3.5" />
                        </button>
                        <button
                          className="p-1.5 rounded-sm hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/80 hover:text-[var(--color-stone)] transition-colors"
                          onClick={() => {
                            setCloneDialogWorkspaceId(ws.id)
                            setCloneUrl('')
                          }}
                          title="Clone GitHub repository"
                        >
                          <Download className="w-3.5 h-3.5" />
                        </button>
                        <button
                          className="p-1.5 rounded-sm hover:bg-[var(--color-vermillion)]/10 text-[var(--color-stone)]/80 hover:text-[var(--color-vermillion)] transition-colors"
                          onClick={() =>
                            setDeleteConfirm({ type: 'workspace', id: ws.id, name: ws.name })
                          }
                          title="Delete workspace"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>

                    {/* Projects in this workspace */}
                    {projectsByWorkspace[ws.id] && projectsByWorkspace[ws.id].length > 0 && (
                      <div className="mt-3 pt-3 border-t border-[rgba(163,163,163,0.08)] space-y-0.5">
                        {projectsByWorkspace[ws.id].map((project) => (
                          <div
                            key={project.id}
                            className="grid grid-cols-[1fr_auto_auto] items-center gap-x-3 text-caption px-2 py-1 -mx-2 rounded-sm hover:bg-[rgba(163,163,163,0.08)] transition-colors cursor-pointer"
                            onClick={() => {
                              navigate(`/board?project=${encodeURIComponent(project.name)}`)
                            }}
                          >
                            <span className="text-[var(--color-stone)]/80 truncate pl-4">
                              {project.name}
                            </span>
                            <span className="flex items-center gap-1.5 text-[var(--color-stone)]/70 text-caption min-w-[180px] justify-end">
                              <GitBranch className="w-3 h-3 text-[var(--color-stone)]/60 shrink-0" />
                              <span className="truncate max-w-[140px]">
                                {project.git_branch || 'no git'}
                              </span>
                            </span>
                            <div
                              className="min-w-[60px] flex justify-end"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <GitSyncButton
                                project={project}
                                compact
                                onSyncComplete={(success, _message, updatedProject) => {
                                  if (success && updatedProject) {
                                    // Incrementally update just this project's state
                                    setProjects((prev) =>
                                      prev.map((p) =>
                                        p.id === updatedProject.id ? updatedProject : p
                                      )
                                    )
                                  }
                                }}
                              />
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Add workspace form */}
            {showAddForm ? (
              <form
                onSubmit={handleAddWorkspace}
                className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-4"
              >
                <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                  Add Workspace
                </h3>
                <div className="space-y-3">
                  <div>
                    <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/80 mb-1">
                      Name
                    </label>
                    <input
                      type="text"
                      value={newWorkspaceName}
                      onChange={(e) => setNewWorkspaceName(e.target.value)}
                      placeholder="my-workspace"
                      className="w-full px-3 py-2 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30"
                    />
                  </div>
                  <div>
                    <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/80 mb-1">
                      Path
                    </label>
                    <input
                      type="text"
                      value={newWorkspacePath}
                      onChange={(e) => setNewWorkspacePath(e.target.value)}
                      placeholder="/Users/you/workspaces"
                      className="w-full px-3 py-2 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30"
                    />
                  </div>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={autoScan}
                      onChange={(e) => setAutoScan(e.target.checked)}
                      className="rounded border-[rgba(163,163,163,0.3)]"
                    />
                    <span className="text-caption text-[var(--color-stone)]/80">
                      Auto-scan for projects on creation
                    </span>
                  </label>
                </div>
                <div className="flex items-center gap-2 pt-2">
                  <button
                    type="submit"
                    disabled={
                      addingWorkspace || !newWorkspaceName.trim() || !newWorkspacePath.trim()
                    }
                    className="px-3 py-1.5 text-caption uppercase tracking-widest bg-[var(--color-paper)] text-[var(--color-void)] rounded-sm hover:opacity-90 disabled:opacity-50 transition-colors"
                  >
                    {addingWorkspace ? 'Adding...' : 'Add Workspace'}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setShowAddForm(false)
                      setNewWorkspaceName('')
                      setNewWorkspacePath('')
                    }}
                    className="px-3 py-1.5 text-caption uppercase tracking-widest text-[var(--color-stone)]/80 hover:text-[var(--color-stone)] transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <button
                onClick={() => setShowAddForm(true)}
                className="flex items-center gap-2 px-4 py-2.5 text-caption text-[var(--color-stone)]/80 hover:text-[var(--color-paper)] hover:bg-[rgba(163,163,163,0.05)] border border-dashed border-[rgba(163,163,163,0.2)] hover:border-[rgba(163,163,163,0.3)] rounded-sm transition-colors w-full justify-center"
              >
                <Plus className="w-3.5 h-3.5" />
                Add Workspace
              </button>
            )}
          </div>
        )}

        {/* Projects Tab */}
        {tab === 'projects' && (
          <div className="space-y-4">
            {projects.length === 0 ? (
              <div className="text-center py-8 text-[var(--color-stone)]/80 text-body">
                No projects registered. Add a workspace to auto-discover projects.
              </div>
            ) : (
              <div className="space-y-2">
                {projects.map((project) => {
                  const workspace = workspaces.find((w) => w.id === project.workspace_id)
                  return (
                    <div
                      key={project.id}
                      className="flex items-center gap-4 p-3 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm hover:bg-[rgba(163,163,163,0.08)] transition-colors cursor-pointer"
                      onClick={() => {
                        navigate(`/board?project=${encodeURIComponent(project.name)}`)
                      }}
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <h3 className="text-title font-medium text-[var(--color-paper)] truncate">
                            {project.name}
                          </h3>
                          {workspace && (
                            <span className="text-caption uppercase tracking-widest px-1.5 py-0.5 bg-[rgba(163,163,163,0.1)] rounded text-[var(--color-stone)]/70">
                              {workspace.name}
                            </span>
                          )}
                        </div>
                        <p className="text-caption text-[var(--color-stone)]/70 truncate mt-0.5">
                          {project.path}
                        </p>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        {project.git_branch && (
                          <div className="flex items-center gap-1.5 text-caption text-[var(--color-stone)]/80 min-w-0">
                            <GitBranch className="w-3 h-3 shrink-0" />
                            {/* Cap the branch name so a long branch (e.g.
                                feat/long-feature-name) can't squeeze the project
                                name/path out of the row on narrow screens. */}
                            <span className="truncate max-w-[100px] sm:max-w-[160px]">
                              {project.git_branch}
                            </span>
                          </div>
                        )}
                        <div onClick={(e) => e.stopPropagation()}>
                          <GitSyncButton
                            project={project}
                            compact
                            onSyncComplete={(success, _message, updatedProject) => {
                              if (success && updatedProject) {
                                // Incrementally update just this project's state
                                setProjects((prev) =>
                                  prev.map((p) => (p.id === updatedProject.id ? updatedProject : p))
                                )
                              }
                            }}
                          />
                        </div>
                        <button
                          className="p-1.5 rounded-sm hover:bg-[var(--color-vermillion)]/10 text-[var(--color-stone)]/80 hover:text-[var(--color-vermillion)] transition-colors"
                          onClick={(e) => {
                            e.stopPropagation()
                            setDeleteConfirm({
                              type: 'project',
                              id: project.id,
                              name: project.name,
                            })
                          }}
                          title="Delete project"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {/* Account Tab */}
        {tab === 'account' && (
          <div className="space-y-6">
            {/* Profile */}
            <div className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-4">
              <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                Profile
              </h3>
              {currentUser ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/80 mb-1">
                      Display name
                    </label>
                    <p className="text-body text-[var(--color-paper)]">
                      {currentUser.display_name || currentUser.username}
                    </p>
                  </div>
                  <div>
                    <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/80 mb-1">
                      Email
                    </label>
                    <p className="text-body text-[var(--color-paper)]">
                      {currentUser.email || '—'}
                    </p>
                  </div>
                  <p className="text-caption text-[var(--color-stone)]/60 sm:col-span-2">
                    Ask an admin to change your name or email.
                  </p>
                </div>
              ) : (
                <p className="text-caption text-[var(--color-stone)]/60">
                  Profile is only available when authentication is enabled.
                </p>
              )}
            </div>

            {/* Change password */}
            {currentUser && (
              <div className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-2">
                <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                  Password
                </h3>
                <ChangePasswordForm
                  user={currentUser}
                  isAdmin={currentUser.role === 'admin'}
                  onClose={() => {}}
                />
              </div>
            )}

            {/* Connected accounts */}
            <div className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-2">
              <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                Connected Accounts
              </h3>
              <ConnectedAccountsSection />
            </div>
          </div>
        )}

        {/* Preferences Tab */}
        {tab === 'preferences' && (
          <div className="space-y-6">
            <PreferencesSubNav active={prefGroup} onChange={setPrefGroup} />
            {/* LLM Provider */}
            <div
              id="prefs-agent"
              className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-4 scroll-mt-4"
            >
              <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                LLM Provider
              </h3>
              <div>
                <p className="text-title text-[var(--color-paper)]">Provider</p>
                <p className="text-caption text-[var(--color-stone)]/70 mt-1">
                  Choose how Gluon connects to Claude models. Switch between AWS Bedrock,
                  Anthropic's direct API / Claude subscription, Google Vertex AI, and Microsoft
                  Foundry (Azure) — Gluon transparently routes inference through the selected
                  backend.
                </p>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <div className="flex rounded-sm border border-[rgba(163,163,163,0.15)] overflow-hidden flex-wrap">
                  {LLM_PROVIDERS.map((p) => (
                    <button
                      key={p}
                      type="button"
                      onClick={() => handleChangeLlmProvider(p)}
                      disabled={savingKey === 'llm_provider'}
                      className={cn(
                        'px-4 py-2 text-caption transition-colors border-r border-[rgba(163,163,163,0.15)] last:border-r-0',
                        llmProvider === p
                          ? 'bg-[var(--color-paper)] text-[var(--color-void)]'
                          : 'bg-transparent text-[var(--color-stone)]/70 hover:text-[var(--color-stone)]',
                        savingKey === 'llm_provider' && 'opacity-50 cursor-wait'
                      )}
                    >
                      {LLM_PROVIDER_LABELS[p]}
                    </button>
                  ))}
                </div>
                {savedKey === 'llm_provider' && (
                  <span className="text-caption text-[var(--color-jade)] flex items-center gap-1">
                    <Check className="w-3 h-3" /> Saved
                  </span>
                )}
              </div>
              <p className="text-caption text-[var(--color-stone)]/60">
                {LLM_PROVIDER_HINTS[llmProvider]}
              </p>
            </div>

            {/* Git (merged card) */}
            <div
              id="prefs-integrations"
              className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-4 scroll-mt-4"
            >
              <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                Git
              </h3>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-title text-[var(--color-paper)]">Auto-create Pull Requests</p>
                  <p className="text-caption text-[var(--color-stone)]/70 mt-1">
                    Automatically create a PR when a worktree run completes successfully. When
                    disabled, runs will wait in Review until you manually create a PR.
                  </p>
                </div>
                <button
                  onClick={handleToggleAutoCreatePr}
                  disabled={savingKey === 'auto_create_pr'}
                  className={cn(
                    'relative w-11 h-6 rounded-full transition-colors focus:outline-none shrink-0',
                    autoCreatePr ? 'bg-[var(--color-jade)]' : 'bg-[var(--color-stone)]/30',
                    savingKey === 'auto_create_pr' && 'opacity-50 cursor-wait'
                  )}
                >
                  <span
                    className={cn(
                      'absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-transform',
                      autoCreatePr && 'translate-x-5'
                    )}
                  />
                </button>
              </div>

              <div className="border-t border-[rgba(163,163,163,0.08)]" />

              <SaveableSetting
                flush
                label="Author Identity"
                description="Configure the name and email used for commits made by Gluon."
                dirty={gitIdentityDirty}
                saving={savingKey === 'git_identity'}
                saved={savedKey === 'git_identity'}
                onSave={handleSaveGitIdentity}
              >
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/80 mb-1">
                      Name
                    </label>
                    <input
                      type="text"
                      value={gitUserName}
                      onChange={(e) => setGitUserName(e.target.value)}
                      placeholder="Your Name"
                      className="w-full px-3 py-2 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30"
                    />
                  </div>
                  <div>
                    <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/80 mb-1">
                      Email
                    </label>
                    <input
                      type="email"
                      value={gitUserEmail}
                      onChange={(e) => setGitUserEmail(e.target.value)}
                      placeholder="you@example.com"
                      className="w-full px-3 py-2 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30"
                    />
                  </div>
                </div>
              </SaveableSetting>
            </div>

            {/* Agent Configuration */}
            <div className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-4">
              <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                Agent Configuration
              </h3>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-title text-[var(--color-paper)]">
                    Extended Context (1M tokens)
                  </p>
                  <p className="text-caption text-[var(--color-stone)]/70 mt-1">
                    Enable the 1M token context window beta. Useful for large codebases that exceed
                    the default context limit.
                  </p>
                </div>
                <button
                  onClick={handleToggleExtendedContext}
                  disabled={savingKey === 'extended_context'}
                  className={cn(
                    'relative w-11 h-6 rounded-full transition-colors focus:outline-none shrink-0',
                    extendedContextEnabled
                      ? 'bg-[var(--color-jade)]'
                      : 'bg-[var(--color-stone)]/30',
                    savingKey === 'extended_context' && 'opacity-50 cursor-wait'
                  )}
                >
                  <span
                    className={cn(
                      'absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-transform',
                      extendedContextEnabled && 'translate-x-5'
                    )}
                  />
                </button>
              </div>

              <div className="border-t border-[rgba(163,163,163,0.08)]" />

              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-title text-[var(--color-paper)]">File Checkpointing</p>
                  <p className="text-caption text-[var(--color-stone)]/70 mt-1">
                    Enable file checkpointing for session rewind support. Creates restore points
                    during agent execution.
                  </p>
                </div>
                <button
                  onClick={handleToggleFileCheckpointing}
                  disabled={savingKey === 'file_checkpointing'}
                  className={cn(
                    'relative w-11 h-6 rounded-full transition-colors focus:outline-none shrink-0',
                    fileCheckpointingEnabled
                      ? 'bg-[var(--color-jade)]'
                      : 'bg-[var(--color-stone)]/30',
                    savingKey === 'file_checkpointing' && 'opacity-50 cursor-wait'
                  )}
                >
                  <span
                    className={cn(
                      'absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-transform',
                      fileCheckpointingEnabled && 'translate-x-5'
                    )}
                  />
                </button>
              </div>

              <div className="border-t border-[rgba(163,163,163,0.08)]" />

              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-title text-[var(--color-paper)]">Vercel CLI</p>
                  <p className="text-caption text-[var(--color-stone)]/70 mt-1">
                    Enable Vercel CLI for deployment management. Requires a Vercel API token.
                  </p>
                </div>
                <button
                  onClick={handleToggleVercelCli}
                  disabled={savingKey === 'vercel_cli'}
                  className={cn(
                    'relative w-11 h-6 rounded-full transition-colors focus:outline-none shrink-0',
                    vercelCliEnabled ? 'bg-[var(--color-jade)]' : 'bg-[var(--color-stone)]/30',
                    savingKey === 'vercel_cli' && 'opacity-50 cursor-wait'
                  )}
                >
                  <span
                    className={cn(
                      'absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-transform',
                      vercelCliEnabled && 'translate-x-5'
                    )}
                  />
                </button>
              </div>
              {vercelCliEnabled && (
                <SaveableSetting
                  flush
                  label="API Token"
                  dirty={vercelTokenDirty}
                  saving={savingKey === 'vercel_token'}
                  saved={savedKey === 'vercel_token'}
                  onSave={handleSaveVercelToken}
                >
                  <input
                    type="password"
                    value={vercelToken}
                    onChange={(e) => {
                      setVercelToken(e.target.value)
                      setVercelTestResult(null)
                    }}
                    placeholder={
                      vercelTokenFromEnv
                        ? 'Using token from environment (VERCEL_TOKEN)'
                        : 'Enter your Vercel API token'
                    }
                    className="w-full px-3 py-2 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30"
                  />
                  {!vercelToken && vercelTokenFromEnv && (
                    <p className="text-caption text-[var(--color-sky)]/80 mt-1">
                      Using token from environment variable
                    </p>
                  )}
                  <div className="mt-2 flex justify-end">
                    <button
                      type="button"
                      onClick={handleTestVercelToken}
                      disabled={vercelTesting}
                      className={cn(
                        'flex items-center gap-1.5 px-2.5 py-1.5 text-caption uppercase tracking-widest rounded-sm transition-colors border',
                        vercelTesting
                          ? 'bg-[rgba(163,163,163,0.1)] border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                          : vercelTestResult?.valid
                            ? 'bg-[rgba(34,197,94,0.15)] border-[rgba(34,197,94,0.3)] text-[var(--color-jade)]'
                            : vercelTestResult && !vercelTestResult.valid
                              ? 'bg-[rgb(var(--color-vermillion-rgb)/0.15)] border-[rgb(var(--color-vermillion-rgb)/0.3)] text-[var(--color-vermillion)] hover:bg-[rgb(var(--color-vermillion-rgb)/0.25)]'
                              : 'border-[rgba(163,163,163,0.2)] text-[var(--color-stone)] hover:bg-[rgba(163,163,163,0.1)]'
                      )}
                    >
                      {vercelTesting ? (
                        <>
                          <Loader2 className="w-3 h-3 animate-spin" />
                          <span>Testing...</span>
                        </>
                      ) : vercelTestResult?.valid ? (
                        <>
                          <Check className="w-3 h-3" />
                          <span>{vercelTestResult.account}</span>
                        </>
                      ) : vercelTestResult && !vercelTestResult.valid ? (
                        <>
                          <X className="w-3 h-3" />
                          <span>{vercelTestResult.error}</span>
                        </>
                      ) : (
                        <span>Test</span>
                      )}
                    </button>
                  </div>
                </SaveableSetting>
              )}
            </div>

            {/* Notifications */}
            <div className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-4">
              <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                Notifications
              </h3>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <Bell className="w-4 h-4 text-[var(--color-stone)]/80" />
                    <p className="text-title text-[var(--color-paper)]">Browser Notifications</p>
                  </div>
                  <p className="text-caption text-[var(--color-stone)]/70 mt-1">
                    Show browser notifications when the agent completes tasks or needs attention.
                  </p>
                  <p className="text-caption text-[var(--color-amber)]/70 mt-1">
                    Browser notifications require HTTPS for non-localhost access. If accessing Gluon
                    over a local network address, notifications will not work until HTTPS is
                    configured.
                  </p>
                </div>
                <button
                  onClick={handleToggleNotifications}
                  disabled={savingKey === 'notifications'}
                  className={cn(
                    'relative w-11 h-6 rounded-full transition-colors focus:outline-none shrink-0',
                    notificationsEnabled ? 'bg-[var(--color-jade)]' : 'bg-[var(--color-stone)]/30',
                    savingKey === 'notifications' && 'opacity-50 cursor-wait'
                  )}
                >
                  <span
                    className={cn(
                      'absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-transform',
                      notificationsEnabled && 'translate-x-5'
                    )}
                  />
                </button>
              </div>
            </div>

            {/* Tool Restrictions */}
            <div className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-4">
              <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                Tool Restrictions
              </h3>
              <div>
                <p className="text-title text-[var(--color-paper)]">Disallowed Tools</p>
                <p className="text-caption text-[var(--color-stone)]/70 mt-1 mb-3">
                  Block specific tools from being used by the agent. Click a tool to toggle its
                  restriction.
                </p>
                <div className="flex flex-wrap gap-2">
                  {[
                    'Bash',
                    'Read',
                    'Write',
                    'Edit',
                    'MultiEdit',
                    'Glob',
                    'Grep',
                    'WebFetch',
                    'WebSearch',
                    'NotebookEdit',
                    'TodoWrite',
                    'Task',
                  ].map((tool) => {
                    const isBlocked = disallowedTools.includes(tool)
                    return (
                      <button
                        key={tool}
                        onClick={() => handleToggleDisallowedTool(tool)}
                        disabled={savingKey === 'disallowed_tools'}
                        className={cn(
                          'px-2.5 py-1 text-caption rounded-sm border transition-colors',
                          isBlocked
                            ? 'bg-[var(--color-vermillion)]/15 border-[var(--color-vermillion)]/40 text-[var(--color-vermillion)]'
                            : 'bg-transparent border-[rgba(163,163,163,0.15)] text-[var(--color-stone)]/70 hover:border-[rgba(163,163,163,0.3)] hover:text-[var(--color-stone)]',
                          savingKey === 'disallowed_tools' && 'opacity-50 cursor-wait'
                        )}
                      >
                        {isBlocked && <X className="w-3 h-3 inline mr-1" />}
                        {tool}
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>

            {/* Security */}
            <div
              id="prefs-workspace"
              className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-4 scroll-mt-4"
            >
              <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                Security
              </h3>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-title text-[var(--color-paper)]">Sandbox Isolation</p>
                  <p className="text-caption text-[var(--color-stone)]/70 mt-1">
                    Restrict Claude Code's filesystem access to the project directory using
                    {sandboxRuntime ? ` ${sandboxRuntime}` : ' OS-level sandboxing'}. Recommended
                    for security.
                  </p>
                  {!sandboxAvailable && (
                    <p className="text-caption text-[var(--color-vermillion)]/80 mt-1">
                      Sandbox runtime not available on this system.
                    </p>
                  )}
                </div>
                <button
                  onClick={handleToggleSandbox}
                  disabled={savingKey === 'sandbox' || !sandboxAvailable}
                  className={cn(
                    'relative w-11 h-6 rounded-full transition-colors focus:outline-none shrink-0',
                    sandboxEnabled ? 'bg-[var(--color-jade)]' : 'bg-[var(--color-stone)]/30',
                    (savingKey === 'sandbox' || !sandboxAvailable) &&
                      'opacity-50 cursor-not-allowed'
                  )}
                >
                  <span
                    className={cn(
                      'absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-transform',
                      sandboxEnabled && 'translate-x-5'
                    )}
                  />
                </button>
              </div>
            </div>

            {/* Experimental Features */}
            <div
              id="prefs-system"
              className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-4 scroll-mt-4"
            >
              <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                Experimental
              </h3>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-title text-[var(--color-paper)]">Skills</p>
                  <p className="text-caption text-[var(--color-stone)]/70 mt-1">
                    Enable Claude Code skills on agent sessions. When enabled, tasks can use
                    installed slash-command skills (e.g. /plan, /review) during execution.
                  </p>
                </div>
                <button
                  onClick={handleToggleSkills}
                  disabled={savingKey === 'skills'}
                  className={cn(
                    'relative w-11 h-6 rounded-full transition-colors focus:outline-none shrink-0',
                    skillsEnabled ? 'bg-[var(--color-jade)]' : 'bg-[var(--color-stone)]/30',
                    savingKey === 'skills' && 'opacity-50 cursor-wait'
                  )}
                >
                  <span
                    className={cn(
                      'absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-transform',
                      skillsEnabled && 'translate-x-5'
                    )}
                  />
                </button>
              </div>
            </div>

            {/* Appearance */}
            <div className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-4">
              <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                Appearance
              </h3>
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-2">
                  {theme === 'dark' ? (
                    <Moon className="w-4 h-4 text-[var(--color-stone)]/80" />
                  ) : (
                    <Sun className="w-4 h-4 text-[var(--color-stone)]/80" />
                  )}
                  <div>
                    <p className="text-title text-[var(--color-paper)]">Theme</p>
                    <p className="text-caption text-[var(--color-stone)]/70 mt-1">
                      Choose your preferred color scheme.
                    </p>
                  </div>
                </div>
                <div className="flex rounded-sm border border-[rgba(163,163,163,0.15)] overflow-hidden shrink-0">
                  {(['light', 'dark', 'system'] as const).map((opt) => (
                    <button
                      key={opt}
                      type="button"
                      onClick={() => setTheme(opt)}
                      className={cn(
                        'px-3 py-1.5 text-caption capitalize transition-colors border-r border-[rgba(163,163,163,0.15)] last:border-r-0',
                        preference === opt
                          ? 'bg-[var(--color-paper)] text-[var(--color-void)]'
                          : 'bg-transparent text-[var(--color-stone)]/70 hover:text-[var(--color-stone)]'
                      )}
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Version Info */}
            {versionInfo && (
              <div className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-2">
                <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
                  About
                </h3>
                <div className="flex items-baseline gap-2">
                  <span className="text-title text-[var(--color-paper)]">
                    Gluon v{versionInfo.semver}
                  </span>
                  <span className="text-caption text-[var(--color-stone)]/50 font-mono">
                    {versionInfo.version}
                  </span>
                </div>
                <div className="text-caption text-[var(--color-stone)]/50">
                  {versionInfo.environment === 'production' ? 'Production' : 'Development'} · Built{' '}
                  {new Date(versionInfo.build_time).toISOString().slice(0, 10)}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Formulas Tab */}
        {tab === 'formulas' && (
          <div className="space-y-4">
            {loadingFormulas ? (
              <div className="flex items-center justify-center h-32">
                <div className="mark mark-running w-2 h-2" />
              </div>
            ) : formulas.length === 0 ? (
              <div className="text-center py-8 text-[var(--color-stone)]/80 text-body">
                No formulas discovered. Add YAML formula files to your project&apos;s{' '}
                <code className="text-[var(--color-sky)]">.gluon/formulas/</code> directory.
              </div>
            ) : (
              <div className="space-y-3">
                {formulas.map((formula) => (
                  <div
                    key={formula.name}
                    className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm"
                  >
                    <div
                      className="flex items-center justify-between cursor-pointer"
                      onClick={() =>
                        setExpandedFormula(expandedFormula === formula.name ? null : formula.name)
                      }
                    >
                      <div>
                        <h3 className="text-title text-[var(--color-paper)]">{formula.name}</h3>
                        {formula.description && (
                          <p className="text-caption text-[var(--color-stone)]/70 mt-0.5">
                            {formula.description}
                          </p>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-caption text-[var(--color-stone)]/50">
                          {formula.steps.length} step{formula.steps.length !== 1 ? 's' : ''}
                        </span>
                        {expandedFormula === formula.name ? (
                          <ChevronDown className="w-4 h-4 text-[var(--color-stone)]/50" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-[var(--color-stone)]/50" />
                        )}
                      </div>
                    </div>

                    {expandedFormula === formula.name && (
                      <div className="mt-3 space-y-3 border-t border-[rgba(163,163,163,0.08)] pt-3">
                        {/* Variables */}
                        {formula.variables.length > 0 && (
                          <div>
                            <h4 className="text-body uppercase tracking-widest text-[var(--color-stone)]/60 mb-1.5">
                              Variables
                            </h4>
                            <div className="space-y-1">
                              {formula.variables.map((v) => (
                                <div key={v.name} className="flex items-center gap-2 text-caption">
                                  <code className="text-[var(--color-sky)] font-mono">
                                    {v.name}
                                  </code>
                                  <span className="text-[var(--color-stone)]/40">({v.type})</span>
                                  {v.required && (
                                    <span className="text-[var(--color-vermillion)] text-micro uppercase">
                                      required
                                    </span>
                                  )}
                                  {v.default && (
                                    <span className="text-[var(--color-stone)]/40">
                                      = {v.default}
                                    </span>
                                  )}
                                  {v.help && (
                                    <span className="text-[var(--color-stone)]/50 ml-auto">
                                      {v.help}
                                    </span>
                                  )}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Steps */}
                        <div>
                          <h4 className="text-body uppercase tracking-widest text-[var(--color-stone)]/60 mb-1.5">
                            Steps
                          </h4>
                          <div className="space-y-1.5">
                            {formula.steps.map((step, idx) => (
                              <div key={step.id} className="flex items-start gap-2 text-caption">
                                <span className="text-[var(--color-stone)]/30 font-mono w-4 text-right shrink-0">
                                  {idx + 1}
                                </span>
                                <div className="min-w-0">
                                  <span className="text-[var(--color-paper)]">{step.name}</span>
                                  <span className="text-[var(--color-stone)]/40 ml-2">
                                    [{step.profile}]
                                  </span>
                                  {step.depends_on.length > 0 && (
                                    <span className="text-[var(--color-stone)]/30 ml-2">
                                      depends: {step.depends_on.join(', ')}
                                    </span>
                                  )}
                                  <p className="text-[var(--color-stone)]/60 mt-0.5 truncate">
                                    {step.prompt}
                                  </p>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>

                        {/* Metadata */}
                        <div className="flex items-center gap-4 text-caption text-[var(--color-stone)]/40 pt-1">
                          {formula.use_worktree && <span>Uses worktree</span>}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Clone repository modal */}
        {/* Workspace Settings Dialog */}
        {settingsWorkspace && (
          <WorkspaceSettingsDialog
            workspaceId={settingsWorkspace.id}
            workspaceName={settingsWorkspace.name}
            open={!!settingsWorkspace}
            onOpenChange={(open) => {
              if (!open) setSettingsWorkspace(null)
            }}
          />
        )}

        {cloneDialogWorkspaceId && (
          <div className="fixed inset-0 bg-[var(--color-void)]/80 flex items-center justify-center z-50">
            <div className="bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm p-6 max-w-md w-full mx-4 space-y-4">
              <h3 className="text-title font-medium text-[var(--color-paper)]">
                Clone GitHub Repository
              </h3>
              <p className="text-body text-[var(--color-stone)]/70">
                Clone a repository into workspace{' '}
                <span className="text-[var(--color-paper)]">
                  {workspaces.find((w) => w.id === cloneDialogWorkspaceId)?.name}
                </span>
              </p>
              <div>
                <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/80 mb-1">
                  GitHub URL
                </label>
                <input
                  type="url"
                  value={cloneUrl}
                  onChange={(e) => setCloneUrl(e.target.value)}
                  placeholder="https://github.com/owner/repo"
                  className="w-full px-3 py-2 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30"
                  disabled={cloning}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && cloneUrl.trim() && !cloning) {
                      e.preventDefault()
                      handleCloneRepository(cloneDialogWorkspaceId)
                    }
                  }}
                />
              </div>
              {cloning && (
                <div className="flex items-center gap-2 text-body text-[var(--color-sky)]">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Cloning repository... This may take a moment.</span>
                </div>
              )}
              <div className="flex items-center gap-2 pt-2">
                <button
                  onClick={() => handleCloneRepository(cloneDialogWorkspaceId)}
                  disabled={cloning || !cloneUrl.trim()}
                  className="px-3 py-1.5 text-caption uppercase tracking-widest bg-[var(--color-paper)] text-[var(--color-void)] rounded-sm hover:opacity-90 disabled:opacity-50 transition-colors flex items-center gap-2"
                >
                  {cloning && <Loader2 className="w-3 h-3 animate-spin" />}
                  {cloning ? 'Cloning...' : 'Clone'}
                </button>
                <button
                  onClick={() => {
                    setCloneDialogWorkspaceId(null)
                    setCloneUrl('')
                  }}
                  disabled={cloning}
                  className="px-3 py-1.5 text-caption uppercase tracking-widest text-[var(--color-stone)]/80 hover:text-[var(--color-stone)] transition-colors disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Delete confirmation modal */}
        {deleteConfirm && (
          <div className="fixed inset-0 bg-[var(--color-void)]/80 flex items-center justify-center z-50">
            <div className="bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm p-6 max-w-sm w-full mx-4 space-y-4">
              <h3 className="text-title font-medium text-[var(--color-paper)]">
                Delete {deleteConfirm.type}?
              </h3>
              <p className="text-body text-[var(--color-stone)]/70">
                Are you sure you want to delete{' '}
                <span className="text-[var(--color-paper)]">{deleteConfirm.name}</span>?
                {deleteConfirm.type === 'workspace' && ' Projects will be kept but unlinked.'}
                {deleteConfirm.type === 'project' &&
                  ' This will also delete associated runs and sessions.'}
              </p>
              <div className="flex items-center gap-2 pt-2">
                <button
                  onClick={() => {
                    if (deleteConfirm.type === 'workspace') {
                      handleDeleteWorkspace(deleteConfirm.id)
                    } else {
                      handleDeleteProject(deleteConfirm.id)
                    }
                  }}
                  className="px-3 py-1.5 text-caption uppercase tracking-widest bg-[var(--color-vermillion)] text-white rounded-sm hover:opacity-90 transition-colors"
                >
                  Delete
                </button>
                <button
                  onClick={() => setDeleteConfirm(null)}
                  className="px-3 py-1.5 text-caption uppercase tracking-widest text-[var(--color-stone)]/80 hover:text-[var(--color-stone)] transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
