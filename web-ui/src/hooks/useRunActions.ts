import { toast } from 'sonner'
import { cancelRun, createPrForRun, fetchRun } from '@/lib/api'
import type { Run, RunDetail } from '@/lib/types'

/**
 * Options for {@link useRunActions}. RunDetailDialog and RunDetailPage share
 * these action handlers but diverge in a few ways, each captured as an option
 * so every call site keeps its exact current behavior:
 *  - the Page owns its own `run` state (`setRun`); the Dialog receives it as a prop
 *  - `onRunUpdated` is required in the Dialog, optional on the Page
 *  - the Dialog shows a sonner toast on cancel; the Page does not
 *    (the create-PR success toast fires in BOTH variants, so it is unconditional)
 */
export interface UseRunActionsOptions {
  run: Run | null
  onRunUpdated?: (run: Run) => void
  setRun?: (run: Run) => void
  setDetail: (detail: RunDetail) => void
  setCancelling: (value: boolean) => void
  setCreatingPr: (value: boolean) => void
  setPrError: (value: string | null) => void
  cancelToasts?: boolean
}

export interface RunActions {
  handleCancel: () => Promise<void>
  handleCreatePr: () => Promise<void>
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

  return { handleCancel, handleCreatePr }
}
