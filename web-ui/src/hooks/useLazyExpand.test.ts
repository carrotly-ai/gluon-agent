import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useLazyExpand } from './useLazyExpand'

function makeOpts<T>(overrides: Partial<Parameters<typeof useLazyExpand<T>>[0]> = {}) {
  return {
    expanded: null,
    setExpanded: vi.fn(),
    cache: {} as Record<string, T>,
    setCache: vi.fn(),
    load: vi.fn(),
    setLoading: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  }
}

describe('useLazyExpand', () => {
  it('collapses when the same key is re-clicked (no load)', async () => {
    const opts = makeOpts({ expanded: 'k1' })
    const { result } = renderHook(() => useLazyExpand(opts))
    await result.current('k1')
    expect(opts.setExpanded).toHaveBeenCalledWith(null)
    expect(opts.load).not.toHaveBeenCalled()
  })

  it('expands and loads on first open, caching the result', async () => {
    const opts = makeOpts<{ d: string }>({ load: vi.fn().mockResolvedValue({ d: 'detail' }) })
    const { result } = renderHook(() => useLazyExpand(opts))
    await result.current('k2')

    expect(opts.setExpanded).toHaveBeenCalledWith('k2')
    expect(opts.setLoading).toHaveBeenNthCalledWith(1, 'k2')
    expect(opts.load).toHaveBeenCalledWith('k2')
    // setCache called with an updater that adds k2
    const updater = vi.mocked(opts.setCache).mock.calls[0][0]
    expect(updater({})).toEqual({ k2: { d: 'detail' } })
    expect(opts.setLoading).toHaveBeenLastCalledWith(null)
  })

  it('skips loading when the key is already cached', async () => {
    const opts = makeOpts<number>({ cache: { k3: 7 } })
    const { result } = renderHook(() => useLazyExpand(opts))
    await result.current('k3')
    expect(opts.setExpanded).toHaveBeenCalledWith('k3')
    expect(opts.load).not.toHaveBeenCalled()
  })

  it('skips loading when disabled (mirrors the run guard), but still expands', async () => {
    const opts = makeOpts({ enabled: false })
    const { result } = renderHook(() => useLazyExpand(opts))
    await result.current('k4')
    expect(opts.setExpanded).toHaveBeenCalledWith('k4')
    expect(opts.load).not.toHaveBeenCalled()
    expect(opts.setLoading).not.toHaveBeenCalled()
  })

  it('routes a load failure to onError and clears the loading indicator', async () => {
    const opts = makeOpts({ load: vi.fn().mockRejectedValue(new Error('boom')) })
    const { result } = renderHook(() => useLazyExpand(opts))
    await result.current('k5')
    expect(opts.onError).toHaveBeenCalledOnce()
    expect(opts.setCache).not.toHaveBeenCalled()
    expect(opts.setLoading).toHaveBeenLastCalledWith(null)
  })
})
