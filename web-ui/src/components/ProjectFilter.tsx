import { useEffect, useState, useRef } from 'react'
import { ChevronDown, Folder, FolderOpen, Archive } from 'lucide-react'
import { fetchProjects } from '@/lib/api'
import { groupProjectsByWorkspace, type Project, type ProjectWithWorkspace } from '@/lib/types'
import { type RouteFilter } from '@/hooks/useRouteSync'
import { cn } from '@/lib/utils'

interface ProjectFilterProps {
  filter: RouteFilter
  onFilterChange: (filter: RouteFilter) => void
}

export function ProjectFilter({ filter, onFilterChange }: ProjectFilterProps) {
  const [projects, setProjects] = useState<Project[]>([])
  const [open, setOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

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

  const grouped = groupProjectsByWorkspace(projects)

  // Get display label
  const getDisplayLabel = (): string => {
    if (filter.type === 'all') return 'All Projects'
    if (filter.type === 'archived') return 'Archived'
    if (filter.type === 'workspace') return filter.value || 'All Projects'
    if (filter.type === 'project') {
      const project = projects.find(p => p.name === filter.value)
      return project?.name || filter.value || 'All Projects'
    }
    return 'All Projects'
  }

  const handleSelect = (newFilter: RouteFilter) => {
    onFilterChange(newFilter)
    setOpen(false)
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        className="flex items-center gap-2 px-3 py-1.5 text-[0.6875rem] text-[var(--color-stone)] hover:text-[var(--color-paper)] transition-colors bg-[rgba(163,163,163,0.05)] hover:bg-[rgba(163,163,163,0.1)] rounded-sm border border-[rgba(163,163,163,0.1)]"
        onClick={() => setOpen(!open)}
      >
        <span className="max-w-[120px] sm:max-w-[180px] truncate">{getDisplayLabel()}</span>
        <ChevronDown className={cn('w-3 h-3 transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 w-64 max-h-80 overflow-auto bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-sm shadow-xl z-50">
          {/* All Projects option */}
          <button
            className={cn(
              'w-full px-3 py-2 text-left text-[0.6875rem] hover:bg-[rgba(163,163,163,0.1)] transition-colors flex items-center gap-2',
              filter.type === 'all' ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]' : 'text-[var(--color-stone)]'
            )}
            onClick={() => handleSelect({ type: 'all', value: null })}
          >
            <FolderOpen className="w-3 h-3" />
            All Projects
          </button>

          {/* Archived option */}
          <button
            className={cn(
              'w-full px-3 py-2 text-left text-[0.6875rem] hover:bg-[rgba(163,163,163,0.1)] transition-colors flex items-center gap-2',
              filter.type === 'archived' ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]' : 'text-[var(--color-stone)]'
            )}
            onClick={() => handleSelect({ type: 'archived', value: null })}
          >
            <Archive className="w-3 h-3" />
            Archived
          </button>

          <div className="border-t border-[rgba(163,163,163,0.08)] my-1" />

          {/* Workspaces and projects */}
          {Array.from(grouped.entries()).map(([workspace, workspaceProjects]) => (
            <div key={workspace}>
              {/* Workspace header */}
              <button
                className={cn(
                  'w-full px-3 py-2 text-left text-[0.625rem] uppercase tracking-widest hover:bg-[rgba(163,163,163,0.1)] transition-colors flex items-center gap-2',
                  filter.type === 'workspace' && filter.value === workspace
                    ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                    : 'text-[var(--color-stone)]/70'
                )}
                onClick={() => handleSelect({ type: 'workspace', value: workspace })}
              >
                <Folder className="w-3 h-3" />
                {workspace}
                <span className="ml-auto text-[var(--color-stone)]/55">({workspaceProjects.length})</span>
              </button>

              {/* Projects in workspace */}
              {workspaceProjects.map((project: ProjectWithWorkspace) => (
                <button
                  key={project.id}
                  className={cn(
                    'w-full pl-7 pr-3 py-1.5 text-left text-[0.6875rem] hover:bg-[rgba(163,163,163,0.1)] transition-colors truncate',
                    filter.type === 'project' && filter.value === project.name
                      ? 'text-[var(--color-paper)] bg-[rgba(163,163,163,0.08)]'
                      : 'text-[var(--color-stone)]/80'
                  )}
                  onClick={() => handleSelect({ type: 'project', value: project.name })}
                >
                  {project.name}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
