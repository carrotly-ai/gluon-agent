import { File, Folder } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ProjectFile } from '@/lib/types'
import { cn } from '@/lib/utils'

interface FileAutocompleteProps {
  files: ProjectFile[]
  filter: string
  visible: boolean
  onSelect: (file: ProjectFile) => void
  onClose: () => void
  anchorRef: React.RefObject<HTMLTextAreaElement | null>
  loading?: boolean
  truncated?: boolean
}

function getFileExtension(path: string): string {
  const lastDot = path.lastIndexOf('.')
  const lastSlash = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  if (lastDot > lastSlash + 1) {
    return path.slice(lastDot + 1).toLowerCase()
  }
  return ''
}

export function FileAutocomplete({
  files,
  filter,
  visible,
  onSelect,
  onClose,
  anchorRef,
  loading = false,
  truncated = false,
}: FileAutocompleteProps) {
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [position, setPosition] = useState({ top: 0, left: 0 })
  const listRef = useRef<HTMLDivElement>(null)

  // Filter files by path (case-insensitive) - matches anywhere in path
  const filteredFiles = files.filter((file) =>
    file.path.toLowerCase().includes(filter.toLowerCase())
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
  }, [])

  // Scroll selected item into view
  useEffect(() => {
    if (listRef.current && filteredFiles.length > 0) {
      const items = listRef.current.querySelectorAll('[data-file-item]')
      const selectedEl = items[selectedIndex] as HTMLElement
      selectedEl?.scrollIntoView({ block: 'nearest' })
    }
  }, [selectedIndex, filteredFiles.length])

  // Handle keyboard navigation
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!visible || filteredFiles.length === 0) return

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          setSelectedIndex((i) => Math.min(i + 1, filteredFiles.length - 1))
          break
        case 'ArrowUp':
          e.preventDefault()
          setSelectedIndex((i) => Math.max(i - 1, 0))
          break
        case 'Enter':
        case 'Tab':
          e.preventDefault()
          onSelect(filteredFiles[selectedIndex])
          break
        case 'Escape':
          e.preventDefault()
          onClose()
          break
      }
    },
    [visible, filteredFiles, selectedIndex, onSelect, onClose]
  )

  // Add keyboard listener
  useEffect(() => {
    if (visible) {
      document.addEventListener('keydown', handleKeyDown, true)
      return () => document.removeEventListener('keydown', handleKeyDown, true)
    }
  }, [visible, handleKeyDown])

  if (!visible) return null

  // Show loading state or empty state
  if (loading) {
    return createPortal(
      <div
        className="fixed z-[99999] w-[min(400px,calc(100vw-2rem))] rounded-md border border-[rgba(163,163,163,0.15)] bg-[var(--color-ink)] shadow-2xl p-4 touch-manipulation"
        style={{
          top: Math.max(position.top, 60),
          left: position.left,
          transform: 'translateY(-100%)',
        }}
      >
        <div className="flex items-center gap-2 text-[var(--color-stone)]">
          <div className="animate-spin h-4 w-4 border-2 border-[var(--color-stone)]/30 border-t-[var(--color-paper)] rounded-full" />
          <span className="text-body">Loading files...</span>
        </div>
      </div>,
      document.body
    )
  }

  if (filteredFiles.length === 0) {
    return createPortal(
      <div
        className="fixed z-[99999] w-[min(400px,calc(100vw-2rem))] rounded-md border border-[rgba(163,163,163,0.15)] bg-[var(--color-ink)] shadow-2xl p-4 touch-manipulation"
        style={{
          top: Math.max(position.top, 60),
          left: position.left,
          transform: 'translateY(-100%)',
        }}
      >
        <div className="text-body text-[var(--color-stone)]">
          {filter ? `No files matching "${filter}"` : 'No files found'}
        </div>
      </div>,
      document.body
    )
  }

  // Use portal to render outside dialog stacking context
  return createPortal(
    <div
      className="fixed z-[99999] w-[min(400px,calc(100vw-2rem))] max-h-[300px] overflow-y-auto rounded-md border border-[rgba(163,163,163,0.15)] bg-[var(--color-ink)] shadow-2xl touch-manipulation"
      style={{
        top: Math.max(position.top, 60),
        left: position.left,
        transform: 'translateY(-100%)', // Position above the anchor
      }}
      ref={listRef}
      role="listbox"
      aria-label="File suggestions"
    >
      <div className="p-1">
        {filteredFiles.map((file, index) => {
          const ext = file.type === 'file' ? getFileExtension(file.path) : ''
          return (
            <button
              key={file.path}
              type="button"
              data-file-item
              role="option"
              aria-selected={index === selectedIndex}
              className={cn(
                'w-full flex items-center gap-3 px-3 py-2 text-left rounded-sm transition-colors',
                index === selectedIndex
                  ? 'bg-[var(--color-paper)]/8 text-[var(--color-paper)]'
                  : 'text-[var(--color-paper)] hover:bg-[var(--color-paper)]/5'
              )}
              onClick={() => onSelect(file)}
              onMouseEnter={() => setSelectedIndex(index)}
            >
              <div className="flex-shrink-0">
                {file.type === 'directory' ? (
                  <Folder className="h-4 w-4 text-[var(--color-stone)]" />
                ) : (
                  <File className="h-4 w-4 text-[var(--color-stone)]" />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <span className="font-mono text-body truncate block">{file.path}</span>
              </div>
              {/* Extension and directory hints — single muted mono treatment.
                  No rainbow palette: the path itself carries the meaning,
                  the ext/"dir" tag is a small intentional whisper. */}
              {ext && (
                <span className="flex-shrink-0 font-mono text-micro uppercase text-[var(--color-stone)]/60">
                  {ext}
                </span>
              )}
              {file.type === 'directory' && (
                <span className="flex-shrink-0 font-mono text-micro uppercase text-[var(--color-stone)]/60">
                  dir
                </span>
              )}
            </button>
          )
        })}
      </div>
      <div className="border-t border-[rgba(163,163,163,0.1)] px-3 py-1.5 text-micro uppercase text-[var(--color-stone)]/60 flex justify-between">
        <span>
          <kbd className="font-mono px-1 py-0.5 rounded-sm bg-[var(--color-paper)]/8 text-[var(--color-stone)]">
            ↑↓
          </kbd>{' '}
          navigate{' '}
          <kbd className="font-mono px-1 py-0.5 rounded-sm bg-[var(--color-paper)]/8 text-[var(--color-stone)]">
            Tab
          </kbd>{' '}
          select{' '}
          <kbd className="font-mono px-1 py-0.5 rounded-sm bg-[var(--color-paper)]/8 text-[var(--color-stone)]">
            Esc
          </kbd>{' '}
          close
        </span>
        {truncated && <span className="text-[var(--color-stone)]/60">More results available…</span>}
      </div>
    </div>,
    document.body
  )
}
