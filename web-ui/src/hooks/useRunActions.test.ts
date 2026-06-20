import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Run, RunDetail } from '@/lib/types'
import { useRunActions } from './useRunActions'

// Mock the API module and toast so the hook's side effects are observable.
vi.mock('@/lib/api', () => ({
  cancelRun: vi.fn(),
  createPrForRun: vi.fn(),
  fetchRun: vi.fn(),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { toast } from 'sonner'
import { cancelRun, createPrForRun, fetchRun } from '@/lib/api'

const RUN = { id: 'run-123' } as Run
const DETAIL = { id: 'run-123', pr_number: 42, pr_url: 'https://x/pr/42' } as RunDetail

function makeOptions(overrides: Partial<Parameters<typeof useRunActions>[0]> = {}) {
  return {
    run: RUN,
    setDetail: vi.fn(),
    setCancelling: vi.fn(),
    setCreatingPr: vi.fn(),
    setPrError: vi.fn(),
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
