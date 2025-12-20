/**
 * Timezone-aware timestamp utilities.
 * All timestamps from the backend are UTC-encoded ISO strings.
 * This module provides utilities to properly handle them in the frontend.
 */

/**
 * Parse a UTC ISO timestamp string and return a Date object.
 * Handles ISO strings without explicit timezone (assumes UTC).
 *
 * @param isoString - ISO 8601 timestamp (e.g., "2024-12-11T12:20:15.123456+00:00" or "2024-12-11T12:20:15")
 * @returns Date object in browser's local timezone
 */
export function parseUtcTimestamp(isoString: string | null): Date | null {
  if (!isoString) return null
  // If the ISO string already has timezone info (+00:00 or Z), Date will parse it correctly
  // If it doesn't have timezone info, we need to treat it as UTC
  const date = new Date(isoString)
  // Date constructor parses strings with explicit timezone correctly,
  // but for naive strings it might interpret as local time. To be safe,
  // we ensure UTC interpretation by checking if string has timezone info
  if (!isoString.includes('+') && !isoString.includes('Z') && !isoString.includes('-00:00')) {
    // No timezone info - ISO 8601 basic format without Z. Treat as UTC.
    // The backend now sends UTC timestamps, so we add Z to ensure correct parsing
    const timestamp = new Date(`${isoString}Z`)
    return timestamp
  }
  return date
}

/**
 * Format a relative time string (e.g., "5m ago", "2h ago").
 *
 * @param dateStr - ISO 8601 timestamp string or null
 * @returns Relative time string
 */
export function formatRelativeTime(dateStr: string | null): string {
  const date = parseUtcTimestamp(dateStr)
  if (!date) return ''

  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  const diffMin = Math.floor(diffSec / 60)
  const diffHour = Math.floor(diffMin / 60)
  const diffDay = Math.floor(diffHour / 24)

  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  if (diffHour < 24) return `${diffHour}h ago`
  if (diffDay < 7) return `${diffDay}d ago`
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

/**
 * Format a full date and time string.
 *
 * @param dateStr - ISO 8601 timestamp string or null
 * @returns Formatted date/time string
 */
export function formatFullDateTime(dateStr: string | null): string {
  const date = parseUtcTimestamp(dateStr)
  if (!date) return ''

  return date.toLocaleString([], {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Format time only.
 *
 * @param dateStr - ISO 8601 timestamp string or null
 * @returns Formatted time string
 */
export function formatTime(dateStr: string | null): string {
  const date = parseUtcTimestamp(dateStr)
  if (!date) return '-'

  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Format date only.
 *
 * @param dateStr - ISO 8601 timestamp string or null
 * @returns Formatted date string
 */
export function formatDate(dateStr: string | null): string {
  const date = parseUtcTimestamp(dateStr)
  if (!date) return '-'

  return date.toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
  })
}

/**
 * Check if a timestamp is today.
 *
 * @param dateStr - ISO 8601 timestamp string or null
 * @returns true if the timestamp is today
 */
export function isToday(dateStr: string | null): boolean {
  const date = parseUtcTimestamp(dateStr)
  if (!date) return false

  const today = new Date()
  return date.toDateString() === today.toDateString()
}

/**
 * Format date with relative context (e.g., "Today, 14:30" or "Dec 11, 14:30").
 *
 * @param dateStr - ISO 8601 timestamp string or null
 * @returns Formatted date string with relative context
 */
export function formatDateWithContext(dateStr: string | null): string {
  if (!dateStr) return '-'

  if (isToday(dateStr)) {
    return `Today, ${formatTime(dateStr)}`
  }

  return `${formatDate(dateStr)}, ${formatTime(dateStr)}`
}

/**
 * Format a message timestamp for log/chat display (HH:MM:SS).
 *
 * @param dateStr - ISO 8601 timestamp string or null
 * @returns Formatted time string with seconds
 */
export function formatMessageTime(dateStr: string | null): string {
  const date = parseUtcTimestamp(dateStr)
  if (!date) return ''

  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}
