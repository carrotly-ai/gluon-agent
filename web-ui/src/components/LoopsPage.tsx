/**
 * Agent Loops page (loop-engineering Phase 2 — docs/design/agent-loops.md).
 *
 * List of agent loops with status/budget/metrics columns and pause/resume/
 * cancel actions; clicking a row opens a detail dialog with the objective,
 * stop-condition state, per-loop effectiveness metrics, and the iteration
 * timeline (including independent-verifier iterations). "New loop" opens a
 * create dialog. Follows the SchedulesPage layout patterns (DataPage +
 * PageHeader + table + dialogs).
 */

import { Ban, Pause, Play, Plus, RefreshCw, Repeat2, ShieldCheck } from 'lucide-react'
import type React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  cancelLoop,
  createLoop,
  fetchLoop,
  fetchLoops,
  fetchProjects,
  pauseLoop,
  resumeLoop,
} from '@/lib/api'
import { formatCost } from '@/lib/format'
import { formatRelativeTime } from '@/lib/timestamps'
import type { AgentLoop, Project } from '@/lib/types'
import { cn } from '@/lib/utils'
import { DataPage } from './ui/DataPage'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog'
import { PageHeader } from './ui/PageHeader'
import { StatusDot } from './ui/StatusDot'

const LOOP_STATUS_COLORS: Record<string, string> = {
  running: 'var(--color-sky)',
  paused: 'var(--color-harvest)',
  completed: 'var(--color-jade)',
  failed: 'var(--color-vermillion)',
  cancelled: 'var(--color-stone)',
}

function loopStatusColor(status: string): string {
  return LOOP_STATUS_COLORS[status] ?? 'var(--color-stone)'
}

