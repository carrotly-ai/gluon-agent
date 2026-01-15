import {
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
  Maximize2,
  Minus,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCw,
  Sparkles,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import {
  answerQuestion,
  cancelRun,
  createPrForRun,
  deleteQueuedMessage,
  editQueuedMessage,
  fetchCommitDetail,
  fetchFileDiff,
  fetchLogs,
  fetchRun,
  fetchRunAttachments,
  fetchRunCommits,
  fetchRunFiles,
  fetchRunQuestions,
  fetchSessionHistory,
  getImageFileUrl,
  mergeRunBranch,
  queueFollowup,
  recoverRun,
  resumeRun,
  uploadAndAttachImage,
} from '@/lib/api'
import { formatDateWithContext, formatRelativeTime } from '@/lib/timestamps'
import type {
  CommitDetail,
  FileDiff,
  ImageAttachment,
  PendingQuestion,
  Run,
  RunCommitsResponse,
  RunDetail,
  RunFilesResponse,
  RunStatus,
} from '@/lib/types'
import { formatFileSize } from '@/lib/types'
import { cn } from '@/lib/utils'
import { LoopProgressTab } from './LoopProgressTab'
import { QuestionModal } from './QuestionModal'
import { StreamingLogViewer } from './StreamingLogViewer'

type TabType =
  | 'output'
  | 'errors'
  | 'messages'
  | 'history'
  | 'commits'
  | 'files'
  | 'attachments'
  | 'loop'

interface RunDetailDialogProps {
  run: Run | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onRunUpdated: (run: Run) => void
  initialTab?: TabType
  onTabChange?: (tab: TabType) => void
}

// Pending image for resume feature
interface ResumePendingImage {
  file: File
  preview: string
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

function formatTokens(tokens: number | null): string {
  if (tokens === null || tokens === undefined) return '-'
  if (tokens < 1000) return `${tokens}`
  if (tokens < 1000000) return `${(tokens / 1000).toFixed(1)}k`
  return `${(tokens / 1000000).toFixed(2)}M`
}

interface AgentMessage {
  timestamp: string
  type: 'text' | 'tool_use' | 'system' | 'error' | 'result' | 'user'
  content: string
  metadata?: {
    tool?: string
    tool_id?: string
    input?: unknown
    session_id?: string
    cost?: number
    tokens_in?: number
    tokens_out?: number
  }
}

function parseMessages(messagesContent: string): AgentMessage[] {
  if (!messagesContent) return []
  const lines = messagesContent.trim().split('\n')
  const messages: AgentMessage[] = []
  for (const line of lines) {
    if (!line.trim()) continue
    try {
      messages.push(JSON.parse(line))
    } catch {
      // Skip invalid JSON lines
    }
  }
  return messages
}

export function RunDetailDialog({
  run,
  open,
  onOpenChange,
  onRunUpdated,
  initialTab,
  onTabChange,
}: RunDetailDialogProps) {
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [logs, setLogs] = useState<{ stdout: string; stderr: string; messages: string }>({
    stdout: '',
    stderr: '',
    messages: '',
  })
  const [activeTab, setActiveTabInternal] = useState<TabType>(initialTab || 'messages')

  // Wrap setActiveTab to notify parent
  const setActiveTab = useCallback(
    (tab: TabType) => {
      setActiveTabInternal(tab)
      onTabChange?.(tab)
    },
    [onTabChange]
  )

  // Sync with initialTab when it changes (URL navigation)
  useEffect(() => {
    if (initialTab && initialTab !== activeTab) {
      setActiveTabInternal(initialTab)
    }
  }, [initialTab, activeTab])
  const [loading, setLoading] = useState(false)
  const [commitsData, setCommitsData] = useState<RunCommitsResponse | null>(null)
  const [filesData, setFilesData] = useState<RunFilesResponse | null>(null)
  const [loadingCommits, setLoadingCommits] = useState(false)
  const [loadingFiles, setLoadingFiles] = useState(false)
  // Expanded commit/file state for lazy loading details
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
  const [resumeError, setResumeError] = useState<string | null>(null)
  const [sessionHistory, setSessionHistory] = useState<Run[]>([])
  const [expandedHistoryRun, setExpandedHistoryRun] = useState<string | null>(null)
  const [historyLogs, setHistoryLogs] = useState<
    Record<string, { stdout: string; stderr: string }>
  >({})
  // Archive state removed - Archive button currently disabled
  const [creatingPr, setCreatingPr] = useState(false)
  const [prError, setPrError] = useState<string | null>(null)
  const [merging, setMerging] = useState(false)
  const [mergeError, setMergeError] = useState<string | null>(null)
  const [recovering, setRecovering] = useState(false)
  const [recoverError, setRecoverError] = useState<string | null>(null)
  const [queuing, setQueuing] = useState(false)
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null)
  const [editingMessageText, setEditingMessageText] = useState('')

  // Resume image paste support
  const [resumePendingImages, setResumePendingImages] = useState<ResumePendingImage[]>([])

  // Pending questions from AskUserQuestion tool
  const [pendingQuestions, setPendingQuestions] = useState<PendingQuestion[]>([])

  // Refs for auto-scroll
  const messagesContainerRef = useRef<HTMLDivElement>(null)
  const outputContainerRef = useRef<HTMLPreElement>(null)
  const prevMessagesRef = useRef<string>('')
  const prevOutputRef = useRef<string>('')
  const resumeTextareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!open || !run) {
      setDetail(null)
      setLogs({ stdout: '', stderr: '', messages: '' })
      setActiveTabInternal(initialTab || 'messages')
      setResumePrompt('')
      setResumeError(null)
      // Cleanup resume image previews
      resumePendingImages.forEach((img) => URL.revokeObjectURL(img.preview))
      setResumePendingImages([])
      setSessionHistory([])
      setExpandedHistoryRun(null)
      setHistoryLogs({})
      setPrError(null)
      setMergeError(null)
      setRecoverError(null)
      setCommitsData(null)
      setFilesData(null)
      setExpandedCommit(null)
      setCommitDetails({})
      setExpandedFile(null)
      setFileDiffs({})
      setAttachments([])
      setPendingQuestions([])
      return
    }

    const runId = run.id

    async function load() {
      setLoading(true)
      try {
        const [runDetail, stdoutLogs, stderrLogs, messagesLogs] = await Promise.all([
          fetchRun(runId),
          fetchLogs(runId, 'stdout').catch(() => ({ content: '' })),
          fetchLogs(runId, 'stderr').catch(() => ({ content: '' })),
          fetchLogs(runId, 'messages').catch(() => ({ content: '' })),
        ])
        setDetail(runDetail)
        setLogs({
          stdout: stdoutLogs.content || '',
          stderr: stderrLogs.content || '',
          messages: messagesLogs.content || '',
        })

        // Fetch session history if there's a session
        if (runDetail.session_id) {
          try {
            const history = await fetchSessionHistory(runId)
            // Filter out the current run and only show previous runs
            const previousRuns = history.runs.filter((r) => r.id !== runId)
            setSessionHistory(previousRuns)
          } catch {
            // Session history is optional, don't fail if it errors
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
  }, [
    open,
    run,
    initialTab, // Cleanup resume image previews
    resumePendingImages.forEach,
  ])

  // Auto-refresh for active runs
  useEffect(() => {
    if (!open || !run) return
    const isRunActive = run.status === 'running' || run.status === 'pending'
    if (!isRunActive) return

    const intervalId = setInterval(async () => {
      try {
        const [runDetail, stdoutLogs, stderrLogs, messagesLogs, newCommitsData, newFilesData] =
          await Promise.all([
            fetchRun(run.id),
            fetchLogs(run.id, 'stdout').catch(() => ({ content: '' })),
            fetchLogs(run.id, 'stderr').catch(() => ({ content: '' })),
            fetchLogs(run.id, 'messages').catch(() => ({ content: '' })),
            // Also refresh commits and files during active runs
            fetchRunCommits(run.id).catch(() => null),
            fetchRunFiles(run.id).catch(() => null),
          ])
        setDetail(runDetail)
        setLogs({
          stdout: stdoutLogs.content || '',
          stderr: stderrLogs.content || '',
          messages: messagesLogs.content || '',
        })
        // Update commits and files if fetched successfully
        if (newCommitsData) setCommitsData(newCommitsData)
        if (newFilesData) setFilesData(newFilesData)
        onRunUpdated(runDetail)
      } catch (err) {
        console.error('Auto-refresh failed:', err)
      }
    }, 3000) // Refresh every 3 seconds

    return () => clearInterval(intervalId)
  }, [open, run?.id, run?.status, onRunUpdated, run])

  // Poll PR status when dialog is open with an open PR
  // This catches when user merges PR on GitHub
  useEffect(() => {
    if (!open || !detail) return
    // Only poll if we have an open PR and run is not active
    const hasOpenPr = detail.pr_status === 'open' && detail.pr_url
    const isRunActive = detail.status === 'running' || detail.status === 'pending'
    if (!hasOpenPr || isRunActive) return

    const pollInterval = setInterval(async () => {
      try {
        const updatedDetail = await fetchRun(detail.id)
        // Check if PR status changed (e.g., merged on GitHub)
        if (updatedDetail.pr_status !== detail.pr_status) {
          setDetail(updatedDetail)
          onRunUpdated(updatedDetail)
        }
      } catch (err) {
        console.error('PR status poll failed:', err)
      }
    }, 7000) // Poll every 7 seconds for PR status changes

    return () => clearInterval(pollInterval)
  }, [open, detail?.id, detail?.pr_status, detail?.status, onRunUpdated, detail])

  // Poll for pending questions when run is active
  useEffect(() => {
    if (!open || !run) return
    const isRunActive = run.status === 'running' || run.status === 'pending'
    if (!isRunActive) return

    // Initial fetch
    const fetchQuestions = async () => {
      try {
        const response = await fetchRunQuestions(run.id)
        setPendingQuestions(response.questions)
      } catch {
        // Ignore errors - questions are optional
      }
    }

    fetchQuestions()

    // Poll every 2 seconds for new questions
    const pollInterval = setInterval(fetchQuestions, 2000)

    return () => clearInterval(pollInterval)
  }, [open, run?.id, run?.status, run])

  // Handle answering a question
  const handleAnswerQuestion = useCallback(
    async (questionId: string, selectedLabels: string[]) => {
      await answerQuestion(questionId, selectedLabels)
      // Refresh questions list
      if (run) {
        const response = await fetchRunQuestions(run.id)
        setPendingQuestions(response.questions)
      }
    },
    [run]
  )

  // Auto-scroll to bottom when content changes
  useEffect(() => {
    // Only scroll if content actually changed
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

  const handleCancel = async () => {
    if (!run) return
    setCancelling(true)
    try {
      const updated = await cancelRun(run.id)
      onRunUpdated(updated)
    } catch (err) {
      console.error('Failed to cancel run:', err)
    } finally {
      setCancelling(false)
    }
  }

  const handleRefresh = async () => {
    if (!run) return
    setLoading(true)
    try {
      // Fetch run details (which refreshes PR status from GitHub if PR is open),
      // logs, commits, and files all in parallel
      const [runDetail, stdoutLogs, stderrLogs, messagesLogs, newCommitsData, newFilesData] =
        await Promise.all([
          fetchRun(run.id),
          fetchLogs(run.id, 'stdout').catch(() => ({ content: '' })),
          fetchLogs(run.id, 'stderr').catch(() => ({ content: '' })),
          fetchLogs(run.id, 'messages').catch(() => ({ content: '' })),
          // Also refresh commits and files data
          fetchRunCommits(run.id).catch(() => null),
          fetchRunFiles(run.id).catch(() => null),
        ])
      setDetail(runDetail)
      setLogs({
        stdout: stdoutLogs.content || '',
        stderr: stderrLogs.content || '',
        messages: messagesLogs.content || '',
      })
      // Update commits and files if fetched successfully
      if (newCommitsData) {
        setCommitsData(newCommitsData)
        // Clear cached commit details since files may have changed
        setCommitDetails({})
        setExpandedCommit(null)
      }
      if (newFilesData) {
        setFilesData(newFilesData)
        // Clear cached file diffs since content may have changed
        setFileDiffs({})
        setExpandedFile(null)
      }
      onRunUpdated(runDetail)
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

  // Handle paste for resume textarea
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
      // Resume continues the same run in-place
      const result = await resumeRun(run.id, resumePrompt.trim())

      // Upload images to the run if any (same run_id)
      if (resumePendingImages.length > 0 && result.run_id) {
        const uploadPromises = resumePendingImages.map((img) =>
          uploadAndAttachImage(result.run_id, img.file).catch((err) => {
            console.error(`Failed to upload image ${img.file.name}:`, err)
            return null
          })
        )
        await Promise.all(uploadPromises)
      }

      // Cleanup images and prompt, but DON'T close dialog
      // The run is now RUNNING again - stay open to watch progress
      resumePendingImages.forEach((img) => URL.revokeObjectURL(img.preview))
      setResumePendingImages([])
      setResumePrompt('')
      // Reset textarea height
      if (resumeTextareaRef.current) {
        resumeTextareaRef.current.style.height = 'auto'
      }

      // Refresh the run data to show new status
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
    // Load logs if not already cached
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

  // Handler for expanding a commit to see full message + files
  const handleExpandCommit = async (sha: string) => {
    if (expandedCommit === sha) {
      setExpandedCommit(null)
      return
    }
    setExpandedCommit(sha)
    // Load commit details if not already cached
    if (!commitDetails[sha] && run) {
      setLoadingCommitDetail(sha)
      try {
        const detail = await fetchCommitDetail(run.id, sha)
        setCommitDetails((prev) => ({ ...prev, [sha]: detail }))
      } catch (err) {
        console.error('Failed to load commit details:', err)
      } finally {
        setLoadingCommitDetail(null)
      }
    }
  }

  // Handler for expanding a file to see diff
  const handleExpandFile = async (filePath: string) => {
    if (expandedFile === filePath) {
      setExpandedFile(null)
      return
    }
    setExpandedFile(filePath)
    // Load file diff if not already cached
    if (!fileDiffs[filePath] && run) {
      setLoadingFileDiff(filePath)
      try {
        const diff = await fetchFileDiff(run.id, filePath)
        setFileDiffs((prev) => ({ ...prev, [filePath]: diff }))
      } catch (err) {
        console.error('Failed to load file diff:', err)
      } finally {
        setLoadingFileDiff(null)
      }
    }
  }

  // handleArchive removed - Archive button currently disabled

  const handleCreatePr = async () => {
    if (!run) return
    setCreatingPr(true)
    setPrError(null)
    try {
      const result = await createPrForRun(run.id)
      if (result.success && result.pr_url) {
        // Refresh the run details to get updated PR info
        const updatedDetail = await fetchRun(run.id)
        setDetail(updatedDetail)
        onRunUpdated(updatedDetail)
        toast.success('Pull request created', {
          description: `PR #${updatedDetail.pr_number} opened`,
          action: {
            label: 'View',
            onClick: () => window.open(result.pr_url, '_blank'),
          },
        })
      } else {
        setPrError(result.error || 'Failed to create PR')
      }
    } catch (err) {
      setPrError(err instanceof Error ? err.message : 'Failed to create PR')
    } finally {
      setCreatingPr(false)
    }
  }

  const handleMerge = async () => {
    if (!run) return
    setMerging(true)
    setMergeError(null)
    try {
      const result = await mergeRunBranch(run.id)
      if (result.success) {
        // Refresh the run details to get updated PR status (will show as merged)
        const updatedDetail = await fetchRun(run.id)
        setDetail(updatedDetail)
        onRunUpdated(updatedDetail)
        toast.success('Branch merged successfully', {
          description: `Merged into ${detail?.source_branch || 'main'}`,
        })
      } else if (
        result.has_conflicts &&
        result.conflicting_files &&
        result.conflicting_files.length > 0
      ) {
        // Merge conflicts detected - prompt user to resolve via agent resume
        const filesStr = result.conflicting_files.slice(0, 10).join('\n- ')
        const moreCount =
          result.conflicting_files.length > 10 ? result.conflicting_files.length - 10 : 0
        const conflictPrompt = `The merge has conflicts that need to be resolved. Please fix these merge conflicts:

Conflicting files:
- ${filesStr}${moreCount > 0 ? `\n- ... and ${moreCount} more files` : ''}

Steps to resolve:
1. In the worktree, run: git merge ${detail?.source_branch || 'main'}
2. Resolve each conflict by understanding both changes and merging them appropriately
3. After resolving all conflicts, commit the merge
4. Push the changes

Focus on preserving functionality from both sides where possible.`

        setResumePrompt(conflictPrompt)
        setMergeError(
          `Merge conflicts in ${result.conflicting_files.length} file(s). Use the resume prompt below to have Claude resolve them.`
        )

        // Scroll to resume section
        setTimeout(() => {
          document
            .querySelector('textarea[placeholder*="Continue with follow-up"]')
            ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }, 100)
      } else {
        setMergeError(result.error || 'Failed to merge branch')
      }
    } catch (err) {
      setMergeError(err instanceof Error ? err.message : 'Failed to merge branch')
    } finally {
      setMerging(false)
    }
  }

  // Helper to detect context overflow errors
  const isContextOverflowError = (errorMessage: string | null | undefined): boolean => {
    if (!errorMessage) return false
    const msg = errorMessage.toLowerCase()
    return (
      (msg.includes('400') && msg.includes('too long')) ||
      (msg.includes('400') && msg.includes('input') && msg.includes('long')) ||
      msg.includes('input is too long') ||
      (msg.includes('context') && msg.includes('overflow')) ||
      (msg.includes('context') && msg.includes('exceeded')) ||
      (msg.includes('token') && msg.includes('limit') && msg.includes('exceeded'))
    )
  }

  const handleRecover = async () => {
    if (!run) return
    setRecovering(true)
    setRecoverError(null)
    try {
      const result = await recoverRun(run.id, false) // In-place recovery
      if (result.run_id) {
        toast.success('Recovery started', {
          description: `Found ${result.completed_work.length} completed task(s) to preserve`,
        })
        // Refresh the run data to show new status
        handleRefresh()
      }
    } catch (err) {
      setRecoverError(err instanceof Error ? err.message : 'Failed to recover run')
    } finally {
      setRecovering(false)
    }
  }

  // Lazy load commits when switching to commits tab
  const loadCommits = useCallback(async () => {
    if (!run || commitsData || loadingCommits) return
    setLoadingCommits(true)
    try {
      const data = await fetchRunCommits(run.id)
      setCommitsData(data)
    } catch (err) {
      console.error('Failed to load commits:', err)
    } finally {
      setLoadingCommits(false)
    }
  }, [run, commitsData, loadingCommits])

  // Lazy load files when switching to files tab
  const loadFiles = useCallback(async () => {
    if (!run || filesData || loadingFiles) return
    setLoadingFiles(true)
    try {
      const data = await fetchRunFiles(run.id)
      setFilesData(data)
    } catch (err) {
      console.error('Failed to load files:', err)
    } finally {
      setLoadingFiles(false)
    }
  }, [run, filesData, loadingFiles])

  // Lazy load attachments when switching to attachments tab
  const loadAttachments = useCallback(async () => {
    if (!run || attachments.length > 0 || loadingAttachments) return
    setLoadingAttachments(true)
    try {
      const data = await fetchRunAttachments(run.id)
      setAttachments(data.images)
    } catch (err) {
      console.error('Failed to load attachments:', err)
    } finally {
      setLoadingAttachments(false)
    }
  }, [run, attachments.length, loadingAttachments])

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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="dialog-content sm:max-w-6xl w-[95vw] max-h-[90vh] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden">
        {/* Compact Header Bar */}
        <div className="flex items-center justify-between px-4 sm:px-5 py-2.5 border-b border-[rgba(163,163,163,0.1)] bg-[var(--color-void)]">
          {/* Left: Back (mobile) + Run Identity */}
          <div className="flex items-center gap-2 min-w-0">
            <button
              className="md:hidden p-1 -ml-1 text-[var(--color-stone)] hover:text-[var(--color-paper)] transition-colors shrink-0"
              onClick={() => onOpenChange(false)}
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            {/* Run status and ID */}
            <div className="flex items-center gap-2 shrink-0">
              <div className={cn('mark', `mark-${run?.status}`)} />
              <span className="text-mono text-[var(--color-stone)]/60 text-body">
                {run?.id.slice(0, 8)}
              </span>
              <span className="text-body uppercase tracking-widest text-[var(--color-stone)]/55">
                {run?.status}
              </span>
            </div>
            {/* Git context - branch and commit */}
            {detail?.branch_name && (
              <div className="hidden sm:flex items-center gap-1.5 ml-1 text-body text-[var(--color-stone)]/50">
                <span className="text-[var(--color-stone)]/30">on</span>
                <GitBranch className="w-2.5 h-2.5 text-purple-400/70" />
                <span className="text-purple-300/80 truncate max-w-[100px]">
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

          {/* Right: PR Status + Actions */}
          <div className="flex items-center gap-2 shrink-0">
            {/* PR badge - informational, links to GitHub */}
            {detail?.pr_number && detail?.pr_url && (
              <a
                href={detail.pr_url}
                target="_blank"
                rel="noopener noreferrer"
                className={cn(
                  'hidden sm:flex items-center gap-1.5 px-2 py-1 rounded-sm text-body transition-colors',
                  detail.pr_mergeable === 'CONFLICTING' &&
                    'bg-[rgba(239,68,68,0.15)] border border-[rgba(239,68,68,0.3)] text-red-400 hover:bg-[rgba(239,68,68,0.2)]',
                  detail.pr_mergeable !== 'CONFLICTING' &&
                    detail.pr_status === 'open' &&
                    'bg-[rgba(34,197,94,0.1)] border border-[rgba(34,197,94,0.2)] text-green-400 hover:bg-[rgba(34,197,94,0.15)]',
                  detail.pr_status === 'merged' &&
                    'bg-[rgba(168,85,247,0.1)] border border-[rgba(168,85,247,0.2)] text-purple-400',
                  detail.pr_status === 'closed' &&
                    'bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.2)] text-red-400',
                  detail.pr_status === 'draft' &&
                    'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]'
                )}
                title={
                  detail.pr_mergeable === 'CONFLICTING'
                    ? 'PR has merge conflicts - click to view on GitHub'
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

            {/* Divider between info and actions */}
            {(detail?.pr_number || detail?.branch_name) && (
              <div className="hidden sm:block w-px h-4 bg-[var(--color-stone)]/20" />
            )}

            {/* Action buttons */}
            <div className="flex items-center gap-1 pr-8">
              {/* Merge - only show when PR is open AND mergeable (no conflicts) */}
              {detail?.pr_status === 'open' &&
                detail?.pr_mergeable !== 'CONFLICTING' &&
                detail?.branch_name &&
                !isActive && (
                  <button
                    onClick={handleMerge}
                    disabled={merging}
                    className={cn(
                      'flex items-center gap-1.5 px-2.5 py-1 text-body uppercase tracking-widest rounded-sm transition-colors',
                      merging
                        ? 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                        : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]'
                    )}
                    title="Merge branch locally and push to remote"
                  >
                    <GitMerge className="w-3 h-3" />
                    <span>{merging ? 'Merging...' : 'Merge'}</span>
                  </button>
                )}
              {/* Resolve Conflicts - show when PR has conflicts and run is resumable */}
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
                    // Scroll to resume section
                    setTimeout(() => {
                      document
                        .querySelector('textarea[placeholder*="Continue with follow-up"]')
                        ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                    }, 100)
                  }}
                  className="flex items-center gap-1.5 px-2.5 py-1 text-body uppercase tracking-widest rounded-sm transition-colors bg-[rgba(168,85,247,0.15)] border border-[rgba(168,85,247,0.3)] text-purple-400 hover:bg-[rgba(168,85,247,0.25)]"
                  title="Use Claude to resolve merge conflicts"
                >
                  <Sparkles className="w-3 h-3" />
                  <span>Resolve</span>
                </button>
              )}
              {/* Create PR - show for worktree runs with branch, has remote, but no PR */}
              {detail?.use_worktree &&
                detail?.branch_name &&
                detail?.has_remote &&
                !detail?.pr_url &&
                !isActive && (
                  <button
                    onClick={handleCreatePr}
                    disabled={creatingPr}
                    className={cn(
                      'flex items-center gap-1.5 px-2.5 py-1 text-body uppercase tracking-widest rounded-sm transition-colors',
                      creatingPr
                        ? 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                        : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]'
                    )}
                  >
                    <GitPullRequest className="w-3 h-3" />
                    <span>{creatingPr ? 'Creating...' : 'Create PR'}</span>
                  </button>
                )}
              {/* Merge (local) - show for worktree runs with branch but NO remote, hide if already merged */}
              {detail?.use_worktree &&
                detail?.branch_name &&
                !detail?.has_remote &&
                detail?.pr_status !== 'merged' &&
                !isActive && (
                  <button
                    onClick={handleMerge}
                    disabled={merging}
                    className={cn(
                      'flex items-center gap-1.5 px-2.5 py-1 text-body uppercase tracking-widest rounded-sm transition-colors',
                      merging
                        ? 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                        : 'bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-green-400 hover:bg-[rgba(34,197,94,0.25)]'
                    )}
                    title="Merge branch locally"
                  >
                    <GitMerge className="w-3 h-3" />
                    <span>{merging ? 'Merging...' : 'Merge'}</span>
                  </button>
                )}
              {/* Cancel - for active runs */}
              {isActive && (
                <button
                  className="flex items-center gap-1.5 px-2.5 py-1 text-body uppercase tracking-widest text-[var(--color-vermillion)] hover:text-[var(--color-vermillion)] border border-[var(--color-vermillion)]/30 hover:border-[var(--color-vermillion)]/50 hover:bg-[rgba(199,62,58,0.1)] rounded-sm transition-colors"
                  onClick={handleCancel}
                  disabled={cancelling}
                >
                  {cancelling ? 'Cancelling...' : 'Cancel'}
                </button>
              )}
              {/* Archive - for completed runs */}
              {/* {!isActive && (
                <button
                  className="flex items-center gap-1.5 px-2 py-1 text-body uppercase tracking-widest text-[var(--color-stone)]/60 hover:text-[var(--color-stone)] border border-[var(--color-stone)]/15 hover:border-[var(--color-stone)]/30 rounded-sm transition-colors"
                  onClick={handleArchive}
                  disabled={archiving}
                  title="Archive this run"
                >
                  <Archive className="w-3 h-3" />
                  <span>{archiving ? '...' : 'Archive'}</span>
                </button>
              )} */}
              {/* Full-screen view toggle */}
              <Link
                to={`/runs/${run?.id}/${activeTab}`}
                className="p-1.5 text-[var(--color-stone)]/50 hover:text-[var(--color-paper)] transition-colors rounded-sm hover:bg-[var(--color-paper)]/5"
                title="Open in full screen"
              >
                <Maximize2 className="w-3.5 h-3.5" />
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

        {/* Main Content */}
        <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
          <div className="p-4 sm:p-5 flex flex-col flex-1 min-h-0">
            {/* Project + Meta Row */}
            <div className="flex items-center gap-4 text-body text-[var(--color-stone)]/60 mb-4 shrink-0 flex-wrap">
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
              {detail?.cost_usd != null && detail.cost_usd > 0 && (
                <span className="text-mono text-[var(--color-harvest)]">
                  ${detail.cost_usd.toFixed(4)}
                </span>
              )}
              {/* Tool count - calculated from messages */}
              {(() => {
                const toolCount = parseMessages(logs.messages).filter(
                  (m) => m.type === 'tool_use'
                ).length
                return toolCount > 0 ? (
                  <span className="text-mono text-[var(--color-sky)]">{toolCount} tools</span>
                ) : null
              })()}
            </div>

            {/* Git Info Row - show on mobile only (desktop shows in header) */}
            {(detail?.branch_name || detail?.pr_number) && (
              <div className="flex sm:hidden items-center gap-3 text-body mb-4 shrink-0 flex-wrap">
                {detail.branch_name && (
                  <div className="flex items-center gap-1.5 px-2 py-1 bg-[rgba(168,85,247,0.1)] border border-[rgba(168,85,247,0.2)] rounded-sm">
                    <GitBranch className="w-3 h-3 text-purple-400" />
                    <span className="text-purple-300">{detail.branch_name}</span>
                  </div>
                )}
                {detail.git_commit_sha && (
                  <div className="flex items-center gap-1.5 text-[var(--color-stone)]/60">
                    <GitCommit className="w-3 h-3" />
                    <span className="text-mono">{detail.git_commit_sha.slice(0, 7)}</span>
                  </div>
                )}
                {detail.pr_number && detail.pr_url && (
                  <a
                    href={detail.pr_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={cn(
                      'flex items-center gap-1.5 px-2 py-1 rounded-sm transition-colors',
                      detail.pr_mergeable === 'CONFLICTING' &&
                        'bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.2)] text-red-400 hover:bg-[rgba(239,68,68,0.15)]',
                      detail.pr_mergeable !== 'CONFLICTING' &&
                        detail.pr_status === 'open' &&
                        'bg-[rgba(34,197,94,0.1)] border border-[rgba(34,197,94,0.2)] text-green-400 hover:bg-[rgba(34,197,94,0.15)]',
                      detail.pr_status === 'merged' &&
                        'bg-[rgba(168,85,247,0.1)] border border-[rgba(168,85,247,0.2)] text-purple-400',
                      detail.pr_status === 'closed' &&
                        'bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.2)] text-red-400',
                      detail.pr_status === 'draft' &&
                        'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]'
                    )}
                  >
                    <span>PR #{detail.pr_number}</span>
                    {detail.pr_mergeable === 'CONFLICTING' ? (
                      <span className="text-body uppercase text-red-400">CONFLICTS</span>
                    ) : (
                      <span className="text-body uppercase">{detail.pr_status}</span>
                    )}
                    <ExternalLink className="w-2.5 h-2.5" />
                  </a>
                )}
              </div>
            )}
            {/* PR creation error */}
            {prError && (
              <div className="mb-4 p-2 bg-[rgba(199,62,58,0.08)] border border-[rgba(199,62,58,0.2)] rounded-sm shrink-0">
                <p className="text-body text-[var(--color-vermillion)]">{prError}</p>
              </div>
            )}
            {/* Merge error */}
            {mergeError && (
              <div className="mb-4 p-2 bg-[rgba(199,62,58,0.08)] border border-[rgba(199,62,58,0.2)] rounded-sm shrink-0">
                <p className="text-body text-[var(--color-vermillion)]">{mergeError}</p>
              </div>
            )}

            {/* Prompt - Constrained height with scroll */}
            <div className="mb-4 shrink-0">
              <div className="flex items-start justify-between gap-3">
                <div className="max-h-24 overflow-y-auto flex-1 pr-2 scrollbar-thin">
                  <p className="text-body text-[var(--color-paper)] leading-relaxed font-light">
                    {run?.prompt}
                  </p>
                </div>
              </div>
            </div>

            {/* Error Message - Prominent if exists */}
            {run?.error_message && (
              <div className="mb-6 p-3 bg-[rgba(199,62,58,0.08)] border border-[rgba(199,62,58,0.2)] rounded-sm shrink-0">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1">
                    <p className="text-body uppercase tracking-widest text-[var(--color-vermillion)]/70 mb-1.5">
                      Error
                    </p>
                    <pre className="text-body text-[var(--color-vermillion)] whitespace-pre-wrap break-words font-mono">
                      {run.error_message}
                    </pre>
                  </div>
                  {/* Recover button for context overflow errors - show for failed or review status */}
                  {(run.status === 'failed' || run.status === 'review') &&
                    isContextOverflowError(run.error_message) && (
                      <button
                        onClick={handleRecover}
                        disabled={recovering}
                        className={cn(
                          'flex items-center gap-1.5 px-3 py-1.5 text-body uppercase tracking-widest rounded-sm transition-colors shrink-0',
                          recovering
                            ? 'bg-[rgba(163,163,163,0.1)] border border-[rgba(163,163,163,0.2)] text-[var(--color-stone)]/50 cursor-wait'
                            : 'bg-[rgba(168,85,247,0.15)] border border-[rgba(168,85,247,0.3)] text-purple-400 hover:bg-[rgba(168,85,247,0.25)]'
                        )}
                        title="Recover from context overflow - starts fresh session with progress summary"
                      >
                        <Sparkles className="w-3 h-3" />
                        <span>{recovering ? 'Recovering...' : 'Recover'}</span>
                      </button>
                    )}
                </div>
                {/* Recovery error message */}
                {recoverError && (
                  <p className="text-body text-[var(--color-vermillion)] mt-2 border-t border-[rgba(199,62,58,0.2)] pt-2">
                    Recovery failed: {recoverError}
                  </p>
                )}
                {/* Context overflow help text */}
                {(run.status === 'failed' || run.status === 'review') &&
                  isContextOverflowError(run.error_message) && (
                    <p className="text-body text-[var(--color-stone)]/60 mt-2 border-t border-[rgba(199,62,58,0.15)] pt-2">
                      This run exceeded the context limit. Click Recover to start a fresh session
                      that preserves progress from completed tasks.
                    </p>
                  )}
              </div>
            )}

            {/* Logs Section */}
            <div className="flex flex-col flex-1 min-h-0">
              {/* Tab Bar */}
              <div className="flex items-center justify-between mb-3 shrink-0 gap-2">
                <div className="flex items-center gap-1 overflow-x-auto scrollbar-hide flex-nowrap min-w-0">
                  <button
                    className={cn(
                      'px-2.5 py-1 text-body uppercase tracking-widest transition-colors rounded-sm shrink-0',
                      activeTab === 'messages'
                        ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                        : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                    )}
                    onClick={() => setActiveTab('messages')}
                  >
                    Messages
                  </button>
                  <button
                    className={cn(
                      'px-2.5 py-1 text-body uppercase tracking-widest transition-colors rounded-sm shrink-0',
                      activeTab === 'output'
                        ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                        : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                    )}
                    onClick={() => setActiveTab('output')}
                  >
                    Output
                  </button>
                  <button
                    className={cn(
                      'px-2.5 py-1 text-body uppercase tracking-widest transition-colors rounded-sm shrink-0 flex items-center gap-1.5',
                      activeTab === 'errors'
                        ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                        : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                    )}
                    onClick={() => setActiveTab('errors')}
                  >
                    Errors
                    {hasErrors && (
                      <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-vermillion)]" />
                    )}
                  </button>
                  {hasHistory && (
                    <button
                      className={cn(
                        'px-2.5 py-1 text-body uppercase tracking-widest transition-colors rounded-sm shrink-0 flex items-center gap-1.5',
                        activeTab === 'history'
                          ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                          : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                      )}
                      onClick={() => setActiveTab('history')}
                    >
                      <Clock className="w-3 h-3" />
                      History
                      <span className="text-body text-[var(--color-stone)]/50">
                        ({sessionHistory.length})
                      </span>
                    </button>
                  )}
                  {detail?.branch_name && (
                    <>
                      <button
                        className={cn(
                          'px-2.5 py-1 text-body uppercase tracking-widest transition-colors rounded-sm shrink-0 flex items-center gap-1.5',
                          activeTab === 'commits'
                            ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                            : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                        )}
                        onClick={() => setActiveTab('commits')}
                      >
                        <GitCommit className="w-3 h-3" />
                        Commits
                        {(() => {
                          const count = commitsData?.commit_count ?? detail?.commit_count
                          return count && count > 0 ? (
                            <span className="text-body text-[var(--color-stone)]/50">
                              ({count})
                            </span>
                          ) : null
                        })()}
                      </button>
                      <button
                        className={cn(
                          'px-2.5 py-1 text-body uppercase tracking-widest transition-colors rounded-sm shrink-0 flex items-center gap-1.5',
                          activeTab === 'files'
                            ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                            : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                        )}
                        onClick={() => setActiveTab('files')}
                      >
                        <FileCode className="w-3 h-3" />
                        Files
                        {(() => {
                          const count = filesData?.file_count ?? detail?.file_count
                          return count && count > 0 ? (
                            <span className="text-body text-[var(--color-stone)]/50">
                              ({count})
                            </span>
                          ) : null
                        })()}
                      </button>
                    </>
                  )}
                  <button
                    className={cn(
                      'px-2.5 py-1 text-body uppercase tracking-widest transition-colors rounded-sm shrink-0 flex items-center gap-1.5',
                      activeTab === 'attachments'
                        ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                        : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                    )}
                    onClick={() => setActiveTab('attachments')}
                  >
                    <ImageIcon className="w-3 h-3" />
                    Images
                    {attachments.length > 0 && (
                      <span className="text-body text-[var(--color-stone)]/50">
                        ({attachments.length})
                      </span>
                    )}
                  </button>
                  {detail?.ralph_enabled && (
                    <button
                      className={cn(
                        'px-2.5 py-1 text-body uppercase tracking-widest transition-colors rounded-sm shrink-0 flex items-center gap-1.5',
                        activeTab === 'loop'
                          ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                          : 'text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]'
                      )}
                      onClick={() => setActiveTab('loop')}
                    >
                      <RefreshCw
                        className={cn('w-3 h-3', run?.status === 'running' && 'animate-spin')}
                      />
                      Loop
                      {(detail.loop_count || 0) > 0 && (
                        <span className="text-body text-[var(--color-stone)]/50">
                          ({detail.loop_count}/{detail.max_loops || 50})
                        </span>
                      )}
                    </button>
                  )}
                </div>
                <button
                  className={cn(
                    'flex items-center gap-1.5 px-2 py-1 text-body uppercase tracking-widest transition-colors rounded-sm',
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
              <div className="bg-[var(--color-void)] border border-[rgba(163,163,163,0.08)] rounded-sm flex-1 min-h-[200px] overflow-auto">
                {activeTab === 'output' && (
                  <pre
                    ref={outputContainerRef}
                    className="p-3 text-mono text-[var(--color-paper)]/70 whitespace-pre-wrap break-words text-body leading-relaxed h-full overflow-auto"
                  >
                    {logs.stdout || (
                      <span className="text-[var(--color-stone)]/50 italic">No output</span>
                    )}
                  </pre>
                )}
                {activeTab === 'errors' && (
                  <pre
                    className={cn(
                      'p-3 text-mono whitespace-pre-wrap break-words text-body leading-relaxed',
                      logs.stderr
                        ? 'text-[var(--color-vermillion)]/90'
                        : 'text-[var(--color-stone)]/50 italic'
                    )}
                  >
                    {logs.stderr || 'No errors'}
                  </pre>
                )}
                {activeTab === 'messages' && (
                  <div className="h-full overflow-hidden">
                    <StreamingLogViewer
                      runId={run?.id ?? null}
                      runStatus={(run?.status ?? 'pending') as RunStatus}
                      initialMessages={parseMessages(logs.messages)}
                    />
                  </div>
                )}
                {activeTab === 'history' && (
                  <div className="p-3 overflow-y-auto h-full">
                    <p className="text-body text-[var(--color-stone)]/70 mb-3">
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
                                <p className="text-body text-[var(--color-stone)]/50 mt-0.5">
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
                                <pre className="text-mono text-body text-[var(--color-paper)]/60 whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                                  {historyLogs[historyRun.id].stdout || (
                                    <span className="text-[var(--color-stone)]/40 italic">
                                      No output
                                    </span>
                                  )}
                                </pre>
                              ) : (
                                <span className="text-body text-[var(--color-stone)]/50">
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
                        {/* Branch info header */}
                        <div className="flex items-center gap-2 mb-3 pb-2 border-b border-[rgba(163,163,163,0.08)]">
                          <GitBranch className="w-3.5 h-3.5 text-purple-400" />
                          <span className="text-body text-purple-300">
                            {commitsData.branch_name}
                          </span>
                          <span className="text-body text-[var(--color-stone)]/50">
                            {commitsData.commit_count} commit
                            {commitsData.commit_count !== 1 ? 's' : ''} ahead of{' '}
                            {commitsData.base_branch}
                          </span>
                        </div>
                        {/* Commits list - compact expandable (newest first) */}
                        {commitsData.commits
                          .slice()
                          .reverse()
                          .map((commit, idx) => {
                            const isExpanded = expandedCommit === commit.sha
                            const detail = commitDetails[commit.sha]
                            const isLoading = loadingCommitDetail === commit.sha

                            return (
                              <div
                                key={commit.sha}
                                className={cn(
                                  'border-b border-[rgba(163,163,163,0.05)]',
                                  idx === commitsData.commits.length - 1 && 'border-b-0'
                                )}
                              >
                                {/* Commit header - compact single line */}
                                <button
                                  className="w-full flex items-center gap-2 py-1.5 text-left hover:bg-[var(--color-paper)]/5 transition-colors px-1 -mx-1 rounded"
                                  onClick={() => handleExpandCommit(commit.sha)}
                                >
                                  {/* Expand chevron */}
                                  <ChevronRight
                                    className={cn(
                                      'w-3 h-3 text-[var(--color-stone)]/40 transition-transform shrink-0',
                                      isExpanded && 'rotate-90'
                                    )}
                                  />
                                  {/* Commit message */}
                                  <span className="text-body text-[var(--color-paper)]/90 truncate flex-1 min-w-0">
                                    {commit.message}
                                  </span>
                                  {/* Relative time */}
                                  <span className="text-body text-[var(--color-stone)]/50 shrink-0">
                                    {formatRelativeTime(commit.date)}
                                  </span>
                                  {/* Short hash */}
                                  <span className="text-mono text-body text-[var(--color-stone)]/40 shrink-0">
                                    {commit.sha.slice(0, 7)}
                                  </span>
                                </button>
                                {/* Expanded content */}
                                {isExpanded && (
                                  <div className="ml-5 pl-3 border-l border-[rgba(163,163,163,0.15)] mb-2">
                                    {isLoading ? (
                                      <div className="py-2 flex items-center gap-2">
                                        <RotateCw className="w-3 h-3 animate-spin text-[var(--color-stone)]/50" />
                                        <span className="text-body text-[var(--color-stone)]/50">
                                          Loading...
                                        </span>
                                      </div>
                                    ) : detail ? (
                                      <div className="py-2 space-y-2">
                                        {/* Full commit message */}
                                        {detail.message && detail.message !== commit.message && (
                                          <pre className="text-body text-[var(--color-paper)]/70 whitespace-pre-wrap font-sans leading-relaxed">
                                            {detail.message}
                                          </pre>
                                        )}
                                        {/* Files changed in this commit */}
                                        {detail.files && detail.files.length > 0 && (
                                          <div className="space-y-1">
                                            <p className="text-body text-[var(--color-stone)]/60 font-medium">
                                              {detail.files.length} file
                                              {detail.files.length !== 1 ? 's' : ''} changed
                                            </p>
                                            <div className="space-y-0.5">
                                              {detail.files.map((file) => (
                                                <div
                                                  key={file.file_path}
                                                  className="flex items-center gap-2 text-body"
                                                >
                                                  <span
                                                    className={cn(
                                                      'uppercase px-1 py-0.5 rounded font-medium text-body',
                                                      file.change_type === 'added' &&
                                                        'bg-[rgba(45,212,191,0.15)] text-[var(--color-jade)]',
                                                      file.change_type === 'modified' &&
                                                        'bg-[rgba(102,178,255,0.15)] text-[var(--color-sky)]',
                                                      file.change_type === 'deleted' &&
                                                        'bg-[rgba(199,62,58,0.15)] text-[var(--color-vermillion)]',
                                                      file.change_type === 'renamed' &&
                                                        'bg-[rgba(168,85,247,0.15)] text-purple-400'
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
                            <span className="text-body">
                              Branch merged into {detail?.source_branch || 'main'}
                            </span>
                            <span className="text-body mt-1 opacity-70">
                              Commit history no longer available
                            </span>
                          </>
                        ) : (
                          <span className="text-body">No commits on this branch</span>
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
                        {/* Summary header */}
                        <div className="flex items-center justify-between mb-3 pb-2 border-b border-[rgba(163,163,163,0.08)]">
                          <div className="flex items-center gap-2">
                            <FileCode className="w-3.5 h-3.5 text-[var(--color-sky)]" />
                            <span className="text-body text-[var(--color-paper)]/80">
                              {filesData.file_count} file{filesData.file_count !== 1 ? 's' : ''}{' '}
                              changed
                            </span>
                          </div>
                          <div className="flex items-center gap-3 text-body">
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
                        {/* Files list - expandable */}
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
                              {/* File header - clickable */}
                              <button
                                className="w-full flex items-center justify-between py-2 gap-3 text-left hover:bg-[var(--color-paper)]/5 transition-colors px-1 -mx-1 rounded"
                                onClick={() => handleExpandFile(file.file_path)}
                              >
                                {/* File path with change type indicator */}
                                <div className="flex items-center gap-2 min-w-0 flex-1">
                                  <ChevronRight
                                    className={cn(
                                      'w-3 h-3 text-[var(--color-stone)]/50 transition-transform shrink-0',
                                      isExpanded && 'rotate-90'
                                    )}
                                  />
                                  <span
                                    className={cn(
                                      'text-body uppercase px-1 py-0.5 rounded font-medium shrink-0',
                                      file.change_type === 'added' &&
                                        'bg-[rgba(45,212,191,0.15)] text-[var(--color-jade)]',
                                      file.change_type === 'modified' &&
                                        'bg-[rgba(102,178,255,0.15)] text-[var(--color-sky)]',
                                      file.change_type === 'deleted' &&
                                        'bg-[rgba(199,62,58,0.15)] text-[var(--color-vermillion)]',
                                      file.change_type === 'renamed' &&
                                        'bg-[rgba(168,85,247,0.15)] text-purple-400'
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
                                  <span className="text-body text-[var(--color-paper)]/80 truncate font-mono">
                                    {file.file_path}
                                  </span>
                                </div>
                                {/* Changes stats and bar */}
                                <div className="flex items-center gap-3 shrink-0">
                                  <div className="flex items-center gap-1.5 text-body min-w-[60px] justify-end">
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
                                  {/* Visual diff bar */}
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
                              {/* Expanded diff content */}
                              {isExpanded && (
                                <div className="ml-4 mb-2 border-l border-[rgba(163,163,163,0.15)] pl-3">
                                  {isLoading ? (
                                    <div className="py-2 flex items-center gap-2">
                                      <RotateCw className="w-3 h-3 animate-spin text-[var(--color-stone)]/50" />
                                      <span className="text-body text-[var(--color-stone)]/50">
                                        Loading diff...
                                      </span>
                                    </div>
                                  ) : diff?.diff ? (
                                    <pre className="text-mono text-body leading-relaxed whitespace-pre-wrap overflow-x-auto max-h-80 overflow-y-auto bg-[var(--color-void)]/50 rounded p-2">
                                      {diff.diff.split('\n').map((line, lineIdx) => {
                                        let lineClass = 'text-[var(--color-paper)]/60'
                                        if (line.startsWith('+') && !line.startsWith('+++')) {
                                          lineClass =
                                            'text-[var(--color-jade)] bg-[rgba(45,212,191,0.08)]'
                                        } else if (
                                          line.startsWith('-') &&
                                          !line.startsWith('---')
                                        ) {
                                          lineClass =
                                            'text-[var(--color-vermillion)] bg-[rgba(199,62,58,0.08)]'
                                        } else if (line.startsWith('@@')) {
                                          lineClass = 'text-purple-400'
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
                                    <div className="py-2 text-body text-[var(--color-stone)]/50 italic">
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
                            <span className="text-body">
                              Branch merged into {detail?.source_branch || 'main'}
                            </span>
                            <span className="text-body mt-1 opacity-70">
                              File changes no longer available
                            </span>
                          </>
                        ) : (
                          <span className="text-body">No files changed on this branch</span>
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
                        {/* Summary header */}
                        <div className="flex items-center justify-between mb-3 pb-2 border-b border-[rgba(163,163,163,0.08)]">
                          <div className="flex items-center gap-2">
                            <ImageIcon className="w-3.5 h-3.5 text-[var(--color-harvest)]" />
                            <span className="text-body text-[var(--color-paper)]/80">
                              {attachments.length} image{attachments.length !== 1 ? 's' : ''}{' '}
                              attached
                            </span>
                          </div>
                        </div>
                        {/* Image grid */}
                        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                          {attachments.map((image) => (
                            <div
                              key={image.id}
                              className="group relative rounded-sm overflow-hidden border border-[rgba(163,163,163,0.1)] bg-[var(--color-void)]"
                            >
                              <img
                                src={getImageFileUrl(image.id)}
                                alt={image.original_name}
                                className="w-full h-24 object-cover"
                              />
                              {/* Hover overlay with actions */}
                              <div className="absolute inset-0 bg-[var(--color-void)]/80 opacity-0 group-hover:opacity-100 transition-opacity flex flex-col items-center justify-center gap-2">
                                <a
                                  href={getImageFileUrl(image.id)}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="flex items-center gap-1.5 px-2 py-1 text-body uppercase tracking-widest text-[var(--color-paper)] bg-[var(--color-paper)]/10 rounded-sm hover:bg-[var(--color-paper)]/20 transition-colors"
                                >
                                  <Download className="w-3 h-3" />
                                  View
                                </a>
                              </div>
                              {/* File info footer */}
                              <div className="absolute bottom-0 left-0 right-0 px-2 py-1 bg-[var(--color-void)]/90">
                                <p className="text-body text-[var(--color-paper)]/80 truncate">
                                  {image.original_name}
                                </p>
                                <p className="text-body text-[var(--color-stone)]/60">
                                  {formatFileSize(image.size_bytes)}
                                </p>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <div className="flex flex-col items-center justify-center h-32 text-[var(--color-stone)]/50">
                        <ImageIcon className="w-6 h-6 mb-2 opacity-50" />
                        <span className="text-body">No images attached</span>
                      </div>
                    )}
                  </div>
                )}
                {activeTab === 'loop' && detail && (
                  <LoopProgressTab
                    run={detail}
                    onRunUpdated={(updatedRun) => {
                      setDetail(updatedRun)
                      onRunUpdated(updatedRun)
                    }}
                  />
                )}
              </div>
            </div>

            {/* Follow-up Section - Always visible except for cancelled runs */}
            {run && run.status !== 'cancelled' && (
              <div className="mt-4 p-3 bg-[var(--color-void)] border border-[rgba(163,163,163,0.1)] rounded-sm shrink-0">
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
                              className="flex-1 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.1)] rounded-sm px-2 py-1 text-body text-[var(--color-paper)] placeholder:text-[var(--color-stone)]/40 focus:outline-none focus:border-[rgba(163,163,163,0.2)] resize-none min-h-[32px]"
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
                              autoFocus
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
                            <p className="text-body text-[var(--color-sky)]/80 flex-1">
                              <span className="text-[var(--color-sky)]/60 uppercase tracking-widest text-[10px] mr-2">
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
                          <span className="text-body text-white font-bold">×</span>
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
                    onChange={(e) => setResumePrompt(e.target.value)}
                    onKeyDown={(e) => {
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
                      // Auto-resize textarea
                      const target = e.target as HTMLTextAreaElement
                      target.style.height = 'auto'
                      target.style.height = `${Math.min(target.scrollHeight, 128)}px`
                    }}
                  />
                  {isActive ? (
                    /* Active task - show Queue and Send Now buttons */
                    <div className="flex gap-1.5 shrink-0 self-start">
                      <button
                        className={cn(
                          'flex items-center justify-center rounded-sm text-body uppercase tracking-widest transition-colors',
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
                        <span className="hidden sm:inline">
                          {queuing ? 'Queueing...' : 'Queue'}
                        </span>
                      </button>
                      <button
                        className={cn(
                          'flex items-center justify-center rounded-sm text-body uppercase tracking-widest transition-colors',
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
                        'flex items-center justify-center rounded-sm text-body uppercase tracking-widest transition-colors shrink-0 self-start',
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
                      <span className="hidden sm:inline">
                        {resuming ? 'Resuming...' : 'Resume'}
                      </span>
                    </button>
                  )}
                </div>
                {resumeError && (
                  <p className="text-body text-[var(--color-vermillion)] mt-2">{resumeError}</p>
                )}
              </div>
            )}

            {/* Footer Meta - hidden on mobile */}
            {(detail?.session_id || detail?.input_tokens || detail?.model_used) && (
              <div className="mt-4 pt-3 border-t border-[rgba(163,163,163,0.06)] shrink-0 hidden sm:flex items-center justify-between">
                <span className="text-mono text-body text-[var(--color-stone)]/50">
                  {detail?.session_id ? `session ${detail.session_id.slice(0, 12)}` : ''}
                </span>
                <div className="flex items-center gap-4 text-mono text-body text-[var(--color-stone)]/50">
                  {(detail?.input_tokens || detail?.output_tokens) && (
                    <span>
                      {formatTokens(detail.input_tokens)} input →{' '}
                      {formatTokens(detail.output_tokens)} output
                    </span>
                  )}
                  {detail?.model_used && (
                    <span className="text-[var(--color-stone)]/40">{detail.model_used}</span>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Question Modal - renders when there are pending questions */}
        {pendingQuestions.some((q) => q.status === 'pending') && run && (
          <QuestionModal
            runId={run.id}
            questions={pendingQuestions}
            onAnswer={handleAnswerQuestion}
            onClose={() => {
              // Refresh questions to see if all are answered
              fetchRunQuestions(run.id).then((response) => {
                setPendingQuestions(response.questions)
              })
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
