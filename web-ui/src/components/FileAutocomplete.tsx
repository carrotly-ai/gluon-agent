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

function getExtensionColor(ext: string): string {
  switch (ext) {
    case 'ts':
    case 'tsx':
      return 'bg-blue-900/50 text-blue-300'
    case 'js':
    case 'jsx':
      return 'bg-yellow-900/50 text-yellow-300'
    case 'py':
      return 'bg-green-900/50 text-green-300'
    case 'md':
    case 'mdx':
      return 'bg-purple-900/50 text-purple-300'
    case 'json':
    case 'yaml':
    case 'yml':
      return 'bg-orange-900/50 text-orange-300'
    case 'css':
    case 'scss':
      return 'bg-pink-900/50 text-pink-300'
    case 'html':
      return 'bg-red-900/50 text-red-300'
    default:
      return 'bg-zinc-800/50 text-zinc-400'
  }
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
        className="fixed z-[99999] w-[400px] rounded-md border border-zinc-700 bg-zinc-900 shadow-2xl p-4"
        style={{
          top: position.top,
          left: position.left,
          transform: 'translateY(-100%)',
        }}
      >
        <div className="flex items-center gap-2 text-zinc-400">
          <div className="animate-spin h-4 w-4 border-2 border-zinc-600 border-t-zinc-300 rounded-full" />
          <span className="text-sm">Loading files...</span>
        </div>
      </div>,
      document.body
    )
  }

  if (filteredFiles.length === 0) {
    return createPortal(
      <div
        className="fixed z-[99999] w-[400px] rounded-md border border-zinc-700 bg-zinc-900 shadow-2xl p-4"
        style={{
          top: position.top,
          left: position.left,
          transform: 'translateY(-100%)',
        }}
      >
        <div className="text-sm text-zinc-500">
          {filter ? `No files matching "${filter}"` : 'No files found'}
        </div>
      </div>,
      document.body
    )
  }

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
        {filteredFiles.map((file, index) => {
          const ext = file.type === 'file' ? getFileExtension(file.path) : ''
          return (
            <button
              key={file.path}
              type="button"
              data-file-item
              className={cn(
                'w-full flex items-center gap-3 px-3 py-2 text-left rounded-md transition-colors',
                index === selectedIndex
                  ? 'bg-zinc-700 text-zinc-100'
                  : 'text-zinc-300 hover:bg-zinc-800'
              )}
              onClick={() => onSelect(file)}
              onMouseEnter={() => setSelectedIndex(index)}
            >
              <div className="flex-shrink-0">
                {file.type === 'directory' ? (
                  <Folder className="h-4 w-4 text-amber-400" />
                ) : (
                  <File className="h-4 w-4 text-zinc-400" />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <span className="font-mono text-sm truncate block">{file.path}</span>
              </div>
              {ext && (
                <div className="flex-shrink-0">
                  <span className={cn('text-[10px] px-1.5 py-0.5 rounded', getExtensionColor(ext))}>
                    {ext}
                  </span>
                </div>
              )}
              {file.type === 'directory' && (
                <div className="flex-shrink-0">
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-300">
                    dir
                  </span>
                </div>
              )}
            </button>
          )
        })}
      </div>
      <div className="border-t border-zinc-700 px-3 py-1.5 text-[10px] text-zinc-500 flex justify-between">
        <span>
          <kbd className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-400">↑↓</kbd> navigate{' '}
          <kbd className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-400">Tab</kbd> select{' '}
          <kbd className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-400">Esc</kbd> close
        </span>
        {truncated && <span className="text-zinc-600">More results available...</span>}
      </div>
    </div>,
    document.body
  )
}
