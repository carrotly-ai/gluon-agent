import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  GitCommit,
  Loader2,
  RefreshCw,
} from 'lucide-react'
import { useState } from 'react'
import { syncProjectGit } from '@/lib/api'
import type { GitStatusInfo, Project } from '@/lib/types'
import { cn } from '@/lib/utils'

interface GitSyncButtonProps {
  project: Project
  onSyncComplete?: (success: boolean, message: string, updatedProject?: Project) => void
  compact?: boolean
}

/** Map GitStatusInfo from API response back to Project git fields */
function applyGitStatusToProject(project: Project, status: GitStatusInfo): Project {
  // Compute sync_action from status
  let sync_action: Project['sync_action'] = null
  if (status.is_diverged) {
    sync_action = 'diverged'
  } else if (status.needs_pull) {
    sync_action = 'pull'
  } else if (status.needs_push) {
    sync_action = 'push'
  } else if (status.uncommitted_count > 0) {
    sync_action = 'commit+push'
  }

  return {
    ...project,
    git_branch: status.branch,
    git_ahead: status.commits_ahead,
    git_behind: status.commits_behind,
    git_uncommitted_count: status.uncommitted_count,
    git_has_remote: status.remote !== null,
    git_has_conflicts: status.has_conflicts,
    git_has_operation_in_progress: status.has_operation_in_progress,
    sync_action,
    can_sync: !status.has_conflicts && !status.has_operation_in_progress,
  }
}

type SyncState =
  | 'synced'
  | 'pull'
  | 'push'
  | 'commit+push'
  | 'diverged'
  | 'conflict'
  | 'no-remote'
  | 'not-git'

function getSyncState(project: Project): SyncState {
  // Check if it's a git repo
  if (!project.git_branch) {
    return 'not-git'
  }

  // Check if no remote
  if (project.git_has_remote === false) {
    return 'no-remote'
  }

  // Check for conflicts or operation in progress
  if (project.git_has_conflicts || project.git_has_operation_in_progress) {
    return 'conflict'
  }

  // Use sync_action from backend if available
  if (project.sync_action) {
    return project.sync_action as SyncState
  }

  // Fallback: compute from ahead/behind
  const ahead = project.git_ahead ?? 0
  const behind = project.git_behind ?? 0

  if (ahead > 0 && behind > 0) {
    return 'diverged'
  }
  if (behind > 0) {
    return 'pull'
  }
  if (ahead > 0) {
    return 'push'
  }
  if ((project.git_uncommitted_count ?? 0) > 0) {
    return 'commit+push'
  }

  return 'synced'
}

interface ButtonConfig {
  label: string
  icon: React.ReactNode
  bgClass: string
  textClass: string
  canSync: boolean
  tooltip: string
}

