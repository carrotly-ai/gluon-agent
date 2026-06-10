import { Check, Clock, GitBranch } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { fetchRun } from '@/lib/api'
import type { PendingQuestion, QuestionOption, Run } from '@/lib/types'
import { cn } from '@/lib/utils'

interface QuestionModalProps {
  runId: string
  questions: PendingQuestion[]
  onAnswer: (questionId: string, selectedLabels: string[]) => Promise<void>
  onClose: () => void
}

export function QuestionModal({ runId, questions, onAnswer, onClose }: QuestionModalProps) {
  const pendingQuestions = questions.filter((q) => q.status === 'pending')

  // Track current question index (for multi-question batches)
  const [currentIdx, setCurrentIdx] = useState(0)
  const [selected, setSelected] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [timeRemaining, setTimeRemaining] = useState<number | null>(null)
  const [run, setRun] = useState<Run | null>(null)

  const currentQuestion = pendingQuestions[currentIdx]

  // Fetch run metadata for context bar
  useEffect(() => {
    if (!runId) return
    fetchRun(runId)
      .then(setRun)
      .catch(() => {})
  }, [runId])

  // Calculate time remaining until pause
  useEffect(() => {
    if (!currentQuestion?.expires_at) {
      setTimeRemaining(null)
      return
    }

    const updateTimer = () => {
      const expiresAt = new Date(currentQuestion.expires_at!).getTime()
      const now = Date.now()
      const remaining = Math.max(0, Math.floor((expiresAt - now) / 1000))
      setTimeRemaining(remaining)
    }

    updateTimer()
    const interval = setInterval(updateTimer, 1000)

    return () => clearInterval(interval)
  }, [currentQuestion?.expires_at])

  // Reset selection when question changes
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset selection when active question changes
  useEffect(() => {
    setSelected([])
  }, [currentQuestion?.id])

  const handleSelect = useCallback(
    (label: string) => {
      if (!currentQuestion) return

      if (currentQuestion.multi_select) {
        setSelected((prev) =>
          prev.includes(label) ? prev.filter((l) => l !== label) : [...prev, label]
        )
      } else {
        setSelected([label])
      }
    },
    [currentQuestion]
  )

  const handleSubmit = useCallback(async () => {
    if (!currentQuestion || selected.length === 0) return

    setSubmitting(true)
    setSubmitError(null)
    try {
      await onAnswer(currentQuestion.id, selected)
      // Move to next question or close
      if (currentIdx < pendingQuestions.length - 1) {
        setCurrentIdx((prev) => prev + 1)
        setSelected([])
      } else {
        // All questions answered
        onClose()
      }
    } catch (err) {
      // Keep the selection intact so the user can retry before the question
      // expires, and surface the failure instead of leaving the run blocked
      // with no feedback.
      console.error('Failed to submit answer:', err)
      const message = err instanceof Error ? err.message : 'Failed to submit answer'
      setSubmitError(message)
      toast.error(message)
    } finally {
      setSubmitting(false)
    }
  }, [currentQuestion, currentIdx, pendingQuestions.length, onAnswer, onClose, selected])

  // If no pending questions, don't render
  if (pendingQuestions.length === 0 || !currentQuestion) {
    return null
  }

  // Helper to check if an option is recommended
  const isRecommended = (option: QuestionOption): boolean => {
    const label = option.label.toLowerCase()
    return label.includes('(recommended)') || label.includes('recommended')
  }

  // Format time remaining
  const formatTime = (seconds: number): string => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  // Truncate prompt for display
  const taskPrompt = run?.prompt ?? null
  const truncatedPrompt = taskPrompt
    ? taskPrompt.length > 120
      ? `${taskPrompt.substring(0, 120)}\u2026`
      : taskPrompt
    : null

  const shortRunId = runId.substring(0, 8)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="dialog-content sm:max-w-lg outline-none max-h-[85vh] overflow-y-auto">
        {/* Run context bar */}
        {run && (
          <div className="border-b border-[var(--color-stone)]/10 pb-3 mb-1 -mt-1 space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-caption font-medium text-[var(--color-paper)]">
                {run.project_name}
              </span>
              <span className="text-caption text-[var(--color-stone)]/20">/</span>
              <span className="text-caption text-[var(--color-stone)]/40 font-mono">
                {shortRunId}
              </span>
              {run.branch_name && (
                <>
                  <span className="text-caption text-[var(--color-stone)]/20">/</span>
                  <span className="flex items-center gap-1 text-caption text-[var(--color-sky)]/70">
                    <GitBranch className="w-3 h-3" />
                    <span className="truncate max-w-[140px]">{run.branch_name}</span>
                  </span>
                </>
              )}
            </div>
            {truncatedPrompt && (
              <p className="text-caption text-[var(--color-stone)]/40 leading-relaxed line-clamp-2">
                {truncatedPrompt}
              </p>
            )}
          </div>
        )}

        <DialogHeader>
          {/* Header badge + question counter */}
          <div className="flex items-center gap-2 mb-2">
            <span className="px-2 py-0.5 bg-[var(--color-sky)]/10 text-[var(--color-sky)] text-body uppercase tracking-widest rounded-sm">
              {currentQuestion.header}
            </span>
            {pendingQuestions.length > 1 && (
              <span className="text-body text-[var(--color-stone)]/50">
                Question {currentIdx + 1} of {pendingQuestions.length}
              </span>
            )}
            {currentQuestion.multi_select && (
              <span className="text-body text-[var(--color-harvest)]/80">Select multiple</span>
            )}
          </div>
          <DialogTitle className="text-[var(--color-paper)] font-normal text-lg leading-relaxed">
            {currentQuestion.question_text}
          </DialogTitle>
        </DialogHeader>

        {/* Options */}
        <div className="space-y-2 py-2">
          {currentQuestion.options.map((option) => {
            const isSelected = selected.includes(option.label)
            const recommended = isRecommended(option)

            return (
              <button
                key={option.label}
                type="button"
                onClick={() => handleSelect(option.label)}
                disabled={submitting}
                className={cn(
                  'w-full p-3 text-left rounded-sm border transition-colors outline-none',
                  isSelected
                    ? 'border-[var(--color-sky)] bg-[var(--color-sky)]/8'
                    : 'border-[var(--color-stone)]/10 hover:border-[var(--color-stone)]/25 bg-[var(--color-stone)]/3',
                  submitting && 'opacity-60 cursor-not-allowed'
                )}
              >
                <div className="flex items-start gap-2">
                  {/* Selection indicator */}
                  <div
                    className={cn(
                      'w-4 h-4 mt-0.5 shrink-0 border flex items-center justify-center',
                      currentQuestion.multi_select ? 'rounded-sm' : 'rounded-full',
                      isSelected
                        ? 'bg-[var(--color-sky)] border-[var(--color-sky)]'
                        : 'border-[var(--color-stone)]/25'
                    )}
                  >
                    {isSelected && <Check className="w-3 h-3 text-[var(--color-void)]" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-body text-[var(--color-paper)] font-medium">
                        {option.label}
                      </span>
                      {recommended && (
                        <span className="px-1.5 py-0.5 bg-[var(--color-jade)]/10 text-[var(--color-jade)] text-body uppercase tracking-widest rounded-sm">
                          Recommended
                        </span>
                      )}
                    </div>
                    {option.description && (
                      <p className="text-body text-[var(--color-stone)]/50 mt-1 leading-relaxed">
                        {option.description}
                      </p>
                    )}
                  </div>
                </div>
              </button>
            )
          })}
        </div>

        {submitError && (
          <p
            className="text-caption text-[var(--color-vermillion)] bg-[var(--color-vermillion)]/10 rounded-md px-3 py-2"
            role="alert"
          >
            {submitError} — your selection was kept; try again.
          </p>
        )}

        <DialogFooter className="flex items-center justify-between sm:justify-between gap-4">
          {/* Timer warning */}
          {timeRemaining !== null && timeRemaining > 0 ? (
            <div className="flex items-center gap-1.5 text-body text-[var(--color-stone)]/50">
              <Clock className="w-3 h-3" />
              <span>
                Pauses in{' '}
                <span
                  className={cn(
                    timeRemaining <= 60 && 'text-[var(--color-harvest)]',
                    timeRemaining <= 30 && 'text-[var(--color-vermillion)]'
                  )}
                >
                  {formatTime(timeRemaining)}
                </span>
              </span>
            </div>
          ) : (
            <div />
          )}

          {/* Submit button */}
          <button
            type="button"
            onClick={handleSubmit}
            disabled={selected.length === 0 || submitting}
            className={cn(
              'flex items-center gap-2 px-4 py-2 text-body uppercase tracking-widest rounded-sm transition-colors',
              selected.length > 0 && !submitting
                ? 'bg-[var(--color-sky)] text-[var(--color-void)] hover:opacity-90'
                : 'bg-[var(--color-stone)]/10 text-[var(--color-stone)]/40 cursor-not-allowed'
            )}
          >
            {submitting ? 'Submitting...' : 'Submit Answer'}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