export function LoopsPage() {
  const [loops, setLoops] = useState<AgentLoop[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set())
  const [detailId, setDetailId] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)

  const reload = useCallback(async () => {
    try {
      const [list, projs] = await Promise.all([fetchLoops(), fetchProjects()])
      setLoops(list.loops)
      setProjects(projs)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load loops')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const markBusy = (id: string, busy: boolean) => {
    setBusyIds((prev) => {
      const next = new Set(prev)
      if (busy) next.add(id)
      else next.delete(id)
      return next
    })
  }

  const applyUpdate = (updated: AgentLoop) => {
    setLoops((prev) => prev.map((x) => (x.id === updated.id ? { ...x, ...updated } : x)))
  }

  const handleAction = async (
    loop: AgentLoop,
    action: (id: string) => Promise<AgentLoop>,
    verb: string
  ) => {
    markBusy(loop.id, true)
    try {
      applyUpdate(await action(loop.id))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : `Failed to ${verb} loop`)
    } finally {
      markBusy(loop.id, false)
    }
  }

  const handleCancel = async (loop: AgentLoop) => {
    if (
      !window.confirm(`Cancel loop "${loop.objective.slice(0, 60)}…"? Pending tasks are dropped.`)
    )
      return
    await handleAction(loop, cancelLoop, 'cancel')
  }

  const sorted = useMemo(() => {
    const rank = (s: string) => (s === 'running' ? 0 : s === 'paused' ? 1 : 2)
    return [...loops].sort((a, b) => {
      if (rank(a.status) !== rank(b.status)) return rank(a.status) - rank(b.status)
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    })
  }, [loops])

  return (
    <DataPage>
      <PageHeader
        title="Loops"
        icon={Repeat2}
        count={sorted.length}
        countLabel="loop"
        subtitle="Objective-driven agent loops — the agent authors each iteration; the harness enforces gates, budgets, and stop conditions."
        actions={
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-caption bg-[var(--color-paper)]/10 hover:bg-[var(--color-paper)]/15 text-[var(--color-paper)] transition-colors"
          >
            <Plus className="w-3.5 h-3.5" /> New loop
          </button>
        }
      />
      <DataPage.Body>
        {loading && (
          <div className="flex items-center justify-center h-full">
            <div className="mark mark-running w-2 h-2" />
          </div>
        )}
        {!loading && error && (
          <div className="flex items-center justify-center h-full px-4">
            <p className="text-caption accent-vermillion text-center">{error}</p>
          </div>
        )}
        {!loading && !error && sorted.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-3 px-4 text-center">
            <Repeat2 className="w-8 h-8 text-[var(--color-stone)]/30" />
            <p className="text-body text-[var(--color-stone)]">
              No agent loops yet. Give an objective, a gate, and a budget — the agent authors the
              rest.
            </p>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-caption bg-[var(--color-paper)]/10 hover:bg-[var(--color-paper)]/15 text-[var(--color-paper)] transition-colors"
            >
              <Plus className="w-3.5 h-3.5" /> New loop
            </button>
          </div>
        )}
        {!loading && !error && sorted.length > 0 && (
          <table className="w-full text-body">
            <thead className="sticky top-0 bg-[var(--color-void)] border-b border-[rgba(163,163,163,0.1)]">
              <tr className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60">
                <th className="text-left px-4 py-2">Objective</th>
                <th className="text-left px-4 py-2 hidden sm:table-cell">Project</th>
                <th className="text-left px-4 py-2">Status</th>
                <th className="text-right px-4 py-2">Iter</th>
                <th className="text-right px-4 py-2 hidden md:table-cell">Cost</th>
                <th className="text-right px-4 py-2 hidden md:table-cell">Pending</th>
                <th className="text-left px-4 py-2 hidden lg:table-cell">Verification</th>
                <th className="text-right px-4 py-2">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((loop) => {
                const busy = busyIds.has(loop.id)
                return (
                  <tr
                    key={loop.id}
                    className={cn(
                      'border-b border-[rgba(163,163,163,0.05)] hover:bg-[var(--color-paper)]/3 transition-colors cursor-pointer',
                      (loop.status === 'cancelled' || loop.status === 'failed') && 'opacity-60'
                    )}
                    onClick={() => setDetailId(loop.id)}
                  >
                    <td className="px-4 py-2.5 max-w-[26rem]">
                      <span className="block truncate text-[var(--color-paper)]">
                        {loop.objective}
                      </span>
                      {loop.status_reason && (
                        <span className="block truncate text-caption text-[var(--color-stone)]/60">
                          {loop.status_reason}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 hidden sm:table-cell text-[var(--color-stone)]">
                      {loop.project_name ?? loop.project_id.slice(0, 8)}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="inline-flex items-center gap-1.5">
                        <span
                          className="w-2 h-2 rounded-full inline-block"
                          style={{ background: loopStatusColor(loop.status) }}
                        />
                        <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]">
                          {loop.status}
                        </span>
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-[var(--color-stone)]">
                      {loop.iteration_count}/{loop.max_iterations}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums hidden md:table-cell text-[var(--color-stone)]">
                      {formatCost(loop.total_cost_usd)}
                      {loop.max_cost_usd != null && (
                        <span className="text-[var(--color-stone)]/50">
                          {' '}
                          / {formatCost(loop.max_cost_usd)}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums hidden md:table-cell text-[var(--color-stone)]">
                      {loop.pending_tasks}
                    </td>
                    <td className="px-4 py-2.5 hidden lg:table-cell">
                      <span className="inline-flex items-center gap-1.5 text-caption text-[var(--color-stone)]">
                        {loop.readiness}
                        {loop.agent_verifier && (
                          <span title="Independent verifier judges completion claims">
                            <ShieldCheck className="w-3.5 h-3.5 text-[var(--color-jade)]/80" />
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="px-4 py-2.5" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-end gap-1">
                        {loop.status === 'running' && (
                          <IconButton
                            title="Pause loop"
                            disabled={busy}
                            onClick={() => void handleAction(loop, pauseLoop, 'pause')}
                          >
                            <Pause className="w-3.5 h-3.5" />
                          </IconButton>
                        )}
                        {loop.status === 'paused' && (
                          <IconButton
                            title="Resume loop"
                            accent
                            disabled={busy}
                            onClick={() => void handleAction(loop, resumeLoop, 'resume')}
                          >
                            <Play className="w-3.5 h-3.5" />
                          </IconButton>
                        )}
                        {(loop.status === 'running' || loop.status === 'paused') && (
                          <IconButton
                            title="Cancel loop"
                            danger
                            disabled={busy}
                            onClick={() => void handleCancel(loop)}
                          >
                            <Ban className="w-3.5 h-3.5" />
                          </IconButton>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </DataPage.Body>
      <LoopDetailDialog loopId={detailId} onClose={() => setDetailId(null)} />
      <CreateLoopDialog
        open={createOpen}
        projects={projects}
        onClose={() => setCreateOpen(false)}
        onCreated={(loop) => {
          setLoops((prev) => [loop, ...prev])
          setCreateOpen(false)
        }}
      />
    </DataPage>
  )
}

function LoopDetailDialog({ loopId, onClose }: { loopId: string | null; onClose: () => void }) {
  const [loop, setLoop] = useState<AgentLoop | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!loopId) {
      setLoop(null)
      return
    }
    setLoading(true)
    fetchLoop(loopId)
      .then(setLoop)
      .catch((e) => toast.error(e instanceof Error ? e.message : 'Failed to load loop'))
      .finally(() => setLoading(false))
  }, [loopId])

  return (
    <Dialog open={loopId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Repeat2 className="w-4 h-4 text-[var(--color-stone)]/70" />
            Agent loop {loopId?.slice(0, 8)}
            {loop && (
              <span
                className="text-caption uppercase tracking-widest px-1.5 py-0.5 rounded-sm"
                style={{ color: loopStatusColor(loop.status) }}
              >
                {loop.status}
              </span>
            )}
          </DialogTitle>
        </DialogHeader>
        {loading && (
          <div className="flex items-center justify-center py-8">
            <div className="mark mark-running w-2 h-2" />
          </div>
        )}
        {loop && (
          <div className="space-y-4 text-body">
            <section>
              <h3 className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60 mb-1">
                Objective
              </h3>
              <p className="whitespace-pre-wrap text-[var(--color-paper)]">{loop.objective}</p>
            </section>

            <section className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <DetailStat
                label="Iterations"
                value={`${loop.iteration_count}/${loop.max_iterations}`}
              />
              <DetailStat
                label="Cost"
                value={
                  formatCost(loop.total_cost_usd) +
                  (loop.max_cost_usd != null ? ` / ${formatCost(loop.max_cost_usd)}` : '')
                }
              />
              <DetailStat label="Pending tasks" value={String(loop.pending_tasks)} />
              <DetailStat label="Stalls" value={`${loop.stall_count}/${loop.max_stalls}`} />
              <DetailStat
                label="Gate"
                value={loop.verify_cmd ?? 'gateless'}
                title={loop.verify_cmd ?? undefined}
              />
              <DetailStat label="Independent verifier" value={loop.agent_verifier ? 'yes' : 'no'} />
            </section>

            {loop.status_reason && (
              <section>
                <h3 className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60 mb-1">
                  Status reason
                </h3>
                <p className="text-[var(--color-stone)]">{loop.status_reason}</p>
              </section>
            )}

            {loop.completion_summary && (
              <section>
                <h3 className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60 mb-1">
                  Completion summary
                </h3>
                <p className="whitespace-pre-wrap text-[var(--color-stone)]">
                  {loop.completion_summary}
                </p>
              </section>
            )}

            {loop.metrics && loop.metrics.runs > 0 && (
              <section>
                <h3 className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60 mb-1">
                  Effectiveness
                </h3>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <DetailStat label="Runs" value={String(loop.metrics.runs)} />
                  <DetailStat
                    label="PRs merged"
                    value={`${loop.metrics.accepted}/${loop.metrics.pr_producing}`}
                  />
                  <DetailStat
                    label="Acceptance"
                    value={`${Math.round(loop.metrics.acceptance_rate * 100)}%`}
                  />
                  <DetailStat
                    label="Cost / accepted"
                    value={
                      loop.metrics.cost_per_accepted_usd != null
                        ? formatCost(loop.metrics.cost_per_accepted_usd)
                        : '—'
                    }
                  />
                </div>
              </section>
            )}

            <section>
              <h3 className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60 mb-1">
                Iteration timeline
              </h3>
              {loop.recent_runs.length === 0 ? (
                <p className="text-caption text-[var(--color-stone)]/60">
                  No iterations yet — the seed task is waiting for dispatch.
                </p>
              ) : (
                <ol className="space-y-1.5">
                  {loop.recent_runs.map((r) => (
                    <li key={r.id} className="flex items-start gap-2">
                      <StatusDot
                        state={
                          ([
                            'pending',
                            'running',
                            'completed',
                            'review',
                            'failed',
                            'cancelled',
                          ].includes(r.status)
                            ? r.status
                            : 'pending') as
                            | 'pending'
                            | 'running'
                            | 'completed'
                            | 'review'
                            | 'failed'
                            | 'cancelled'
                        }
                        size="sm"
                      />
                      <div className="min-w-0 flex-1">
                        <span className="block truncate text-[var(--color-paper)]">
                          {r.verifier && (
                            <span title="Independent verifier iteration">
                              <ShieldCheck className="inline w-3.5 h-3.5 mr-1 text-[var(--color-jade)]/80" />
                            </span>
                          )}
                          {r.title}
                        </span>
                        <span className="text-caption text-[var(--color-stone)]/60">
                          {r.id.slice(0, 8)} · {r.status} · {formatCost(r.cost_usd)} ·{' '}
                          {formatRelativeTime(r.created_at)}
                        </span>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function DetailStat({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-caption uppercase tracking-widest text-[var(--color-stone)]/50">
        {label}
      </div>
      <div className="text-[var(--color-paper)] truncate" title={title}>
        {value}
      </div>
    </div>
  )
}

function CreateLoopDialog({
  open,
  projects,
  onClose,
  onCreated,
}: {
  open: boolean
  projects: Project[]
  onClose: () => void
  onCreated: (loop: AgentLoop) => void
}) {
  const [projectName, setProjectName] = useState('')
  const [objective, setObjective] = useState('')
  const [verifyCmd, setVerifyCmd] = useState('')
  const [agentVerifier, setAgentVerifier] = useState(false)
  const [useWorktree, setUseWorktree] = useState(true)
  const [maxIterations, setMaxIterations] = useState(20)
  const [maxCost, setMaxCost] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const canSubmit = projectName.trim() !== '' && objective.trim() !== '' && !submitting

  const handleSubmit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    try {
      const loop = await createLoop({
        project_name: projectName,
        objective: objective.trim(),
        verify_cmd: verifyCmd.trim() || null,
        agent_verifier: agentVerifier,
        use_worktree: useWorktree,
        max_iterations: maxIterations,
        max_cost_usd: maxCost.trim() ? Number(maxCost) : null,
      })
      toast.success('Agent loop created — iteration 1 dispatching')
      onCreated(loop)
      setObjective('')
      setVerifyCmd('')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to create loop')
    } finally {
      setSubmitting(false)
    }
  }

  const inputClass =
    'w-full bg-[var(--color-void)] border border-[rgba(163,163,163,0.15)] rounded-sm px-2.5 py-1.5 text-body text-[var(--color-paper)] focus:outline-none focus:border-[var(--color-sky)]/50'

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Repeat2 className="w-4 h-4 text-[var(--color-stone)]/70" /> New agent loop
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-body">
          <label className="block">
            <span className="text-caption text-[var(--color-stone)] block mb-1">Project</span>
            <select
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              className={inputClass}
            >
              <option value="">Select a project…</option>
              {projects.map((p) => (
                <option key={p.id} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-caption text-[var(--color-stone)] block mb-1">
              Objective — the durable goal the loop works toward
            </span>
            <textarea
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              rows={4}
              placeholder="e.g. Raise test coverage of the auth module above 90% with meaningful tests"
              className={inputClass}
            />
          </label>
          <label className="block">
            <span className="text-caption text-[var(--color-stone)] block mb-1">
              Verification gate (optional) — completion requires exit 0
            </span>
            <input
              value={verifyCmd}
              onChange={(e) => setVerifyCmd(e.target.value)}
              placeholder="e.g. uv run pytest"
              className={inputClass}
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-caption text-[var(--color-stone)] block mb-1">
                Max iterations
              </span>
              <input
                type="number"
                min={1}
                max={500}
                value={maxIterations}
                onChange={(e) => setMaxIterations(Number(e.target.value) || 20)}
                className={inputClass}
              />
            </label>
            <label className="block">
              <span className="text-caption text-[var(--color-stone)] block mb-1">
                Cost cap USD (optional)
              </span>
              <input
                type="number"
                min={0}
                step="0.5"
                value={maxCost}
                onChange={(e) => setMaxCost(e.target.value)}
                placeholder="no cap"
                className={inputClass}
              />
            </label>
          </div>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={agentVerifier}
              onChange={(e) => setAgentVerifier(e.target.checked)}
            />
            <span className="text-caption text-[var(--color-stone)]">
              Independent verifier — a fresh agent judges completion claims
            </span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={useWorktree}
              onChange={(e) => setUseWorktree(e.target.checked)}
            />
            <span className="text-caption text-[var(--color-stone)]">
              Run iterations in isolated Git worktrees
            </span>
          </label>
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 rounded-sm text-caption text-[var(--color-stone)] hover:bg-[var(--color-paper)]/5 transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={!canSubmit}
              onClick={() => void handleSubmit()}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-caption transition-colors',
                canSubmit
                  ? 'bg-[var(--color-paper)]/10 hover:bg-[var(--color-paper)]/15 text-[var(--color-paper)]'
                  : 'bg-[var(--color-paper)]/5 text-[var(--color-stone)]/40 cursor-not-allowed'
              )}
            >
              {submitting ? (
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Plus className="w-3.5 h-3.5" />
              )}
              Create loop
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function IconButton({
  children,
  title,
  onClick,
  disabled,
  accent,
  danger,
}: {
  children: React.ReactNode
  title: string
  onClick: () => void
  disabled?: boolean
  accent?: boolean
  danger?: boolean
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'p-1.5 rounded-sm transition-colors',
        disabled && 'opacity-30 cursor-not-allowed',
        !disabled && accent && 'text-[var(--color-sky)] hover:bg-[rgba(102,178,255,0.1)]',
        !disabled &&
          danger &&
          'text-[var(--color-stone)]/40 hover:text-[var(--color-vermillion)] hover:bg-[var(--color-vermillion)]/10',
        !disabled &&
          !accent &&
          !danger &&
          'text-[var(--color-stone)]/60 hover:text-[var(--color-paper)] hover:bg-[var(--color-paper)]/5'
      )}
    >
      {children}
    </button>
  )
}