function getButtonConfig(state: SyncState, project: Project, compact: boolean): ButtonConfig {
  const ahead = project.git_ahead ?? 0
  const behind = project.git_behind ?? 0
  const uncommitted = project.git_uncommitted_count ?? 0

  switch (state) {
    case 'synced':
      return {
        label: compact ? '' : 'Synced',
        icon: <Check className="w-3 h-3" />,
        bgClass: 'bg-[var(--color-jade)]/10',
        textClass: 'text-[var(--color-jade)]',
        canSync: false,
        tooltip: 'Repository is up to date',
      }

    case 'pull': {
      const canPull = uncommitted === 0
      return {
        label: compact ? `↓${behind}` : `Pull ↓${behind}`,
        icon: <ArrowDown className="w-3 h-3" />,
        bgClass: canPull
          ? 'bg-[var(--color-sky)]/10 hover:bg-[var(--color-sky)]/20'
          : 'bg-[var(--color-vermillion)]/10',
        textClass: canPull ? 'text-[var(--color-sky)]' : 'text-[var(--color-vermillion)]',
        canSync: canPull,
        tooltip: canPull
          ? `${behind} commits behind. Click to pull.`
          : `Cannot pull: ${uncommitted} uncommitted changes`,
      }
    }

    case 'push':
      return {
        label: compact ? `↑${ahead}` : `Push ↑${ahead}`,
        icon: <ArrowUp className="w-3 h-3" />,
        bgClass: 'bg-[var(--color-sky)]/10 hover:bg-[var(--color-sky)]/20',
        textClass: 'text-[var(--color-sky)]',
        canSync: true,
        tooltip: `${ahead} commits ahead. Click to push.`,
      }

    case 'commit+push':
      return {
        label: compact ? `●${uncommitted}` : `Commit ${uncommitted}`,
        icon: <GitCommit className="w-3 h-3" />,
        bgClass: 'bg-amber-500/10 hover:bg-amber-500/20',
        textClass: 'text-amber-400',
        canSync: true,
        tooltip: `${uncommitted} uncommitted changes. Click to commit and push.`,
      }

    case 'diverged':
      return {
        label: compact ? '⚠' : 'Diverged',
        icon: <AlertTriangle className="w-3 h-3" />,
        bgClass: 'bg-[var(--color-vermillion)]/10',
        textClass: 'text-[var(--color-vermillion)]',
        canSync: false,
        tooltip: `Branch diverged: ${ahead} ahead, ${behind} behind. Manual rebase required.`,
      }

    case 'conflict':
      return {
        label: compact ? '⚠' : 'Conflict',
        icon: <AlertTriangle className="w-3 h-3" />,
        bgClass: 'bg-[var(--color-vermillion)]/10',
        textClass: 'text-[var(--color-vermillion)]',
        canSync: false,
        tooltip: 'Resolve conflicts before syncing',
      }

    case 'no-remote':
      return {
        label: compact ? '—' : 'No remote',
        icon: null,
        bgClass: 'bg-[var(--color-stone)]/5',
        textClass: 'text-[var(--color-stone)]/50',
        canSync: false,
        tooltip: 'No remote configured',
      }

    default:
      return {
        label: '',
        icon: null,
        bgClass: '',
        textClass: 'text-[var(--color-stone)]/30',
        canSync: false,
        tooltip: 'Not a git repository',
      }
  }
}

export function GitSyncButton({ project, onSyncComplete, compact = false }: GitSyncButtonProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const state = getSyncState(project)
  const config = getButtonConfig(state, project, compact)

  // Don't render anything for non-git repos
  if (state === 'not-git') {
    return null
  }

  const handleSync = async () => {
    if (!config.canSync || loading) return

    setLoading(true)
    setError(null)

    try {
      const result = await syncProjectGit(project.id, 'auto')

      if (result.success) {
        // Apply updated git status to project if available
        const updatedProject = result.updated_status
          ? applyGitStatusToProject(project, result.updated_status)
          : undefined
        onSyncComplete?.(true, result.message, updatedProject)
      } else {
        setError(result.error || 'Sync failed')
        onSyncComplete?.(false, result.error || 'Sync failed')
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Sync failed'
      setError(msg)
      onSyncComplete?.(false, msg)
    } finally {
      setLoading(false)
    }
  }

  const handleRefresh = async (e: React.MouseEvent) => {
    e.stopPropagation()
    // Just trigger a data refresh - parent component handles this
    onSyncComplete?.(true, 'Refreshed')
  }

  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={config.canSync ? handleSync : undefined}
        disabled={!config.canSync || loading}
        title={error || config.tooltip}
        className={cn(
          'flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-caption transition-colors',
          config.bgClass,
          config.textClass,
          config.canSync && !loading ? 'cursor-pointer' : 'cursor-default',
          loading && 'opacity-70'
        )}
      >
        {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : config.icon}
        {config.label && <span>{config.label}</span>}
      </button>

      {/* Refresh button for manual status refresh */}
      {!compact && state !== 'no-remote' && (
        <button
          type="button"
          onClick={handleRefresh}
          disabled={loading}
          title="Refresh git status"
          className={cn(
            'p-0.5 rounded-sm transition-colors',
            'text-[var(--color-stone)]/40 hover:text-[var(--color-stone)]/70',
            'hover:bg-[var(--color-stone)]/10'
          )}
        >
          <RefreshCw className={cn('w-2.5 h-2.5', loading && 'animate-spin')} />
        </button>
      )}
    </div>
  )
}
