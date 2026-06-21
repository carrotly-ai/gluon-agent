import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Run, RunDetail } from '@/lib/types'
import { useRunActions } from './useRunActions'

// Mock the API module and toast so the hook's side effects are observable.
vi.mock('@/lib/api', () => ({
  cancelRun: vi.fn(),
  createPrForRun: vi.fn(),
  fetchRun: vi.fn(),
  mergeRunBranch: vi.fn(),
  deleteQueuedMessage: vi.fn(),
  editQueuedMessage: vi.fn(),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { toast } from 'sonner'
import {
  cancelRun,
  createPrForRun,
  deleteQueuedMessage,
  editQueuedMessage,
  fetchRun,
  mergeRunBranch,
} from '@/lib/api'

const RUN = { id: 'run-123' } as Run
const DETAIL = { id: 'run-123', pr_number: 42, pr_url: 'https://x/pr/42' } as RunDetail

function makeOptions(overrides: Partial<Parameters<typeof useRunActions>[0]> = {}) {
  return {
    run: RUN,
    setDetail: vi.fn(),
    setCancelling: vi.fn(),
    setCreatingPr: vi.fn(),
    setPrError: vi.fn(),
    setMerging: vi.fn(),
    setMergeError: vi.fn(),
    setResumePrompt: vi.fn(),
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useRunActions.handleCancel', () => {
  it('dialog variant: cancels, reports up, and toasts (no setRun)', async () => {
    vi.mocked(cancelRun).mockResolvedValue({ id: 'run-123' } as Run)
    const onRunUpdated = vi.fn()
    const opts = makeOptions({ onRunUpdated, cancelToasts: true })
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleCancel()

    expect(cancelRun).toHaveBeenCalledWith('run-123')
    expect(onRunUpdated).toHaveBeenCalledOnce()
    expect(toast.success).toHaveBeenCalledWith('Run cancelled')
    expect(opts.setCancelling).toHaveBeenNthCalledWith(1, true)
    expect(opts.setCancelling).toHaveBeenLastCalledWith(false)
  })

  it('page variant: cancels via setRun, optional callback, NO toast', async () => {
    vi.mocked(cancelRun).mockResolvedValue({ id: 'run-123' } as Run)
    const setRun = vi.fn()
    const onRunUpdated = vi.fn()
    const opts = makeOptions({ setRun, onRunUpdated, cancelToasts: false })
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleCancel()

    expect(setRun).toHaveBeenCalledOnce()
    expect(onRunUpdated).toHaveBeenCalledOnce()
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('toasts an error on failure only when cancelToasts is set', async () => {
    vi.mocked(cancelRun).mockRejectedValue(new Error('boom'))
    const opts = makeOptions({ cancelToasts: true })
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleCancel()

    expect(toast.error).toHaveBeenCalledWith('Failed to cancel run')
    expect(opts.setCancelling).toHaveBeenLastCalledWith(false)
  })

  it('no-ops when run is null', async () => {
    const opts = makeOptions({ run: null })
    const { result } = renderHook(() => useRunActions(opts))
    await result.current.handleCancel()
    expect(cancelRun).not.toHaveBeenCalled()
    expect(opts.setCancelling).not.toHaveBeenCalled()
  })
})

describe('useRunActions.handleCreatePr', () => {
  it('success: refreshes detail, reports up, and toasts (both variants)', async () => {
    vi.mocked(createPrForRun).mockResolvedValue({
      success: true,
      pr_url: 'https://x/pr/42',
    } as never)
    vi.mocked(fetchRun).mockResolvedValue(DETAIL)
    const setRun = vi.fn()
    const onRunUpdated = vi.fn()
    const opts = makeOptions({ setRun, onRunUpdated })
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleCreatePr()

    expect(opts.setPrError).toHaveBeenNthCalledWith(1, null)
    expect(fetchRun).toHaveBeenCalledWith('run-123')
    expect(opts.setDetail).toHaveBeenCalledWith(DETAIL)
    expect(setRun).toHaveBeenCalledWith(DETAIL)
    expect(onRunUpdated).toHaveBeenCalledWith(DETAIL)
    expect(toast.success).toHaveBeenCalled()
    expect(opts.setCreatingPr).toHaveBeenLastCalledWith(false)
  })

  it('sets prError when the API reports failure', async () => {
    vi.mocked(createPrForRun).mockResolvedValue({ success: false, error: 'no remote' } as never)
    const opts = makeOptions()
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleCreatePr()

    expect(opts.setPrError).toHaveBeenLastCalledWith('no remote')
    expect(fetchRun).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('sets prError on a thrown error', async () => {
    vi.mocked(createPrForRun).mockRejectedValue(new Error('network'))
    const opts = makeOptions()
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleCreatePr()

    expect(opts.setPrError).toHaveBeenLastCalledWith('network')
    expect(opts.setCreatingPr).toHaveBeenLastCalledWith(false)
  })
})

describe('useRunActions.handleMerge', () => {
  it('success: refreshes detail, reports up, toasts (both variants)', async () => {
    vi.mocked(mergeRunBranch).mockResolvedValue({ success: true } as never)
    vi.mocked(fetchRun).mockResolvedValue(DETAIL)
    const setRun = vi.fn()
    const onRunUpdated = vi.fn()
    const opts = makeOptions({ setRun, onRunUpdated, sourceBranch: 'develop' })
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleMerge()

    expect(opts.setDetail).toHaveBeenCalledWith(DETAIL)
    expect(setRun).toHaveBeenCalledWith(DETAIL)
    expect(onRunUpdated).toHaveBeenCalledWith(DETAIL)
    expect(toast.success).toHaveBeenCalledWith(
      'Branch merged successfully',
      expect.objectContaining({ description: 'Merged into develop' })
    )
    expect(opts.setMergeError).toHaveBeenNthCalledWith(1, null)
    expect(opts.setMerging).toHaveBeenLastCalledWith(false)
  })

  it('conflict: sets resume prompt + error and fires onMergeConflict (dialog-only)', async () => {
    vi.mocked(mergeRunBranch).mockResolvedValue({
      success: false,
      has_conflicts: true,
      conflicting_files: ['a.ts', 'b.ts'],
    } as never)
    const onMergeConflict = vi.fn()
    const opts = makeOptions({ onMergeConflict, sourceBranch: 'main' })
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleMerge()

    const prompt = vi.mocked(opts.setResumePrompt).mock.calls[0][0] as string
    expect(prompt).toContain('a.ts')
    expect(prompt).toContain('git merge main')
    expect(opts.setMergeError).toHaveBeenLastCalledWith(expect.stringContaining('2 file(s)'))
    expect(onMergeConflict).toHaveBeenCalledOnce()
    expect(fetchRun).not.toHaveBeenCalled()
  })

  it('conflict without onMergeConflict (page variant) does not throw', async () => {
    vi.mocked(mergeRunBranch).mockResolvedValue({
      success: false,
      has_conflicts: true,
      conflicting_files: ['x.ts'],
    } as never)
    const opts = makeOptions()
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleMerge()
    expect(opts.setResumePrompt).toHaveBeenCalledOnce()
  })

  it('plain failure: sets mergeError, no resume prompt', async () => {
    vi.mocked(mergeRunBranch).mockResolvedValue({ success: false, error: 'merge blocked' } as never)
    const opts = makeOptions()
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleMerge()
    expect(opts.setMergeError).toHaveBeenLastCalledWith('merge blocked')
    expect(opts.setResumePrompt).not.toHaveBeenCalled()
  })

  it('no-ops when run is null', async () => {
    const opts = makeOptions({ run: null })
    const { result } = renderHook(() => useRunActions(opts))
    await result.current.handleMerge()
    expect(mergeRunBranch).not.toHaveBeenCalled()
  })
})

describe('useRunActions queued-message handlers', () => {
  it('handleDeleteQueuedMessage deletes then refreshes', async () => {
    vi.mocked(deleteQueuedMessage).mockResolvedValue(undefined as never)
    const onRefresh = vi.fn()
    const opts = makeOptions({ onRefresh })
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleDeleteQueuedMessage('msg-1')

    expect(deleteQueuedMessage).toHaveBeenCalledWith('run-123', 'msg-1')
    expect(onRefresh).toHaveBeenCalledOnce()
  })

  it('handleDeleteQueuedMessage toasts on error', async () => {
    vi.mocked(deleteQueuedMessage).mockRejectedValue(new Error('nope'))
    const onRefresh = vi.fn()
    const opts = makeOptions({ onRefresh })
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleDeleteQueuedMessage('msg-1')

    expect(toast.error).toHaveBeenCalledWith('nope')
    expect(onRefresh).not.toHaveBeenCalled()
  })

  it('handleEditQueuedMessage edits, clears editing state, refreshes', async () => {
    vi.mocked(editQueuedMessage).mockResolvedValue(undefined as never)
    const onRefresh = vi.fn()
    const setEditingMessageId = vi.fn()
    const setEditingMessageText = vi.fn()
    const opts = makeOptions({ onRefresh, setEditingMessageId, setEditingMessageText })
    const { result } = renderHook(() => useRunActions(opts))

    await result.current.handleEditQueuedMessage('msg-1', '  new text  ')

    expect(editQueuedMessage).toHaveBeenCalledWith('run-123', 'msg-1', 'new text')
    expect(setEditingMessageId).toHaveBeenCalledWith(null)
    expect(setEditingMessageText).toHaveBeenCalledWith('')
    expect(onRefresh).toHaveBeenCalledOnce()
  })

  it('handleEditQueuedMessage no-ops on empty text', async () => {
    const opts = makeOptions()
    const { result } = renderHook(() => useRunActions(opts))
    await result.current.handleEditQueuedMessage('msg-1', '   ')
    expect(editQueuedMessage).not.toHaveBeenCalled()
  })
})
