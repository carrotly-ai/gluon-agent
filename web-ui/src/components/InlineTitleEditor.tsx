import { Check, Pencil, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'

interface InlineTitleEditorProps {
  /** Current title (falls back to placeholder when empty). */
  value: string | null
  /** Placeholder shown when value is empty. Usually the run's prompt. */
  placeholder: string
  /** Called with the trimmed new value (or null when cleared) on commit. */
  onSave: (next: string | null) => void | Promise<void>
  /** Optional className to apply to the static (non-editing) text. */
  className?: string
  /** Max length — enforced at the input level and at the API boundary. */
  maxLength?: number
}

/**
 * Compact "click pencil to edit" title control.
 *
 * Renders the value inline, with a hover-revealed pencil affordance. Once
 * clicked it becomes an input; Enter saves, Esc cancels, blur saves.
 */
export function InlineTitleEditor({
  value,
  placeholder,
  onSave,
  className,
  maxLength = 200,
}: InlineTitleEditorProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value ?? '')
  const [saving, setSaving] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [editing])

  useEffect(() => {
    setDraft(value ?? '')
  }, [value])

  const commit = async () => {
    const next = draft.trim()
    if (next === (value ?? '')) {
      setEditing(false)
      return
    }
    setSaving(true)
    try {
      await onSave(next || null)
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    const display = value?.trim() || placeholder
    return (
      <button
        type="button"
        className={cn(
          'group/title inline-flex items-center gap-1 text-left min-w-0',
          'hover:text-[var(--color-paper)] transition-colors',
          className
        )}
        onClick={(e) => {
          e.stopPropagation()
          setEditing(true)
        }}
        title="Click to rename"
        aria-label={`Rename: ${display}`}
      >
        <span className="truncate">{display}</span>
        {/* Pencil affordance — invisible on hover devices until hover, but
            kept partially visible on touch (where there is no hover) so the
            control is discoverable. Stays subtle to fit Tokyo Minimal. */}
        <Pencil className="w-2.5 h-2.5 shrink-0 opacity-0 group-hover/title:opacity-60 [@media(pointer:coarse)]:opacity-40 transition-opacity" />
      </button>
    )
  }

  return (
    <div className="flex items-center gap-1 min-w-0">
      <input
        ref={inputRef}
        type="text"
        className={cn(
          'flex-1 min-w-0 bg-[var(--color-ink)] border border-[rgba(163,163,163,0.2)]',
          'rounded-sm px-1.5 py-0.5 text-body text-[var(--color-paper)] focus:outline-none',
          'focus:border-[rgba(163,163,163,0.4)]'
        )}
        value={draft}
        maxLength={maxLength}
        onChange={(e) => setDraft(e.target.value)}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Enter') void commit()
          if (e.key === 'Escape') {
            setDraft(value ?? '')
            setEditing(false)
          }
        }}
        onBlur={() => void commit()}
        disabled={saving}
        placeholder={placeholder}
      />
      <button
        type="button"
        className="p-0.5 text-[var(--color-jade)]/80 hover:text-[var(--color-jade)]"
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => {
          e.stopPropagation()
          void commit()
        }}
        title="Save"
        aria-label="Save title"
      >
        <Check className="w-3 h-3" />
      </button>
      <button
        type="button"
        className="p-0.5 text-[var(--color-stone)]/60 hover:text-[var(--color-stone)]"
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => {
          e.stopPropagation()
          setDraft(value ?? '')
          setEditing(false)
        }}
        title="Cancel"
        aria-label="Cancel rename"
      >
        <X className="w-3 h-3" />
      </button>
    </div>
  )
}
