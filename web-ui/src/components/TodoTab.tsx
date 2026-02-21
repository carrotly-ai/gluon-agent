import { CheckCircle, Circle, Loader2, ListChecks } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { fetchRunTodos } from '@/lib/api'
import type { RunDetail, RunTodosResponse, TodoItem } from '@/lib/types'
import { cn } from '@/lib/utils'

interface TodoTabProps {
  run: RunDetail
}

function StatusIcon({ status }: { status: TodoItem['status'] }) {
  switch (status) {
    case 'completed':
      return <CheckCircle className="w-3.5 h-3.5 text-green-400 shrink-0" />
    case 'in_progress':
      return <Loader2 className="w-3.5 h-3.5 text-amber-400 shrink-0 animate-spin" />
    case 'pending':
    default:
      return <Circle className="w-3.5 h-3.5 text-[var(--color-stone)]/40 shrink-0" />
  }
}

export function TodoTab({ run }: TodoTabProps) {
  const [data, setData] = useState<RunTodosResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadTodos = useCallback(async () => {
    try {
      const result = await fetchRunTodos(run.id)
      setData(result)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load todos')
    } finally {
      setLoading(false)
    }
  }, [run.id])

  // Initial load and auto-refresh while running
  useEffect(() => {
    loadTodos()

    if (run.status === 'running') {
      const intervalId = setInterval(loadTodos, 3000)
      return () => clearInterval(intervalId)
    }
  }, [loadTodos, run.status])

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center h-full min-h-[200px]">
        <Loader2 className="w-5 h-5 text-[var(--color-stone)]/40 animate-spin" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full min-h-[200px] text-[var(--color-vermillion)]/70 text-body">
        {error}
      </div>
    )
  }

  if (!data || data.todos.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[200px] gap-2">
        <ListChecks className="w-6 h-6 text-[var(--color-stone)]/30" />
        <span className="text-[var(--color-stone)]/50 text-body italic">
          No task tracking data yet
        </span>
        {run.status === 'running' && (
          <span className="text-[var(--color-stone)]/30 text-body">
            Todos will appear when the agent starts tracking tasks
          </span>
        )}
      </div>
    )
  }

  const progressPercent =
    data.todo_count > 0 ? Math.round((data.completed_count / data.todo_count) * 100) : 0

  return (
    <div className="flex flex-col h-full">
      {/* Summary bar */}
      <div className="flex items-center gap-3 px-3 py-2 border-b border-[rgba(163,163,163,0.08)]">
        <div className="flex items-center gap-1.5">
          <CheckCircle className="w-3.5 h-3.5 text-green-400" />
          <span className="text-body text-[var(--color-paper)]/70">
            {data.completed_count}/{data.todo_count} completed
          </span>
        </div>
        {data.in_progress_count > 0 && (
          <div className="flex items-center gap-1.5">
            <Loader2 className="w-3.5 h-3.5 text-amber-400 animate-spin" />
            <span className="text-body text-amber-400/70">{data.in_progress_count} active</span>
          </div>
        )}
        {data.pending_count > 0 && (
          <div className="flex items-center gap-1.5">
            <Circle className="w-3.5 h-3.5 text-[var(--color-stone)]/40" />
            <span className="text-body text-[var(--color-stone)]/50">
              {data.pending_count} pending
            </span>
          </div>
        )}
        {/* Progress bar */}
        <div className="ml-auto flex items-center gap-2">
          <div className="w-24 h-1.5 bg-[var(--color-stone)]/10 rounded-full overflow-hidden">
            <div
              className="h-full bg-green-400/70 rounded-full transition-all duration-300"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
          <span className="text-body text-[var(--color-stone)]/40">{progressPercent}%</span>
        </div>
      </div>

      {/* Todo list */}
      <div className="flex-1 overflow-auto p-3 space-y-1">
        {data.todos.map((todo, i) => (
          <div
            key={`${todo.content}-${i}`}
            className={cn(
              'flex items-start gap-2.5 py-1.5 px-2 rounded-sm',
              todo.status === 'in_progress' && 'bg-amber-400/5',
              todo.status === 'completed' && 'opacity-60'
            )}
          >
            <div className="mt-0.5">
              <StatusIcon status={todo.status} />
            </div>
            <span
              className={cn(
                'text-body leading-relaxed',
                todo.status === 'completed'
                  ? 'text-[var(--color-stone)]/50 line-through'
                  : todo.status === 'in_progress'
                    ? 'text-[var(--color-paper)]/90'
                    : 'text-[var(--color-paper)]/70'
              )}
            >
              {todo.status === 'in_progress' ? todo.active_form || todo.content : todo.content}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
