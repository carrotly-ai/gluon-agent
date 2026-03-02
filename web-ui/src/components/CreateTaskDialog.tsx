import {
  ChevronDown,
  ChevronRight,
  GitBranch,
  Image as ImageIcon,
  Play,
  RefreshCw,
  Settings,
  Trash2,
  Users,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { CommandAutocomplete } from '@/components/CommandAutocomplete'
import { FileAutocomplete } from '@/components/FileAutocomplete'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import {
  createRun,
  fetchCommands,
  fetchFormulas,
  fetchProjectFiles,
  fetchProjects,
  runFormula,
  uploadAndAttachImage,
} from '@/lib/api'
import type {
  EffortLevel,
  FormulaTemplate,
  Project,
  ProjectFile,
  ProjectWithWorkspace,
  SlashCommand,
  TaskProfile,
  ThinkingBudget,
} from '@/lib/types'
import { groupProjectsByWorkspace } from '@/lib/types'
import { cn } from '@/lib/utils'

interface CreateTaskDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onTaskCreated: () => void
  initialProject?: string
}

// Task profile options
const PROFILE_OPTIONS = [
  { value: 'quick', label: 'Quick', description: 'Haiku - Fast responses', model: 'haiku' },
  {
    value: 'standard',
    label: 'Standard',
    description: 'Sonnet - Balanced',
    model: 'sonnet',
  },
  {
    value: 'deep',
    label: 'Deep',
    description: 'Opus 4.6 - Maximum reasoning (default)',
    model: 'opus-4.6',
  },
  {
    value: 'planning',
    label: 'Planning',
    description: 'Opus 4.6 - Plan before executing',
    model: 'opus-4.6',
  },
]

// Model options for advanced override
const MODEL_OPTIONS = [
  { value: '', label: 'Use profile default', description: '' },
  { value: 'opus-4.6', label: 'Opus 4.6', description: 'Highest quality (default)' },
  { value: 'opus-4.5', label: 'Opus 4.5', description: 'Previous generation' },
  { value: 'sonnet', label: 'Sonnet 4.6', description: 'Balanced' },
  { value: 'haiku', label: 'Haiku 4.5', description: 'Fastest' },
]

// Thinking budget options for advanced override
const THINKING_OPTIONS = [
  { value: '', label: 'Use profile default', description: '' },
  { value: 'none', label: 'None', description: '0 tokens' },
  { value: 'low', label: 'Low', description: '4k tokens' },
  { value: 'medium', label: 'Medium', description: '10k tokens' },
  { value: 'high', label: 'High', description: '16k tokens' },
  { value: 'ultrathink', label: 'Ultrathink', description: '32k tokens' },
  { value: 'adaptive', label: 'Adaptive', description: 'CLI decides' },
]

// Effort level options for advanced override
const EFFORT_OPTIONS = [
  { value: '', label: 'Use profile default', description: '' },
  { value: 'low', label: 'Low', description: 'Fast, simple reasoning' },
  { value: 'medium', label: 'Medium', description: 'Balanced depth' },
  { value: 'high', label: 'High', description: 'Deep reasoning' },
  { value: 'max', label: 'Max', description: 'Maximum reasoning effort' },
]

// Model transition options (only shown when Planning profile is selected)
const MODEL_TRANSITION_OPTIONS = [
  { value: '', label: 'None (single model)', description: 'Use one model throughout' },
  {
    value: 'opus-to-sonnet',
    label: 'Opus \u2192 Sonnet',
    description: 'Plan with Opus, implement with Sonnet',
  },
  {
    value: 'opus-to-haiku',
    label: 'Opus \u2192 Haiku',
    description: 'Plan with Opus, implement with Haiku',
  },
]

const DEFAULT_PROFILE = 'deep'
const PROFILE_STORAGE_KEY = 'gluon-profile'
const WORKTREE_STORAGE_KEY = 'gluon-use-worktree'
const RALPH_ENABLED_STORAGE_KEY = 'gluon-ralph-enabled'
const RALPH_MAX_LOOPS_STORAGE_KEY = 'gluon-ralph-max-loops'

const AGENT_TEAMS_TEMPLATE = `Create an agent team to design and implement: [describe the feature]

Spawn 3 teammates that actively debate and build on each other's ideas:

- Product Thinker: explore user needs, propose UX flows, and define acceptance criteria. Challenge the Architect on complexity that doesn't serve users.
- Architect: investigate the codebase, propose a technical design, and identify risks. Push back on Product when ideas conflict with existing patterns.
- Critic: stress-test both proposals — find edge cases, security gaps, and missing requirements. Force the others to defend their choices.

Phase 1 — Brainstorm: all three teammates explore the problem independently, then share findings and debate trade-offs with each other.
Phase 2 — Converge: teammates work together to produce a single implementation plan that addresses the Critic's concerns. Require plan approval before proceeding.
Phase 3 — Implement: Architect implements the feature, Product writes tests validating acceptance criteria, Critic reviews both for gaps. Each teammate owns separate files.

Wait for all teammates to complete before synthesising a summary.`

// Get last used profile from sessionStorage
function getLastUsedProfile(): string {
  if (typeof window === 'undefined') return DEFAULT_PROFILE
  return sessionStorage.getItem(PROFILE_STORAGE_KEY) || DEFAULT_PROFILE
}

// Get last worktree setting from sessionStorage
function getLastWorktreeSetting(): boolean {
  if (typeof window === 'undefined') return true
  const stored = sessionStorage.getItem(WORKTREE_STORAGE_KEY)
  return stored === null ? true : stored === 'true'
}

// Get last ralph enabled setting from sessionStorage
function getLastRalphEnabledSetting(): boolean {
  if (typeof window === 'undefined') return false
  return sessionStorage.getItem(RALPH_ENABLED_STORAGE_KEY) === 'true'
}

