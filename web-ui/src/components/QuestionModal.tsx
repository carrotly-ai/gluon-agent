import { Check, Clock } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { PendingQuestion, QuestionOption } from '@/lib/types'
import { cn } from '@/lib/utils'

interface QuestionModalProps {
  runId: string
  questions: PendingQuestion[]
  onAnswer: (questionId: string, selectedLabels: string[]) => Promise<void>
  onClose: () => void
}

export function QuestionModal({ runId: _runId, questions, onAnswer, onClose }: QuestionModalProps) {
  const pendingQuestions = questions.filter((q) => q.status === 'pending')

  // Track current question index (for multi-question batches)
  const [currentIdx, setCurrentIdx] = useState(0)
  const [selected, setSelected] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [timeRemaining, setTimeRemaining] = useState<number | null>(null)

  const currentQuestion = pendingQuestions[currentIdx]

  // Calculate time remaining until auto-answer
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
      console.error('Failed to submit answer:', err)
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

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          {/* Header badge */}
          <div className="flex items-center gap-2 mb-2">
            <span className="px-2 py-0.5 bg-[rgba(102,178,255,0.15)] border border-[rgba(102,178,255,0.3)] text-[var(--color-sky)] text-body uppercase tracking-widest rounded-sm">
              {currentQuestion.header}
            </span>
            {pendingQuestions.length > 1 && (
              <span className="text-body text-[var(--color-stone)]/60">
                Question {currentIdx + 1} of {pendingQuestions.length}
              </span>
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
                  'w-full p-3 text-left rounded-sm border transition-colors',
                  isSelected
                    ? 'border-[var(--color-sky)] bg-[rgba(102,178,255,0.1)]'
                    : 'border-[rgba(163,163,163,0.15)] hover:border-[rgba(163,163,163,0.3)] bg-[var(--color-void)]',
                  submitting && 'opacity-60 cursor-not-allowed'
                )}
              >
                <div className="flex items-start gap-2">
                  {/* Selection indicator */}
                  <div
                    className={cn(
                      'w-4 h-4 mt-0.5 rounded-sm border flex items-center justify-center shrink-0',
                      isSelected
                        ? 'bg-[var(--color-sky)] border-[var(--color-sky)]'
                        : 'border-[rgba(163,163,163,0.3)]'
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
                        <span className="px-1.5 py-0.5 bg-[rgba(34,197,94,0.15)] border border-[rgba(34,197,94,0.3)] text-green-400 text-body uppercase tracking-widest rounded-sm">
                          Recommended
                        </span>
                      )}
                    </div>
                    {option.description && (
                      <p className="text-body text-[var(--color-stone)]/70 mt-1 leading-relaxed">
                        {option.description}
                      </p>
                    )}
                  </div>
                </div>
              </button>
            )
          })}
        </div>

        <DialogFooter className="flex items-center justify-between sm:justify-between gap-4">
          {/* Timer warning */}
          {timeRemaining !== null && timeRemaining > 0 ? (
            <div className="flex items-center gap-1.5 text-body text-[var(--color-stone)]/60">
              <Clock className="w-3 h-3" />
              <span>
                Auto-answers in{' '}
                <span
                  className={cn(
                    timeRemaining <= 30 && 'text-[var(--color-harvest)]',
                    timeRemaining <= 10 && 'text-[var(--color-vermillion)]'
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
                ? 'bg-[var(--color-paper)] text-[var(--color-void)] hover:opacity-90'
                : 'bg-[var(--color-stone)]/20 text-[var(--color-stone)]/50 cursor-not-allowed'
            )}
          >
            {submitting ? 'Submitting...' : 'Submit Answer'}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
