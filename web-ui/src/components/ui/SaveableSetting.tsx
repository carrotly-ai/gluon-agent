import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'

/**
 * Card wrapper for an individual saveable setting. Standardises the
 * label + description + control + save pattern that's currently implemented
 * four different ways across SettingsPage, WorkspaceSettingsDialog, and
 * ScheduleEditorDialog.
 *
 * The save pill auto-dismisses 2s after `saved` flips true.
 *
 *   <SaveableSetting
 *     label="API token"
 *     description="Used for outbound webhooks."
 *     dirty={value !== savedValue}
 *     saving={saving}
 *     saved={justSaved}
 *     onSave={save}
 *   >
 *     <input value={value} onChange={(e) => setValue(e.target.value)} />
 *   </SaveableSetting>
 */
export interface SaveableSettingProps {
  label: string
  description?: string
  /** Whether the control has unsaved changes (controls Save button enabled state). */
  dirty?: boolean
  saving?: boolean
  saved?: boolean
  onSave?: () => void
  children: React.ReactNode
  className?: string
}

export function SaveableSetting({
  label,
  description,
  dirty = false,
  saving = false,
  saved = false,
  onSave,
  children,
  className,
}: SaveableSettingProps) {
  const [showSaved, setShowSaved] = useState(false)

  // Auto-dismiss "Saved" pill 2s after it appears.
  useEffect(() => {
    if (!saved) {
      setShowSaved(false)
      return
    }
    setShowSaved(true)
    const t = setTimeout(() => setShowSaved(false), 2000)
    return () => clearTimeout(t)
  }, [saved])

  const statePill = saving ? 'Saving…' : showSaved ? 'Saved' : null

  return (
    <div
      className={cn(
        'p-4 bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.1)] rounded-sm',
        className
      )}
    >
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="min-w-0">
          <h4 className="text-title text-[var(--color-paper)]">{label}</h4>
          {description ? (
            <p className="text-caption text-[var(--color-stone)] mt-0.5">{description}</p>
          ) : null}
        </div>
        {statePill ? (
          <span
            className={cn(
              'text-micro uppercase shrink-0',
              saving ? 'text-[var(--color-stone)]/60' : 'text-[var(--color-jade)]'
            )}
            aria-live="polite"
          >
            {statePill}
          </span>
        ) : null}
      </div>

      <div className="text-body">{children}</div>

      {onSave ? (
        <div className="mt-3 flex justify-end">
          <button
            type="button"
            onClick={onSave}
            disabled={!dirty || saving}
            className={cn(
              'px-3 py-1 text-caption uppercase tracking-widest rounded-sm transition-colors',
              dirty && !saving
                ? 'bg-[var(--color-paper)] text-[var(--color-void)] hover:opacity-90'
                : 'bg-[rgba(163,163,163,0.08)] text-[var(--color-stone)]/40 cursor-not-allowed'
            )}
          >
            Save
          </button>
        </div>
      ) : null}
    </div>
  )
}
