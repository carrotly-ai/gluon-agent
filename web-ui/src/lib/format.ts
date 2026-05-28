/**
 * Shared formatting helpers used across the dashboard.
 *
 * Consolidated here to stop the four-way drift of `formatDuration` (was
 * duplicated in RunDetailDialog, RunDetailPage, LoopProgressTab, and
 * StreamingLogViewer). New callers should import from here.
 *
 * TODO(stream-followup): migrate the remaining copies in RunDetailDialog.tsx,
 * RunDetailPage.tsx, LoopProgressTab.tsx, and StreamingLogViewer.tsx
 * (formatDurationMs) to this module. Owned by other streams in Wave 1 so
 * they're out of scope here.
 */

/**
 * Format a duration in seconds into a compact human-readable string.
 * - `null` / undefined  → `'-'`
 * - <60s                → `42s`
 * - <1h                 → `5m 12s`
 * - >=1h                → `2h 14m`
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

/**
 * Format milliseconds. Same shape as `formatDuration` but accepts ms — used by
 * streaming log viewers that already have ms-resolution deltas.
 */
export function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '-'
  return formatDuration(ms / 1000)
}

/**
 * Format a USD cost. Mirrors the inline helpers in UsagePage / RunCard.
 * - null / undefined → `'-'`
 * - 0                → `'$0.00'`
 * - <0.01            → 4 decimal places
 * - otherwise        → 2 decimal places
 */
export function formatCost(cost: number | null | undefined): string {
  if (cost === null || cost === undefined) return '-'
  if (cost === 0) return '$0.00'
  if (cost < 0.01) return `$${cost.toFixed(4)}`
  return `$${cost.toFixed(2)}`
}

/**
 * Format a raw token count into a compact label.
 * - <1k    → `987`
 * - <1M    → `12.3k`
 * - >=1M   → `1.42M`
 */
export function formatTokens(tokens: number | null | undefined): string {
  if (tokens === null || tokens === undefined) return '-'
  if (tokens < 1000) return `${tokens}`
  if (tokens < 1_000_000) return `${(tokens / 1000).toFixed(1)}k`
  return `${(tokens / 1_000_000).toFixed(2)}M`
}

/**
 * Deterministic colour for a project name — used for project chips, chart
 * series, etc. Replaces the previous `name.charCodeAt(0) * 137 % 360` hash
 * which collided heavily on the first character (every project starting with
 * the same letter would get the same hue).
 *
 * Uses an FNV-1a hash over the full name, then maps into one of 12 evenly-
 * distributed hues. Consecutive characters affect the output, so projects
 * like `gluon-agent` and `gluon-bot` map to different colours.
 *
 * Stable across renders/sessions.
 */
const PROJECT_PALETTE_HUES = [
  210, // sky
  150, // jade
  35, // harvest
  340, // rose
  270, // violet
  185, // teal
  110, // moss
  20, // ember
  300, // orchid
  60, // citron
  240, // indigo
  0, // vermillion
]

export function projectHue(name: string): number {
  if (!name) return PROJECT_PALETTE_HUES[0]
  // FNV-1a, 32-bit
  let hash = 0x811c9dc5
  for (let i = 0; i < name.length; i++) {
    hash ^= name.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  // Use the unsigned-shifted value modulo palette length.
  const idx = (hash >>> 0) % PROJECT_PALETTE_HUES.length
  return PROJECT_PALETTE_HUES[idx]
}

/** Convenience: HSL string for a project's swatch dot. */
export function projectColor(name: string, saturation = 55, lightness = 55): string {
  return `hsl(${projectHue(name)}, ${saturation}%, ${lightness}%)`
}
