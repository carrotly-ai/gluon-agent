import { toast } from 'sonner'
import { cancelRun, createPrForRun, fetchRun, mergeRunBranch } from '@/lib/api'
import type { Run, RunDetail } from '@/lib/types'

/**
 * Options for {@link useRunActions}. RunDetailDialog and RunDetailPage share
 * these action handlers but diverge in a few ways, each captured as an option
 * so every call site keeps its exact current behavior:
 *  - the Page owns its own `run` state (`setRun`); the Dialog receives it as a prop
 *  - `onRunUpdated` is required in the Dialog, optional on the Page
 *  - the Dialog shows a sonner toast on cancel; the Page does not
 *    (the create-PR and merge toasts fire in BOTH variants, so they are unconditional)
 *  - on a merge conflict the Dialog scrolls to the resume box; the Page does not
 *    (passed as the optional `onMergeConflict` callback)
 */
export interface UseRunActionsOptions {
  run: Run | null
  onRunUpdated?: (run: Run) => void
  setRun?: (run: Run) => void
  setDetail: (detail: RunDetail) => void
  setCancelling: (value: boolean) => void
  setCreatingPr: (value: boolean) => void
  setPrError: (value: string | null) => void
  setMerging: (value: boolean) => void
  setMergeError: (value: string | null) => void
  setResumePrompt: (value: string) => void
  /** `detail?.source_branch` — used in the merge toast and conflict prompt. */
  sourceBranch?: string | null
  /** Dialog-only: scroll to the resume box after a merge conflict is detected. */
  onMergeConflict?: () => void
  cancelToasts?: boolean
}

export interface RunActions {
  handleCancel: () => Promise<void>
  handleCreatePr: () => Promise<void>
  handleMerge: () => Promise<void>
}

/**
 * Shared run-action handlers extracted from RunDetailDialog and RunDetailPage
 * (#165). Behavior-identical to the previous inline copies — the per-variant
 * differences are passed via {@link UseRunActionsOptions}.
 */
export function useRunActions(options: UseRunActionsOptions): RunActions {
  const {
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
    sourceBranch,
    onMergeConflict,
    cancelToasts,
  } = options

  const handleCancel = async (): Promise<void> => {
    if (!run) return
    setCancelling(true)
    try {
      const updated = await cancelRun(run.id)
      setRun?.(updated)
      onRunUpdated?.(updated)
      if (cancelToasts) toast.success('Run cancelled')
    } catch (err) {
      console.error('Failed to cancel run:', err)
      if (cancelToasts) toast.error('Failed to cancel run')
    } finally {
      setCancelling(false)
    }
  }

  const handleCreatePr = async (): Promise<void> => {
    if (!run) return
    setCreatingPr(true)
    setPrError(null)
    try {
      const result = await createPrForRun(run.id)
      if (result.success && result.pr_url) {
        const updatedDetail = await fetchRun(run.id)
        setDetail(updatedDetail)
        setRun?.(updatedDetail)
        onRunUpdated?.(updatedDetail)
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

  const handleMerge = async (): Promise<void> => {
    if (!run) return
    setMerging(true)
    setMergeError(null)
    try {
      const result = await mergeRunBranch(run.id)
      if (result.success) {
        const updatedDetail = await fetchRun(run.id)
        setDetail(updatedDetail)
        setRun?.(updatedDetail)
        onRunUpdated?.(updatedDetail)
        toast.success('Branch merged successfully', {
          description: `Merged into ${sourceBranch || 'main'}`,
        })
      } else if (
        result.has_conflicts &&
        result.conflicting_files &&
        result.conflicting_files.length > 0
      ) {
        const filesStr = result.conflicting_files.slice(0, 10).join('\n- ')
        const moreCount =
          result.conflicting_files.length > 10 ? result.conflicting_files.length - 10 : 0
        const conflictPrompt = `The merge has conflicts that need to be resolved. Please fix these merge conflicts:

Conflicting files:
- ${filesStr}${moreCount > 0 ? `\n- ... and ${moreCount} more files` : ''}

Steps to resolve:
1. In the worktree, run: git merge ${sourceBranch || 'main'}
2. Resolve each conflict by understanding both changes and merging them appropriately
3. After resolving all conflicts, commit the merge
4. Push the changes

Focus on preserving functionality from both sides where possible.`

        setResumePrompt(conflictPrompt)
        setMergeError(
          `Merge conflicts in ${result.conflicting_files.length} file(s). Use the resume prompt below to have Claude resolve them.`
        )
        onMergeConflict?.()
      } else {
        setMergeError(result.error || 'Failed to merge branch')
      }
    } catch (err) {
      setMergeError(err instanceof Error ? err.message : 'Failed to merge branch')
    } finally {
      setMerging(false)
    }
  }

  return { handleCancel, handleCreatePr, handleMerge }
}
