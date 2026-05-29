import { GitBranch, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { forkRun } from '@/lib/api'
import type { Run } from '@/lib/types'
import { cn } from '@/lib/utils'

interface ForkRunDialogProps {
  open: boolean
  /** The run being forked. Provides parent title context. */
  parent: Run | null
  onClose: () => void
  /** Called with the newly-created child run when the fork succeeds. */
  onForked: (child: Run) => void
}

/**
 * Modal for forking a Claude session.
 *
 * The user enters a redirect prompt (e.g., "now write the docs") and an
 * optional custom title. The new child run inherits the parent's conversation
 * state via the SDK's `fork_session=True` mechanism — see `runner.fork_run`.
 */
export function ForkRunDialog({ open, parent, onClose, onForked }: ForkRunDialogProps) {
  const [prompt, setPrompt] = useState('')
  const [title, setTitle] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const promptRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (open) {
      setPrompt('')
      setTitle('')
      setTimeout(() => promptRef.current?.focus(), 50)
    }
  }, [open])

  // Escape, overlay click and focus trap are provided by the Radix Dialog
  // primitive — no bespoke keydown listener needed.

  const submit = async () => {
    if (!prompt.trim() || !parent) return
    setSubmitting(true)
    try {
      const child = await forkRun(parent.id, {
        prompt: prompt.trim(),
        custom_title: title.trim() || null,
      })
      toast.success('Forked', {
        description: child.custom_title || 'New thread started from this session.',
      })
      onForked(child)
      onClose()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to fork')
    } finally {
      setSubmitting(false)
    }
  }

  const parentLabel = parent ? parent.custom_title?.trim() || parent.prompt.slice(0, 80) : ''

  return (
    <Dialog open={open && parent !== null} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="w-full max-w-md p-0 gap-0 flex flex-col" showCloseButton={false}>
        <div className="flex items-start justify-between px-4 pt-4 pb-2">
          <div className="min-w-0">
            <DialogTitle className="flex items-center gap-2 text-caption font-normal leading-normal uppercase tracking-widest text-[var(--color-stone)]/60">
              <GitBranch className="w-3 h-3" />
              <span>Fork session</span>
            </DialogTitle>
            <p
              className="text-body text-[var(--color-paper)] mt-1 line-clamp-2"
              title={parent?.prompt}
            >
              from: {parentLabel}
            </p>
          </div>
          <button
            type="button"
            className="p-1 text-[var(--color-stone)]/60 hover:text-[var(--color-stone)] rounded-sm"
            onClick={onClose}
            aria-label="Close fork dialog"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
        <div className="px-4 pb-4 flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60">
              New direction
            </span>
            <textarea
              ref={promptRef}
              className={cn(
                'bg-[var(--color-void)] border border-[rgba(163,163,163,0.1)] rounded-sm',
                'px-3 py-2 text-input text-[var(--color-paper)] focus:outline-none',
                'focus:border-[rgba(163,163,163,0.2)] resize-none min-h-[80px]'
              )}
              placeholder="e.g. now write the docs for what we just built"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                  e.preventDefault()
                  void submit()
                }
              }}
              disabled={submitting}
              rows={3}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60">
              Title <span className="lowercase opacity-60">(optional)</span>
            </span>
            <input
              type="text"
              className={cn(
                'bg-[var(--color-void)] border border-[rgba(163,163,163,0.1)] rounded-sm',
                'px-3 py-2 text-input text-[var(--color-paper)] focus:outline-none',
                'focus:border-[rgba(163,163,163,0.2)]'
              )}
              placeholder="e.g. Docs spinoff"
              value={title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
              disabled={submitting}
            />
          </label>
        </div>
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-[rgba(163,163,163,0.08)]">
          <button
            type="button"
            className="px-3 py-1.5 text-caption uppercase tracking-widest text-[var(--color-stone)] hover:text-[var(--color-paper)] rounded-sm"
            onClick={onClose}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            type="button"
            className={cn(
              'px-3 py-1.5 text-caption uppercase tracking-widest rounded-sm transition-colors',
              prompt.trim() && !submitting
                ? 'bg-[var(--color-paper)] text-[var(--color-void)] hover:opacity-90'
                : 'bg-[var(--color-stone)]/20 text-[var(--color-stone)]/50 cursor-not-allowed'
            )}
            onClick={() => void submit()}
            disabled={!prompt.trim() || submitting}
          >
            {submitting ? 'Forking…' : 'Fork'}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
