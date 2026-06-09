import { Archive, ChevronDown, Folder, FolderOpen } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { RouteFilter } from '@/hooks/useRouteSync'
import { fetchProjects } from '@/lib/api'
import { groupProjectsByWorkspace, type Project, type ProjectWithWorkspace } from '@/lib/types'
import { cn } from '@/lib/utils'

interface ProjectFilterProps {
  filter: RouteFilter
  onFilterChange: (filter: RouteFilter) => void
}

export function ProjectFilter({ filter, onFilterChange }: ProjectFilterProps) {
  const [projects, setProjects] = useState<Project[]>([])
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    fetchProjects().then(setProjects).catch(console.error)
  }, [])

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Memoize so `grouped` (and the `flatItems` below) keep a stable identity
  // across the parent's polling re-renders. Without this, every poll tick
  // produced a fresh Map → new flatItems → the seed effect re-ran and reset
  // the keyboard cursor, scrolling the open dropdown back to the top.
  const grouped = useMemo(() => groupProjectsByWorkspace(projects), [projects])

  // Flat list of selectable filters for keyboard navigation. The order
  // mirrors the visible DOM order so ArrowDown moves the highlight one row
  // at a time across mixed "All / Archived / workspace / project" rows.
  const flatItems = useMemo(() => {
    const items: { label: string; filter: RouteFilter }[] = [
      { label: 'All Projects', filter: { type: 'all', value: null } },
      { label: 'Archived', filter: { type: 'archived', value: null } },
    ]
    for (const [workspace, workspaceProjects] of grouped.entries()) {
      items.push({ label: workspace, filter: { type: 'workspace', value: workspace } })
      for (const project of workspaceProjects) {
        items.push({ label: project.name, filter: { type: 'project', value: project.name } })
      }
    }
    return items
  }, [grouped])

  // Seed the keyboard cursor on the current filter, but ONLY on the
  // closed→open transition. Depending on `filter`/`flatItems` here would
  // re-seed (and scroll to top) on every parent poll while the menu is open.
  const wasOpenRef = useRef(false)
  useEffect(() => {
    if (open && !wasOpenRef.current) {
      const currentIdx = flatItems.findIndex(
        (item) => item.filter.type === filter.type && item.filter.value === filter.value
      )
      setActiveIndex(currentIdx >= 0 ? currentIdx : 0)
    }
    wasOpenRef.current = open
  }, [open, filter, flatItems])

  // Keep the highlighted row in view as the user arrows through.
  useEffect(() => {
    if (!open || !listRef.current) return
    const el = listRef.current.querySelector<HTMLElement>(`[data-pf-index="${activeIndex}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open])

  // Get display label
  const getDisplayLabel = (): string => {
    if (filter.type === 'all') return 'All Projects'
    if (filter.type === 'archived') return 'Archived'
    if (filter.type === 'workspace') return filter.value || 'All Projects'
    if (filter.type === 'project') {
      const project = projects.find((p) => p.name === filter.value)
      return project?.name || filter.value || 'All Projects'
    }
    return 'All Projects'
  }

  const handleSelect = (newFilter: RouteFilter) => {
    onFilterChange(newFilter)
    setOpen(false)
    // Return focus to the trigger so keyboard users don't lose their place.
    requestAnimationFrame(() => triggerRef.current?.focus())
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
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
        setActiveIndex((i) => Math.min(i + 1, flatItems.length - 1))
        break
      case 'ArrowUp':
        e.preventDefault()
        setActiveIndex((i) => Math.max(i - 1, 0))
        break
      case 'Home':
        e.preventDefault()
        setActiveIndex(0)
        break
      case 'End':
        e.preventDefault()
        setActiveIndex(flatItems.length - 1)
        break
      case 'Enter':
        e.preventDefault()
        if (flatItems[activeIndex]) handleSelect(flatItems[activeIndex].filter)
        break
      case 'Escape':
        e.preventDefault()
        setOpen(false)
        triggerRef.current?.focus()
        break
    }
  }

  // Match an item against the keyboard cursor for visual highlight.
  const isCursorOn = (idx: number) => open && activeIndex === idx

  let runningIdx = -1
  const nextIdx = () => {
    runningIdx += 1
    return runningIdx
  }

  return (
    <div className="relative" ref={dropdownRef} onKeyDown={handleKeyDown}>
      <button
        ref={triggerRef}
        type="button"
        className="flex items-center gap-2 px-3 py-1.5 text-caption text-[var(--color-stone)] hover:text-[var(--color-paper)] transition-colors bg-[rgba(163,163,163,0.05)] hover:bg-[rgba(163,163,163,0.1)] rounded-sm border border-[rgba(163,163,163,0.1)]"
        onClick={() => setOpen(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Project filter: ${getDisplayLabel()}`}
      >
        <span className="max-w-[120px] sm:max-w-[180px] truncate">{getDisplayLabel()}</span>
        <ChevronDown className={cn('w-3 h-3 transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <div
          ref={listRef}
          className="absolute top-full left-0 mt-1 w-64 max-h-80 overflow-auto bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-xl z-50"
          role="listbox"
          aria-label="Project filter options"
        >
          {/* All Projects option */}
          {(() => {
            const idx = nextIdx()
            return (
              <button
                type="button"
                data-pf-index={idx}
                role="option"
                aria-selected={filter.type === 'all'}
                className={cn(
                  'w-full px-3 py-2 text-left text-caption transition-colors flex items-center gap-2',
                  filter.type === 'all'
                    ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                    : 'text-[var(--color-stone)]',
                  isCursorOn(idx) && 'bg-[rgba(163,163,163,0.12)]'
                )}
                onClick={() => handleSelect({ type: 'all', value: null })}
                onMouseEnter={() => setActiveIndex(idx)}
              >
                <FolderOpen className="w-3 h-3" />
                All Projects
              </button>
            )
          })()}

          {/* Archived option */}
          {(() => {
            const idx = nextIdx()
            return (
              <button
                type="button"
                data-pf-index={idx}
                role="option"
                aria-selected={filter.type === 'archived'}
                className={cn(
                  'w-full px-3 py-2 text-left text-caption transition-colors flex items-center gap-2',
                  filter.type === 'archived'
                    ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                    : 'text-[var(--color-stone)]',
                  isCursorOn(idx) && 'bg-[rgba(163,163,163,0.12)]'
                )}
                onClick={() => handleSelect({ type: 'archived', value: null })}
                onMouseEnter={() => setActiveIndex(idx)}
              >
                <Archive className="w-3 h-3" />
                Archived
              </button>
            )
          })()}

          <div className="border-t border-[rgba(163,163,163,0.08)] my-1" />

          {/* Workspaces and projects */}
          {Array.from(grouped.entries()).map(([workspace, workspaceProjects]) => (
            <div key={workspace}>
              {/* Workspace header */}
              {(() => {
                const wsIdx = nextIdx()
                return (
                  <button
                    type="button"
                    data-pf-index={wsIdx}
                    role="option"
                    aria-selected={filter.type === 'workspace' && filter.value === workspace}
                    className={cn(
                      'w-full px-3 py-2 text-left text-micro uppercase tracking-widest transition-colors flex items-center gap-2',
                      filter.type === 'workspace' && filter.value === workspace
                        ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                        : 'text-[var(--color-stone)]/70',
                      isCursorOn(wsIdx) && 'bg-[rgba(163,163,163,0.12)]'
                    )}
                    onClick={() => handleSelect({ type: 'workspace', value: workspace })}
                    onMouseEnter={() => setActiveIndex(wsIdx)}
                  >
                    <Folder className="w-3 h-3" />
                    {workspace}
                    <span className="ml-auto text-[var(--color-stone)]/60">
                      ({workspaceProjects.length})
                    </span>
                  </button>
                )
              })()}

              {/* Projects in workspace */}
              {workspaceProjects.map((project: ProjectWithWorkspace) => {
                const pIdx = nextIdx()
                return (
                  <button
                    key={project.id}
                    type="button"
                    data-pf-index={pIdx}
                    role="option"
                    aria-selected={filter.type === 'project' && filter.value === project.name}
                    className={cn(
                      'w-full pl-7 pr-3 py-1.5 text-left text-caption transition-colors truncate',
                      filter.type === 'project' && filter.value === project.name
                        ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                        : 'text-[var(--color-stone)]/80',
                      isCursorOn(pIdx) && 'bg-[rgba(163,163,163,0.12)]'
                    )}
                    onClick={() => handleSelect({ type: 'project', value: project.name })}
                    onMouseEnter={() => setActiveIndex(pIdx)}
                  >
                    {project.name}
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
