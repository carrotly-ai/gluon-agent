import { useState, useEffect } from 'react'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Play, X, ChevronDown } from 'lucide-react'
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

export function CreateTaskDialog({ open, onOpenChange, onTaskCreated, initialProject }: CreateTaskDialogProps) {
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProject, setSelectedProject] = useState<string>('')
  const [prompt, setPrompt] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [projectDropdownOpen, setProjectDropdownOpen] = useState(false)

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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="dialog-content max-w-lg w-[90vw] p-0 gap-0 overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 sm:px-5 py-3 border-b border-[rgba(163,163,163,0.1)] bg-[#0c0c0c]">
          <span className="text-[0.75rem] text-[#fafaf9] font-normal">New Task</span>
          <button
            className="p-1 text-[#a3a3a3]/60 hover:text-[#fafaf9] transition-colors"
            onClick={() => onOpenChange(false)}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-4 sm:p-5 space-y-4">
          {/* Project Select */}
          <div>
            <label className="block text-[0.625rem] uppercase tracking-widest text-[#a3a3a3]/70 mb-2">
              Project
            </label>
            <div className="relative">
              <button
                type="button"
                className="w-full flex items-center justify-between px-3 py-2 text-[0.8125rem] text-left bg-[#0c0c0c] border border-[rgba(163,163,163,0.15)] rounded-sm hover:border-[rgba(163,163,163,0.3)] transition-colors"
                onClick={() => setProjectDropdownOpen(!projectDropdownOpen)}
              >
                <span className={selectedProject ? 'text-[#fafaf9]' : 'text-[#a3a3a3]/60'}>
                  {selectedProject || 'Select a project...'}
                </span>
                <ChevronDown className={cn('w-4 h-4 text-[#a3a3a3]/60 transition-transform', projectDropdownOpen && 'rotate-180')} />
              </button>

              {projectDropdownOpen && (
                <div className="absolute top-full left-0 right-0 mt-1 max-h-60 overflow-auto bg-[#171717] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-xl z-50">
                  {Array.from(grouped.entries()).map(([workspace, workspaceProjects]) => (
                    <div key={workspace}>
                      <div className="px-3 py-1.5 text-[0.625rem] uppercase tracking-widest text-[#a3a3a3]/60 bg-[#0c0c0c]">
                        {workspace}
                      </div>
                      {workspaceProjects.map((project: ProjectWithWorkspace) => (
                        <button
                          key={project.id}
                          type="button"
                          className={cn(
                            'w-full px-3 py-2 text-left text-[0.8125rem] hover:bg-[rgba(163,163,163,0.1)] transition-colors',
                            selectedProject === project.name
                              ? 'text-[#fafaf9] bg-[rgba(163,163,163,0.08)]'
                              : 'text-[#a3a3a3]'
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
            <label className="block text-[0.625rem] uppercase tracking-widest text-[#a3a3a3]/70 mb-2">
              Prompt
            </label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="What would you like the agent to do?"
              className="w-full px-3 py-2.5 text-[0.8125rem] text-[#fafaf9] bg-[#0c0c0c] border border-[rgba(163,163,163,0.15)] rounded-sm resize-none h-32 placeholder:text-[#a3a3a3]/50 focus:outline-none focus:border-[rgba(163,163,163,0.3)] transition-colors"
              autoFocus
            />
          </div>

          {/* Error */}
          {error && (
            <p className="text-[0.75rem] text-[#c73e3a]">{error}</p>
          )}

          {/* Actions */}
          <div className="flex items-center justify-end gap-3 pt-2">
            <button
              type="button"
              className="px-4 py-2 text-[0.6875rem] uppercase tracking-widest text-[#a3a3a3] hover:text-[#fafaf9] transition-colors"
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
                  ? 'bg-[#fafaf9] text-[#0c0c0c] hover:bg-[#e5e5e5]'
                  : 'bg-[rgba(163,163,163,0.1)] text-[#a3a3a3]/60 cursor-not-allowed'
              )}
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
