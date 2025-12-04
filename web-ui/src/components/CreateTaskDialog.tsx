import { useState, useEffect, useRef, useCallback } from 'react'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Play, X, ChevronDown, GitBranch, Image as ImageIcon, Trash2 } from 'lucide-react'
import { fetchProjects, createRun, uploadAndAttachImage } from '@/lib/api'
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

// Pending image (uploaded before run creation)
interface PendingImage {
  file: File
  preview: string
}

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

  // Image upload state
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([])
  const [isDragging, setIsDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

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
      setUseWorktree(true)
      // Revoke preview URLs to prevent memory leaks
      pendingImages.forEach(img => URL.revokeObjectURL(img.preview))
      setPendingImages([])
    }
  }, [open, initialProject])

  // Image handling functions
  const handleFileSelect = useCallback((files: FileList | null) => {
    if (!files) return

    const validTypes = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
    const maxSize = 50 * 1024 * 1024 // 50MB

    const newImages: PendingImage[] = []
    for (const file of Array.from(files)) {
      if (!validTypes.includes(file.type)) {
        setError(`Invalid file type: ${file.name}. Use PNG, JPEG, GIF, or WebP.`)
        continue
      }
      if (file.size > maxSize) {
        setError(`File too large: ${file.name}. Max size is 50MB.`)
        continue
      }
      newImages.push({
        file,
        preview: URL.createObjectURL(file),
      })
    }

    setPendingImages(prev => [...prev, ...newImages])
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    handleFileSelect(e.dataTransfer.files)
  }, [handleFileSelect])

  // Handle paste events (Ctrl/Cmd+V with images)
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items
    if (!items) return

    const imageFiles: File[] = []
    for (const item of Array.from(items)) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) {
          // Generate a name for pasted images
          const ext = file.type.split('/')[1] || 'png'
          const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
          const namedFile = new File([file], `pasted-image-${timestamp}.${ext}`, { type: file.type })
          imageFiles.push(namedFile)
        }
      }
    }

    if (imageFiles.length > 0) {
      // Create a FileList-like object
      const dataTransfer = new DataTransfer()
      imageFiles.forEach(f => dataTransfer.items.add(f))
      handleFileSelect(dataTransfer.files)
    }
  }, [handleFileSelect])

  const removeImage = useCallback((index: number) => {
    setPendingImages(prev => {
      const updated = [...prev]
      URL.revokeObjectURL(updated[index].preview)
      updated.splice(index, 1)
      return updated
    })
  }, [])

  const grouped = groupProjectsByWorkspace(projects)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedProject || !prompt.trim()) return

    setSubmitting(true)
    setError(null)

    try {
      // Create the run first
      const run = await createRun({
        project_name: selectedProject,
        prompt: prompt.trim(),
        model,
        use_worktree: useWorktree,
      })

      // Upload and attach images
      if (pendingImages.length > 0) {
        const uploadPromises = pendingImages.map(img =>
          uploadAndAttachImage(run.id, img.file).catch(err => {
            console.error(`Failed to upload image ${img.file.name}:`, err)
            return null
          })
        )
        await Promise.all(uploadPromises)
      }

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
              onPaste={handlePaste}
              placeholder="What would you like the agent to do? (Paste images with ⌘V)"
              className="w-full px-3 py-2.5 text-[0.8125rem] text-[var(--color-paper)] bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm resize-none h-32 placeholder:text-[var(--color-stone)]/50 focus:outline-none focus:border-[rgba(163,163,163,0.3)] transition-colors"
              autoFocus
            />
          </div>

          {/* Image Attachments */}
          <div>
            <label className="block text-[0.625rem] uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
              Attachments (optional)
            </label>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp"
              multiple
              className="hidden"
              onChange={(e) => handleFileSelect(e.target.files)}
            />
            <div
              className={cn(
                'border-2 border-dashed rounded-sm p-3 transition-colors cursor-pointer',
                isDragging
                  ? 'border-[var(--color-paper)] bg-[rgba(163,163,163,0.1)]'
                  : 'border-[rgba(163,163,163,0.15)] hover:border-[rgba(163,163,163,0.3)]'
              )}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onPaste={handlePaste}
              onClick={() => fileInputRef.current?.click()}
              tabIndex={0}
            >
              {pendingImages.length === 0 ? (
                <div className="flex flex-col items-center py-2 text-center">
                  <ImageIcon className="w-6 h-6 text-[var(--color-stone)]/40 mb-1" />
                  <span className="text-[0.75rem] text-[var(--color-stone)]/60">
                    Drop images here or click to upload
                  </span>
                  <span className="text-[0.625rem] text-[var(--color-stone)]/40 mt-0.5">
                    PNG, JPEG, GIF, WebP • Max 50MB
                  </span>
                </div>
              ) : (
                <div className="grid grid-cols-4 gap-2" onClick={(e) => e.stopPropagation()}>
                  {pendingImages.map((img, index) => (
                    <div key={index} className="relative group">
                      <img
                        src={img.preview}
                        alt={img.file.name}
                        className="w-full h-16 object-cover rounded-sm border border-[rgba(163,163,163,0.15)]"
                      />
                      <button
                        type="button"
                        className="absolute top-0.5 right-0.5 p-0.5 bg-[var(--color-void)]/80 rounded-sm opacity-0 group-hover:opacity-100 transition-opacity"
                        onClick={(e) => {
                          e.stopPropagation()
                          removeImage(index)
                        }}
                      >
                        <Trash2 className="w-3 h-3 text-[var(--color-vermillion)]" />
                      </button>
                      <span className="absolute bottom-0.5 left-0.5 right-0.5 text-[0.5rem] text-[var(--color-paper)] bg-[var(--color-void)]/80 px-1 truncate rounded-sm">
                        {img.file.name}
                      </span>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="h-16 border border-dashed border-[rgba(163,163,163,0.2)] rounded-sm flex items-center justify-center hover:border-[rgba(163,163,163,0.4)] transition-colors"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <ImageIcon className="w-4 h-4 text-[var(--color-stone)]/40" />
                  </button>
                </div>
              )}
            </div>
            {pendingImages.length > 0 && (
              <p className="text-[0.625rem] text-[var(--color-stone)]/60 mt-1">
                {pendingImages.length} image{pendingImages.length !== 1 ? 's' : ''} selected
              </p>
            )}
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
