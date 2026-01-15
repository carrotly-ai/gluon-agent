import { Command, Terminal } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { SlashCommand } from '@/lib/types'
import { cn } from '@/lib/utils'

interface CommandAutocompleteProps {
  commands: SlashCommand[]
  filter: string
  visible: boolean
  onSelect: (command: SlashCommand) => void
  onClose: () => void
  anchorRef: React.RefObject<HTMLTextAreaElement | null>
}

export function CommandAutocomplete({
  commands,
  filter,
  visible,
  onSelect,
  onClose,
  anchorRef,
}: CommandAutocompleteProps) {
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [position, setPosition] = useState({ top: 0, left: 0 })
  const listRef = useRef<HTMLDivElement>(null)

  // Filter commands by name prefix
  const filteredCommands = commands.filter((cmd) =>
    cmd.name.toLowerCase().startsWith(filter.toLowerCase())
  )

  // Calculate position based on anchor element
  useEffect(() => {
    if (visible && anchorRef.current) {
      const rect = anchorRef.current.getBoundingClientRect()
      // Position above the textarea
      setPosition({
        top: rect.top - 8, // 8px gap above textarea
        left: rect.left,
      })
    }
  }, [visible, anchorRef])

  // Reset selection when filter changes
  useEffect(() => {
    setSelectedIndex(0)
  }, [filter])

  // Scroll selected item into view
  useEffect(() => {
    if (listRef.current && filteredCommands.length > 0) {
      const items = listRef.current.querySelectorAll('[data-command-item]')
      const selectedEl = items[selectedIndex] as HTMLElement
      selectedEl?.scrollIntoView({ block: 'nearest' })
    }
  }, [selectedIndex, filteredCommands.length])

  // Handle keyboard navigation
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!visible || filteredCommands.length === 0) return

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          setSelectedIndex((i) => Math.min(i + 1, filteredCommands.length - 1))
          break
        case 'ArrowUp':
          e.preventDefault()
          setSelectedIndex((i) => Math.max(i - 1, 0))
          break
        case 'Enter':
        case 'Tab':
          e.preventDefault()
          onSelect(filteredCommands[selectedIndex])
          break
        case 'Escape':
          e.preventDefault()
          onClose()
          break
      }
    },
    [visible, filteredCommands, selectedIndex, onSelect, onClose]
  )

  // Add keyboard listener
  useEffect(() => {
    if (visible) {
      document.addEventListener('keydown', handleKeyDown, true)
      return () => document.removeEventListener('keydown', handleKeyDown, true)
    }
  }, [visible, handleKeyDown])

  if (!visible || filteredCommands.length === 0) return null

  // Use portal to render outside dialog stacking context
  return createPortal(
    <div
      className="fixed z-[99999] w-[400px] max-h-[300px] overflow-y-auto rounded-md border border-zinc-700 bg-zinc-900 shadow-2xl"
      style={{
        top: position.top,
        left: position.left,
        transform: 'translateY(-100%)', // Position above the anchor
      }}
      ref={listRef}
    >
      <div className="p-1">
        {filteredCommands.map((cmd, index) => (
          <button
            key={`${cmd.type}-${cmd.name}`}
            type="button"
            data-command-item
            className={cn(
              'w-full flex items-start gap-3 px-3 py-2 text-left rounded-md transition-colors',
              index === selectedIndex
                ? 'bg-zinc-700 text-zinc-100'
                : 'text-zinc-300 hover:bg-zinc-800'
            )}
            onClick={() => onSelect(cmd)}
            onMouseEnter={() => setSelectedIndex(index)}
          >
            <div className="flex-shrink-0 mt-0.5">
              {cmd.type === 'command' ? (
                <Terminal className="h-4 w-4 text-blue-400" />
              ) : (
                <Command className="h-4 w-4 text-purple-400" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm text-zinc-100">/{cmd.name}</span>
                {cmd.argument_hint && (
                  <span className="text-xs text-zinc-500">{cmd.argument_hint}</span>
                )}
              </div>
              <p className="text-xs text-zinc-400 truncate mt-0.5">{cmd.description}</p>
            </div>
            <div className="flex-shrink-0">
              <span
                className={cn(
                  'text-[10px] px-1.5 py-0.5 rounded',
                  cmd.type === 'command'
                    ? 'bg-blue-900/50 text-blue-300'
                    : 'bg-purple-900/50 text-purple-300'
                )}
              >
                {cmd.type}
              </span>
            </div>
          </button>
        ))}
      </div>
      <div className="border-t border-zinc-700 px-3 py-1.5 text-[10px] text-zinc-500">
        <kbd className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-400">↑↓</kbd> navigate{' '}
        <kbd className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-400">Tab</kbd> select{' '}
        <kbd className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-400">Esc</kbd> close
      </div>
    </div>,
    document.body
  )
}
