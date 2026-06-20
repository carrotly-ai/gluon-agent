import {
  Archive,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  Copy,
  Download,
  ExternalLink,
  FileCode,
  GitBranch,
  GitCommit,
  GitMerge,
  GitPullRequest,
  Image as ImageIcon,
  Minimize2,
  Minus,
  Pencil,
  Play,
  Plus,
  RotateCw,
  Sparkles,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { ImageLightbox } from '@/components/ImageLightbox'
import { useRunActions } from '@/hooks/useRunActions'
import { useWebSocket } from '@/hooks/useWebSocket'
import { parseMessages } from '@/lib/agentMessage'
import {
  cancelRun,
  deleteQueuedMessage,
  editQueuedMessage,
  fetchCommands,
  fetchCommitDetail,
  fetchFileDiff,
  fetchLogs,
  fetchProjectFiles,
  fetchRun,
  fetchRunAttachments,
  fetchRunCommits,
  fetchRunFiles,
  fetchSessionHistory,
  getImageFileUrl,
  queueFollowup,
  resumeRun,
  uploadAndAttachImage,
} from '@/lib/api'
import { formatDuration, formatTokens } from '@/lib/format'
import { formatDateWithContext, formatRelativeTime } from '@/lib/timestamps'
import type {
  CommitDetail,
  FileDiff,
  ImageAttachment,
  ProjectFile,
  Run,
  RunCommitsResponse,
  RunDetail,
  RunFilesResponse,
  RunStatus,
  SlashCommand,
  WSMessage,
} from '@/lib/types'
import { formatFileSize } from '@/lib/types'
import { cn } from '@/lib/utils'
import { CommandAutocomplete } from './CommandAutocomplete'
import { FileAutocomplete } from './FileAutocomplete'
import { StreamingLogViewer } from './StreamingLogViewer'

type TabType = 'output' | 'errors' | 'messages' | 'history' | 'commits' | 'files' | 'attachments'

// Pending image for resume feature
interface ResumePendingImage {
  file: File
  preview: string
}

interface RunDetailPageProps {
  onRunUpdated?: (run: Run) => void
}

export function RunDetailPage({ onRunUpdated }: RunDetailPageProps) {
  const { runId, tab } = useParams<{ runId: string; tab?: string }>()
  const navigate = useNavigate()
  const [run, setRun] = useState<Run | null>(null)
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [logs, setLogs] = useState<{ stdout: string; stderr: string; messages: string }>({
    stdout: '',
    stderr: '',
    messages: '',
  })
  const [activeTab, setActiveTab] = useState<TabType>((tab as TabType) || 'messages')
  const [loading, setLoading] = useState(true)
  const [commitsData, setCommitsData] = useState<RunCommitsResponse | null>(null)
  const [filesData, setFilesData] = useState<RunFilesResponse | null>(null)
  const [loadingCommits, setLoadingCommits] = useState(false)
  const [loadingFiles, setLoadingFiles] = useState(false)
  const [expandedCommit, setExpandedCommit] = useState<string | null>(null)
  const [commitDetails, setCommitDetails] = useState<Record<string, CommitDetail>>({})
  const [loadingCommitDetail, setLoadingCommitDetail] = useState<string | null>(null)
  const [expandedFile, setExpandedFile] = useState<string | null>(null)
  const [fileDiffs, setFileDiffs] = useState<Record<string, FileDiff>>({})
  const [loadingFileDiff, setLoadingFileDiff] = useState<string | null>(null)
  const [attachments, setAttachments] = useState<ImageAttachment[]>([])
  const [loadingAttachments, setLoadingAttachments] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [logsCopied, setLogsCopied] = useState(false)
  const [resumePrompt, setResumePrompt] = useState('')
  const [resuming, setResuming] = useState(false)
  const [queuing, setQueuing] = useState(false)
  const [resumeError, setResumeError] = useState<string | null>(null)
  const [sessionHistory, setSessionHistory] = useState<Run[]>([])
  const [expandedHistoryRun, setExpandedHistoryRun] = useState<string | null>(null)
  const [historyLogs, setHistoryLogs] = useState<
    Record<string, { stdout: string; stderr: string }>
  >({})
  const [creatingPr, setCreatingPr] = useState(false)
  const [prError, setPrError] = useState<string | null>(null)
  const [merging, setMerging] = useState(false)
  const [mergeError, setMergeError] = useState<string | null>(null)
  const [resumePendingImages, setResumePendingImages] = useState<ResumePendingImage[]>([])
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null)
  const [editingMessageText, setEditingMessageText] = useState('')

  // Autocomplete state for follow-up textarea
  const [commands, setCommands] = useState<SlashCommand[]>([])
  const [projectFiles, setProjectFiles] = useState<ProjectFile[]>([])
  const [showCommandAutocomplete, setShowCommandAutocomplete] = useState(false)
  const [showFileAutocomplete, setShowFileAutocomplete] = useState(false)
  const [commandFilter, setCommandFilter] = useState('')
  const [fileFilter, setFileFilter] = useState('')
  const [filesLoading, setFilesLoading] = useState(false)
  const [filesTruncated, setFilesTruncated] = useState(false)

  const messagesContainerRef = useRef<HTMLDivElement>(null)
  const outputContainerRef = useRef<HTMLPreElement>(null)
  const prevMessagesRef = useRef<string>('')
  const prevOutputRef = useRef<string>('')
  const resumeTextareaRef = useRef<HTMLTextAreaElement>(null)

  // Track previous status to detect completion
  const prevStatusRef = useRef<RunStatus | null>(null)

  // Helper to refetch commits and files data
  const refetchCommitsAndFiles = useCallback(async () => {
    if (!runId) return
    try {
      const [newCommitsData, newFilesData] = await Promise.all([
        fetchRunCommits(runId).catch(() => null),
        fetchRunFiles(runId).catch(() => null),
      ])
      if (newCommitsData) {
        setCommitsData(newCommitsData)
        setCommitDetails({})
        setExpandedCommit(null)
      }
      if (newFilesData) {
        setFilesData(newFilesData)
        setFileDiffs({})
        setExpandedFile(null)
      }
    } catch (err) {
      console.error('Failed to refetch commits/files:', err)
    }
  }, [runId])

  // WebSocket handler for run_updated broadcasts
  const handleWebSocketMessage = useCallback(
    (message: WSMessage) => {
      if (message.type === 'run_updated') {
        const updatedRun = (message as { type: 'run_updated'; run: Run }).run
        // Only process updates for this run
        if (updatedRun.id === runId) {
          const wasActive =
            prevStatusRef.current === 'running' || prevStatusRef.current === 'pending'
          const isNowComplete = updatedRun.status === 'completed' || updatedRun.status === 'failed'

          // Update local state with the new run data
          setRun(updatedRun)
          onRunUpdated?.(updatedRun)

          // If run just completed, refetch commits and files to get final state
          if (wasActive && isNowComplete) {
            refetchCommitsAndFiles()
          }

          prevStatusRef.current = updatedRun.status
        }
      }
    },
    [runId, onRunUpdated, refetchCommitsAndFiles]
  )

  // Connect to WebSocket for real-time run updates
  useWebSocket(handleWebSocketMessage)

  // Update URL when tab changes
  const handleTabChange = useCallback(
    (newTab: TabType) => {
      setActiveTab(newTab)
      navigate(`/runs/${runId}/${newTab}`, { replace: true })
    },
    [navigate, runId]
  )

  // Load run data
  useEffect(() => {
    if (!runId) return

    async function load() {
      setLoading(true)
      try {
        const [runDetail, stdoutLogs, stderrLogs, messagesLogs] = await Promise.all([
          fetchRun(runId!),
          fetchLogs(runId!, 'stdout').catch(() => ({ content: '' })),
          fetchLogs(runId!, 'stderr').catch(() => ({ content: '' })),
          fetchLogs(runId!, 'messages').catch(() => ({ content: '' })),
        ])
        setRun(runDetail)
        setDetail(runDetail)
        // Initialize status ref for WebSocket completion detection
        prevStatusRef.current = runDetail.status
        setLogs({
          stdout: stdoutLogs.content || '',
          stderr: stderrLogs.content || '',
          messages: messagesLogs.content || '',
        })

        if (runDetail.session_id) {
          try {
            const history = await fetchSessionHistory(runId!)
            const previousRuns = history.runs.filter((r) => r.id !== runId)
            setSessionHistory(previousRuns)
          } catch {
            setSessionHistory([])
          }
        }
      } catch (err) {
        console.error('Failed to load run details:', err)
      } finally {
        setLoading(false)
      }
    }

    load()
  }, [runId])

  // Auto-refresh for active runs
  useEffect(() => {
    if (!runId || !run) return
    const isRunActive = run.status === 'running' || run.status === 'pending'
    if (!isRunActive) return

    const intervalId = setInterval(async () => {
      try {
        const [runDetail, stdoutLogs, stderrLogs, messagesLogs, newCommitsData, newFilesData] =
          await Promise.all([
            fetchRun(runId),
            fetchLogs(runId, 'stdout').catch(() => ({ content: '' })),
            fetchLogs(runId, 'stderr').catch(() => ({ content: '' })),
            fetchLogs(runId, 'messages').catch(() => ({ content: '' })),
            fetchRunCommits(runId).catch(() => null),
            fetchRunFiles(runId).catch(() => null),
          ])
        setRun(runDetail)
        setDetail(runDetail)
        setLogs({
          stdout: stdoutLogs.content || '',
          stderr: stderrLogs.content || '',
          messages: messagesLogs.content || '',
        })
        if (newCommitsData) setCommitsData(newCommitsData)
        if (newFilesData) setFilesData(newFilesData)
        onRunUpdated?.(runDetail)
      } catch (err) {
        console.error('Auto-refresh failed:', err)
      }
    }, 3000)

    return () => clearInterval(intervalId)
  }, [runId, run?.status, onRunUpdated, run])

  // Auto-scroll to bottom when content changes
  useEffect(() => {
    if (activeTab === 'messages' && logs.messages !== prevMessagesRef.current) {
      prevMessagesRef.current = logs.messages
      if (messagesContainerRef.current) {
        messagesContainerRef.current.scrollTop = messagesContainerRef.current.scrollHeight
      }
    }
    if (activeTab === 'output' && logs.stdout !== prevOutputRef.current) {
      prevOutputRef.current = logs.stdout
      if (outputContainerRef.current) {
        outputContainerRef.current.scrollTop = outputContainerRef.current.scrollHeight
      }
    }
  }, [logs.messages, logs.stdout, activeTab])

  // Poll PR status every 30s when run has an open PR
  // This catches when user merges PR on GitHub website
  useEffect(() => {
    if (!runId || !run) return
    // Only poll if run has an open PR
    if (run.pr_status !== 'open' || !run.pr_number) return

    const intervalId = setInterval(async () => {
      try {
        // fetchRun calls backend with refresh_pr=True which checks GitHub
        const runDetail = await fetchRun(runId)
        // Only update if PR status actually changed
        if (runDetail.pr_status !== run.pr_status) {
          setRun(runDetail)
          setDetail(runDetail)
          onRunUpdated?.(runDetail)
        }
      } catch (err) {
        console.error('PR status polling failed:', err)
      }
    }, 30000) // 30 seconds

    return () => clearInterval(intervalId)
  }, [runId, run?.pr_status, run?.pr_number, run, onRunUpdated])

  // Load commands and files for autocomplete when run detail loads
  useEffect(() => {
    if (!detail?.project_id) return

    // Fetch project-specific commands
    fetchCommands(detail.project_id).then(setCommands).catch(console.error)

    // Fetch project files
    setFilesLoading(true)
    fetchProjectFiles(detail.project_id)
      .then(({ files, truncated }) => {
        setProjectFiles(files)
        setFilesTruncated(truncated)
      })
      .catch(console.error)
      .finally(() => setFilesLoading(false))
  }, [detail?.project_id])

  // Shared run actions (#165). Page owns its run state (setRun), has an
  // optional onRunUpdated, shows no cancel toast, and does not scroll on a
  // merge conflict (no onMergeConflict).
  const { handleCancel, handleCreatePr, handleMerge } = useRunActions({
    run,
    onRunUpdated,
    setRun,
    setDetail,
    setCancelling,
    setCreatingPr,
    setPrError,
    setMerging,
    setMergeError,
    setResumePrompt,
    sourceBranch: detail?.source_branch,
    cancelToasts: false,
  })

  const handleRefresh = async () => {
    if (!runId) return
    setLoading(true)
    try {
      const [runDetail, stdoutLogs, stderrLogs, messagesLogs, newCommitsData, newFilesData] =
        await Promise.all([
          fetchRun(runId),
          fetchLogs(runId, 'stdout').catch(() => ({ content: '' })),
          fetchLogs(runId, 'stderr').catch(() => ({ content: '' })),
          fetchLogs(runId, 'messages').catch(() => ({ content: '' })),
          fetchRunCommits(runId).catch(() => null),
          fetchRunFiles(runId).catch(() => null),
        ])
      setRun(runDetail)
      setDetail(runDetail)
      setLogs({
        stdout: stdoutLogs.content || '',
        stderr: stderrLogs.content || '',
        messages: messagesLogs.content || '',
      })
      if (newCommitsData) {
        setCommitsData(newCommitsData)
        setCommitDetails({})
        setExpandedCommit(null)
      }
      if (newFilesData) {
        setFilesData(newFilesData)
        setFileDiffs({})
        setExpandedFile(null)
      }
      onRunUpdated?.(runDetail)
    } catch (err) {
      console.error('Failed to refresh:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleCopyLogs = async () => {
    const content = activeTab === 'output' ? logs.stdout : logs.stderr
    if (!content) return
    await navigator.clipboard.writeText(content)
    setLogsCopied(true)
    setTimeout(() => setLogsCopied(false), 2000)
  }

  const handleResumePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items
    if (!items) return

    const imageFiles: File[] = []
    for (const item of Array.from(items)) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) {
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
      const validTypes = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
      const maxSize = 50 * 1024 * 1024

      const newImages: ResumePendingImage[] = []
      for (const file of imageFiles) {
        if (!validTypes.includes(file.type)) continue
        if (file.size > maxSize) continue
        newImages.push({
          file,
          preview: URL.createObjectURL(file),
        })
      }
      setResumePendingImages((prev) => [...prev, ...newImages])
    }
  }, [])

  const removeResumeImage = useCallback((index: number) => {
    setResumePendingImages((prev) => {
      const updated = [...prev]
      URL.revokeObjectURL(updated[index].preview)
      updated.splice(index, 1)
      return updated
    })
  }, [])

  const handleResume = async () => {
    if (!run || !resumePrompt.trim()) return
    setResuming(true)
    setResumeError(null)
    try {
      const result = await resumeRun(run.id, resumePrompt.trim())

      if (resumePendingImages.length > 0 && result.run_id) {
        const uploadPromises = resumePendingImages.map((img) =>
          uploadAndAttachImage(result.run_id, img.file).catch((err) => {
            console.error(`Failed to upload image ${img.file.name}:`, err)
            return null
          })
        )
        await Promise.all(uploadPromises)
      }

      resumePendingImages.forEach((img) => URL.revokeObjectURL(img.preview))
      setResumePendingImages([])
      setResumePrompt('')
      // Reset textarea height
      if (resumeTextareaRef.current) {
        resumeTextareaRef.current.style.height = 'auto'
      }
      handleRefresh()
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : 'Failed to resume run')
    } finally {
      setResuming(false)
    }
  }

  // Queue a follow-up message for a running task (will auto-resume after completion)
  const handleQueueFollowup = async () => {
    if (!run || !resumePrompt.trim()) return
    setQueuing(true)
    setResumeError(null)
    try {
      const result = await queueFollowup(run.id, resumePrompt.trim())

      if (result.action === 'resume_now') {
        // Task is not running - use normal resume instead
        await handleResume()
        return
      }

      // Message queued - clear prompt and refresh to show indicator
      setResumePrompt('')
      // Reset textarea height
      if (resumeTextareaRef.current) {
        resumeTextareaRef.current.style.height = 'auto'
      }
      toast.success('Message queued - will continue after current task completes')
      handleRefresh()
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : 'Failed to queue follow-up')
    } finally {
      setQueuing(false)
    }
  }

  // Send message immediately by cancelling current task and resuming with new message
  const handleSendNow = async () => {
    if (!run || !resumePrompt.trim()) return
    setResuming(true)
    setResumeError(null)
    try {
      // Cancel current execution
      await cancelRun(run.id)

      // Small delay for cancellation to propagate
      await new Promise((r) => setTimeout(r, 500))

      // Resume with new message
      await resumeRun(run.id, resumePrompt.trim())

      // Upload images if any
      if (resumePendingImages.length > 0) {
        const uploadPromises = resumePendingImages.map((img) =>
          uploadAndAttachImage(run.id, img.file).catch((err) => {
            console.error(`Failed to upload image ${img.file.name}:`, err)
            return null
          })
        )
        await Promise.all(uploadPromises)
      }

      // Cleanup
      resumePendingImages.forEach((img) => URL.revokeObjectURL(img.preview))
      setResumePendingImages([])
      setResumePrompt('')
      // Reset textarea height
      if (resumeTextareaRef.current) {
        resumeTextareaRef.current.style.height = 'auto'
      }

      // Refresh
      handleRefresh()
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : 'Failed to send message')
    } finally {
      setResuming(false)
    }
  }

  // Handle autocomplete trigger detection
  const handleResumePromptChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value
    setResumePrompt(value)

    const cursorPos = e.target.selectionStart
    const textBeforeCursor = value.slice(0, cursorPos)

    // Slash command trigger: "/" at start or after whitespace
    const slashMatch = textBeforeCursor.match(/(?:^|\s)\/(\S*)$/)
    // File mention trigger: "@" at start or after whitespace
    const atMatch = textBeforeCursor.match(/(?:^|\s)@(\S*)$/)

    if (slashMatch) {
      setCommandFilter(slashMatch[1])
      setShowCommandAutocomplete(true)
      setShowFileAutocomplete(false)
    } else if (atMatch) {
      setFileFilter(atMatch[1])
      setShowFileAutocomplete(true)
      setShowCommandAutocomplete(false)
    } else {
      setShowCommandAutocomplete(false)
      setShowFileAutocomplete(false)
    }
  }, [])

  // Handle command selection from autocomplete
  const handleCommandSelect = useCallback(
    (command: SlashCommand) => {
      if (!resumeTextareaRef.current) return

      const cursorPos = resumeTextareaRef.current.selectionStart
      const textBeforeCursor = resumePrompt.slice(0, cursorPos)
      const textAfterCursor = resumePrompt.slice(cursorPos)

      const slashMatch = textBeforeCursor.match(/(?:^|\s)(\/\S*)$/)
      if (slashMatch) {
        const matchStart = textBeforeCursor.length - slashMatch[1].length
        const newText = `${resumePrompt.slice(0, matchStart)}/${command.name} ${textAfterCursor}`
        setResumePrompt(newText)

        const newCursorPos = matchStart + command.name.length + 2
        setTimeout(() => {
          resumeTextareaRef.current?.focus()
          resumeTextareaRef.current?.setSelectionRange(newCursorPos, newCursorPos)
        }, 0)
      }

      setShowCommandAutocomplete(false)
      setCommandFilter('')
    },
    [resumePrompt]
  )

  // Handle file selection from autocomplete
  const handleFileSelect = useCallback(
    (file: ProjectFile) => {
      if (!resumeTextareaRef.current) return

      const cursorPos = resumeTextareaRef.current.selectionStart
      const textBeforeCursor = resumePrompt.slice(0, cursorPos)
      const textAfterCursor = resumePrompt.slice(cursorPos)

      const atMatch = textBeforeCursor.match(/(?:^|\s)(@\S*)$/)
      if (atMatch) {
        const matchStart = textBeforeCursor.length - atMatch[1].length
        const newText = `${resumePrompt.slice(0, matchStart)}@${file.path} ${textAfterCursor}`
        setResumePrompt(newText)

        const newCursorPos = matchStart + file.path.length + 2
        setTimeout(() => {
          resumeTextareaRef.current?.focus()
          resumeTextareaRef.current?.setSelectionRange(newCursorPos, newCursorPos)
        }, 0)
      }

      setShowFileAutocomplete(false)
      setFileFilter('')
    },
    [resumePrompt]
  )

  // Handle autocomplete close
  const handleAutocompleteClose = useCallback(() => {
    setShowCommandAutocomplete(false)
    setShowFileAutocomplete(false)
    resumeTextareaRef.current?.focus()
  }, [])

  // Edit a queued message
  const handleEditQueuedMessage = async (messageId: string, newText: string) => {
    if (!run || !newText.trim()) return
    try {
      await editQueuedMessage(run.id, messageId, newText.trim())
      setEditingMessageId(null)
      setEditingMessageText('')
      handleRefresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to edit message')
    }
  }

  // Delete a queued message
  const handleDeleteQueuedMessage = async (messageId: string) => {
    if (!run) return
    try {
      await deleteQueuedMessage(run.id, messageId)
      handleRefresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete message')
    }
  }

  const handleExpandHistoryRun = async (historyRunId: string) => {
    if (expandedHistoryRun === historyRunId) {
      setExpandedHistoryRun(null)
      return
    }
    setExpandedHistoryRun(historyRunId)
    if (!historyLogs[historyRunId]) {
      try {
        const [stdout, stderr] = await Promise.all([
          fetchLogs(historyRunId, 'stdout').catch(() => ({ content: '' })),
          fetchLogs(historyRunId, 'stderr').catch(() => ({ content: '' })),
        ])
        setHistoryLogs((prev) => ({
          ...prev,
          [historyRunId]: { stdout: stdout.content || '', stderr: stderr.content || '' },
        }))
      } catch {
        setHistoryLogs((prev) => ({
          ...prev,
          [historyRunId]: { stdout: '', stderr: '' },
        }))
      }
    }
  }

  const handleExpandCommit = async (sha: string) => {
    if (expandedCommit === sha) {
      setExpandedCommit(null)
      return
    }
    setExpandedCommit(sha)
    if (!commitDetails[sha] && runId) {
      setLoadingCommitDetail(sha)
      try {
        const detail = await fetchCommitDetail(runId, sha)
        setCommitDetails((prev) => ({ ...prev, [sha]: detail }))
      } catch (err) {
        console.error('Failed to load commit details:', err)
      } finally {
        setLoadingCommitDetail(null)
      }
    }
  }

  const handleExpandFile = async (filePath: string) => {
    if (expandedFile === filePath) {
      setExpandedFile(null)
      return
    }
    setExpandedFile(filePath)
    if (!fileDiffs[filePath] && runId) {
      setLoadingFileDiff(filePath)
      try {
        const diff = await fetchFileDiff(runId, filePath)
        setFileDiffs((prev) => ({ ...prev, [filePath]: diff }))
      } catch (err) {
        console.error('Failed to load file diff:', err)
      } finally {
        setLoadingFileDiff(null)
      }
    }
  }

  // Lazy load commits
  const loadCommits = useCallback(async () => {
    if (!runId || commitsData || loadingCommits) return
    setLoadingCommits(true)
    try {
      const data = await fetchRunCommits(runId)
      setCommitsData(data)
    } catch (err) {
      console.error('Failed to load commits:', err)
    } finally {
      setLoadingCommits(false)
    }
  }, [runId, commitsData, loadingCommits])

  // Lazy load files
  const loadFiles = useCallback(async () => {
    if (!runId || filesData || loadingFiles) return
    setLoadingFiles(true)
    try {
      const data = await fetchRunFiles(runId)
      setFilesData(data)
    } catch (err) {
      console.error('Failed to load files:', err)
    } finally {
      setLoadingFiles(false)
    }
  }, [runId, filesData, loadingFiles])

  // Lazy load attachments
  const loadAttachments = useCallback(async () => {
    if (!runId || attachments.length > 0 || loadingAttachments) return
    setLoadingAttachments(true)
    try {
      const data = await fetchRunAttachments(runId)
      setAttachments(data.images)
    } catch (err) {
      console.error('Failed to load attachments:', err)
    } finally {
      setLoadingAttachments(false)
    }
  }, [runId, attachments.length, loadingAttachments])

  // Load data when tab changes
  useEffect(() => {
    if (activeTab === 'commits' && !commitsData && !loadingCommits) {
      loadCommits()
    } else if (activeTab === 'files' && !filesData && !loadingFiles) {
      loadFiles()
    } else if (activeTab === 'attachments' && attachments.length === 0 && !loadingAttachments) {
      loadAttachments()
    }
  }, [
    activeTab,
    attachments.length,
    commitsData,
    filesData,
    loadAttachments,
    loadCommits,
    loadFiles,
    loadingAttachments,
    loadingCommits,
    loadingFiles,
  ])

  const isActive = run?.status === 'running' || run?.status === 'pending'
  const hasErrors = !!logs.stderr
  // Allow resuming any non-active run (completed, failed, review)
  // Session ID is optional - backend will start fresh if no prior session
  const isResumable =
    run?.status === 'completed' || run?.status === 'failed' || run?.status === 'review'
  const hasHistory = sessionHistory.length > 0

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--color-void)]">
        <div className="mark mark-running w-3 h-3" />
      </div>
    )
  }

  if (!run) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-[var(--color-void)]">
        <p className="text-[var(--color-stone)]/60 mb-4">Run not found</p>
        <Link to="/board" className="text-[var(--color-sky)] hover:underline text-sm">
          ← Back to board
        </Link>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex flex-col bg-[var(--color-void)]">
      {/* Header */}
      <header className="border-b border-[rgba(163,163,163,0.1)] shrink-0">
        <div className="flex items-center justify-between px-4 sm:px-6 h-12 sm:h-14">
          {/* Left - Back + Run Identity */}
          <div className="flex items-center gap-3 min-w-0">
            <Link
              to="/board"
              className="flex items-center gap-1.5 text-[var(--color-stone)]/60 hover:text-[var(--color-paper)] transition-colors"
            >
              <ChevronLeft className="w-4 h-4" />
              <span className="text-caption uppercase tracking-widest hidden sm:inline">Board</span>
            </Link>
            <div className="w-px h-4 bg-[var(--color-stone)]/20" />
            <div className="flex items-center gap-2 shrink-0">
              <div className={cn('mark', `mark-${run?.status}`)} />
              <span className="text-mono text-[var(--color-stone)]/60 text-caption">
                {run?.id.slice(0, 8)}
              </span>
              <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]/55">
                {run?.status}
              </span>
            </div>
            {detail?.branch_name && (
              <div className="hidden sm:flex items-center gap-1.5 ml-1 text-caption text-[var(--color-stone)]/50">
                <span className="text-[var(--color-stone)]/30">on</span>
                <GitBranch className="w-2.5 h-2.5 text-[var(--color-orchid)]/70" />
                <span className="text-[var(--color-orchid)]/80 truncate max-w-[100px]">
                  {detail.branch_name}
                </span>
                {detail.git_commit_sha && (
                  <>
                    <span className="text-[var(--color-stone)]/30">@</span>
                    <span className="text-mono text-[var(--color-stone)]/50">
                      {detail.git_commit_sha.slice(0, 7)}
                    </span>
                  </>
                )}
              </div>
            )}
          </div>

          {/* Right - Actions */}
          <div className="flex items-center gap-2 shrink-0">
            {/* PR badge */}
            {detail?.pr_number && detail?.pr_url && (
              <a
                href={detail.pr_url}
                target="_blank"
                rel="noopener noreferrer"
                className={cn(
                  'hidden sm:flex items-center gap-1.5 px-2 py-1 rounded-sm text-caption transition-colors',
                  detail.pr_mergeable === 'CONFLICTING' &&
                    'bg-[rgba(239,68,68,0.15)] border border-[rgba(239,68,68,0.3)] text-[var(--color-vermillion)] hover:bg-[rgba(239,68,68,0.2)]',
                  detail.pr_mergeable !== 'CONFLICTING' &&
                    detail.pr_status === 'open' &&
                    'bg-[rgba(34,197,94,0.1)] border border-[rgba(34,197,94,0.2)] text-[var(--color-jade)] hover:bg-[rgba(34,197,94,0.15)]',
                  detail.pr_status === 'merged' &&
                    'bg-[rgba(168,85,247,0.1)] border border-[rgba(168,85,247,0.2)] text-[var(--color-orchid)]',
                  detail.pr_status === 'closed' &&
                    'bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.2)] text-[var(--color-vermillion)]',
                  detail.pr_status === 'draft' &&
                    'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]'
                )}
                title={
                  detail.pr_mergeable === 'CONFLICTING'
                    ? 'PR has merge conflicts'
                    : `View PR #${detail.pr_number} on GitHub`
                }
              >
                <GitPullRequest className="w-3 h-3" />
                <span>#{detail.pr_number}</span>
                {detail.pr_mergeable === 'CONFLICTING' ? (
                  <span className="uppercase font-medium">Conflicts</span>
                ) : (
                  <span className="uppercase">{detail.pr_status}</span>
                )}
                <ExternalLink className="w-2.5 h-2.5 opacity-60" />
              </a>
            )}

            {(detail?.pr_number || detail?.branch_name) && (
              <div className="hidden sm:block w-px h-4 bg-[var(--color-stone)]/20" />
            )}

            {/* Action buttons */}
            <div className="flex items-center gap-1">
              {/* Merge */}
              {detail?.pr_status === 'open' &&
                detail?.pr_mergeable !== 'CONFLICTING' &&
                detail?.branch_name &&
                !isActive && (
                  <button
                    onClick={handleMerge}
                    disabled={merging}
                    className={cn(
                      'flex items-center gap-1.5 px-2.5 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors',
                      merging
                        ? 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                        : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-[var(--color-jade)] hover:bg-[rgba(34,197,94,0.25)]'
                    )}
                  >
                    <GitMerge className="w-3 h-3" />
                    <span>{merging ? 'Merging...' : 'Merge'}</span>
                  </button>
                )}
              {/* Resolve Conflicts */}
              {detail?.pr_mergeable === 'CONFLICTING' && isResumable && !isActive && (
                <button
                  onClick={() => {
                    const conflictPrompt = `The PR for this branch has merge conflicts. Please resolve them:

1. Rebase this branch onto ${detail?.source_branch || 'main'}
2. For each conflict, understand the intent of both changes and merge them intelligently
3. After resolving all conflicts, force-push the rebased branch
4. The PR should become mergeable after this

Focus on preserving the functionality from both sides where possible.`
                    setResumePrompt(conflictPrompt)
                  }}
                  className="flex items-center gap-1.5 px-2.5 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors bg-[rgba(168,85,247,0.15)] border border-[rgba(168,85,247,0.3)] text-[var(--color-orchid)] hover:bg-[rgba(168,85,247,0.25)]"
                >
                  <Sparkles className="w-3 h-3" />
                  <span>Resolve</span>
                </button>
              )}
              {/* Create PR */}
              {detail?.use_worktree &&
                detail?.branch_name &&
                detail?.has_remote &&
                !detail?.pr_url &&
                !isActive && (
                  <button
                    onClick={handleCreatePr}
                    disabled={creatingPr}
                    className={cn(
                      'flex items-center gap-1.5 px-2.5 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors',
                      creatingPr
                        ? 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                        : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-[var(--color-jade)] hover:bg-[rgba(34,197,94,0.25)]'
                    )}
                  >
                    <GitPullRequest className="w-3 h-3" />
                    <span>{creatingPr ? 'Creating...' : 'Create PR'}</span>
                  </button>
                )}
              {/* Merge (local) */}
              {detail?.use_worktree &&
                detail?.branch_name &&
                !detail?.has_remote &&
                detail?.pr_status !== 'merged' &&
                !isActive && (
                  <button
                    onClick={handleMerge}
                    disabled={merging}
                    className={cn(
                      'flex items-center gap-1.5 px-2.5 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors',
                      merging
                        ? 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                        : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-[var(--color-jade)] hover:bg-[rgba(34,197,94,0.25)]'
                    )}
                  >
                    <GitMerge className="w-3 h-3" />
                    <span>{merging ? 'Merging...' : 'Merge'}</span>
                  </button>
                )}
              {/* Cancel */}
              {isActive && (
                <button
                  className="flex items-center gap-1.5 px-2.5 py-1 text-caption uppercase tracking-widest text-[var(--color-vermillion)] hover:text-[var(--color-vermillion)] border border-[var(--color-vermillion)]/30 hover:border-[var(--color-vermillion)]/50 hover:bg-[rgb(var(--color-vermillion-rgb)/0.1)] rounded-sm transition-colors"
                  onClick={handleCancel}
                  disabled={cancelling}
                >
                  {cancelling ? 'Cancelling...' : 'Cancel'}
                </button>
              )}
              {/* Compact view toggle */}
              <Link
                to={`/board/${runId}/${activeTab}`}
                className="p-1.5 text-[var(--color-stone)]/50 hover:text-[var(--color-paper)] transition-colors rounded-sm hover:bg-[var(--color-paper)]/5"
                title="View in modal"
              >
                <Minimize2 className="w-3.5 h-3.5" />
              </Link>
              {/* Refresh */}
              <button
                className="p-1.5 text-[var(--color-stone)]/50 hover:text-[var(--color-paper)] transition-colors rounded-sm hover:bg-[var(--color-paper)]/5"
                onClick={handleRefresh}
                disabled={loading}
                title="Refresh"
              >
                <RotateCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 overflow-auto">
        <div className="max-w-6xl mx-auto p-4 sm:p-6 lg:p-8">
          {/* Project + Meta Row */}
          <div className="flex items-center gap-4 text-caption text-[var(--color-stone)]/60 mb-4 flex-wrap">
            <span className="text-[var(--color-paper)]/80">{run?.project_name}</span>
            <span className="hidden sm:inline">
              {formatDateWithContext(run?.created_at ?? null)}
            </span>
            {run?.duration_seconds !== null && (
              <span className="text-mono">{formatDuration(run?.duration_seconds ?? null)}</span>
            )}
            {detail?.exit_code !== null && detail?.exit_code !== undefined && (
              <span className="text-mono">exit {detail?.exit_code}</span>
            )}
            {detail?.stop_reason && (
              <span className="text-mono text-[var(--color-stone)]/50">{detail.stop_reason}</span>
            )}
            {detail?.cost_usd != null && detail.cost_usd > 0 && (
              <span className="text-mono text-[var(--color-harvest)]">
                ${detail.cost_usd.toFixed(4)}
              </span>
            )}
            {(() => {
              const toolCount = parseMessages(logs.messages).filter(
                (m) => m.type === 'tool_use'
              ).length
              return toolCount > 0 ? (
                <span className="text-mono text-[var(--color-sky)]">{toolCount} tools</span>
              ) : null
            })()}
          </div>

          {/* PR/Merge errors */}
          {prError && (
            <div className="mb-4 p-2 bg-[rgb(var(--color-vermillion-rgb)/0.08)] border border-[rgb(var(--color-vermillion-rgb)/0.2)] rounded-sm">
              <p className="text-caption text-[var(--color-vermillion)]">{prError}</p>
            </div>
          )}
          {mergeError && (
            <div className="mb-4 p-2 bg-[rgb(var(--color-vermillion-rgb)/0.08)] border border-[rgb(var(--color-vermillion-rgb)/0.2)] rounded-sm">
              <p className="text-caption text-[var(--color-vermillion)]">{mergeError}</p>
            </div>
          )}

          {/* Prompt */}
          <div className="mb-6">
            <p className="text-title text-[var(--color-paper)] leading-relaxed font-light">
              {run?.prompt}
            </p>
          </div>

          {/* Error Message */}
          {run?.error_message && (
            <div className="mb-6 p-3 bg-[rgb(var(--color-vermillion-rgb)/0.08)] border border-[rgb(var(--color-vermillion-rgb)/0.2)] rounded-sm">
              <p className="text-caption uppercase tracking-widest text-[var(--color-vermillion)]/70 mb-1.5">
                Error
              </p>
              <pre className="text-body text-[var(--color-vermillion)] whitespace-pre-wrap break-words font-mono">
                {run.error_message}
              </pre>
            </div>
          )}

          {/* Tab Bar */}
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-1">
              <button
                className={cn(
                  'px-3 py-1.5 text-caption uppercase tracking-widest transition-colors rounded-sm',
                  activeTab === 'messages'
                    ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                    : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                )}
                onClick={() => handleTabChange('messages')}
              >
                Messages
              </button>
              <button
                className={cn(
                  'px-3 py-1.5 text-caption uppercase tracking-widest transition-colors rounded-sm',
                  activeTab === 'output'
                    ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                    : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                )}
                onClick={() => handleTabChange('output')}
              >
                Output
              </button>
              <button
                className={cn(
                  'px-3 py-1.5 text-caption uppercase tracking-widest transition-colors rounded-sm flex items-center gap-1.5',
                  activeTab === 'errors'
                    ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                    : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                )}
                onClick={() => handleTabChange('errors')}
              >
                Errors
                {hasErrors && (
                  <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-vermillion)]" />
                )}
              </button>
              {hasHistory && (
                <button
                  className={cn(
                    'px-3 py-1.5 text-caption uppercase tracking-widest transition-colors rounded-sm flex items-center gap-1.5',
                    activeTab === 'history'
                      ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                      : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                  )}
                  onClick={() => handleTabChange('history')}
                >
                  <Clock className="w-3 h-3" />
                  History
                  <span className="text-micro text-[var(--color-stone)]/50">
                    ({sessionHistory.length})
                  </span>
                </button>
              )}
              {detail?.branch_name && (
                <>
                  <button
                    className={cn(
                      'px-3 py-1.5 text-caption uppercase tracking-widest transition-colors rounded-sm flex items-center gap-1.5',
                      activeTab === 'commits'
                        ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                        : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                    )}
                    onClick={() => handleTabChange('commits')}
                  >
                    <GitCommit className="w-3 h-3" />
                    Commits
                    {(() => {
                      const count = commitsData?.commit_count ?? detail?.commit_count
                      return count && count > 0 ? (
                        <span className="text-micro text-[var(--color-stone)]/50">({count})</span>
                      ) : null
                    })()}
                  </button>
                  <button
                    className={cn(
                      'px-3 py-1.5 text-caption uppercase tracking-widest transition-colors rounded-sm flex items-center gap-1.5',
                      activeTab === 'files'
                        ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                        : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                    )}
                    onClick={() => handleTabChange('files')}
                  >
                    <FileCode className="w-3 h-3" />
                    Files
                    {(() => {
                      const count = filesData?.file_count ?? detail?.file_count
                      return count && count > 0 ? (
                        <span className="text-micro text-[var(--color-stone)]/50">({count})</span>
                      ) : null
                    })()}
                  </button>
                </>
              )}
              <button
                className={cn(
                  'px-3 py-1.5 text-caption uppercase tracking-widest transition-colors rounded-sm flex items-center gap-1.5',
                  activeTab === 'attachments'
                    ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                    : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                )}
                onClick={() => handleTabChange('attachments')}
              >
                <ImageIcon className="w-3 h-3" />
                Images
                {attachments.length > 0 && (
                  <span className="text-micro text-[var(--color-stone)]/50">
                    ({attachments.length})
                  </span>
                )}
              </button>
            </div>
            <button
              className={cn(
                'flex items-center gap-1.5 px-2 py-1 text-caption uppercase tracking-widest transition-colors rounded-sm',
                (activeTab === 'output' ? logs.stdout : logs.stderr)
                  ? 'text-[var(--color-stone)]/60 hover:text-[var(--color-paper)]'
                  : 'text-[var(--color-stone)]/40 cursor-not-allowed'
              )}
              onClick={handleCopyLogs}
              disabled={!(activeTab === 'output' ? logs.stdout : logs.stderr)}
              title={`Copy ${activeTab}`}
            >
              {logsCopied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
              <span className="hidden sm:inline">{logsCopied ? 'Copied' : 'Copy'}</span>
            </button>
          </div>

          {/* Log Content */}
          <div className="bg-[var(--color-void)] border border-[rgba(163,163,163,0.08)] rounded-sm min-h-[400px] max-h-[600px] overflow-auto">
            {activeTab === 'output' && (
              <pre
                ref={outputContainerRef}
                className="p-3 text-mono text-[var(--color-paper)]/70 whitespace-pre-wrap break-words text-caption leading-relaxed h-full overflow-auto"
              >
                {logs.stdout || (
                  <span className="text-[var(--color-stone)]/50 italic">No output</span>
                )}
              </pre>
            )}
            {activeTab === 'errors' && (
              <pre
                className={cn(
                  'p-3 text-mono whitespace-pre-wrap break-words text-caption leading-relaxed',
                  logs.stderr
                    ? 'text-[var(--color-vermillion)]/90'
                    : 'text-[var(--color-stone)]/50 italic'
                )}
              >
                {logs.stderr || 'No errors'}
              </pre>
            )}
            {activeTab === 'messages' && (
              <div className="h-[500px] overflow-hidden">
                <StreamingLogViewer
                  runId={detail?.id ?? runId ?? null}
                  runStatus={(detail?.status ?? 'pending') as RunStatus}
                  initialMessages={parseMessages(logs.messages)}
                />
              </div>
            )}
            {activeTab === 'history' && (
              <div className="p-3 overflow-y-auto h-full">
                <p className="text-caption text-[var(--color-stone)]/70 mb-3">
                  Previous runs in this session (oldest first):
                </p>
                <div className="space-y-2">
                  {sessionHistory.map((historyRun) => (
                    <div
                      key={historyRun.id}
                      className="border border-[rgba(163,163,163,0.08)] rounded-sm"
                    >
                      <button
                        className="w-full p-3 flex items-center justify-between hover:bg-[var(--color-paper)]/5 transition-colors"
                        onClick={() => handleExpandHistoryRun(historyRun.id)}
                      >
                        <div className="flex items-center gap-3 text-left">
                          <div className={cn('mark', `mark-${historyRun.status}`)} />
                          <div>
                            <p className="text-body text-[var(--color-paper)]/80 line-clamp-1">
                              {historyRun.prompt}
                            </p>
                            <p className="text-caption text-[var(--color-stone)]/50 mt-0.5">
                              {formatDateWithContext(historyRun.created_at)} ·{' '}
                              {formatDuration(historyRun.duration_seconds)}
                            </p>
                          </div>
                        </div>
                        <ChevronDown
                          className={cn(
                            'w-4 h-4 text-[var(--color-stone)]/50 transition-transform',
                            expandedHistoryRun === historyRun.id && 'rotate-180'
                          )}
                        />
                      </button>
                      {expandedHistoryRun === historyRun.id && (
                        <div className="border-t border-[rgba(163,163,163,0.08)] p-3">
                          {historyLogs[historyRun.id] ? (
                            <pre className="text-mono text-caption text-[var(--color-paper)]/60 whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                              {historyLogs[historyRun.id].stdout || (
                                <span className="text-[var(--color-stone)]/40 italic">
                                  No output
                                </span>
                              )}
                            </pre>
                          ) : (
                            <span className="text-caption text-[var(--color-stone)]/50">
                              Loading...
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {activeTab === 'commits' && (
              <div className="p-3 overflow-y-auto h-full">
                {loadingCommits ? (
                  <div className="flex items-center justify-center h-32">
                    <RotateCw className="w-4 h-4 animate-spin text-[var(--color-stone)]/50" />
                  </div>
                ) : commitsData && commitsData.commits.length > 0 ? (
                  <div className="space-y-0">
                    <div className="flex items-center gap-2 mb-3 pb-2 border-b border-[rgba(163,163,163,0.08)]">
                      <GitBranch className="w-3.5 h-3.5 text-[var(--color-orchid)]" />
                      <span className="text-caption text-[var(--color-orchid)]">
                        {commitsData.branch_name}
                      </span>
                      <span className="text-caption text-[var(--color-stone)]/50">
                        {commitsData.commit_count} commit{commitsData.commit_count !== 1 ? 's' : ''}{' '}
                        ahead of {commitsData.base_branch}
                      </span>
                      {commitsData.from_snapshot && (
                        <span className="flex items-center gap-1 px-1.5 py-0.5 text-micro uppercase tracking-widest bg-[rgba(168,85,247,0.15)] text-[var(--color-orchid)] rounded-sm border border-[rgba(168,85,247,0.2)]">
                          <Archive className="w-2.5 h-2.5" />
                          Snapshot
                        </span>
                      )}
                    </div>
                    {commitsData.commits
                      .slice()
                      .reverse()
                      .map((commit, idx) => {
                        const isExpanded = expandedCommit === commit.sha
                        const commitDetail = commitDetails[commit.sha]
                        const isLoading = loadingCommitDetail === commit.sha

                        return (
                          <div
                            key={commit.sha}
                            className={cn(
                              'border-b border-[rgba(163,163,163,0.05)]',
                              idx === commitsData.commits.length - 1 && 'border-b-0'
                            )}
                          >
                            <button
                              className="w-full flex items-center gap-2 py-1.5 text-left hover:bg-[var(--color-paper)]/5 transition-colors px-1 -mx-1 rounded"
                              onClick={() => handleExpandCommit(commit.sha)}
                            >
                              <ChevronRight
                                className={cn(
                                  'w-3 h-3 text-[var(--color-stone)]/40 transition-transform shrink-0',
                                  isExpanded && 'rotate-90'
                                )}
                              />
                              <span className="text-caption text-[var(--color-paper)]/90 truncate flex-1 min-w-0">
                                {commit.message}
                              </span>
                              <span className="text-caption text-[var(--color-stone)]/50 shrink-0">
                                {formatRelativeTime(commit.date)}
                              </span>
                              <span className="text-mono text-caption text-[var(--color-stone)]/40 shrink-0">
                                {commit.sha.slice(0, 7)}
                              </span>
                            </button>
                            {isExpanded && (
                              <div className="ml-5 pl-3 border-l border-[rgba(163,163,163,0.15)] mb-2">
                                {isLoading ? (
                                  <div className="py-2 flex items-center gap-2">
                                    <RotateCw className="w-3 h-3 animate-spin text-[var(--color-stone)]/50" />
                                    <span className="text-caption text-[var(--color-stone)]/50">
                                      Loading...
                                    </span>
                                  </div>
                                ) : commitDetail ? (
                                  <div className="py-2 space-y-2">
                                    {commitDetail.message &&
                                      commitDetail.message !== commit.message && (
                                        <pre className="text-caption text-[var(--color-paper)]/70 whitespace-pre-wrap font-sans leading-relaxed">
                                          {commitDetail.message}
                                        </pre>
                                      )}
                                    {commitDetail.files && commitDetail.files.length > 0 && (
                                      <div className="space-y-1">
                                        <p className="text-caption text-[var(--color-stone)]/60 font-medium">
                                          {commitDetail.files.length} file
                                          {commitDetail.files.length !== 1 ? 's' : ''} changed
                                        </p>
                                        <div className="space-y-0.5">
                                          {commitDetail.files.map((file) => (
                                            <div
                                              key={file.file_path}
                                              className="flex items-center gap-2 text-caption"
                                            >
                                              <span
                                                className={cn(
                                                  'uppercase px-1 py-0.5 rounded font-medium text-micro',
                                                  file.change_type === 'added' &&
                                                    'bg-[rgba(45,212,191,0.15)] text-[var(--color-jade)]',
                                                  file.change_type === 'modified' &&
                                                    'bg-[rgba(102,178,255,0.15)] text-[var(--color-sky)]',
                                                  file.change_type === 'deleted' &&
                                                    'bg-[rgb(var(--color-vermillion-rgb)/0.15)] text-[var(--color-vermillion)]',
                                                  file.change_type === 'renamed' &&
                                                    'bg-[rgba(168,85,247,0.15)] text-[var(--color-orchid)]'
                                                )}
                                              >
                                                {file.change_type === 'added'
                                                  ? 'A'
                                                  : file.change_type === 'modified'
                                                    ? 'M'
                                                    : file.change_type === 'deleted'
                                                      ? 'D'
                                                      : 'R'}
                                              </span>
                                              <span className="text-[var(--color-paper)]/70 font-mono truncate">
                                                {file.file_path}
                                              </span>
                                              <span className="text-[var(--color-jade)] shrink-0">
                                                +{file.additions}
                                              </span>
                                              <span className="text-[var(--color-vermillion)] shrink-0">
                                                -{file.deletions}
                                              </span>
                                            </div>
                                          ))}
                                        </div>
                                      </div>
                                    )}
                                  </div>
                                ) : null}
                              </div>
                            )}
                          </div>
                        )
                      })}
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-32 text-[var(--color-stone)]/50">
                    <GitCommit className="w-6 h-6 mb-2 opacity-50" />
                    {detail?.pr_status === 'merged' ? (
                      <>
                        <span className="text-caption">
                          Branch merged into {detail?.source_branch || 'main'}
                        </span>
                        <span className="text-caption mt-1 opacity-70">
                          Commit history no longer available
                        </span>
                      </>
                    ) : (
                      <span className="text-caption">No commits on this branch</span>
                    )}
                  </div>
                )}
              </div>
            )}
            {activeTab === 'files' && (
              <div className="p-3 overflow-y-auto h-full">
                {loadingFiles ? (
                  <div className="flex items-center justify-center h-32">
                    <RotateCw className="w-4 h-4 animate-spin text-[var(--color-stone)]/50" />
                  </div>
                ) : filesData && filesData.files.length > 0 ? (
                  <div className="space-y-0">
                    <div className="flex items-center justify-between mb-3 pb-2 border-b border-[rgba(163,163,163,0.08)]">
                      <div className="flex items-center gap-2">
                        <FileCode className="w-3.5 h-3.5 text-[var(--color-sky)]" />
                        <span className="text-caption text-[var(--color-paper)]/80">
                          {filesData.file_count} file{filesData.file_count !== 1 ? 's' : ''} changed
                        </span>
                        {filesData.from_snapshot && (
                          <span className="flex items-center gap-1 px-1.5 py-0.5 text-micro uppercase tracking-widest bg-[rgba(168,85,247,0.15)] text-[var(--color-orchid)] rounded-sm border border-[rgba(168,85,247,0.2)]">
                            <Archive className="w-2.5 h-2.5" />
                            Snapshot
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 text-caption">
                        <span className="flex items-center gap-1 text-[var(--color-jade)]">
                          <Plus className="w-3 h-3" />
                          {filesData.total_additions}
                        </span>
                        <span className="flex items-center gap-1 text-[var(--color-vermillion)]">
                          <Minus className="w-3 h-3" />
                          {filesData.total_deletions}
                        </span>
                      </div>
                    </div>
                    {filesData.files.map((file, idx) => {
                      const totalChanges = file.additions + file.deletions
                      const maxBarWidth = 100
                      const additionWidth =
                        totalChanges > 0
                          ? Math.max(
                              (file.additions / totalChanges) * maxBarWidth,
                              file.additions > 0 ? 4 : 0
                            )
                          : 0
                      const deletionWidth =
                        totalChanges > 0
                          ? Math.max(
                              (file.deletions / totalChanges) * maxBarWidth,
                              file.deletions > 0 ? 4 : 0
                            )
                          : 0
                      const isExpanded = expandedFile === file.file_path
                      const diff = fileDiffs[file.file_path]
                      const isLoading = loadingFileDiff === file.file_path

                      return (
                        <div
                          key={file.file_path}
                          className={cn(
                            'border-b border-[rgba(163,163,163,0.05)]',
                            idx === filesData.files.length - 1 && 'border-b-0'
                          )}
                        >
                          <button
                            className="w-full flex items-center justify-between py-2 gap-3 text-left hover:bg-[var(--color-paper)]/5 transition-colors px-1 -mx-1 rounded"
                            onClick={() => handleExpandFile(file.file_path)}
                          >
                            <div className="flex items-center gap-2 min-w-0 flex-1">
                              <ChevronRight
                                className={cn(
                                  'w-3 h-3 text-[var(--color-stone)]/50 transition-transform shrink-0',
                                  isExpanded && 'rotate-90'
                                )}
                              />
                              <span
                                className={cn(
                                  'text-micro uppercase px-1 py-0.5 rounded font-medium shrink-0',
                                  file.change_type === 'added' &&
                                    'bg-[rgba(45,212,191,0.15)] text-[var(--color-jade)]',
                                  file.change_type === 'modified' &&
                                    'bg-[rgba(102,178,255,0.15)] text-[var(--color-sky)]',
                                  file.change_type === 'deleted' &&
                                    'bg-[rgb(var(--color-vermillion-rgb)/0.15)] text-[var(--color-vermillion)]',
                                  file.change_type === 'renamed' &&
                                    'bg-[rgba(168,85,247,0.15)] text-[var(--color-orchid)]'
                                )}
                              >
                                {file.change_type === 'added'
                                  ? 'A'
                                  : file.change_type === 'modified'
                                    ? 'M'
                                    : file.change_type === 'deleted'
                                      ? 'D'
                                      : 'R'}
                              </span>
                              <span className="text-caption text-[var(--color-paper)]/80 truncate font-mono">
                                {file.file_path}
                              </span>
                            </div>
                            <div className="flex items-center gap-3 shrink-0">
                              <div className="flex items-center gap-1.5 text-caption min-w-[60px] justify-end">
                                {file.additions > 0 && (
                                  <span className="text-[var(--color-jade)]">
                                    +{file.additions}
                                  </span>
                                )}
                                {file.deletions > 0 && (
                                  <span className="text-[var(--color-vermillion)]">
                                    -{file.deletions}
                                  </span>
                                )}
                              </div>
                              <div className="flex h-2 w-[80px] rounded-sm overflow-hidden bg-[var(--color-void)]">
                                <div
                                  className="bg-[var(--color-jade)]"
                                  style={{ width: `${additionWidth}%` }}
                                />
                                <div
                                  className="bg-[var(--color-vermillion)]"
                                  style={{ width: `${deletionWidth}%` }}
                                />
                              </div>
                            </div>
                          </button>
                          {isExpanded && (
                            <div className="ml-4 mb-2 border-l border-[rgba(163,163,163,0.15)] pl-3">
                              {isLoading ? (
                                <div className="py-2 flex items-center gap-2">
                                  <RotateCw className="w-3 h-3 animate-spin text-[var(--color-stone)]/50" />
                                  <span className="text-caption text-[var(--color-stone)]/50">
                                    Loading diff...
                                  </span>
                                </div>
                              ) : diff?.diff ? (
                                <pre className="text-mono text-caption leading-relaxed whitespace-pre-wrap overflow-x-auto max-h-80 overflow-y-auto bg-[var(--color-void)]/50 rounded p-2">
                                  {diff.diff.split('\n').map((line, lineIdx) => {
                                    let lineClass = 'text-[var(--color-paper)]/60'
                                    if (line.startsWith('+') && !line.startsWith('+++')) {
                                      lineClass =
                                        'text-[var(--color-jade)] bg-[rgba(45,212,191,0.08)]'
                                    } else if (line.startsWith('-') && !line.startsWith('---')) {
                                      lineClass =
                                        'text-[var(--color-vermillion)] bg-[rgb(var(--color-vermillion-rgb)/0.08)]'
                                    } else if (line.startsWith('@@')) {
                                      lineClass = 'text-[var(--color-orchid)]'
                                    } else if (
                                      line.startsWith('diff ') ||
                                      line.startsWith('index ') ||
                                      line.startsWith('---') ||
                                      line.startsWith('+++')
                                    ) {
                                      lineClass = 'text-[var(--color-stone)]/50'
                                    }
                                    return (
                                      <div
                                        // biome-ignore lint/suspicious/noArrayIndexKey: diff lines can be identical
                                        key={lineIdx}
                                        className={cn('px-1 -mx-1', lineClass)}
                                      >
                                        {line || ' '}
                                      </div>
                                    )
                                  })}
                                </pre>
                              ) : (
                                <div className="py-2 text-caption text-[var(--color-stone)]/50 italic">
                                  No diff available
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-32 text-[var(--color-stone)]/50">
                    <FileCode className="w-6 h-6 mb-2 opacity-50" />
                    {detail?.pr_status === 'merged' ? (
                      <>
                        <span className="text-caption">
                          Branch merged into {detail?.source_branch || 'main'}
                        </span>
                        <span className="text-caption mt-1 opacity-70">
                          File changes no longer available
                        </span>
                      </>
                    ) : (
                      <span className="text-caption">No files changed on this branch</span>
                    )}
                  </div>
                )}
              </div>
            )}
            {activeTab === 'attachments' && (
              <div className="p-3 overflow-y-auto h-full">
                {loadingAttachments ? (
                  <div className="flex items-center justify-center h-32">
                    <RotateCw className="w-4 h-4 animate-spin text-[var(--color-stone)]/50" />
                  </div>
                ) : attachments.length > 0 ? (
                  <div className="space-y-0">
                    <div className="flex items-center justify-between mb-3 pb-2 border-b border-[rgba(163,163,163,0.08)]">
                      <div className="flex items-center gap-2">
                        <ImageIcon className="w-3.5 h-3.5 text-[var(--color-harvest)]" />
                        <span className="text-caption text-[var(--color-paper)]/80">
                          {attachments.length} image{attachments.length !== 1 ? 's' : ''} attached
                        </span>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                      {attachments.map((image) => (
                        <ImageLightbox
                          key={image.id}
                          src={getImageFileUrl(image.id)}
                          alt={image.original_name}
                        >
                          <div className="group relative rounded-sm overflow-hidden border border-[rgba(163,163,163,0.1)] bg-[var(--color-void)] cursor-pointer">
                            <img
                              src={getImageFileUrl(image.id)}
                              alt={image.original_name}
                              className="w-full h-24 object-cover"
                            />
                            <div className="absolute inset-0 bg-[var(--color-void)]/80 opacity-0 group-hover:opacity-100 transition-opacity flex flex-col items-center justify-center gap-2">
                              <span className="flex items-center gap-1.5 px-2 py-1 text-micro uppercase tracking-widest text-[var(--color-paper)] bg-[var(--color-paper)]/10 rounded-sm">
                                <Download className="w-3 h-3" />
                                View
                              </span>
                            </div>
                            <div className="absolute bottom-0 left-0 right-0 px-2 py-1 bg-[var(--color-void)]/90">
                              <p className="text-micro text-[var(--color-paper)]/80 truncate">
                                {image.original_name}
                              </p>
                              <p className="text-micro text-[var(--color-stone)]/60">
                                {formatFileSize(image.size_bytes)}
                              </p>
                            </div>
                          </div>
                        </ImageLightbox>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-32 text-[var(--color-stone)]/50">
                    <ImageIcon className="w-6 h-6 mb-2 opacity-50" />
                    <span className="text-caption">No images attached</span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Follow-up Section - Always visible for all run statuses */}
          {run && (
            <div className="mt-4 p-3 bg-[var(--color-void)] border border-[rgba(163,163,163,0.1)] rounded-sm">
              {/* Queued messages list */}
              {detail?.queued_messages && detail.queued_messages.length > 0 && (
                <div className="mb-2 space-y-1.5">
                  {detail.queued_messages.map((msg, idx) => (
                    <div
                      key={msg.id}
                      className="p-2 bg-[rgba(102,178,255,0.1)] border border-[rgba(102,178,255,0.2)] rounded-sm group"
                    >
                      {editingMessageId === msg.id ? (
                        <div className="flex gap-2">
                          <textarea
                            className="flex-1 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.1)] rounded-sm px-2 py-1 text-caption text-[var(--color-paper)] placeholder:text-[var(--color-stone)]/40 focus:outline-none focus:border-[rgba(163,163,163,0.2)] resize-none min-h-[32px]"
                            value={editingMessageText}
                            onChange={(e) => setEditingMessageText(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault()
                                handleEditQueuedMessage(msg.id, editingMessageText)
                              } else if (e.key === 'Escape') {
                                setEditingMessageId(null)
                                setEditingMessageText('')
                              }
                            }}
                            rows={1}
                            onInput={(e) => {
                              const target = e.target as HTMLTextAreaElement
                              target.style.height = 'auto'
                              target.style.height = `${Math.min(target.scrollHeight, 80)}px`
                            }}
                          />
                          <button
                            className="p-1.5 text-[var(--color-leaf)] hover:bg-[var(--color-leaf)]/10 rounded-sm transition-colors"
                            onClick={() => handleEditQueuedMessage(msg.id, editingMessageText)}
                            title="Save"
                          >
                            <Check className="w-3.5 h-3.5" />
                          </button>
                          <button
                            className="p-1.5 text-[var(--color-stone)] hover:bg-[var(--color-stone)]/10 rounded-sm transition-colors"
                            onClick={() => {
                              setEditingMessageId(null)
                              setEditingMessageText('')
                            }}
                            title="Cancel"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-caption text-[var(--color-sky)]/80 flex-1">
                            <span className="text-[var(--color-sky)]/60 uppercase tracking-widest text-micro mr-2">
                              {idx + 1}
                            </span>
                            {msg.message.length > 100
                              ? `${msg.message.slice(0, 100)}...`
                              : msg.message}
                          </p>
                          <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                            <button
                              className="p-1 text-[var(--color-stone)] hover:text-[var(--color-paper)] transition-colors"
                              onClick={() => {
                                setEditingMessageId(msg.id)
                                setEditingMessageText(msg.message)
                              }}
                              title="Edit"
                            >
                              <Pencil className="w-3 h-3" />
                            </button>
                            <button
                              className="p-1 text-[var(--color-stone)] hover:text-[var(--color-vermillion)] transition-colors"
                              onClick={() => handleDeleteQueuedMessage(msg.id)}
                              title="Delete"
                            >
                              <X className="w-3 h-3" />
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Pasted image previews */}
              {resumePendingImages.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-2">
                  {resumePendingImages.map((img, idx) => (
                    <div key={img.preview} className="relative group">
                      <img
                        src={img.preview}
                        alt={img.file.name}
                        className="h-12 w-auto rounded-sm border border-[rgba(163,163,163,0.15)]"
                      />
                      <button
                        type="button"
                        className="absolute -top-1 -right-1 w-4 h-4 bg-[var(--color-vermillion)] rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                        onClick={() => removeResumeImage(idx)}
                      >
                        <span className="text-micro text-white font-bold">×</span>
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <div className="flex gap-2">
                <textarea
                  ref={resumeTextareaRef}
                  className="flex-1 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.1)] rounded-sm px-3 py-2 text-input text-[var(--color-paper)] placeholder:text-[var(--color-stone)]/40 focus:outline-none focus:border-[rgba(163,163,163,0.2)] resize-none min-h-[38px] max-h-32"
                  placeholder={
                    isActive
                      ? 'Send follow-up message... (⌘V to paste images)'
                      : 'Continue with follow-up... (⌘V to paste images)'
                  }
                  value={resumePrompt}
                  onChange={handleResumePromptChange}
                  onKeyDown={(e) => {
                    // Let autocomplete handle navigation keys
                    if (
                      (showCommandAutocomplete || showFileAutocomplete) &&
                      ['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape'].includes(e.key)
                    ) {
                      return
                    }
                    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                      e.preventDefault()
                      if (resumePrompt.trim() && !resuming && !queuing) {
                        if (isActive) {
                          handleQueueFollowup()
                        } else {
                          handleResume()
                        }
                      }
                    }
                  }}
                  onPaste={handleResumePaste}
                  disabled={resuming || queuing}
                  rows={1}
                  onInput={(e) => {
                    const target = e.target as HTMLTextAreaElement
                    target.style.height = 'auto'
                    target.style.height = `${Math.min(target.scrollHeight, 128)}px`
                  }}
                />
                <CommandAutocomplete
                  commands={commands}
                  filter={commandFilter}
                  visible={showCommandAutocomplete}
                  onSelect={handleCommandSelect}
                  onClose={handleAutocompleteClose}
                  anchorRef={resumeTextareaRef}
                />
                <FileAutocomplete
                  files={projectFiles}
                  filter={fileFilter}
                  visible={showFileAutocomplete}
                  onSelect={handleFileSelect}
                  onClose={handleAutocompleteClose}
                  anchorRef={resumeTextareaRef}
                  loading={filesLoading}
                  truncated={filesTruncated}
                />
                {isActive ? (
                  /* Active task - show Queue and Send Now buttons */
                  <div className="flex gap-1.5 shrink-0 self-start">
                    <button
                      className={cn(
                        'flex items-center justify-center rounded-sm text-caption uppercase tracking-widest transition-colors',
                        'p-2 sm:px-3 sm:py-2 sm:gap-1.5',
                        resumePrompt.trim() && !queuing && !resuming
                          ? 'bg-[var(--color-stone)]/20 text-[var(--color-paper)] hover:bg-[var(--color-stone)]/30'
                          : 'bg-[var(--color-stone)]/10 text-[var(--color-stone)]/40 cursor-not-allowed'
                      )}
                      onClick={handleQueueFollowup}
                      disabled={!resumePrompt.trim() || queuing || resuming}
                      title={queuing ? 'Queueing...' : 'Add to queue'}
                    >
                      <Clock className="w-3 h-3" />
                      <span className="hidden sm:inline">{queuing ? 'Queueing...' : 'Queue'}</span>
                    </button>
                    <button
                      className={cn(
                        'flex items-center justify-center rounded-sm text-caption uppercase tracking-widest transition-colors',
                        'p-2 sm:px-3 sm:py-2 sm:gap-1.5',
                        resumePrompt.trim() && !resuming && !queuing
                          ? 'bg-[var(--color-paper)] text-[var(--color-void)] hover:opacity-90'
                          : 'bg-[var(--color-stone)]/20 text-[var(--color-stone)]/50 cursor-not-allowed'
                      )}
                      onClick={handleSendNow}
                      disabled={!resumePrompt.trim() || resuming || queuing}
                      title="Cancel current task and send immediately"
                    >
                      <Play className="w-3 h-3" />
                      <span className="hidden sm:inline">
                        {resuming ? 'Sending...' : 'Send Now'}
                      </span>
                    </button>
                  </div>
                ) : (
                  /* Not active - show single Resume button */
                  <button
                    className={cn(
                      'flex items-center justify-center rounded-sm text-caption uppercase tracking-widest transition-colors shrink-0 self-start',
                      'p-2 sm:px-4 sm:py-2 sm:gap-2',
                      resumePrompt.trim() && !resuming
                        ? 'bg-[var(--color-paper)] text-[var(--color-void)] hover:opacity-90'
                        : 'bg-[var(--color-stone)]/20 text-[var(--color-stone)]/50 cursor-not-allowed'
                    )}
                    onClick={handleResume}
                    disabled={!resumePrompt.trim() || resuming}
                    title={resuming ? 'Resuming...' : 'Resume'}
                  >
                    <Play className="w-3 h-3" />
                    <span className="hidden sm:inline">{resuming ? 'Resuming...' : 'Resume'}</span>
                  </button>
                )}
              </div>
              {resumeError && (
                <p className="text-caption text-[var(--color-vermillion)] mt-2">{resumeError}</p>
              )}
            </div>
          )}

          {/* Footer Meta */}
          {(detail?.session_id || detail?.input_tokens || detail?.model_used) && (
            <div className="mt-4 pt-3 border-t border-[rgba(163,163,163,0.06)] flex items-center justify-between">
              {detail?.session_id ? (
                <Link
                  to={`/sessions?selected=${detail.session_id}`}
                  className="text-mono text-caption text-[var(--color-stone)]/50 hover:text-[var(--color-sky)] transition-colors"
                  title="View session in Session Browser"
                >
                  session {detail.session_id.slice(0, 12)}
                </Link>
              ) : (
                <span className="text-mono text-caption text-[var(--color-stone)]/50" />
              )}
              <div className="flex items-center gap-4 text-mono text-caption text-[var(--color-stone)]/50">
                {(detail?.input_tokens || detail?.output_tokens) && (
                  <span>
                    {formatTokens(detail.input_tokens)} input → {formatTokens(detail.output_tokens)}{' '}
                    output
                  </span>
                )}
                {detail?.model_used && (
                  <span className="text-[var(--color-stone)]/40">{detail.model_used}</span>
                )}
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
