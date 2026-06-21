import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Run } from '@/lib/types'
import { useRunResume } from './useRunResume'

vi.mock('@/lib/api', () => ({
  cancelRun: vi.fn(),
  resumeRun: vi.fn(),
  queueFollowup: vi.fn(),
  uploadAndAttachImage: vi.fn(),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { toast } from 'sonner'
import { cancelRun, queueFollowup, resumeRun } from '@/lib/api'

const RUN = { id: 'run-123' } as Run

function makeOptions(overrides: Partial<Parameters<typeof useRunResume>[0]> = {}) {
  return {
    run: RUN,
    resumePrompt: 'do more work',
    setResumePrompt: vi.fn(),
    resumePendingImages: [],
    setResumePendingImages: vi.fn(),
    resumeTextareaRef: { current: null },
    setResuming: vi.fn(),
    setQueuing: vi.fn(),
    setResumeError: vi.fn(),
    onRefresh: vi.fn(),
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useRunResume.handleResume', () => {
  it('resumes, clears the prompt, refreshes', async () => {
    vi.mocked(resumeRun).mockResolvedValue({ run_id: 'run-123' } as never)
    const opts = makeOptions()
    const { result } = renderHook(() => useRunResume(opts))

    await result.current.handleResume()

    expect(resumeRun).toHaveBeenCalledWith('run-123', 'do more work')
    expect(opts.setResumePrompt).toHaveBeenCalledWith('')
    expect(opts.onRefresh).toHaveBeenCalledOnce()
    expect(opts.setResuming).toHaveBeenLastCalledWith(false)
  })

  it('sets resumeError on failure', async () => {
    vi.mocked(resumeRun).mockRejectedValue(new Error('boom'))
    const opts = makeOptions()
    const { result } = renderHook(() => useRunResume(opts))

    await result.current.handleResume()

    expect(opts.setResumeError).toHaveBeenLastCalledWith('boom')
    expect(opts.onRefresh).not.toHaveBeenCalled()
  })

  it('no-ops when prompt is blank', async () => {
    const opts = makeOptions({ resumePrompt: '   ' })
    const { result } = renderHook(() => useRunResume(opts))
    await result.current.handleResume()
    expect(resumeRun).not.toHaveBeenCalled()
  })
})

describe('useRunResume.handleQueueFollowup', () => {
  it('queued: toasts, clears prompt, refreshes', async () => {
    vi.mocked(queueFollowup).mockResolvedValue({ action: 'queued' } as never)
    const opts = makeOptions()
    const { result } = renderHook(() => useRunResume(opts))

    await result.current.handleQueueFollowup()

    expect(queueFollowup).toHaveBeenCalledWith('run-123', 'do more work')
    expect(toast.success).toHaveBeenCalled()
    expect(opts.setResumePrompt).toHaveBeenCalledWith('')
    expect(opts.onRefresh).toHaveBeenCalledOnce()
    expect(opts.setQueuing).toHaveBeenLastCalledWith(false)
  })

  it('resume_now: delegates to handleResume (resumeRun, not just queue)', async () => {
    vi.mocked(queueFollowup).mockResolvedValue({ action: 'resume_now' } as never)
    vi.mocked(resumeRun).mockResolvedValue({ run_id: 'run-123' } as never)
    const opts = makeOptions()
    const { result } = renderHook(() => useRunResume(opts))

    await result.current.handleQueueFollowup()

    expect(resumeRun).toHaveBeenCalledWith('run-123', 'do more work')
    expect(toast.success).not.toHaveBeenCalled()
  })
})

describe('useRunResume.handleSendNow', () => {
  it('cancels then resumes then refreshes', async () => {
    vi.mocked(cancelRun).mockResolvedValue({} as never)
    vi.mocked(resumeRun).mockResolvedValue({ run_id: 'run-123' } as never)
    const opts = makeOptions()
    const { result } = renderHook(() => useRunResume(opts))

    await result.current.handleSendNow()

    expect(cancelRun).toHaveBeenCalledWith('run-123')
    expect(resumeRun).toHaveBeenCalledWith('run-123', 'do more work')
    expect(opts.onRefresh).toHaveBeenCalledOnce()
    expect(opts.setResuming).toHaveBeenLastCalledWith(false)
  })

  it('no-ops when run is null', async () => {
    const opts = makeOptions({ run: null })
    const { result } = renderHook(() => useRunResume(opts))
    await result.current.handleSendNow()
    expect(cancelRun).not.toHaveBeenCalled()
  })
})
