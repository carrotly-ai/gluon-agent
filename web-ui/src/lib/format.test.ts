import { describe, expect, it } from 'vitest'
import { formatCost, formatDuration, formatTokens } from '@/lib/format'

// Smoke coverage for the shared formatters — also proves the `@/` alias and
// module resolution work under the vitest harness.
describe('formatDuration', () => {
  it('returns a dash for null/undefined', () => {
    expect(formatDuration(null)).toBe('-')
    expect(formatDuration(undefined)).toBe('-')
  })

  it('formats sub-minute as seconds', () => {
    expect(formatDuration(42)).toBe('42s')
  })

  it('formats minutes and hours', () => {
    expect(formatDuration(90)).toBe('1m 30s')
    expect(formatDuration(3700)).toBe('1h 1m')
  })
})

describe('formatCost', () => {
  it('handles zero and small/large values', () => {
    expect(formatCost(null)).toBe('-')
    expect(formatCost(0)).toBe('$0.00')
    expect(formatCost(0.0001)).toBe('$0.0001')
    expect(formatCost(1.5)).toBe('$1.50')
  })
})

describe('formatTokens', () => {
  it('compacts large counts', () => {
    expect(formatTokens(987)).toBe('987')
    expect(formatTokens(12_300)).toBe('12.3k')
    expect(formatTokens(1_420_000)).toBe('1.42M')
  })
})
