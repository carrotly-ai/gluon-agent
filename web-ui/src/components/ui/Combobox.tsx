import { ChevronDown } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { cn } from '@/lib/utils'

export interface ComboboxOption<T extends string = string> {
  value: T
  label: string
  /** Optional muted suffix shown after the label (e.g. a model id or hint). */
  description?: string
  disabled?: boolean
}

interface ComboboxProps<T extends string = string> {
  options: ComboboxOption<T>[]
  value: T
  onChange: (value: T) => void
  /** Shown on the trigger when no option matches `value`. */
  placeholder?: string
  /** Accessible name for the listbox/trigger (e.g. "Model override"). */
  label: string
  className?: string
  buttonClassName?: string
  disabled?: boolean
}

/**
 * Accessible single-select dropdown.
 *
 * Extracted from the keyboard/listbox pattern in `ProjectFilter` so the various
 * hand-rolled `<button>` + `<div>` selects across the app (notably the six in
 * CreateTaskDialog) share one implementation that supports full keyboard
 * navigation (Arrow/Home/End/Enter/Esc), outside-click dismissal, focus return,
 * and the `listbox`/`option` ARIA roles screen readers need.
 */
export function Combobox<T extends string = string>({
  options,
  value,
  onChange,
  placeholder = 'Select…',
  label,
  className,
  buttonClassName,
  disabled,
}: ComboboxProps<T>) {
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listId = useId()

  const selected = useMemo(() => options.find((o) => o.value === value), [options, value])

  // Close on outside click.
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // Intercept Escape so it dismisses only this dropdown — not an enclosing Radix
  // Dialog. Radix listens on `document` in the capture phase and bails when the
  // event is `defaultPrevented`, so we must preventDefault earlier: a capture
  // listener on `window` runs before `document`'s.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      setOpen(false)
      triggerRef.current?.focus()
    }
    window.addEventListener('keydown', onKey, { capture: true })
    return () => window.removeEventListener('keydown', onKey, { capture: true })
  }, [open])

  // Seed the cursor on the current value when opening.
  useEffect(() => {
    if (!open) return
    const idx = options.findIndex((o) => o.value === value)
    setActiveIndex(idx >= 0 ? idx : 0)
  }, [open, value, options])

  // Keep the active row in view.
  useEffect(() => {
    if (!open || !listRef.current) return
    listRef.current
      .querySelector<HTMLElement>(`[data-cb-index="${activeIndex}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open])

  const select = (idx: number) => {
    const opt = options[idx]
    if (!opt || opt.disabled) return
    onChange(opt.value)
    setOpen(false)
    requestAnimationFrame(() => triggerRef.current?.focus())
  }

  const moveTo = (start: number, dir: 1 | -1) => {
    const n = options.length
    for (let step = 1; step <= n; step++) {
      const i = (start + dir * step + n * step) % n
      if (!options[i]?.disabled) return i
    }
    return start
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        setOpen(true)
      }
      return
    }
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setActiveIndex((i) => moveTo(i, 1))
        break
      case 'ArrowUp':
        e.preventDefault()
        setActiveIndex((i) => moveTo(i, -1))
        break
      case 'Home':
        e.preventDefault()
        setActiveIndex(moveTo(-1, 1))
        break
      case 'End':
        e.preventDefault()
        setActiveIndex(moveTo(0, -1))
        break
      case 'Enter':
        e.preventDefault()
        select(activeIndex)
        break
      // Escape is handled by the window-capture listener above so it can beat
      // Radix Dialog's document-capture handler.
    }
  }

  return (
    <div className={cn('relative', className)} ref={rootRef} onKeyDown={handleKeyDown}>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        className={cn(
          'w-full flex items-center justify-between px-3 py-2 text-body text-left bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm hover:border-[rgba(163,163,163,0.3)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
          buttonClassName
        )}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${selected?.label ?? placeholder}`}
      >
        <span className={selected ? 'text-[var(--color-paper)]' : 'text-[var(--color-stone)]/60'}>
          {selected?.label ?? placeholder}
          {selected?.description && (
            <span className="ml-2 text-[var(--color-stone)]/60">{selected.description}</span>
          )}
        </span>
        <ChevronDown
          className={cn(
            'w-3 h-3 text-[var(--color-stone)]/60 transition-transform shrink-0',
            open && 'rotate-180'
          )}
        />
      </button>

      {open && (
        <div
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label={label}
          className="absolute top-full left-0 right-0 mt-1 max-h-72 overflow-auto bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-xl z-50"
        >
          {options.map((option, idx) => (
            <button
              key={option.value}
              type="button"
              data-cb-index={idx}
              role="option"
              aria-selected={option.value === value}
              disabled={option.disabled}
              className={cn(
                'w-full px-3 py-2 text-left text-body transition-colors disabled:opacity-40',
                option.value === value
                  ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                  : 'text-[var(--color-stone)]',
                activeIndex === idx && 'bg-[rgba(163,163,163,0.12)]'
              )}
              onClick={() => select(idx)}
              onMouseEnter={() => setActiveIndex(idx)}
            >
              {option.label}
              {option.description && (
                <span className="ml-2 text-[var(--color-stone)]/60">{option.description}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
