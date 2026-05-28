import { useEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'

interface SnoozePopoverProps {
  /** When true, popover is shown. */
  open: boolean
  /** Anchor element (popover renders below it). */
  anchorRect: DOMRect | null
  /** Called with an ISO datetime string (or null to clear) when user picks. */
  onPick: (until: string | null) => void
  /** Called when the user clicks outside or presses Esc. */
  onClose: () => void
}

interface SnoozeOption {
  label: string
  /** Compute the ISO datetime relative to now. */
  compute: () => Date
}

const OPTIONS: SnoozeOption[] = [
  {
    label: '1 hour',
    compute: () => new Date(Date.now() + 60 * 60 * 1000),
  },
  {
    label: 'Tomorrow morning',
    compute: () => {
      const d = new Date()
      d.setDate(d.getDate() + 1)
      d.setHours(9, 0, 0, 0)
      return d
    },
  },
  {
    label: 'Monday',
    compute: () => {
      const d = new Date()
      const day = d.getDay() // 0 = Sun, 1 = Mon
      const delta = day === 1 ? 7 : (8 - day) % 7 || 7
      d.setDate(d.getDate() + delta)
      d.setHours(9, 0, 0, 0)
      return d
    },
  },
  {
    label: 'Next week',
    compute: () => new Date(Date.now() + 7 * 24 * 60 * 60 * 1000),
  },
]

/**
 * Snooze the active run for a preset duration or a custom date.
 *
 * Pure presentation — caller handles the API call. Closes on outside click,
 * Esc, or after a pick. Includes an "Unsnooze" row when the row is currently
 * snoozed (caller can choose to render it via the `currentlySnoozed` prop,
 * surfaced as a separate "Wake up now" choice).
 */
export function SnoozePopover({ open, anchorRect, onPick, onClose }: SnoozePopoverProps) {
  const popoverRef = useRef<HTMLDivElement>(null)
  const customInputRef = useRef<HTMLInputElement>(null)
  const [customMode, setCustomMode] = useState(false)
  const [customValue, setCustomValue] = useState('')

  // Flat row order for ArrowUp/Down navigation:
  // 0..3 = preset options, 4 = "Pick a date…", 5 = "Wake up now"
  const TOTAL_ROWS = OPTIONS.length + 2
  const [activeRow, setActiveRow] = useState(0)

  // Reset highlight whenever the popover opens.
  useEffect(() => {
    if (open) setActiveRow(0)
  }, [open])

  // Focus the date input once it appears — replaces auto-focus (a11y rule).
  useEffect(() => {
    if (customMode) customInputRef.current?.focus()
  }, [customMode])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
        return
      }
      // Skip arrow nav while typing into the custom date input.
      if (customMode) return
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          setActiveRow((r) => Math.min(r + 1, TOTAL_ROWS - 1))
          break
        case 'ArrowUp':
          e.preventDefault()
          setActiveRow((r) => Math.max(r - 1, 0))
          break
        case 'Home':
          e.preventDefault()
          setActiveRow(0)
          break
        case 'End':
          e.preventDefault()
          setActiveRow(TOTAL_ROWS - 1)
          break
        case 'Enter':
          e.preventDefault()
          if (activeRow < OPTIONS.length) {
            onPick(OPTIONS[activeRow].compute().toISOString())
          } else if (activeRow === OPTIONS.length) {
            setCustomMode(true)
          } else {
            onPick(null)
          }
          break
      }
    }
    const onClick = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [open, onClose, activeRow, customMode, onPick, TOTAL_ROWS])

  useEffect(() => {
    if (!open) setCustomMode(false)
  }, [open])

  if (!open || !anchorRect) return null

  // Position below anchor; clamp horizontally so we don't overflow.
  const top = anchorRect.bottom + 4
  const maxLeft = window.innerWidth - 240 - 8
  const left = Math.min(anchorRect.left, maxLeft)

  return (
    <div
      ref={popoverRef}
      role="dialog"
      aria-label="Snooze until"
      className={cn(
        'fixed z-50 min-w-[220px] bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)]',
        'rounded-md shadow-xl py-1 text-body'
      )}
      style={{ top, left }}
    >
      <div className="px-3 py-1.5 text-caption uppercase tracking-widest text-[var(--color-stone)]/60">
        Snooze until
      </div>
      {OPTIONS.map((opt, idx) => (
        <button
          key={opt.label}
          type="button"
          className={cn(
            'w-full text-left px-3 py-1.5 hover:bg-[var(--color-paper)]/5 text-[var(--color-paper)]',
            activeRow === idx && 'bg-[var(--color-paper)]/5'
          )}
          onClick={() => onPick(opt.compute().toISOString())}
          onMouseEnter={() => setActiveRow(idx)}
          aria-label={`Snooze ${opt.label}`}
        >
          {opt.label}
          <span className="ml-2 text-caption text-[var(--color-stone)]/60">
            {fmt(opt.compute())}
          </span>
        </button>
      ))}
      <div className="border-t border-[rgba(163,163,163,0.08)] mt-1 pt-1">
        {customMode ? (
          <div className="px-2 py-1 flex gap-1">
            <input
              ref={customInputRef}
              type="datetime-local"
              className={cn(
                'flex-1 bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)]',
                'rounded-sm px-2 py-1 text-body text-[var(--color-paper)] focus:outline-none'
              )}
              value={customValue}
              onChange={(e) => setCustomValue(e.target.value)}
              min={new Date().toISOString().slice(0, 16)}
            />
            <button
              type="button"
              className="px-2 py-1 bg-[var(--color-paper)] text-[var(--color-void)] rounded-sm text-caption uppercase tracking-widest"
              disabled={!customValue}
              onClick={() => onPick(new Date(customValue).toISOString())}
            >
              OK
            </button>
          </div>
        ) : (
          <button
            type="button"
            className={cn(
              'w-full text-left px-3 py-1.5 hover:bg-[var(--color-paper)]/5 text-[var(--color-stone)]',
              activeRow === OPTIONS.length && 'bg-[var(--color-paper)]/5'
            )}
            onClick={() => setCustomMode(true)}
            onMouseEnter={() => setActiveRow(OPTIONS.length)}
          >
            Pick a date…
          </button>
        )}
      </div>
      <div className="border-t border-[rgba(163,163,163,0.08)] mt-1 pt-1">
        <button
          type="button"
          className={cn(
            'w-full text-left px-3 py-1.5 hover:bg-[var(--color-paper)]/5 text-[var(--color-stone)]',
            activeRow === OPTIONS.length + 1 && 'bg-[var(--color-paper)]/5'
          )}
          onClick={() => onPick(null)}
          onMouseEnter={() => setActiveRow(OPTIONS.length + 1)}
        >
          Wake up now
        </button>
      </div>
    </div>
  )
}

function fmt(d: Date): string {
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  if (sameDay) {
    return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  }
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}
