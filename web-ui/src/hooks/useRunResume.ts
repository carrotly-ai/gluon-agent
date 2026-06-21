import type { RefObject } from 'react'
import { toast } from 'sonner'
import { cancelRun, queueFollowup, resumeRun, uploadAndAttachImage } from '@/lib/api'
import type { Run } from '@/lib/types'

export interface ResumePendingImage {
  file: File
  preview: string
}

/**
 * Options for {@link useRunResume}. RunDetailDialog and RunDetailPage had
 * byte-identical resume / send-now / queue-follow-up handlers sharing one tight
 * state cluster (the resume prompt, pending image uploads, the textarea ref,
 * and the resuming/queuing flags). The state stays in each component (it drives
 * their JSX); the handlers move here, behavior-identical.
 */
export interface UseRunResumeOptions {
  run: Run | null
  resumePrompt: string
  setResumePrompt: (value: string) => void
  resumePendingImages: ResumePendingImage[]
  setResumePendingImages: (images: ResumePendingImage[]) => void
  resumeTextareaRef: RefObject<HTMLTextAreaElement | null>
  setResuming: (value: boolean) => void
  setQueuing: (value: boolean) => void
  setResumeError: (value: string | null) => void
  /** Refresh the run after the action (both call handleRefresh). */
  onRefresh: () => void
}

export interface RunResumeActions {
  handleResume: () => Promise<void>
  handleSendNow: () => Promise<void>
  handleQueueFollowup: () => Promise<void>
}

/** Shared resume / send-now / queue-follow-up handlers (#165). */
export function useRunResume(options: UseRunResumeOptions): RunResumeActions {
  const {
    run,
    resumePrompt,
    setResumePrompt,
    resumePendingImages,
    setResumePendingImages,
    resumeTextareaRef,
    setResuming,
    setQueuing,
    setResumeError,
    onRefresh,
  } = options

  const resetTextareaHeight = (): void => {
    if (resumeTextareaRef.current) {
      resumeTextareaRef.current.style.height = 'auto'
    }
  }

  const handleResume = async (): Promise<void> => {
    if (!run || !resumePrompt.trim()) return
    setResuming(true)
    setResumeError(null)
    try {
      // Resume continues the same run in-place
      const result = await resumeRun(run.id, resumePrompt.trim())

      // Upload images to the run if any (same run_id)
      if (resumePendingImages.length > 0 && result.run_id) {
        const uploadPromises = resumePendingImages.map((img) =>
          uploadAndAttachImage(result.run_id, img.file).catch((err) => {
            console.error(`Failed to upload image ${img.file.name}:`, err)
            return null
          })
        )
        await Promise.all(uploadPromises)
      }

      resumePendingImages.forEach((img) => URL.revokeObjectURL(img.preview))
      setResumePendingImages([])
      setResumePrompt('')
      resetTextareaHeight()
      onRefresh()
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : 'Failed to resume run')
    } finally {
      setResuming(false)
    }
  }

  const handleQueueFollowup = async (): Promise<void> => {
    if (!run || !resumePrompt.trim()) return
    setQueuing(true)
    setResumeError(null)
    try {
      const result = await queueFollowup(run.id, resumePrompt.trim())

      if (result.action === 'resume_now') {
        // Task is not running - use normal resume instead
        await handleResume()
        return
      }

      // Message queued - clear prompt and refresh to show indicator
      setResumePrompt('')
      resetTextareaHeight()
      toast.success('Message queued - will continue after current task completes')
      onRefresh()
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : 'Failed to queue follow-up')
    } finally {
      setQueuing(false)
    }
  }

  const handleSendNow = async (): Promise<void> => {
    if (!run || !resumePrompt.trim()) return
    setResuming(true)
    setResumeError(null)
    try {
      // Cancel current execution
      await cancelRun(run.id)

      // Small delay for cancellation to propagate
      await new Promise((r) => setTimeout(r, 500))

      // Resume with new message
      await resumeRun(run.id, resumePrompt.trim())

      // Upload images if any
      if (resumePendingImages.length > 0) {
        const uploadPromises = resumePendingImages.map((img) =>
          uploadAndAttachImage(run.id, img.file).catch((err) => {
            console.error(`Failed to upload image ${img.file.name}:`, err)
            return null
          })
        )
        await Promise.all(uploadPromises)
      }

      resumePendingImages.forEach((img) => URL.revokeObjectURL(img.preview))
      setResumePendingImages([])
      setResumePrompt('')
      resetTextareaHeight()
      onRefresh()
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : 'Failed to send message')
    } finally {
      setResuming(false)
    }
  }

  return { handleResume, handleSendNow, handleQueueFollowup }
}
