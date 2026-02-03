import {
  AlertTriangle,
  Check,
  CheckCircle,
  CircleSlash,
  Clock,
  DollarSign,
  FileCode,
  RefreshCw,
  Square,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
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

// Compact safety badge component with tooltip
function SafetyBadge({
  label,
  value,
  threshold,
  tooltip,
}: {
  label: string
  value: number
  threshold: number
  tooltip: string
}) {
  const isWarning = value >= threshold * 0.6
  const isDanger = value >= threshold

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            'flex items-center gap-1 cursor-help',
            isDanger
              ? 'text-[var(--color-vermillion)]'
              : isWarning
                ? 'text-yellow-400'
                : 'text-[var(--color-jade)]'
          )}
        >
          {isDanger ? <AlertTriangle className="w-3 h-3" /> : <Check className="w-3 h-3" />}
          <span className="text-[var(--color-stone)]/70">{label}</span>
          <span className="font-mono">
            ({value}/{threshold})
          </span>
        </span>
      </TooltipTrigger>
      <TooltipContent>
        <p>{tooltip}</p>
        {isDanger && (
          <p className="mt-1 text-[var(--color-vermillion)]">
            Threshold reached - circuit may open
          </p>
        )}
      </TooltipContent>
    </Tooltip>
  )
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
  const maxCostUsd = run.max_cost_usd || null
  const callsThisHour = run.calls_this_hour || 0
  const maxCallsPerHour = run.max_calls_per_hour || 100

  // Safety metrics
  const noProgressStreak = run.consecutive_no_progress || 0
  const sameErrorStreak = run.consecutive_same_error || 0
  const testOnlyLoops = run.test_only_loops || 0

  // Calculate total cost from iterations (accurate sum)
  const totalCost = iterations.reduce((sum, iter) => sum + iter.cost_usd, 0)
  // Fall back to run.cost_usd if no iterations loaded yet
  const displayCost = iterations.length > 0 ? totalCost : run.cost_usd || 0

  return (
    <TooltipProvider>
      <div className="p-2 sm:p-3 overflow-y-auto h-full space-y-2 sm:space-y-3">
        {/* Progress Section - Full width with inline Stop button on desktop */}
        <div className="flex items-center gap-2 sm:gap-3">
          <div className="flex-1 border border-[rgba(163,163,163,0.08)] rounded-sm p-2 sm:p-3">
            <div className="flex items-center gap-2 sm:gap-3">
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="text-body uppercase tracking-widest text-[var(--color-stone)]/70 shrink-0 cursor-help">
                    Loop
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  <p className="font-medium">Loop Iteration Counter</p>
                  <p className="text-[var(--color-stone)]/70 mt-1">
                    Number of autonomous iterations completed. Each loop analyzes progress and
                    decides next steps.
                  </p>
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="text-mono text-body sm:text-sm text-[var(--color-paper)] shrink-0 font-medium cursor-help">
                    {loopCount}/{maxLoops}
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  <p>
                    {loopCount} iterations completed out of {maxLoops} maximum allowed
                  </p>
                </TooltipContent>
              </Tooltip>
              <div className="flex-1 h-1.5 sm:h-2 bg-[rgba(163,163,163,0.15)] rounded-full overflow-hidden">
                <div
                  className={cn(
                    'h-full rounded-full transition-all duration-300',
                    run.status === 'running' ? 'bg-[var(--color-sky)]' : 'bg-[var(--color-jade)]'
                  )}
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
              <span className="text-mono text-body sm:text-sm text-[var(--color-stone)]/60 shrink-0">
                {Math.round(progressPercent)}%
              </span>
            </div>
          </div>
          {/* Stop button - inline on desktop */}
          {run.status === 'running' && (
            <button
              onClick={handleStopLoop}
              disabled={stopping}
              className={cn(
                'hidden sm:flex items-center gap-2 px-3 py-2 rounded-sm text-body uppercase tracking-widest transition-colors shrink-0',
                stopping
                  ? 'bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/50 cursor-wait'
                  : 'bg-[rgba(199,62,58,0.15)] border border-[rgba(199,62,58,0.3)] text-[var(--color-vermillion)] hover:bg-[rgba(199,62,58,0.25)]'
              )}
            >
              <Square className="w-3.5 h-3.5" />
              {stopping ? '...' : 'Stop'}
            </button>
          )}
        </div>

        {/* Unified Status Row - Cost, Circuit, Safety, Completion */}
        <div className="flex flex-wrap items-center gap-x-3 sm:gap-x-4 gap-y-1 text-body sm:text-sm px-1">
          {/* Cost */}
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="flex items-center gap-1 cursor-help">
                <DollarSign className="w-3 h-3 text-[var(--color-harvest)]" />
                <span className="font-mono text-[var(--color-harvest)]">
                  ${displayCost.toFixed(2)}
                </span>
                {maxCostUsd && (
                  <span className="text-[var(--color-stone)]/40">/ ${maxCostUsd.toFixed(0)}</span>
                )}
              </span>
            </TooltipTrigger>
            <TooltipContent>
              <p className="font-medium">Cost Tracking</p>
              <p className="text-[var(--color-stone)]/70 mt-1">
                ${displayCost.toFixed(2)} spent
                {maxCostUsd ? ` of $${maxCostUsd.toFixed(0)} budget` : ''}. Loop stops automatically
                if budget is exceeded.
              </p>
            </TooltipContent>
          </Tooltip>

          {/* API Calls */}
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="flex items-center gap-1 cursor-help">
                <span
                  className={cn(
                    'font-mono',
                    callsThisHour >= maxCallsPerHour * 0.8
                      ? 'text-[var(--color-vermillion)]'
                      : 'text-[var(--color-stone)]/70'
                  )}
                >
                  {callsThisHour}/{maxCallsPerHour}
                </span>
                <span className="text-[var(--color-stone)]/40">calls/hr</span>
              </span>
            </TooltipTrigger>
            <TooltipContent>
              <p className="font-medium">Rate Limiting</p>
              <p className="text-[var(--color-stone)]/70 mt-1">
                {callsThisHour} API calls made in the last hour. Limit: {maxCallsPerHour}/hr.
                {callsThisHour >= maxCallsPerHour * 0.8 && (
                  <span className="block mt-1 text-[var(--color-vermillion)]">
                    Approaching rate limit - loop may slow down
                  </span>
                )}
              </p>
            </TooltipContent>
          </Tooltip>

          <span className="text-[var(--color-stone)]/20">|</span>

          {/* Circuit State */}
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className={cn(
                  'flex items-center gap-1 cursor-help',
                  getCircuitStateColor(circuitState)
                )}
              >
                <Zap className="w-3 h-3" />
                <span className="font-mono">{circuitState}</span>
              </span>
            </TooltipTrigger>
            <TooltipContent>
              <p className="font-medium">Circuit Breaker State</p>
              <p className="text-[var(--color-stone)]/70 mt-1">
                {circuitState === 'CLOSED' &&
                  'Normal operation - loop is running and making progress.'}
                {circuitState === 'HALF_OPEN' &&
                  'Testing recovery - checking if the loop can continue after errors.'}
                {circuitState === 'OPEN' &&
                  'Paused - too many consecutive errors or no progress detected. Loop will stop.'}
              </p>
            </TooltipContent>
          </Tooltip>

          {/* Confidence (if > 0) */}
          {completionConfidence > 0 && (
            <span
              className={cn(
                'font-mono',
                completionConfidence >= 80
                  ? 'text-[var(--color-jade)]'
                  : completionConfidence >= 50
                    ? 'text-yellow-400'
                    : 'text-[var(--color-stone)]/60'
              )}
            >
              {Math.round(completionConfidence)}%
            </span>
          )}

          {/* Completion Reason (if completed) */}
          {run.completion_reason && (
            <span
              className="text-[var(--color-jade)] truncate max-w-[200px] sm:max-w-none"
              title={run.completion_reason}
            >
              {run.completion_reason}
            </span>
          )}

          <span className="text-[var(--color-stone)]/20">|</span>

          {/* Safety Badges */}
          <SafetyBadge
            label="Progress"
            value={noProgressStreak}
            threshold={5}
            tooltip="Consecutive loops with no detectable progress. If the agent keeps running without making meaningful changes, the circuit breaker opens to prevent wasted resources."
          />
          <SafetyBadge
            label="Errors"
            value={sameErrorStreak}
            threshold={5}
            tooltip="Consecutive loops hitting the same error. Repeated identical errors suggest the agent is stuck. Circuit opens to allow intervention."
          />
          <SafetyBadge
            label="Tests"
            value={testOnlyLoops}
            threshold={3}
            tooltip="Consecutive loops that only ran tests without making code changes. May indicate the agent is stuck in a test-only cycle."
          />
        </div>

        {/* Iteration History - Responsive table */}
        <div className="border border-[rgba(163,163,163,0.08)] rounded-sm p-2 sm:p-3 flex-1">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-body sm:text-sm uppercase tracking-widest text-[var(--color-stone)]/70">
              Iterations
            </h3>
            <button
              onClick={loadIterations}
              className="p-1 text-[var(--color-stone)]/50 hover:text-[var(--color-paper)] transition-colors"
              title="Refresh"
            >
              <RefreshCw className={cn('w-3.5 h-3.5 sm:w-4 sm:h-4', loading && 'animate-spin')} />
            </button>
          </div>

          {error ? (
            <p className="text-body text-[var(--color-vermillion)]">{error}</p>
          ) : iterations.length === 0 ? (
            <p className="text-body text-[var(--color-stone)]/50 italic">No iterations yet</p>
          ) : (
            <table className="w-full text-body sm:text-sm">
              <thead>
                <tr className="text-[var(--color-stone)]/60 text-left border-b border-[rgba(163,163,163,0.08)]">
                  <th className="py-1 pr-2 sm:pr-4 font-normal w-8 sm:w-12">#</th>
                  <th className="py-1 pr-2 sm:pr-4 font-normal">Time</th>
                  <th className="py-1 pr-2 sm:pr-4 font-normal">Files</th>
                  <th className="py-1 pr-2 sm:pr-4 font-normal text-center w-10 sm:w-14">OK</th>
                  <th className="py-1 pr-2 sm:pr-4 font-normal hidden sm:table-cell">Tokens</th>
                  <th className="py-1 pr-2 sm:pr-4 font-normal">Cost</th>
                  <th className="py-1 font-normal">Status</th>
                </tr>
              </thead>
              <tbody>
                {iterations
                  .slice()
                  .reverse()
                  .map((iteration) => (
                    <tr
                      key={iteration.id}
                      className={cn(
                        'border-b border-[rgba(163,163,163,0.03)]',
                        iteration.has_errors && 'bg-[rgba(199,62,58,0.05)]'
                      )}
                    >
                      {/* Loop number */}
                      <td className="py-1.5 sm:py-2 pr-2 sm:pr-4 font-mono text-[var(--color-paper)]">
                        {iteration.loop_number}
                      </td>

                      {/* Duration */}
                      <td className="py-1.5 sm:py-2 pr-2 sm:pr-4 text-[var(--color-stone)]/70">
                        <span className="flex items-center gap-1">
                          <Clock className="w-3 h-3 shrink-0 hidden sm:block" />
                          {formatDuration(iteration.duration_seconds)}
                        </span>
                      </td>

                      {/* Files changed */}
                      <td className="py-1.5 sm:py-2 pr-2 sm:pr-4">
                        <span
                          className={cn(
                            'flex items-center gap-1',
                            iteration.files_changed > 0
                              ? 'text-[var(--color-sky)]'
                              : 'text-[var(--color-stone)]/50'
                          )}
                        >
                          <FileCode className="w-3 h-3 shrink-0" />
                          {iteration.files_changed}
                        </span>
                      </td>

                      {/* Progress detected */}
                      <td className="py-1.5 sm:py-2 pr-2 sm:pr-4 text-center">
                        {iteration.progress_detected ? (
                          <CheckCircle className="w-4 h-4 text-[var(--color-jade)] inline-block" />
                        ) : (
                          <CircleSlash className="w-4 h-4 text-[var(--color-stone)]/30 inline-block" />
                        )}
                      </td>

                      {/* Tokens - hidden on mobile */}
                      <td className="py-1.5 sm:py-2 pr-2 sm:pr-4 font-mono text-[var(--color-stone)]/60 hidden sm:table-cell">
                        {formatTokens(iteration.input_tokens + iteration.output_tokens)}
                      </td>

                      {/* Cost */}
                      <td className="py-1.5 sm:py-2 pr-2 sm:pr-4 font-mono text-[var(--color-harvest)]">
                        <span className="flex items-center gap-0.5">
                          <DollarSign className="w-3 h-3 shrink-0" />
                          {iteration.cost_usd.toFixed(2)}
                        </span>
                      </td>

                      {/* Status indicators */}
                      <td className="py-1.5 sm:py-2">
                        <div className="flex items-center gap-1 flex-wrap">
                          {iteration.has_completion_signal && (
                            <span
                              className="px-1.5 py-0.5 rounded-sm text-[0.6rem] uppercase bg-[rgba(45,212,191,0.15)] text-[var(--color-jade)]"
                              title="Completion signal detected"
                            >
                              Done
                            </span>
                          )}
                          {iteration.has_errors && (
                            <span
                              className="px-1.5 py-0.5 rounded-sm text-[0.6rem] uppercase bg-[rgba(199,62,58,0.15)] text-[var(--color-vermillion)]"
                              title={iteration.error_message || 'Error occurred'}
                            >
                              Err
                            </span>
                          )}
                          {iteration.is_test_only && (
                            <span
                              className="px-1.5 py-0.5 rounded-sm text-[0.6rem] uppercase bg-[rgba(102,178,255,0.15)] text-[var(--color-sky)]"
                              title="Only ran tests"
                            >
                              Test
                            </span>
                          )}
                          {!iteration.has_completion_signal &&
                            !iteration.has_errors &&
                            !iteration.is_test_only && (
                              <span className="text-[var(--color-stone)]/30">—</span>
                            )}
                        </div>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Mobile-only Stop Button */}
        {run.status === 'running' && (
          <button
            onClick={handleStopLoop}
            disabled={stopping}
            className={cn(
              'sm:hidden flex items-center justify-center gap-2 w-full py-2 rounded-sm text-body uppercase tracking-widest transition-colors',
              stopping
                ? 'bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/50 cursor-wait'
                : 'bg-[rgba(199,62,58,0.15)] border border-[rgba(199,62,58,0.3)] text-[var(--color-vermillion)] hover:bg-[rgba(199,62,58,0.25)]'
            )}
          >
            <Square className="w-3.5 h-3.5" />
            {stopping ? 'Stopping...' : 'Stop Loop'}
          </button>
        )}
      </div>
    </TooltipProvider>
  )
}
