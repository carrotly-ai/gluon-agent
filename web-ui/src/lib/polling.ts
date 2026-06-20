/**
 * Named polling intervals (milliseconds).
 *
 * Page-level list views refresh their data on a timer. Centralising the cadence
 * here keeps the choices consistent and tunable from one place instead of being
 * scattered as magic numbers across components.
 *
 * Tiers:
 *  - POLL_NORMAL — default for most list pages
 *  - POLL_SLOW   — low-churn views where staleness is cheap
 *
 * Functional timers (countdown ticks, WebSocket reconnect backoff, run-detail
 * live polling) are intentionally NOT covered here — they're not list-refresh
 * cadences and have their own semantics.
 */
export const POLL_NORMAL = 10000
export const POLL_SLOW = 30000