// Get last ralph max loops setting from sessionStorage
function getLastRalphMaxLoops(): number {
  if (typeof window === 'undefined') return 10
  const stored = sessionStorage.getItem(RALPH_MAX_LOOPS_STORAGE_KEY)
  return stored ? parseInt(stored, 10) : 100
}

// Pending image (uploaded before run creation)
interface PendingImage {
  file: File
  preview: string
}

export function CreateTaskDialog({
  open,
  onOpenChange,
  onTaskCreated,
  initialProject,
}: CreateTaskDialogProps) {
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProject, setSelectedProject] = useState<string>('')
  const [prompt, setPrompt] = useState('')
  const [profile, setProfile] = useState(getLastUsedProfile)
  const [useWorktree, setUseWorktree] = useState(getLastWorktreeSetting)
  const [ralphEnabled, setRalphEnabled] = useState(getLastRalphEnabledSetting)
  const [agentTeams, setAgentTeams] = useState(false)
  const [maxLoops, setMaxLoops] = useState(getLastRalphMaxLoops)
  const [maxCostUsd, setMaxCostUsd] = useState<string>('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [projectDropdownOpen, setProjectDropdownOpen] = useState(false)
  const [profileDropdownOpen, setProfileDropdownOpen] = useState(false)

  // Formula mode state
  const [mode, setMode] = useState<'manual' | 'formula'>('manual')
  const [formulaTemplates, setFormulaTemplates] = useState<FormulaTemplate[]>([])
  const [selectedFormula, setSelectedFormula] = useState<string>('')
  const [formulaVariables, setFormulaVariables] = useState<Record<string, string>>({})
  const [loadingFormulas, setLoadingFormulas] = useState(false)

  // Advanced options state
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [modelOverride, setModelOverride] = useState('')
  const [thinkingOverride, setThinkingOverride] = useState('')
  const [maxBudgetOverride, setMaxBudgetOverride] = useState<string>('')
  const [advancedModelDropdownOpen, setAdvancedModelDropdownOpen] = useState(false)
  const [advancedThinkingDropdownOpen, setAdvancedThinkingDropdownOpen] = useState(false)
  const [effortOverride, setEffortOverride] = useState('')
  const [advancedEffortDropdownOpen, setAdvancedEffortDropdownOpen] = useState(false)
  const [modelTransition, setModelTransition] = useState('')
  const [modelTransitionDropdownOpen, setModelTransitionDropdownOpen] = useState(false)

  // Image upload state
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([])
  const [isDragging, setIsDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Slash command autocomplete state
  const [commands, setCommands] = useState<SlashCommand[]>([])
  const [showAutocomplete, setShowAutocomplete] = useState(false)
  const [autocompleteFilter, setAutocompleteFilter] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // File autocomplete state (@mentions)
  const [projectFiles, setProjectFiles] = useState<ProjectFile[]>([])
  const [showFileAutocomplete, setShowFileAutocomplete] = useState(false)
  const [fileAutocompleteFilter, setFileAutocompleteFilter] = useState('')
  const [filesLoading, setFilesLoading] = useState(false)
  const [filesTruncated, setFilesTruncated] = useState(false)

  useEffect(() => {
    if (open) {
      fetchProjects().then(setProjects).catch(console.error)
      // Load global commands initially (project-specific loaded when project selected)
      fetchCommands().then(setCommands).catch(console.error)
      setError(null)
      if (initialProject) {
        setSelectedProject(initialProject)
      }
    }
  }, [open, initialProject])

  // Load formulas when switching to formula mode
  useEffect(() => {
    if (mode === 'formula' && formulaTemplates.length === 0 && !loadingFormulas) {
      setLoadingFormulas(true)
      fetchFormulas()
        .then((data) => setFormulaTemplates(data.formulas))
        .catch(console.error)
        .finally(() => setLoadingFormulas(false))
    }
  }, [mode, formulaTemplates.length, loadingFormulas])

  // Load files and project-specific commands when project is selected
  useEffect(() => {
    if (!selectedProject || !open) {
      setProjectFiles([])
      return
    }

    // Find project ID from project name
    const project = projects.find((p) => p.name === selectedProject)
    if (!project) return

    // Fetch project-specific commands (includes global merged)
    fetchCommands(project.id).then(setCommands).catch(console.error)

    // Fetch project files for @mentions
    setFilesLoading(true)
    fetchProjectFiles(project.id)
      .then(({ files, truncated }) => {
        setProjectFiles(files)
        setFilesTruncated(truncated)
      })
      .catch(console.error)
      .finally(() => setFilesLoading(false))
  }, [selectedProject, projects, open])

  // Reset form when closed (but preserve model and worktree preferences)
  useEffect(() => {
    if (!open) {
      setPrompt('')
      setSelectedProject(initialProject || '')
      setError(null)
      // Don't reset model and worktree - they persist via sessionStorage
      // Revoke preview URLs to prevent memory leaks
      pendingImages.forEach((img) => URL.revokeObjectURL(img.preview))
      setPendingImages([])
      setMode('manual')
      setSelectedFormula('')
      setFormulaVariables({})
    }
  }, [
    open,
    initialProject, // Don't reset model and worktree - they persist via sessionStorage
    // Revoke preview URLs to prevent memory leaks
    pendingImages.forEach,
  ])

  // Persist profile selection to sessionStorage
  useEffect(() => {
    sessionStorage.setItem(PROFILE_STORAGE_KEY, profile)
  }, [profile])

  // Persist worktree setting to sessionStorage
  useEffect(() => {
    sessionStorage.setItem(WORKTREE_STORAGE_KEY, String(useWorktree))
  }, [useWorktree])

  // Persist ralph enabled setting to sessionStorage
  useEffect(() => {
    sessionStorage.setItem(RALPH_ENABLED_STORAGE_KEY, String(ralphEnabled))
  }, [ralphEnabled])

  // Persist ralph max loops setting to sessionStorage
  useEffect(() => {
    sessionStorage.setItem(RALPH_MAX_LOOPS_STORAGE_KEY, String(maxLoops))
  }, [maxLoops])

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

    setPendingImages((prev) => [...prev, ...newImages])
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setIsDragging(false)
      handleFileSelect(e.dataTransfer.files)
    },
    [handleFileSelect]
  )

  // Handle paste events (Ctrl/Cmd+V with images)
  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
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
            const namedFile = new File([file], `pasted-image-${timestamp}.${ext}`, {
              type: file.type,
            })
            imageFiles.push(namedFile)
          }
        }
      }

      if (imageFiles.length > 0) {
        // Create a FileList-like object
        const dataTransfer = new DataTransfer()
        imageFiles.forEach((f) => dataTransfer.items.add(f))
        handleFileSelect(dataTransfer.files)
      }
    },
    [handleFileSelect]
  )

  const removeImage = useCallback((index: number) => {
    setPendingImages((prev) => {
      const updated = [...prev]
      URL.revokeObjectURL(updated[index].preview)
      updated.splice(index, 1)
      return updated
    })
  }, [])

  const grouped = groupProjectsByWorkspace(projects)

  // Handle prompt changes and detect slash command / file triggers
  const handlePromptChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const value = e.target.value
      setPrompt(value)

      // Check if we should show autocomplete
      const cursorPos = e.target.selectionStart
      const textBeforeCursor = value.slice(0, cursorPos)

      // Find if there's a `/` at the start or after a space/newline (slash commands)
      const lastSlashMatch = textBeforeCursor.match(/(?:^|\s)\/(\S*)$/)
      // Find if there's a `@` at the start or after a space/newline (file mentions)
      const lastAtMatch = textBeforeCursor.match(/(?:^|\s)@(\S*)$/)

      if (lastSlashMatch) {
        const filter = lastSlashMatch[1]
        setAutocompleteFilter(filter)
        setShowAutocomplete(true)
        setShowFileAutocomplete(false)
        setFileAutocompleteFilter('')
      } else if (lastAtMatch && selectedProject) {
        const filter = lastAtMatch[1]
        setFileAutocompleteFilter(filter)
        setShowFileAutocomplete(true)
        setShowAutocomplete(false)
        setAutocompleteFilter('')
      } else {
        setShowAutocomplete(false)
        setAutocompleteFilter('')
        setShowFileAutocomplete(false)
        setFileAutocompleteFilter('')
      }
    },
    [selectedProject]
  )

  // Handle command selection from autocomplete
  const handleCommandSelect = useCallback(
    (command: SlashCommand) => {
      if (!textareaRef.current) return

      const cursorPos = textareaRef.current.selectionStart
      const textBeforeCursor = prompt.slice(0, cursorPos)
      const textAfterCursor = prompt.slice(cursorPos)

      // Find the start of the slash command we're replacing
      const slashMatch = textBeforeCursor.match(/(?:^|\s)(\/\S*)$/)
      if (slashMatch) {
        const matchStart = textBeforeCursor.length - slashMatch[1].length
        const newText = `${prompt.slice(0, matchStart)}/${command.name} ${textAfterCursor}`
        setPrompt(newText)

        // Move cursor after the inserted command
        const newCursorPos = matchStart + command.name.length + 2 // +2 for '/' and space
        setTimeout(() => {
          textareaRef.current?.focus()
          textareaRef.current?.setSelectionRange(newCursorPos, newCursorPos)
        }, 0)
      }

      setShowAutocomplete(false)
      setAutocompleteFilter('')
    },
    [prompt]
  )

  const handleAutocompleteClose = useCallback(() => {
    setShowAutocomplete(false)
    setAutocompleteFilter('')
    textareaRef.current?.focus()
  }, [])

  // Handle file path selection from autocomplete (@mentions)
  const handleFileMentionSelect = useCallback(
    (file: ProjectFile) => {
      if (!textareaRef.current) return

      const cursorPos = textareaRef.current.selectionStart
      const textBeforeCursor = prompt.slice(0, cursorPos)
      const textAfterCursor = prompt.slice(cursorPos)

      // Find the start of the @mention we're replacing
      const atMatch = textBeforeCursor.match(/(?:^|\s)(@\S*)$/)
      if (atMatch) {
        const matchStart = textBeforeCursor.length - atMatch[1].length
        const newText = `${prompt.slice(0, matchStart)}@${file.path} ${textAfterCursor}`
        setPrompt(newText)

        // Move cursor after the inserted file path
        const newCursorPos = matchStart + file.path.length + 2 // +2 for '@' and space
        setTimeout(() => {
          textareaRef.current?.focus()
          textareaRef.current?.setSelectionRange(newCursorPos, newCursorPos)
        }, 0)
      }

      setShowFileAutocomplete(false)
      setFileAutocompleteFilter('')
    },
    [prompt]
  )

  const handleFileAutocompleteClose = useCallback(() => {
    setShowFileAutocomplete(false)
    setFileAutocompleteFilter('')
    textareaRef.current?.focus()
  }, [])

  const handleFormulaSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedProject || !selectedFormula) return

    const project = projects.find((p) => p.name === selectedProject)
    if (!project) return

    setSubmitting(true)
    setError(null)

    try {
      await runFormula(selectedFormula, {
        project_id: project.id,
        variables: formulaVariables,
      })
      onTaskCreated()
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to run formula')
    } finally {
      setSubmitting(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedProject || !prompt.trim()) return

    setSubmitting(true)
    setError(null)

    try {
      // Create the run first
      const costValue = maxCostUsd ? parseFloat(maxCostUsd) : undefined
      const budgetOverrideValue = maxBudgetOverride ? parseFloat(maxBudgetOverride) : undefined

      // Get profile config for model fallback
      const profileConfig = PROFILE_OPTIONS.find((p) => p.value === profile)

      const run = await createRun({
        project_name: selectedProject,
        prompt: prompt.trim(),
        // Profile-based options
        profile: profile as TaskProfile,
        model: modelOverride || profileConfig?.model || 'sonnet',
        model_override: modelOverride || undefined,
        thinking_override: thinkingOverride ? (thinkingOverride as ThinkingBudget) : undefined,
        effort_override: effortOverride ? (effortOverride as EffortLevel) : undefined,
        max_budget_override: budgetOverrideValue,
        force_planning: profile === 'planning',
        // Existing options
        use_worktree: useWorktree,
        ralph_enabled: ralphEnabled,
        max_loops: ralphEnabled ? maxLoops : undefined,
        max_cost_usd: ralphEnabled && costValue && costValue > 0 ? costValue : undefined,
        agent_teams: agentTeams || undefined,
        model_transition: modelTransition || undefined,
      })

      // Upload and attach images
      if (pendingImages.length > 0) {
        const uploadPromises = pendingImages.map((img) =>
          uploadAndAttachImage(run.id, img.file).catch((err) => {
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

  const selectedProfileOption = PROFILE_OPTIONS.find((p) => p.value === profile)
  const selectedAdvancedModelOption = MODEL_OPTIONS.find((m) => m.value === modelOverride)
  const selectedAdvancedThinkingOption = THINKING_OPTIONS.find((t) => t.value === thinkingOverride)
  const selectedAdvancedEffortOption = EFFORT_OPTIONS.find((e) => e.value === effortOverride)
  const selectedModelTransitionOption = MODEL_TRANSITION_OPTIONS.find(
    (t) => t.value === modelTransition
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="dialog-content max-w-lg w-[90vw] p-0 gap-0 overflow-hidden"
        showCloseButton={false}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 sm:px-5 py-3 border-b border-[rgba(163,163,163,0.1)] bg-[var(--color-void)]">
          <span className="text-body text-[var(--color-paper)] font-normal">New Task</span>
          <button
            className="p-1 text-[var(--color-stone)]/60 hover:text-[var(--color-paper)] transition-colors"
            onClick={() => onOpenChange(false)}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Mode Toggle */}
        <div className="px-4 sm:px-5 pt-3 flex items-center gap-0.5 bg-[rgba(163,163,163,0.03)]">
          <div className="flex items-center gap-0.5 bg-[rgba(163,163,163,0.06)] rounded-sm p-0.5">
            <button
              type="button"
              className={cn(
                'px-2.5 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors',
                mode === 'manual'
                  ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                  : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
              )}
              onClick={() => setMode('manual')}
            >
              Manual
            </button>
            <button
              type="button"
              className={cn(
                'px-2.5 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors',
                mode === 'formula'
                  ? 'bg-[var(--color-paper)]/10 text-[var(--color-paper)]'
                  : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
              )}
              onClick={() => setMode('formula')}
            >
              Formula
            </button>
          </div>
        </div>

        {mode === 'formula' ? (
          <form onSubmit={handleFormulaSubmit} className="p-4 sm:p-5 space-y-4">
            {/* Project Select for Formula */}
            <div>
              <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
                Project
              </label>
              <select
                value={selectedProject}
                onChange={(e) => setSelectedProject(e.target.value)}
                className="w-full px-3 py-2 text-title bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm text-[var(--color-paper)]"
              >
                <option value="">Select project...</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Formula Select */}
            <div>
              <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
                Formula
              </label>
              {loadingFormulas ? (
                <div className="flex items-center gap-2 text-caption text-[var(--color-stone)]/60">
                  <div className="mark mark-running w-2 h-2" />
                  Loading formulas...
                </div>
              ) : (
                <select
                  value={selectedFormula}
                  onChange={(e) => {
                    setSelectedFormula(e.target.value)
                    // Reset variables and populate defaults
                    const tmpl = formulaTemplates.find((f) => f.name === e.target.value)
                    const defaults: Record<string, string> = {}
                    if (tmpl) {
                      for (const v of tmpl.variables) {
                        defaults[v.name] = v.default || ''
                      }
                    }
                    setFormulaVariables(defaults)
                  }}
                  className="w-full px-3 py-2 text-title bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm text-[var(--color-paper)]"
                >
                  <option value="">Select formula...</option>
                  {formulaTemplates.map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.name}
                      {f.description ? ` — ${f.description}` : ''}
                    </option>
                  ))}
                </select>
              )}
            </div>

            {/* Dynamic Variable Inputs */}
            {selectedFormula &&
              (() => {
                const tmpl = formulaTemplates.find((f) => f.name === selectedFormula)
                if (!tmpl || tmpl.variables.length === 0) return null
                return (
                  <div className="space-y-3">
                    <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/70">
                      Variables
                    </label>
                    {tmpl.variables.map((v) => (
                      <div key={v.name}>
                        <label className="block text-caption text-[var(--color-stone)]/60 mb-1">
                          {v.name}
                          {v.required && (
                            <span className="text-[var(--color-vermillion)] ml-1">*</span>
                          )}
                          {v.help && (
                            <span className="text-[var(--color-stone)]/40 ml-2 font-normal">
                              {v.help}
                            </span>
                          )}
                        </label>
                        <input
                          type="text"
                          value={formulaVariables[v.name] || ''}
                          onChange={(e) =>
                            setFormulaVariables((prev) => ({
                              ...prev,
                              [v.name]: e.target.value,
                            }))
                          }
                          placeholder={v.default || `Enter ${v.name}...`}
                          className="w-full px-3 py-2 text-body bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm text-[var(--color-paper)] placeholder:text-[var(--color-stone)]/40 focus:outline-none focus:border-[rgba(163,163,163,0.3)]"
                        />
                      </div>
                    ))}
                  </div>
                )
              })()}

            {/* Step Preview */}
            {selectedFormula &&
              (() => {
                const tmpl = formulaTemplates.find((f) => f.name === selectedFormula)
                if (!tmpl) return null
                return (
                  <div>
                    <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
                      Steps ({tmpl.steps.length})
                    </label>
                    <div className="space-y-1 text-caption">
                      {tmpl.steps.map((step, idx) => (
                        <div
                          key={step.id}
                          className="flex items-center gap-2 text-[var(--color-stone)]/60"
                        >
                          <span className="text-[var(--color-stone)]/30 font-mono w-4 text-right">
                            {idx + 1}
                          </span>
                          <span className="text-[var(--color-paper)]">{step.name}</span>
                          <span className="text-[var(--color-stone)]/30">[{step.profile}]</span>
                          {step.depends_on.length > 0 && (
                            <span className="text-[var(--color-stone)]/20">
                              after: {step.depends_on.join(', ')}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )
              })()}

            {/* Error */}
            {error && <p className="text-caption text-[var(--color-vermillion)]">{error}</p>}

            {/* Submit */}
            <button
              type="submit"
              disabled={submitting || !selectedProject || !selectedFormula}
              className="w-full py-2.5 text-body uppercase tracking-widest bg-[var(--color-paper)] text-[var(--color-void)] rounded-sm hover:opacity-90 disabled:opacity-40 transition-colors flex items-center justify-center gap-2"
            >
              {submitting ? (
                <>
                  <Play className="w-3 h-3 animate-pulse" />
                  Running Formula...
                </>
              ) : (
                <>
                  <Play className="w-3 h-3" />
                  Run Formula
                </>
              )}
            </button>
          </form>
        ) : (
          <form onSubmit={handleSubmit} className="p-4 sm:p-5 space-y-4">
            {/* Project Select */}
            <div>
              <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
                Project
              </label>
              <div className="relative">
                <button
                  type="button"
                  className="w-full flex items-center justify-between px-3 py-2 text-title text-left bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm hover:border-[rgba(163,163,163,0.3)] transition-colors"
                  onClick={() => setProjectDropdownOpen(!projectDropdownOpen)}
                >
                  <span
                    className={
                      selectedProject ? 'text-[var(--color-paper)]' : 'text-[var(--color-stone)]/60'
                    }
                  >
                    {selectedProject || 'Select a project...'}
                  </span>
                  <ChevronDown
                    className={cn(
                      'w-4 h-4 text-[var(--color-stone)]/60 transition-transform',
                      projectDropdownOpen && 'rotate-180'
                    )}
                  />
                </button>

                {projectDropdownOpen && (
                  <div className="absolute top-full left-0 right-0 mt-1 max-h-60 overflow-auto bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-xl z-50">
                    {Array.from(grouped.entries()).map(([workspace, workspaceProjects]) => (
                      <div key={workspace}>
                        <div className="px-3 py-1.5 text-caption uppercase tracking-widest text-[var(--color-stone)]/60 bg-[var(--color-void)]">
                          {workspace}
                        </div>
                        {workspaceProjects.map((project: ProjectWithWorkspace) => (
                          <button
                            key={project.id}
                            type="button"
                            className={cn(
                              'w-full px-3 py-2 text-left text-title hover:bg-[rgba(163,163,163,0.1)] transition-colors',
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
            <div className="relative">
              <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
                Prompt
              </label>
              <textarea
                ref={textareaRef}
                value={prompt}
                onChange={handlePromptChange}
                onKeyDown={(e) => {
                  // Let autocomplete handle navigation keys when visible
                  if (
                    (showAutocomplete || showFileAutocomplete) &&
                    ['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape'].includes(e.key)
                  ) {
                    return
                  }
                  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                    e.preventDefault()
                    if (selectedProject && prompt.trim() && !submitting) {
                      handleSubmit(e as unknown as React.FormEvent)
                    }
                  }
                }}
                onPaste={handlePaste}
                placeholder="Type / for commands, @ for files. Paste images with ⌘V"
                className="w-full px-3 py-2.5 text-title text-[var(--color-paper)] bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm resize-none h-32 placeholder:text-[var(--color-stone)]/50 focus:outline-none focus:border-[rgba(163,163,163,0.3)] transition-colors"
                autoFocus
              />
              {/* Slash command autocomplete - rendered via portal */}
              <CommandAutocomplete
                commands={commands}
                filter={autocompleteFilter}
                visible={showAutocomplete}
                onSelect={handleCommandSelect}
                onClose={handleAutocompleteClose}
                anchorRef={textareaRef}
              />
              {/* File autocomplete (@mentions) - rendered via portal */}
              <FileAutocomplete
                files={projectFiles}
                filter={fileAutocompleteFilter}
                visible={showFileAutocomplete}
                onSelect={handleFileMentionSelect}
                onClose={handleFileAutocompleteClose}
                anchorRef={textareaRef}
                loading={filesLoading}
                truncated={filesTruncated}
              />
            </div>

            {/* Image Attachments - Compact */}
            <div>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/gif,image/webp"
                multiple
                className="hidden"
                onChange={(e) => handleFileSelect(e.target.files)}
              />
              {pendingImages.length === 0 ? (
                <button
                  type="button"
                  className={cn(
                    'w-full flex items-center gap-2 px-3 py-2 rounded-sm transition-colors',
                    isDragging
                      ? 'bg-[rgba(163,163,163,0.1)] border border-dashed border-[var(--color-paper)]'
                      : 'hover:bg-[rgba(163,163,163,0.05)]'
                  )}
                  onClick={() => fileInputRef.current?.click()}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                >
                  <ImageIcon className="w-4 h-4 text-[var(--color-stone)]/40" />
                  <span className="text-body text-[var(--color-stone)]/50">Attach images</span>
                  <span className="text-caption text-[var(--color-stone)]/30 ml-auto">
                    drag, paste, or click
                  </span>
                </button>
              ) : (
                <div
                  className={cn(
                    'border border-dashed rounded-sm p-2 transition-colors',
                    isDragging
                      ? 'border-[var(--color-paper)] bg-[rgba(163,163,163,0.1)]'
                      : 'border-[rgba(163,163,163,0.15)]'
                  )}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                >
                  <div className="grid grid-cols-5 gap-1.5">
                    {pendingImages.map((img, index) => (
                      <div key={img.preview} className="relative group">
                        <img
                          src={img.preview}
                          alt={img.file.name}
                          className="w-full h-12 object-cover rounded-sm border border-[rgba(163,163,163,0.15)]"
                        />
                        <button
                          type="button"
                          className="absolute top-0.5 right-0.5 p-0.5 bg-[var(--color-void)]/80 rounded-sm opacity-0 group-hover:opacity-100 transition-opacity"
                          onClick={() => removeImage(index)}
                        >
                          <Trash2 className="w-2.5 h-2.5 text-[var(--color-vermillion)]" />
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      className="h-12 border border-dashed border-[rgba(163,163,163,0.2)] rounded-sm flex items-center justify-center hover:border-[rgba(163,163,163,0.4)] transition-colors"
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <ImageIcon className="w-3 h-3 text-[var(--color-stone)]/40" />
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* Profile Select */}
            <div>
              <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/70 mb-2">
                Profile
              </label>
              <div className="relative">
                <button
                  type="button"
                  className="w-full flex items-center justify-between px-3 py-2 text-title text-left bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm hover:border-[rgba(163,163,163,0.3)] transition-colors"
                  onClick={() => setProfileDropdownOpen(!profileDropdownOpen)}
                >
                  <span className="text-[var(--color-paper)]">
                    {selectedProfileOption?.label}
                    <span className="ml-2 text-[var(--color-stone)]/60">
                      {selectedProfileOption?.description}
                    </span>
                  </span>
                  <ChevronDown
                    className={cn(
                      'w-4 h-4 text-[var(--color-stone)]/60 transition-transform',
                      profileDropdownOpen && 'rotate-180'
                    )}
                  />
                </button>

                {profileDropdownOpen && (
                  <div className="absolute top-full left-0 right-0 mt-1 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-xl z-50">
                    {PROFILE_OPTIONS.map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        className={cn(
                          'w-full px-3 py-2 text-left text-title hover:bg-[rgba(163,163,163,0.1)] transition-colors',
                          profile === option.value
                            ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                            : 'text-[var(--color-stone)]'
                        )}
                        onClick={() => {
                          setProfile(option.value)
                          setProfileDropdownOpen(false)
                          // Clear model transition when switching away from Planning
                          if (option.value !== 'planning') {
                            setModelTransition('')
                          }
                        }}
                      >
                        {option.label}
                        <span className="ml-2 text-[var(--color-stone)]/60">
                          {option.description}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Advanced Options */}
            <div>
              <button
                type="button"
                className="flex items-center gap-2 text-body text-[var(--color-stone)]/70 hover:text-[var(--color-paper)] transition-colors"
                onClick={() => setShowAdvanced(!showAdvanced)}
              >
                <ChevronRight
                  className={cn('w-4 h-4 transition-transform', showAdvanced && 'rotate-90')}
                />
                <Settings className="w-4 h-4" />
                <span>Advanced Options</span>
              </button>

              {showAdvanced && (
                <div className="mt-3 pl-6 space-y-4 border-l-2 border-[rgba(163,163,163,0.15)]">
                  {/* Worktree Toggle */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <GitBranch className="w-4 h-4 text-[var(--color-stone)]/60" />
                      <div>
                        <span className="text-body text-[var(--color-paper)]">
                          Use Git Worktree
                        </span>
                        <p className="text-caption text-[var(--color-stone)]/60">
                          Run in isolated branch
                        </p>
                      </div>
                    </div>
                    <button
                      type="button"
                      className={cn(
                        'relative w-10 h-5 rounded-full transition-colors shrink-0',
                        useWorktree ? 'bg-[var(--color-paper)]' : 'bg-[rgba(163,163,163,0.2)]'
                      )}
                      onClick={() => setUseWorktree(!useWorktree)}
                    >
                      <span
                        className={cn(
                          'absolute top-0.5 w-4 h-4 rounded-full transition-all',
                          useWorktree ? 'bg-[var(--color-void)]' : 'bg-[var(--color-stone)]'
                        )}
                        style={{ left: useWorktree ? '22px' : '2px' }}
                      />
                    </button>
                  </div>

                  {/* Ralph Loop Toggle */}
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <RefreshCw
                          className={cn(
                            'w-4 h-4 transition-colors',
                            ralphEnabled
                              ? 'text-[var(--color-sky)]'
                              : 'text-[var(--color-stone)]/60'
                          )}
                        />
                        <div>
                          <span className="text-body text-[var(--color-paper)]">
                            Enable Ralph Loop
                          </span>
                          <p className="text-caption text-[var(--color-stone)]/60">
                            Autonomous execution until complete
                          </p>
                        </div>
                      </div>
                      <button
                        type="button"
                        className={cn(
                          'relative w-10 h-5 rounded-full transition-colors shrink-0',
                          ralphEnabled ? 'bg-[var(--color-sky)]' : 'bg-[rgba(163,163,163,0.2)]'
                        )}
                        onClick={() => setRalphEnabled(!ralphEnabled)}
                      >
                        <span
                          className={cn(
                            'absolute top-0.5 w-4 h-4 rounded-full transition-all',
                            ralphEnabled ? 'bg-[var(--color-void)]' : 'bg-[var(--color-stone)]'
                          )}
                          style={{ left: ralphEnabled ? '22px' : '2px' }}
                        />
                      </button>
                    </div>

                    {/* Ralph Options (shown when enabled) */}
                    {ralphEnabled && (
                      <div className="pl-6 space-y-3 border-l-2 border-[var(--color-sky)]/30">
                        <div className="flex items-center justify-between">
                          <label className="text-body text-[var(--color-stone)]">
                            Max Iterations
                          </label>
                          <input
                            type="number"
                            min={1}
                            max={1000}
                            value={maxLoops}
                            onChange={(e) =>
                              setMaxLoops(
                                Math.max(1, Math.min(1000, parseInt(e.target.value, 10) || 1))
                              )
                            }
                            className="w-20 px-2 py-1 text-body text-[var(--color-paper)] text-right bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[rgba(163,163,163,0.3)]"
                          />
                        </div>
                        <div className="flex items-center justify-between">
                          <label className="text-body text-[var(--color-stone)]">
                            Cost Limit (USD)
                          </label>
                          <div className="flex items-center gap-1">
                            <span className="text-body text-[var(--color-stone)]/60">$</span>
                            <input
                              type="text"
                              inputMode="decimal"
                              placeholder="optional"
                              value={maxCostUsd}
                              onChange={(e) => {
                                const value = e.target.value
                                if (value === '' || /^\d*\.?\d*$/.test(value)) {
                                  setMaxCostUsd(value)
                                }
                              }}
                              className="w-20 px-2 py-1 text-body text-[var(--color-paper)] text-right bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[rgba(163,163,163,0.3)] placeholder:text-[var(--color-stone)]/40"
                            />
                          </div>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Agent Teams Toggle */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Users
                        className={cn(
                          'w-4 h-4 transition-colors',
                          agentTeams ? 'text-[var(--color-sky)]' : 'text-[var(--color-stone)]/60'
                        )}
                      />
                      <div>
                        <span className="text-body text-[var(--color-paper)]">
                          Enable Agent Teams
                        </span>
                        <p className="text-caption text-[var(--color-stone)]/60">
                          Coordinated multi-agent collaboration
                        </p>
                      </div>
                    </div>
                    <button
                      type="button"
                      className={cn(
                        'relative w-10 h-5 rounded-full transition-colors shrink-0',
                        agentTeams ? 'bg-[var(--color-sky)]' : 'bg-[rgba(163,163,163,0.2)]'
                      )}
                      onClick={() => {
                        const enabling = !agentTeams
                        setAgentTeams(enabling)
                        if (enabling && !prompt.trim()) {
                          setPrompt(AGENT_TEAMS_TEMPLATE)
                          setTimeout(() => {
                            textareaRef.current?.focus()
                            textareaRef.current?.setSelectionRange(0, 0)
                            textareaRef.current?.scrollTo(0, 0)
                          }, 0)
                        }
                      }}
                    >
                      <span
                        className={cn(
                          'absolute top-0.5 w-4 h-4 rounded-full transition-all',
                          agentTeams ? 'bg-[var(--color-void)]' : 'bg-[var(--color-stone)]'
                        )}
                        style={{ left: agentTeams ? '22px' : '2px' }}
                      />
                    </button>
                  </div>

                  {/* Model Override */}
                  <div>
                    <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/60 mb-1.5">
                      Model Override
                    </label>
                    <div className="relative">
                      <button
                        type="button"
                        className="w-full flex items-center justify-between px-3 py-2 text-body text-left bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm hover:border-[rgba(163,163,163,0.3)] transition-colors"
                        onClick={() => setAdvancedModelDropdownOpen(!advancedModelDropdownOpen)}
                      >
                        <span
                          className={
                            modelOverride
                              ? 'text-[var(--color-paper)]'
                              : 'text-[var(--color-stone)]/60'
                          }
                        >
                          {selectedAdvancedModelOption?.label || 'Use profile default'}
                          {selectedAdvancedModelOption?.description && (
                            <span className="ml-2 text-[var(--color-stone)]/60">
                              {selectedAdvancedModelOption.description}
                            </span>
                          )}
                        </span>
                        <ChevronDown
                          className={cn(
                            'w-3 h-3 text-[var(--color-stone)]/60 transition-transform',
                            advancedModelDropdownOpen && 'rotate-180'
                          )}
                        />
                      </button>

                      {advancedModelDropdownOpen && (
                        <div className="absolute top-full left-0 right-0 mt-1 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-xl z-50">
                          {MODEL_OPTIONS.map((option) => (
                            <button
                              key={option.value}
                              type="button"
                              className={cn(
                                'w-full px-3 py-2 text-left text-body hover:bg-[rgba(163,163,163,0.1)] transition-colors',
                                modelOverride === option.value
                                  ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                                  : 'text-[var(--color-stone)]'
                              )}
                              onClick={() => {
                                setModelOverride(option.value)
                                setAdvancedModelDropdownOpen(false)
                              }}
                            >
                              {option.label}
                              {option.description && (
                                <span className="ml-2 text-[var(--color-stone)]/60">
                                  {option.description}
                                </span>
                              )}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Thinking Budget Override */}
                  <div>
                    <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/60 mb-1.5">
                      Thinking Budget
                    </label>
                    <div className="relative">
                      <button
                        type="button"
                        className="w-full flex items-center justify-between px-3 py-2 text-body text-left bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm hover:border-[rgba(163,163,163,0.3)] transition-colors"
                        onClick={() =>
                          setAdvancedThinkingDropdownOpen(!advancedThinkingDropdownOpen)
                        }
                      >
                        <span
                          className={
                            thinkingOverride
                              ? 'text-[var(--color-paper)]'
                              : 'text-[var(--color-stone)]/60'
                          }
                        >
                          {selectedAdvancedThinkingOption?.label || 'Use profile default'}
                          {selectedAdvancedThinkingOption?.description && (
                            <span className="ml-2 text-[var(--color-stone)]/60">
                              {selectedAdvancedThinkingOption.description}
                            </span>
                          )}
                        </span>
                        <ChevronDown
                          className={cn(
                            'w-3 h-3 text-[var(--color-stone)]/60 transition-transform',
                            advancedThinkingDropdownOpen && 'rotate-180'
                          )}
                        />
                      </button>

                      {advancedThinkingDropdownOpen && (
                        <div className="absolute top-full left-0 right-0 mt-1 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-xl z-50">
                          {THINKING_OPTIONS.map((option) => (
                            <button
                              key={option.value}
                              type="button"
                              className={cn(
                                'w-full px-3 py-2 text-left text-body hover:bg-[rgba(163,163,163,0.1)] transition-colors',
                                thinkingOverride === option.value
                                  ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                                  : 'text-[var(--color-stone)]'
                              )}
                              onClick={() => {
                                setThinkingOverride(option.value)
                                setAdvancedThinkingDropdownOpen(false)
                              }}
                            >
                              {option.label}
                              {option.description && (
                                <span className="ml-2 text-[var(--color-stone)]/60">
                                  {option.description}
                                </span>
                              )}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Effort Override */}
                  <div>
                    <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/60 mb-1.5">
                      Effort Level
                    </label>
                    <div className="relative">
                      <button
                        type="button"
                        className="w-full flex items-center justify-between px-3 py-2 text-body text-left bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm hover:border-[rgba(163,163,163,0.3)] transition-colors"
                        onClick={() => setAdvancedEffortDropdownOpen(!advancedEffortDropdownOpen)}
                      >
                        <span
                          className={
                            effortOverride
                              ? 'text-[var(--color-paper)]'
                              : 'text-[var(--color-stone)]/60'
                          }
                        >
                          {selectedAdvancedEffortOption?.label || 'Use profile default'}
                          {selectedAdvancedEffortOption?.description && (
                            <span className="ml-2 text-[var(--color-stone)]/60">
                              {selectedAdvancedEffortOption.description}
                            </span>
                          )}
                        </span>
                        <ChevronDown
                          className={cn(
                            'w-3 h-3 text-[var(--color-stone)]/60 transition-transform',
                            advancedEffortDropdownOpen && 'rotate-180'
                          )}
                        />
                      </button>

                      {advancedEffortDropdownOpen && (
                        <div className="absolute top-full left-0 right-0 mt-1 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-xl z-50">
                          {EFFORT_OPTIONS.map((option) => (
                            <button
                              key={option.value}
                              type="button"
                              className={cn(
                                'w-full px-3 py-2 text-left text-body hover:bg-[rgba(163,163,163,0.1)] transition-colors',
                                effortOverride === option.value
                                  ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                                  : 'text-[var(--color-stone)]'
                              )}
                              onClick={() => {
                                setEffortOverride(option.value)
                                setAdvancedEffortDropdownOpen(false)
                              }}
                            >
                              {option.label}
                              {option.description && (
                                <span className="ml-2 text-[var(--color-stone)]/60">
                                  {option.description}
                                </span>
                              )}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Max Budget Override */}
                  <div>
                    <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/60 mb-1.5">
                      Max Budget (USD)
                    </label>
                    <div className="flex items-center gap-1">
                      <span className="text-body text-[var(--color-stone)]/60">$</span>
                      <input
                        type="text"
                        inputMode="decimal"
                        placeholder="Use profile default"
                        value={maxBudgetOverride}
                        onChange={(e) => {
                          const value = e.target.value
                          if (value === '' || /^\d*\.?\d*$/.test(value)) {
                            setMaxBudgetOverride(value)
                          }
                        }}
                        className="w-full px-2 py-1.5 text-body text-[var(--color-paper)] bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm focus:outline-none focus:border-[rgba(163,163,163,0.3)] placeholder:text-[var(--color-stone)]/40"
                      />
                    </div>
                  </div>

                  {/* Model Transition (only shown for Planning profile) */}
                  {profile === 'planning' && (
                    <div>
                      <label className="block text-caption uppercase tracking-widest text-[var(--color-stone)]/60 mb-1.5">
                        Model Transition
                      </label>
                      <div className="relative">
                        <button
                          type="button"
                          className="w-full flex items-center justify-between px-3 py-2 text-body text-left bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm hover:border-[rgba(163,163,163,0.3)] transition-colors"
                          onClick={() =>
                            setModelTransitionDropdownOpen(!modelTransitionDropdownOpen)
                          }
                        >
                          <span
                            className={
                              modelTransition
                                ? 'text-[var(--color-paper)]'
                                : 'text-[var(--color-stone)]/60'
                            }
                          >
                            {selectedModelTransitionOption?.label || 'None (single model)'}
                            {selectedModelTransitionOption?.description && modelTransition && (
                              <span className="ml-2 text-[var(--color-stone)]/60">
                                {selectedModelTransitionOption.description}
                              </span>
                            )}
                          </span>
                          <ChevronDown
                            className={cn(
                              'w-3 h-3 text-[var(--color-stone)]/60 transition-transform',
                              modelTransitionDropdownOpen && 'rotate-180'
                            )}
                          />
                        </button>

                        {modelTransitionDropdownOpen && (
                          <div className="absolute top-full left-0 right-0 mt-1 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-xl z-50">
                            {MODEL_TRANSITION_OPTIONS.map((option) => (
                              <button
                                key={option.value}
                                type="button"
                                className={cn(
                                  'w-full px-3 py-2 text-left text-body hover:bg-[rgba(163,163,163,0.1)] transition-colors',
                                  modelTransition === option.value
                                    ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                                    : 'text-[var(--color-stone)]'
                                )}
                                onClick={() => {
                                  setModelTransition(option.value)
                                  setModelTransitionDropdownOpen(false)
                                }}
                              >
                                {option.label}
                                {option.description && (
                                  <span className="ml-2 text-[var(--color-stone)]/60">
                                    {option.description}
                                  </span>
                                )}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Error */}
            {error && <p className="text-body text-[var(--color-vermillion)]">{error}</p>}

            {/* Actions */}
            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                type="button"
                className="px-4 py-2 text-caption uppercase tracking-widest text-[var(--color-stone)] hover:text-[var(--color-paper)] transition-colors"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!selectedProject || !prompt.trim() || submitting}
                className={cn(
                  'flex items-center gap-2 px-4 py-2 text-caption uppercase tracking-widest rounded-sm transition-colors',
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
        )}
      </DialogContent>
    </Dialog>
  )
}
