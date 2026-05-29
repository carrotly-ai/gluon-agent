import { useEffect, useState } from 'react'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'

interface KeyboardShortcutActions {
  onNewTask: () => void
  onNavigateBoard: () => void
  onNavigateList: () => void
  onNavigateUsage: () => void
  onNavigateSettings: () => void
  onRefresh: () => void
}

/** The shortcut reference shown in the `?` help dialog. */
const SHORTCUTS: ReadonlyArray<{ key: string; label: string }> = [
  { key: 'n', label: 'New task' },
  { key: '1', label: 'Board view' },
  { key: '2', label: 'List view' },
  { key: '3', label: 'Usage' },
  { key: '4', label: 'Settings' },
  { key: 'r', label: 'Refresh' },
  { key: '?', label: 'Show shortcuts' },
  { key: 'Esc', label: 'Close dialogs' },
]

interface KeyboardShortcutsState {
  /** Whether the `?` keyboard-help dialog is open. */
  helpOpen: boolean
  /** Imperatively open/close the keyboard-help dialog. */
  setHelpOpen: (open: boolean) => void
}

export function useKeyboardShortcuts(actions: KeyboardShortcutActions): KeyboardShortcutsState {
  const [helpOpen, setHelpOpen] = useState(false)

  useEffect(() => {
    function handler(e: KeyboardEvent) {
      // Skip when typing in inputs, textareas, or contenteditable
      const target = e.target as HTMLElement
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.tagName === 'SELECT' ||
        target.isContentEditable
      ) {
        return
      }

      // Skip if modifier keys are held (allow browser shortcuts)
      if (e.metaKey || e.ctrlKey || e.altKey) return

      switch (e.key) {
        case 'n':
          e.preventDefault()
          actions.onNewTask()
          break
        case '1':
          e.preventDefault()
          actions.onNavigateBoard()
          break
        case '2':
          e.preventDefault()
          actions.onNavigateList()
          break
        case '3':
          e.preventDefault()
          actions.onNavigateUsage()
          break
        case '4':
          e.preventDefault()
          actions.onNavigateSettings()
          break
        case 'r':
          e.preventDefault()
          actions.onRefresh()
          break
        case '?':
          e.preventDefault()
          setHelpOpen((prev) => !prev)
          break
      }
    }

    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [actions])

  return { helpOpen, setHelpOpen }
}

interface KeyboardHelpDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * Keyboard-shortcut reference, opened with `?` and closed with `Esc`.
 *
 * Built on the shared Radix Dialog primitive so it gets a focus trap, Escape
 * handling and an accessible name for free — replacing the previous
 * innerHTML-injected overlay that had none of those.
 */
export function KeyboardHelpDialog({ open, onOpenChange }: KeyboardHelpDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[320px] w-full" showCloseButton={false}>
        <DialogTitle className="text-body font-medium text-[var(--color-paper)] tracking-wide">
          Keyboard Shortcuts
        </DialogTitle>
        <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-caption">
          {SHORTCUTS.map(({ key, label }) => (
            <div key={key} className="contents">
              <kbd className="font-mono bg-[rgba(163,163,163,0.1)] px-1.5 py-0.5 rounded-sm text-[var(--color-paper)] justify-self-start">
                {key}
              </kbd>
              <span className="text-[var(--color-stone)] self-center">{label}</span>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}
