import {
  AlertTriangle,
  CheckCircle,
  CircleSlash,
  Clock,
  DollarSign,
  FileCode,
  RefreshCw,
  Square,
  XCircle,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { fetchRalphIterations, stopLoop } from '@/lib/api'
import type { CircuitState, RalphIteration, RalphIterationsResponse, RunDetail } from '@/lib/types'
import { cn } from '@/lib/utils'

interface LoopProgressTabProps {
  run: RunDetail
  onRunUpdated?: (run: RunDetail) => void
}

// Circuit state color helpers
function getCircuitStateColor(state: CircuitState): string {
  switch (state) {
    case 'CLOSED':
      return 'text-green-400'
    case 'HALF_OPEN':
      return 'text-yellow-400'
    case 'OPEN':
      return 'text-red-400'
    default:
      return 'text-[var(--color-stone)]/60'
  }
}

function getCircuitStateBg(state: CircuitState): string {
  switch (state) {
    case 'CLOSED':
      return 'bg-green-400/15'
    case 'HALF_OPEN':
      return 'bg-yellow-400/15'
    case 'OPEN':
      return 'bg-red-400/15'
    default:
      return 'bg-[var(--color-stone)]/15'
  }
}

function getCircuitStateDescription(state: CircuitState): string {
  switch (state) {
    case 'CLOSED':
      return 'Normal operation - loop continues'
    case 'HALF_OPEN':
      return 'Testing recovery - one more attempt'
    case 'OPEN':
      return 'Stopped - too many failures'
    default:
      return 'Unknown state'
  }
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

function formatTokens(tokens: number | null): string {
  if (tokens === null || tokens === undefined) return '-'
  if (tokens < 1000) return `${tokens}`
  if (tokens < 1000000) return `${(tokens / 1000).toFixed(1)}k`
  return `${(tokens / 1000000).toFixed(2)}M`
}

export function LoopProgressTab({ run, onRunUpdated: _onRunUpdated }: LoopProgressTabProps) {
  const [iterations, setIterations] = useState<RalphIteration[]>([])
  const [loading, setLoading] = useState(true)
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Fetch iterations
  const loadIterations = useCallback(async () => {
    if (!run.ralph_enabled) return

    try {
      const data: RalphIterationsResponse = await fetchRalphIterations(run.id, 100)
      setIterations(data.iterations)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load iterations')
    } finally {
      setLoading(false)
    }
  }, [run.id, run.ralph_enabled])

  // Initial load and auto-refresh for running loops
  useEffect(() => {
    loadIterations()

    // Auto-refresh every 5 seconds if run is still running
    if (run.status === 'running') {
      const intervalId = setInterval(loadIterations, 5000)
      return () => clearInterval(intervalId)
    }
  }, [loadIterations, run.status])

  // Handle stop loop
  const handleStopLoop = async () => {
    if (run.status !== 'running') return

    setStopping(true)
    try {
      const result = await stopLoop(run.id)
      if (result.success) {
        toast.success('Loop stopped', {
          description: result.message,
        })
        // Refresh iterations to show final state
        loadIterations()
      }
    } catch (err) {
      toast.error('Failed to stop loop', {
        description: err instanceof Error ? err.message : 'Unknown error',
      })
    } finally {
      setStopping(false)
    }
  }

  if (!run.ralph_enabled) {
    return (
      <div className="flex flex-col items-center justify-center h-32 text-[var(--color-stone)]/50">
        <RefreshCw className="w-6 h-6 mb-2 opacity-50" />
        <span className="text-body">This run is not using Ralph Loop</span>
      </div>
    )
  }

  if (loading && iterations.length === 0) {
    return (
      <div className="flex items-center justify-center h-32">
        <RefreshCw className="w-4 h-4 animate-spin text-[var(--color-stone)]/50" />
      </div>
    )
  }

  const loopCount = run.loop_count || 0
  const maxLoops = run.max_loops || 50
  const progressPercent = Math.min(100, (loopCount / maxLoops) * 100)
  const circuitState = (run.circuit_state as CircuitState) || 'CLOSED'
  const completionConfidence = run.completion_confidence || 0
  const costUsd = run.cost_usd || 0
  const maxCostUsd = run.max_cost_usd || null
  const callsThisHour = run.calls_this_hour || 0
  const maxCallsPerHour = run.max_calls_per_hour || 100

  // Safety metrics
  const noProgressStreak = run.consecutive_no_progress || 0
  const sameErrorStreak = run.consecutive_same_error || 0
  const testOnlyLoops = run.test_only_loops || 0

  return (
    <div className="p-3 overflow-y-auto h-full space-y-4">
      {/* Loop Status Section */}
      <div className="border border-[rgba(163,163,163,0.08)] rounded-sm p-4">
        <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70 mb-3">
          Loop Status
        </h3>

        {/* Progress bar */}
        <div className="mb-4">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-body text-[var(--color-paper)]">
              Iteration {loopCount} of {maxLoops}
            </span>
            <span className="text-mono text-body text-[var(--color-stone)]/60">
              {Math.round(progressPercent)}%
            </span>
          </div>
          <div className="h-2 bg-[rgba(163,163,163,0.15)] rounded-full overflow-hidden">
            <div
              className={cn(
                'h-full rounded-full transition-all duration-300',
                run.status === 'running' ? 'bg-[var(--color-sky)]' : 'bg-[var(--color-jade)]'
              )}
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>

        {/* Status grid */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {/* Circuit State */}
          <div className="flex flex-col gap-1">
            <span className="text-body text-[var(--color-stone)]/60">Circuit State</span>
            <span
              className={cn(
                'flex items-center gap-1.5 px-2 py-1 rounded-sm text-body w-fit',
                getCircuitStateBg(circuitState),
                getCircuitStateColor(circuitState)
              )}
              title={getCircuitStateDescription(circuitState)}
            >
              <Zap className="w-3 h-3" />
              {circuitState}
            </span>
          </div>

          {/* Completion Confidence */}
          <div className="flex flex-col gap-1">
            <span className="text-body text-[var(--color-stone)]/60">Confidence</span>
            <span
              className={cn(
                'text-body font-mono',
                completionConfidence >= 80
                  ? 'text-[var(--color-jade)]'
                  : completionConfidence >= 50
                    ? 'text-yellow-400'
                    : 'text-[var(--color-paper)]'
              )}
            >
              {Math.round(completionConfidence)}%
            </span>
          </div>

          {/* Cost */}
          <div className="flex flex-col gap-1">
            <span className="text-body text-[var(--color-stone)]/60">Cost</span>
            <span className="text-body font-mono text-[var(--color-harvest)]">
              ${costUsd.toFixed(2)}
              {maxCostUsd && (
                <span className="text-[var(--color-stone)]/50"> / ${maxCostUsd.toFixed(2)}</span>
              )}
            </span>
          </div>

          {/* Rate Limit */}
          <div className="flex flex-col gap-1">
            <span className="text-body text-[var(--color-stone)]/60">Calls/Hour</span>
            <span
              className={cn(
                'text-body font-mono',
                callsThisHour >= maxCallsPerHour * 0.8
                  ? 'text-[var(--color-vermillion)]'
                  : 'text-[var(--color-paper)]'
              )}
            >
              {callsThisHour}/{maxCallsPerHour}
            </span>
          </div>
        </div>

        {/* Completion reason if finished */}
        {run.completion_reason && (
          <div className="mt-3 pt-3 border-t border-[rgba(163,163,163,0.08)]">
            <span className="text-body text-[var(--color-stone)]/60">Completion:</span>
            <span className="ml-2 text-body text-[var(--color-jade)]">{run.completion_reason}</span>
          </div>
        )}
      </div>

      {/* Safety Metrics Section */}
      <div className="border border-[rgba(163,163,163,0.08)] rounded-sm p-4">
        <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70 mb-3">
          Safety Metrics
        </h3>

        <div className="grid grid-cols-3 gap-4">
          {/* No Progress Streak */}
          <div className="flex items-center gap-2">
            <div
              className={cn(
                'w-8 h-8 rounded-full flex items-center justify-center',
                noProgressStreak >= 3
                  ? 'bg-[rgba(199,62,58,0.15)] text-[var(--color-vermillion)]'
                  : noProgressStreak >= 2
                    ? 'bg-[rgba(245,158,11,0.15)] text-yellow-400'
                    : 'bg-[rgba(45,212,191,0.15)] text-[var(--color-jade)]'
              )}
            >
              {noProgressStreak >= 3 ? (
                <XCircle className="w-4 h-4" />
              ) : noProgressStreak >= 2 ? (
                <AlertTriangle className="w-4 h-4" />
              ) : (
                <CheckCircle className="w-4 h-4" />
              )}
            </div>
            <div>
              <p className="text-body text-[var(--color-paper)]">No-Progress</p>
              <p className="text-body text-[var(--color-stone)]/60">
                {noProgressStreak}/5 threshold
              </p>
            </div>
          </div>

          {/* Same Error Streak */}
          <div className="flex items-center gap-2">
            <div
              className={cn(
                'w-8 h-8 rounded-full flex items-center justify-center',
                sameErrorStreak >= 3
                  ? 'bg-[rgba(199,62,58,0.15)] text-[var(--color-vermillion)]'
                  : sameErrorStreak >= 2
                    ? 'bg-[rgba(245,158,11,0.15)] text-yellow-400'
                    : 'bg-[rgba(45,212,191,0.15)] text-[var(--color-jade)]'
              )}
            >
              {sameErrorStreak >= 3 ? (
                <XCircle className="w-4 h-4" />
              ) : sameErrorStreak >= 2 ? (
                <AlertTriangle className="w-4 h-4" />
              ) : (
                <CheckCircle className="w-4 h-4" />
              )}
            </div>
            <div>
              <p className="text-body text-[var(--color-paper)]">Same-Error</p>
              <p className="text-body text-[var(--color-stone)]/60">
                {sameErrorStreak}/5 threshold
              </p>
            </div>
          </div>

          {/* Test-Only Loops */}
          <div className="flex items-center gap-2">
            <div
              className={cn(
                'w-8 h-8 rounded-full flex items-center justify-center',
                testOnlyLoops >= 3
                  ? 'bg-[rgba(199,62,58,0.15)] text-[var(--color-vermillion)]'
                  : testOnlyLoops >= 2
                    ? 'bg-[rgba(245,158,11,0.15)] text-yellow-400'
                    : 'bg-[rgba(45,212,191,0.15)] text-[var(--color-jade)]'
              )}
            >
              {testOnlyLoops >= 3 ? (
                <XCircle className="w-4 h-4" />
              ) : testOnlyLoops >= 2 ? (
                <AlertTriangle className="w-4 h-4" />
              ) : (
                <CheckCircle className="w-4 h-4" />
              )}
            </div>
            <div>
              <p className="text-body text-[var(--color-paper)]">Test-Only</p>
              <p className="text-body text-[var(--color-stone)]/60">{testOnlyLoops}/3 threshold</p>
            </div>
          </div>
        </div>
      </div>

      {/* Iteration History Section */}
      <div className="border border-[rgba(163,163,163,0.08)] rounded-sm p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-body uppercase tracking-widest text-[var(--color-stone)]/70">
            Iteration History
          </h3>
          <button
            onClick={loadIterations}
            className="p-1 text-[var(--color-stone)]/50 hover:text-[var(--color-paper)] transition-colors"
            title="Refresh"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
          </button>
        </div>

        {error ? (
          <p className="text-body text-[var(--color-vermillion)]">{error}</p>
        ) : iterations.length === 0 ? (
          <p className="text-body text-[var(--color-stone)]/50 italic">No iterations yet</p>
        ) : (
          <div className="space-y-1">
            {/* Header */}
            <div className="grid grid-cols-[40px_60px_50px_60px_70px_70px_1fr] gap-2 text-body text-[var(--color-stone)]/60 pb-1 border-b border-[rgba(163,163,163,0.08)]">
              <span>#</span>
              <span>Duration</span>
              <span>Files</span>
              <span>Progress</span>
              <span>Tokens</span>
              <span>Cost</span>
              <span>Status</span>
            </div>

            {/* Rows - newest first */}
            {iterations
              .slice()
              .reverse()
              .map((iteration) => (
                <div
                  key={iteration.id}
                  className={cn(
                    'grid grid-cols-[40px_60px_50px_60px_70px_70px_1fr] gap-2 text-body py-1.5 border-b border-[rgba(163,163,163,0.05)] items-center',
                    iteration.has_errors && 'bg-[rgba(199,62,58,0.05)]'
                  )}
                >
                  {/* Loop number */}
                  <span className="text-[var(--color-paper)] font-mono">
                    {iteration.loop_number}
                  </span>

                  {/* Duration */}
                  <span className="text-[var(--color-stone)]/70 flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    {formatDuration(iteration.duration_seconds)}
                  </span>

                  {/* Files changed */}
                  <span
                    className={cn(
                      'flex items-center gap-1',
                      iteration.files_changed > 0
                        ? 'text-[var(--color-sky)]'
                        : 'text-[var(--color-stone)]/50'
                    )}
                  >
                    <FileCode className="w-3 h-3" />
                    {iteration.files_changed}
                  </span>

                  {/* Progress detected */}
                  <span>
                    {iteration.progress_detected ? (
                      <CheckCircle className="w-4 h-4 text-[var(--color-jade)]" />
                    ) : (
                      <CircleSlash className="w-4 h-4 text-[var(--color-stone)]/40" />
                    )}
                  </span>

                  {/* Tokens */}
                  <span className="text-[var(--color-stone)]/70 font-mono text-[0.65rem]">
                    {formatTokens(iteration.input_tokens + iteration.output_tokens)}
                  </span>

                  {/* Cost */}
                  <span className="text-[var(--color-harvest)] font-mono flex items-center gap-0.5">
                    <DollarSign className="w-2.5 h-2.5" />
                    {iteration.cost_usd.toFixed(2)}
                  </span>

                  {/* Status indicators */}
                  <div className="flex items-center gap-1.5">
                    {iteration.has_completion_signal && (
                      <span
                        className="px-1.5 py-0.5 rounded-sm text-body bg-[rgba(45,212,191,0.15)] text-[var(--color-jade)]"
                        title="Completion signal detected"
                      >
                        DONE
                      </span>
                    )}
                    {iteration.has_errors && (
                      <span
                        className="px-1.5 py-0.5 rounded-sm text-body bg-[rgba(199,62,58,0.15)] text-[var(--color-vermillion)]"
                        title={iteration.error_message || 'Error occurred'}
                      >
                        ERR
                      </span>
                    )}
                    {iteration.is_test_only && (
                      <span
                        className="px-1.5 py-0.5 rounded-sm text-body bg-[rgba(102,178,255,0.15)] text-[var(--color-sky)]"
                        title="Only ran tests, no code changes"
                      >
                        TEST
                      </span>
                    )}
                    {!iteration.has_completion_signal &&
                      !iteration.has_errors &&
                      !iteration.is_test_only && (
                        <span className="text-[var(--color-stone)]/40">-</span>
                      )}
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>

      {/* Stop Loop Button */}
      {run.status === 'running' && (
        <div className="pt-2">
          <button
            onClick={handleStopLoop}
            disabled={stopping}
            className={cn(
              'flex items-center justify-center gap-2 w-full py-2.5 rounded-sm text-body uppercase tracking-widest transition-colors',
              stopping
                ? 'bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/50 cursor-wait'
                : 'bg-[rgba(199,62,58,0.15)] border border-[rgba(199,62,58,0.3)] text-[var(--color-vermillion)] hover:bg-[rgba(199,62,58,0.25)]'
            )}
          >
            <Square className="w-4 h-4" />
            {stopping ? 'Stopping...' : 'Stop Loop Early'}
          </button>
        </div>
      )}
    </div>
  )
}
