import { useState, useEffect, useCallback } from 'react'
import { Plus, FolderOpen, RefreshCw, Trash2, GitBranch, AlertCircle, Check, X } from 'lucide-react'
import { fetchWorkspaces, fetchProjects, createWorkspace, deleteWorkspace, scanWorkspace, deleteProject } from '@/lib/api'
import type { Workspace, Project, ScanResultResponse } from '@/lib/types'
import { cn } from '@/lib/utils'

type Tab = 'workspaces' | 'projects'

export function SettingsPage() {
  const [tab, setTab] = useState<Tab>('workspaces')
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

  // Delete confirmation
  const [deleteConfirm, setDeleteConfirm] = useState<{ type: 'workspace' | 'project'; id: string; name: string } | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [ws, prj] = await Promise.all([fetchWorkspaces(), fetchProjects()])
      setWorkspaces(ws)
      setProjects(prj)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

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
      setWorkspaces(prev => [...prev, ws])
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
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to scan workspace')
    } finally {
      setScanningId(null)
    }
  }

  const handleDeleteWorkspace = async (workspaceId: string) => {
    try {
      await deleteWorkspace(workspaceId)
      setWorkspaces(prev => prev.filter(w => w.id !== workspaceId))
      setDeleteConfirm(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete workspace')
    }
  }

  const handleDeleteProject = async (projectId: string) => {
    try {
      await deleteProject(projectId)
      setProjects(prev => prev.filter(p => p.id !== projectId))
      setDeleteConfirm(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete project')
    }
  }

  // Group projects by workspace
  const projectsByWorkspace = projects.reduce((acc, project) => {
    const wsId = project.workspace_id || 'standalone'
    if (!acc[wsId]) acc[wsId] = []
    acc[wsId].push(project)
    return acc
  }, {} as Record<string, Project[]>)

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
                'px-3 py-1.5 text-[0.625rem] uppercase tracking-widest rounded-sm transition-colors',
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
                'px-3 py-1.5 text-[0.625rem] uppercase tracking-widest rounded-sm transition-colors',
                tab === 'projects'
                  ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                  : 'text-[var(--color-stone)]/80 hover:text-[var(--color-stone)]'
              )}
              onClick={() => setTab('projects')}
            >
              Projects
            </button>
          </div>
        </div>

        {/* Error display */}
        {error && (
          <div className="flex items-center gap-2 p-3 bg-[var(--color-vermillion)]/10 border border-[var(--color-vermillion)]/20 rounded-sm">
            <AlertCircle className="w-4 h-4 text-[var(--color-vermillion)]" />
            <span className="text-[0.75rem] text-[var(--color-vermillion)]">{error}</span>
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
          <div className="flex items-center gap-2 p-3 bg-[var(--color-jade)]/10 border border-[var(--color-jade)]/20 rounded-sm">
            <Check className="w-4 h-4 text-[var(--color-jade)]" />
            <span className="text-[0.75rem] text-[var(--color-jade)]">
              Found {scanResult.projects_found} projects, added {scanResult.projects_added.length} new
              {scanResult.projects_added.length > 0 && `: ${scanResult.projects_added.join(', ')}`}
            </span>
            <button
              className="ml-auto text-[var(--color-jade)] hover:opacity-80"
              onClick={() => setScanResult(null)}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Workspaces Tab */}
        {tab === 'workspaces' && (
          <div className="space-y-4">
            {/* Workspace list */}
            {workspaces.length === 0 ? (
              <div className="text-center py-8 text-[var(--color-stone)]/80 text-[0.75rem]">
                No workspaces configured. Add one to start managing projects.
              </div>
            ) : (
              <div className="space-y-3">
                {workspaces.map(ws => (
                  <div
                    key={ws.id}
                    className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex items-start gap-3 min-w-0">
                        <FolderOpen className="w-4 h-4 text-[var(--color-stone)]/80 mt-0.5 shrink-0" />
                        <div className="min-w-0">
                          <h3 className="text-[0.8125rem] font-medium text-[var(--color-paper)] truncate">
                            {ws.name}
                          </h3>
                          <p className="text-[0.6875rem] text-[var(--color-stone)]/80 truncate mt-0.5">
                            {ws.path}
                          </p>
                          <p className="text-[0.625rem] text-[var(--color-stone)]/60 mt-1">
                            {ws.project_count} project{ws.project_count !== 1 ? 's' : ''}
                          </p>
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
                          title="Rescan for new projects"
                        >
                          <RefreshCw className={cn('w-3.5 h-3.5', scanningId === ws.id && 'animate-spin')} />
                        </button>
                        <button
                          className="p-1.5 rounded-sm hover:bg-[var(--color-vermillion)]/10 text-[var(--color-stone)]/80 hover:text-[var(--color-vermillion)] transition-colors"
                          onClick={() => setDeleteConfirm({ type: 'workspace', id: ws.id, name: ws.name })}
                          title="Delete workspace"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>

                    {/* Projects in this workspace */}
                    {projectsByWorkspace[ws.id] && projectsByWorkspace[ws.id].length > 0 && (
                      <div className="mt-3 pt-3 border-t border-[rgba(163,163,163,0.08)] space-y-0.5">
                        {projectsByWorkspace[ws.id].map(project => (
                          <div
                            key={project.id}
                            className="grid grid-cols-[1fr_auto_auto] items-center gap-x-3 text-[0.6875rem] px-2 py-1 -mx-2 rounded-sm hover:bg-[rgba(163,163,163,0.08)] transition-colors cursor-pointer"
                            onClick={() => { window.location.hash = `project:${project.name}` }}
                          >
                            <span className="text-[var(--color-stone)]/80 truncate pl-4">
                              {project.name}
                            </span>
                            <span className="flex items-center gap-1.5 text-[var(--color-stone)]/70 text-[0.625rem] min-w-[180px] justify-end">
                              <GitBranch className="w-3 h-3 text-[var(--color-stone)]/60 shrink-0" />
                              <span className="truncate max-w-[140px]">{project.git_branch || 'no git'}</span>
                            </span>
                            <span className="text-[0.5625rem] text-[var(--color-stone)]/60 font-mono w-[32px] text-right tabular-nums">
                              {project.git_ahead ? `↑${project.git_ahead}` : ''}{project.git_ahead && project.git_behind ? ' ' : ''}{project.git_behind ? `↓${project.git_behind}` : ''}
                            </span>
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
              <form onSubmit={handleAddWorkspace} className="p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm space-y-4">
                <h3 className="text-[0.75rem] uppercase tracking-widest text-[var(--color-stone)]/70">
                  Add Workspace
                </h3>
                <div className="space-y-3">
                  <div>
                    <label className="block text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/80 mb-1">
                      Name
                    </label>
                    <input
                      type="text"
                      value={newWorkspaceName}
                      onChange={e => setNewWorkspaceName(e.target.value)}
                      placeholder="my-workspace"
                      className="w-full px-3 py-2 text-[0.75rem] bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30"
                    />
                  </div>
                  <div>
                    <label className="block text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/80 mb-1">
                      Path
                    </label>
                    <input
                      type="text"
                      value={newWorkspacePath}
                      onChange={e => setNewWorkspacePath(e.target.value)}
                      placeholder="/Users/you/workspaces"
                      className="w-full px-3 py-2 text-[0.75rem] bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[var(--color-paper)]/30"
                    />
                  </div>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={autoScan}
                      onChange={e => setAutoScan(e.target.checked)}
                      className="rounded border-[rgba(163,163,163,0.3)]"
                    />
                    <span className="text-[0.6875rem] text-[var(--color-stone)]/80">
                      Auto-scan for projects on creation
                    </span>
                  </label>
                </div>
                <div className="flex items-center gap-2 pt-2">
                  <button
                    type="submit"
                    disabled={addingWorkspace || !newWorkspaceName.trim() || !newWorkspacePath.trim()}
                    className="px-3 py-1.5 text-[0.625rem] uppercase tracking-widest bg-[var(--color-paper)] text-[var(--color-void)] rounded-sm hover:opacity-90 disabled:opacity-50 transition-colors"
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
                    className="px-3 py-1.5 text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/80 hover:text-[var(--color-stone)] transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <button
                onClick={() => setShowAddForm(true)}
                className="flex items-center gap-2 px-4 py-2.5 text-[0.6875rem] text-[var(--color-stone)]/80 hover:text-[var(--color-paper)] hover:bg-[rgba(163,163,163,0.05)] border border-dashed border-[rgba(163,163,163,0.2)] hover:border-[rgba(163,163,163,0.3)] rounded-sm transition-colors w-full justify-center"
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
              <div className="text-center py-8 text-[var(--color-stone)]/80 text-[0.75rem]">
                No projects registered. Add a workspace to auto-discover projects.
              </div>
            ) : (
              <div className="space-y-2">
                {projects.map(project => {
                  const workspace = workspaces.find(w => w.id === project.workspace_id)
                  return (
                    <div
                      key={project.id}
                      className="flex items-center gap-4 p-3 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm hover:bg-[rgba(163,163,163,0.08)] transition-colors cursor-pointer"
                      onClick={() => { window.location.hash = `project:${project.name}` }}
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <h3 className="text-[0.8125rem] font-medium text-[var(--color-paper)] truncate">
                            {project.name}
                          </h3>
                          {workspace && (
                            <span className="text-[0.5625rem] uppercase tracking-widest px-1.5 py-0.5 bg-[rgba(163,163,163,0.1)] rounded text-[var(--color-stone)]/70">
                              {workspace.name}
                            </span>
                          )}
                        </div>
                        <p className="text-[0.6875rem] text-[var(--color-stone)]/70 truncate mt-0.5">
                          {project.path}
                        </p>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        {project.git_branch && (
                          <div className="flex items-center gap-1.5 text-[0.625rem] text-[var(--color-stone)]/80">
                            <GitBranch className="w-3 h-3" />
                            <span>{project.git_branch}</span>
                            {(project.git_ahead || project.git_behind) && (
                              <span className="text-[0.5625rem] text-[var(--color-stone)]/60 font-mono ml-1">
                                {project.git_ahead ? `↑${project.git_ahead}` : ''}{project.git_ahead && project.git_behind ? ' ' : ''}{project.git_behind ? `↓${project.git_behind}` : ''}
                              </span>
                            )}
                          </div>
                        )}
                        <button
                          className="p-1.5 rounded-sm hover:bg-[var(--color-vermillion)]/10 text-[var(--color-stone)]/80 hover:text-[var(--color-vermillion)] transition-colors"
                          onClick={(e) => { e.stopPropagation(); setDeleteConfirm({ type: 'project', id: project.id, name: project.name }) }}
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

        {/* Delete confirmation modal */}
        {deleteConfirm && (
          <div className="fixed inset-0 bg-[var(--color-void)]/80 flex items-center justify-center z-50">
            <div className="bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm p-6 max-w-sm w-full mx-4 space-y-4">
              <h3 className="text-[0.875rem] font-medium text-[var(--color-paper)]">
                Delete {deleteConfirm.type}?
              </h3>
              <p className="text-[0.75rem] text-[var(--color-stone)]/70">
                Are you sure you want to delete <span className="text-[var(--color-paper)]">{deleteConfirm.name}</span>?
                {deleteConfirm.type === 'workspace' && ' Projects will be kept but unlinked.'}
                {deleteConfirm.type === 'project' && ' This will also delete associated runs and sessions.'}
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
                  className="px-3 py-1.5 text-[0.625rem] uppercase tracking-widest bg-[var(--color-vermillion)] text-white rounded-sm hover:opacity-90 transition-colors"
                >
                  Delete
                </button>
                <button
                  onClick={() => setDeleteConfirm(null)}
                  className="px-3 py-1.5 text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/80 hover:text-[var(--color-stone)] transition-colors"
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
