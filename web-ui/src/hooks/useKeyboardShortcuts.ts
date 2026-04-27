import { useEffect } from 'react'

interface KeyboardShortcutActions {
  onNewTask: () => void
  onNavigateBoard: () => void
  onNavigateList: () => void
  onNavigateUsage: () => void
  onNavigateSettings: () => void
  onRefresh: () => void
}

export function useKeyboardShortcuts(actions: KeyboardShortcutActions) {
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
          showShortcutHelp()
          break
      }
    }

    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [actions])
}

function showShortcutHelp() {
  const existing = document.getElementById('gluon-shortcut-help')
  if (existing) {
    existing.remove()
    return
  }

  const overlay = document.createElement('div')
  overlay.id = 'gluon-shortcut-help'
  overlay.style.cssText =
    'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6);'

  overlay.innerHTML = `
    <div style="background:var(--color-ink);border:1px solid rgba(163,163,163,0.15);border-radius:4px;padding:24px 32px;max-width:320px;width:100%;">
      <div style="font-size:14px;font-weight:500;color:var(--color-paper);margin-bottom:16px;letter-spacing:0.02em;">Keyboard Shortcuts</div>
      <div style="display:grid;grid-template-columns:auto 1fr;gap:8px 16px;font-size:11px;">
        <kbd style="font-family:var(--font-mono);background:rgba(163,163,163,0.1);padding:2px 6px;border-radius:3px;color:var(--color-paper);">n</kbd>
        <span style="color:var(--color-stone);">New task</span>
        <kbd style="font-family:var(--font-mono);background:rgba(163,163,163,0.1);padding:2px 6px;border-radius:3px;color:var(--color-paper);">1</kbd>
        <span style="color:var(--color-stone);">Board view</span>
        <kbd style="font-family:var(--font-mono);background:rgba(163,163,163,0.1);padding:2px 6px;border-radius:3px;color:var(--color-paper);">2</kbd>
        <span style="color:var(--color-stone);">List view</span>
        <kbd style="font-family:var(--font-mono);background:rgba(163,163,163,0.1);padding:2px 6px;border-radius:3px;color:var(--color-paper);">3</kbd>
        <span style="color:var(--color-stone);">Usage</span>
        <kbd style="font-family:var(--font-mono);background:rgba(163,163,163,0.1);padding:2px 6px;border-radius:3px;color:var(--color-paper);">4</kbd>
        <span style="color:var(--color-stone);">Settings</span>
        <kbd style="font-family:var(--font-mono);background:rgba(163,163,163,0.1);padding:2px 6px;border-radius:3px;color:var(--color-paper);">r</kbd>
        <span style="color:var(--color-stone);">Refresh</span>
        <kbd style="font-family:var(--font-mono);background:rgba(163,163,163,0.1);padding:2px 6px;border-radius:3px;color:var(--color-paper);">?</kbd>
        <span style="color:var(--color-stone);">Show shortcuts</span>
        <kbd style="font-family:var(--font-mono);background:rgba(163,163,163,0.1);padding:2px 6px;border-radius:3px;color:var(--color-paper);">Esc</kbd>
        <span style="color:var(--color-stone);">Close dialogs</span>
      </div>
    </div>
  `

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.remove()
  })

  const escHandler = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      overlay.remove()
      document.removeEventListener('keydown', escHandler)
    }
  }
  document.addEventListener('keydown', escHandler)

  document.body.appendChild(overlay)
}
