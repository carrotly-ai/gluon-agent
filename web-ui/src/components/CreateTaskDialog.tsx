import { useState, useEffect } from 'react'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Play, X, ChevronDown, GitBranch } from 'lucide-react'
import { fetchProjects, createRun } from '@/lib/api'
import { groupProjectsByWorkspace } from '@/lib/types'
import type { Project, ProjectWithWorkspace } from '@/lib/types'
import { cn } from '@/lib/utils'

interface CreateTaskDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onTaskCreated: () => void
  initialProject?: string
}

const MODEL_OPTIONS = [
  { value: 'claude-sonnet-4.5', label: 'Claude Sonnet 4.5', description: 'Fast, high-quality' },
  { value: 'claude-opus-4.5', label: 'Claude Opus 4.5', description: 'Highest quality' },
  { value: 'claude-haiku-4.5', label: 'Claude Haiku 4.5', description: 'Fastest' },
]

export function CreateTaskDialog({ open, onOpenChange, onTaskCreated, initialProject }: CreateTaskDialogProps) {
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProject, setSelectedProject] = useState<string>('')
  const [prompt, setPrompt] = useState('')
  const [model, setModel] = useState(MODEL_OPTIONS[0].value)
  const [useWorktree, setUseWorktree] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [projectDropdownOpen, setProjectDropdownOpen] = useState(false)
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false)

  useEffect(() => {
    if (open) {
      fetchProjects().then(setProjects).catch(console.error)
      setError(null)
      if (initialProject) {
        setSelectedProject(initialProject)
      }
    }
  }, [open, initialProject])

  // Reset form when closed
  useEffect(() => {
    if (!open) {
      setPrompt('')
      setSelectedProject(initialProject || '')
      setError(null)
      setModel(MODEL_OPTIONS[0].value)
      setUseWorktree(false)
    }
  }, [open, initialProject])

  const grouped = groupProjectsByWorkspace(projects)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedProject || !prompt.trim()) return

    setSubmitting(true)
    setError(null)

    try {
      await createRun({
        project_name: selectedProject,
        prompt: prompt.trim(),
        model,
        use_worktree: useWorktree,
      })
      onTaskCreated()
      onOpenChange(false)
      setPrompt('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create task')
    } finally {
      setSubmitting(false)
    }
  }

  const selectedModelOption = MODEL_OPTIONS.find(m => m.value === model)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="dialog-content max-w-lg w-[90vw] p-0 gap-0 overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 sm:px-5 py-3 border-b border-[rgba(163,163,163,0.1)] bg-[var(--color-void)]">
          <span className="text-[0.75rem] text-[var(--color-paper)] font-normal">New Task</span>
          <button
            className="p-1 text-[var(--color-stone)]/60 hover:text-[var(--color-paper)] transition-colors"
            onClick={() => onOpenChange(false)}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-4 sm:p-5 space-y-4">
          {/* Project Select */}
          <div>
            <label className="block text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
              Project
            </label>
            <div className="relative">
              <button
                type="button"
                className="w-full flex items-center justify-between px-3 py-2 text-[0.8125rem] text-left bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm hover:border-[rgba(163,163,163,0.3)] transition-colors"
                onClick={() => setProjectDropdownOpen(!projectDropdownOpen)}
              >
                <span className={selectedProject ? 'text-[var(--color-paper)]' : 'text-[var(--color-stone)]/60'}>
                  {selectedProject || 'Select a project...'}
                </span>
                <ChevronDown className={cn('w-4 h-4 text-[var(--color-stone)]/60 transition-transform', projectDropdownOpen && 'rotate-180')} />
              </button>

              {projectDropdownOpen && (
                <div className="absolute top-full left-0 right-0 mt-1 max-h-60 overflow-auto bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-xl z-50">
                  {Array.from(grouped.entries()).map(([workspace, workspaceProjects]) => (
                    <div key={workspace}>
                      <div className="px-3 py-1.5 text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/60 bg-[var(--color-void)]">
                        {workspace}
                      </div>
                      {workspaceProjects.map((project: ProjectWithWorkspace) => (
                        <button
                          key={project.id}
                          type="button"
                          className={cn(
                            'w-full px-3 py-2 text-left text-[0.8125rem] hover:bg-[rgba(163,163,163,0.1)] transition-colors',
                            selectedProject === project.name
                              ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                              : 'text-[var(--color-stone)]'
                          )}
                          onClick={() => {
                            setSelectedProject(project.name)
                            setProjectDropdownOpen(false)
                          }}
                        >
                          {project.name}
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Prompt */}
          <div>
            <label className="block text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
              Prompt
            </label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                  e.preventDefault()
                  if (selectedProject && prompt.trim() && !submitting) {
                    handleSubmit(e as unknown as React.FormEvent)
                  }
                }
              }}
              placeholder="What would you like the agent to do?"
              className="w-full px-3 py-2.5 text-[0.8125rem] text-[var(--color-paper)] bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm resize-none h-32 placeholder:text-[var(--color-stone)]/50 focus:outline-none focus:border-[rgba(163,163,163,0.3)] transition-colors"
              autoFocus
            />
          </div>

          {/* Model Select */}
          <div>
            <label className="block text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
              Model
            </label>
            <div className="relative">
              <button
                type="button"
                className="w-full flex items-center justify-between px-3 py-2 text-[0.8125rem] text-left bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm hover:border-[rgba(163,163,163,0.3)] transition-colors"
                onClick={() => setModelDropdownOpen(!modelDropdownOpen)}
              >
                <span className="text-[var(--color-paper)]">
                  {selectedModelOption?.label}
                  <span className="ml-2 text-[var(--color-stone)]/60">{selectedModelOption?.description}</span>
                </span>
                <ChevronDown className={cn('w-4 h-4 text-[var(--color-stone)]/60 transition-transform', modelDropdownOpen && 'rotate-180')} />
              </button>

              {modelDropdownOpen && (
                <div className="absolute top-full left-0 right-0 mt-1 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-xl z-50">
                  {MODEL_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={cn(
                        'w-full px-3 py-2 text-left text-[0.8125rem] hover:bg-[rgba(163,163,163,0.1)] transition-colors',
                        model === option.value
                          ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                          : 'text-[var(--color-stone)]'
                      )}
                      onClick={() => {
                        setModel(option.value)
                        setModelDropdownOpen(false)
                      }}
                    >
                      {option.label}
                      <span className="ml-2 text-[var(--color-stone)]/60">{option.description}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Worktree Toggle */}
          <div className="flex items-center justify-between py-2">
            <div className="flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-[var(--color-stone)]/60" />
              <div>
                <span className="text-[0.8125rem] text-[var(--color-paper)]">Use Git Worktree</span>
                <p className="text-[0.6875rem] text-[var(--color-stone)]/60">Run in isolated branch</p>
              </div>
            </div>
            <button
              type="button"
              className={cn(
                'relative w-10 h-5 rounded-full transition-colors',
                useWorktree ? 'bg-[var(--color-paper)]' : 'bg-[rgba(163,163,163,0.2)]'
              )}
              onClick={() => setUseWorktree(!useWorktree)}
            >
              <span
                className={cn(
                  'absolute top-0.5 w-4 h-4 rounded-full transition-all',
                  useWorktree
                    ? 'left-5.5 bg-[var(--color-void)]'
                    : 'left-0.5 bg-[var(--color-stone)]'
                )}
                style={{ left: useWorktree ? '22px' : '2px' }}
              />
            </button>
          </div>

          {/* Error */}
          {error && (
            <p className="text-[0.75rem] text-[var(--color-vermillion)]">{error}</p>
          )}

          {/* Actions */}
          <div className="flex items-center justify-end gap-3 pt-2">
            <button
              type="button"
              className="px-4 py-2 text-[0.6875rem] uppercase tracking-widest text-[var(--color-stone)] hover:text-[var(--color-paper)] transition-colors"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!selectedProject || !prompt.trim() || submitting}
              className={cn(
                'flex items-center gap-2 px-4 py-2 text-[0.6875rem] uppercase tracking-widest rounded-sm transition-colors',
                selectedProject && prompt.trim() && !submitting
                  ? 'bg-[var(--color-paper)] text-[var(--color-void)] hover:opacity-90'
                  : 'bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/60 cursor-not-allowed'
              )}
              title="⌘+Enter to submit"
            >
              <Play className="w-3 h-3" />
              {submitting ? 'Starting...' : 'Start'}
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
