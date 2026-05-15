import { Calendar, Clock, GitBranch, Globe, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { createSchedule, previewSchedule, updateSchedule } from '@/lib/api'
import type { ConcurrencyPolicy, Project, SchedulePreviewResponse, TaskSchedule } from '@/lib/types'
import { cn } from '@/lib/utils'

interface ScheduleEditorDialogProps {
  open: boolean
  /** When set, edit this existing schedule. When null, create a new one. */
  schedule: TaskSchedule | null
  projects: Project[]
  /** Optional default project for new schedules. */
  defaultProjectName?: string
  onClose: () => void
  onSaved: (saved: TaskSchedule) => void
}

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const WEEKDAYS = [0, 1, 2, 3, 4]
const WEEKENDS = [5, 6]
const EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]

const POLICY_OPTIONS: { value: ConcurrencyPolicy; label: string; description: string }[] = [
  {
    value: 'skip',
    label: 'Skip',
    description: 'If a previous run is still going, drop the new firing.',
  },
  {
    value: 'cancel_replace',
    label: 'Cancel & restart',
    description: 'Cancel the in-flight run, then start fresh.',
  },
  {
    value: 'allow_overlap',
    label: 'Allow overlap',
    description: 'Spawn another run alongside the first.',
  },
]

const PROFILE_OPTIONS = ['quick', 'standard', 'deep', 'planning']

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

function setsEqual(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false
  const sa = [...a].sort()
  const sb = [...b].sort()
  return sa.every((v, i) => v === sb[i])
}

/**
 * Modal editor for creating or editing a TaskSchedule.
 *
 * The friendly section captures days + time + timezone; the Advanced disclosure
 * exposes a raw cron string for power users. A live preview line shows the
 * canonical cron + the next 5 fires whenever the user adjusts anything.
 */
export function ScheduleEditorDialog({
  open,
  schedule,
  projects,
  defaultProjectName,
  onClose,
  onSaved,
}: ScheduleEditorDialogProps) {
  const isEdit = Boolean(schedule)

  // ---- Form state ------------------------------------------------------
  const [name, setName] = useState('')
  const [projectName, setProjectName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [profile, setProfile] = useState('standard')
  const [useWorktree, setUseWorktree] = useState(false)
  const [days, setDays] = useState<number[]>(WEEKDAYS)
  const [time, setTime] = useState('09:00')
  const [timezone, setTimezone] = useState(browserTimezone())
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [cron, setCron] = useState('')
  const [policy, setPolicy] = useState<ConcurrencyPolicy>('skip')
  const [enabled, setEnabled] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [preview, setPreview] = useState<SchedulePreviewResponse | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const previewTimer = useRef<number | null>(null)

  // Reset / hydrate when dialog opens
  useEffect(() => {
    if (!open) return
    if (schedule) {
      setName(schedule.name)
      setProjectName(schedule.project_name)
      setPrompt(schedule.prompt)
      setProfile(schedule.profile)
      setUseWorktree(schedule.use_worktree)
      setTimezone(schedule.timezone)
      setDays(schedule.recurrence_days ?? WEEKDAYS)
      setTime(schedule.recurrence_time ?? '09:00')
      setCron(schedule.schedule_cron)
      // If the schedule was authored via raw cron (no friendly fields), show
      // the Advanced disclosure pre-expanded so the user sees the source of truth.
      setAdvancedOpen(!schedule.recurrence_days || !schedule.recurrence_time)
      setPolicy(schedule.concurrency_policy)
      setEnabled(schedule.is_enabled)
    } else {
      setName('')
      setProjectName(defaultProjectName ?? projects[0]?.name ?? '')
      setPrompt('')
      setProfile('standard')
      setUseWorktree(false)
      setDays(WEEKDAYS)
      setTime('09:00')
      setTimezone(browserTimezone())
      setAdvancedOpen(false)
      setCron('')
      setPolicy('skip')
      setEnabled(true)
    }
    setPreview(null)
    setPreviewError(null)
  }, [open, schedule, projects, defaultProjectName])

  // Esc closes
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // ---- Live preview ----------------------------------------------------
  const previewBody = useMemo(() => {
    if (advancedOpen && cron.trim()) {
      return { timezone, schedule_cron: cron.trim() }
    }
    if (days.length > 0 && time) {
      return { timezone, recurrence_days: days, recurrence_time: time }
    }
    return null
  }, [advancedOpen, cron, days, time, timezone])

  useEffect(() => {
    if (!open || !previewBody) {
      setPreview(null)
      setPreviewError(null)
      return
    }
    if (previewTimer.current !== null) window.clearTimeout(previewTimer.current)
    // Debounce by 250ms — typing in the cron box shouldn't hammer the API.
    previewTimer.current = window.setTimeout(() => {
      previewSchedule(previewBody)
        .then((p) => {
          setPreview(p)
          setPreviewError(null)
        })
        .catch((e) => {
          setPreview(null)
          setPreviewError(e instanceof Error ? e.message : 'Preview failed')
        })
    }, 250)
    return () => {
      if (previewTimer.current !== null) window.clearTimeout(previewTimer.current)
    }
  }, [open, previewBody])

  const toggleDay = (day: number) => {
    setDays((prev) =>
      prev.includes(day) ? prev.filter((d) => d !== day).sort() : [...prev, day].sort()
    )
  }

  const isWeekdays = setsEqual(days, WEEKDAYS)
  const isWeekends = setsEqual(days, WEEKENDS)
  const isEveryDay = setsEqual(days, EVERY_DAY)

  // ---- Submit ----------------------------------------------------------
  const canSubmit =
    name.trim() && prompt.trim() && projectName && (advancedOpen ? cron.trim() : days.length > 0)

  const handleSubmit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    try {
      const baseBody = {
        name: name.trim(),
        project_name: projectName,
        prompt: prompt.trim(),
        profile,
        use_worktree: useWorktree,
        timezone,
        concurrency_policy: policy,
        is_enabled: enabled,
        ...(advancedOpen
          ? { schedule_cron: cron.trim(), recurrence_days: null, recurrence_time: null }
          : { recurrence_days: days, recurrence_time: time, schedule_cron: null }),
      }
      const saved =
        isEdit && schedule
          ? await updateSchedule(schedule.id, baseBody)
          : await createSchedule(baseBody)
      toast.success(isEdit ? 'Schedule updated' : 'Schedule created', {
        description: saved.summary,
      })
      onSaved(saved)
      onClose()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save schedule')
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center px-4 overflow-y-auto py-8"
      onClick={onClose}
    >
      <div
        className={cn(
          'bg-[var(--color-ink)] border border-[rgba(163,163,163,0.15)] rounded-md',
          'shadow-2xl w-full max-w-lg flex flex-col my-auto'
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between px-4 pt-4 pb-2">
          <div>
            <div className="flex items-center gap-2 text-caption uppercase tracking-widest text-[var(--color-stone)]/60">
              <Calendar className="w-3 h-3" />
              <span>{isEdit ? 'Edit schedule' : 'New schedule'}</span>
            </div>
            <p className="text-display text-[var(--color-paper)] mt-1">
              {isEdit ? schedule?.name : 'Recurring task'}
            </p>
          </div>
          <button
            type="button"
            className="p-1 text-[var(--color-stone)]/40 hover:text-[var(--color-stone)] rounded-sm"
            onClick={onClose}
            title="Close (Esc)"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="px-4 pb-4 flex flex-col gap-3">
          {/* Name + project + prompt */}
          <Field label="Name">
            <input
              type="text"
              className={inputClass}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Morning audit"
              maxLength={120}
            />
          </Field>

          <Field label="Project">
            <select
              className={inputClass}
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
            >
              <option value="">Pick a project…</option>
              {projects.map((p) => (
                <option key={p.id} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Prompt">
            <textarea
              className={cn(inputClass, 'resize-none min-h-[80px]')}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="The task to run each time. Use the same prompts you'd type into a normal Gluon run."
              rows={4}
            />
          </Field>

          {/* Recurrence — friendly editor */}
          <div className="border border-[rgba(163,163,163,0.1)] rounded-sm p-3 flex flex-col gap-2.5">
            <div className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60">
              Recurrence
            </div>

            {!advancedOpen && (
              <>
                {/* Day preset chips */}
                <div className="flex gap-1.5 flex-wrap">
                  <PresetChip active={isWeekdays} onClick={() => setDays(WEEKDAYS)}>
                    Weekdays
                  </PresetChip>
                  <PresetChip active={isWeekends} onClick={() => setDays(WEEKENDS)}>
                    Weekends
                  </PresetChip>
                  <PresetChip active={isEveryDay} onClick={() => setDays(EVERY_DAY)}>
                    Every day
                  </PresetChip>
                </div>
                {/* Per-day toggles */}
                <div className="flex gap-1">
                  {DAY_LABELS.map((label, idx) => (
                    <button
                      key={label}
                      type="button"
                      className={cn(
                        'flex-1 py-1.5 rounded-sm text-caption uppercase tracking-widest transition-colors',
                        days.includes(idx)
                          ? 'bg-[var(--color-paper)] text-[var(--color-void)]'
                          : 'bg-[rgba(163,163,163,0.06)] text-[var(--color-stone)] hover:bg-[rgba(163,163,163,0.12)]'
                      )}
                      onClick={() => toggleDay(idx)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                {/* Time + tz */}
                <div className="flex gap-2">
                  <label className="flex-1 flex flex-col gap-1">
                    <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60 flex items-center gap-1">
                      <Clock className="w-3 h-3" /> Time
                    </span>
                    <input
                      type="time"
                      className={inputClass}
                      value={time}
                      onChange={(e) => setTime(e.target.value)}
                    />
                  </label>
                  <label className="flex-1 flex flex-col gap-1 min-w-0">
                    <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60 flex items-center gap-1">
                      <Globe className="w-3 h-3" /> Timezone
                    </span>
                    <input
                      type="text"
                      className={inputClass}
                      value={timezone}
                      onChange={(e) => setTimezone(e.target.value)}
                      placeholder="Asia/Singapore"
                    />
                  </label>
                </div>
              </>
            )}

            {advancedOpen && (
              <Field label="Cron expression">
                <input
                  type="text"
                  className={cn(inputClass, 'font-mono')}
                  value={cron}
                  onChange={(e) => setCron(e.target.value)}
                  placeholder="0 9 * * 1-5"
                />
                <Field label="Timezone" className="mt-2">
                  <input
                    type="text"
                    className={inputClass}
                    value={timezone}
                    onChange={(e) => setTimezone(e.target.value)}
                    placeholder="Asia/Singapore"
                  />
                </Field>
              </Field>
            )}

            <button
              type="button"
              className="self-start text-caption uppercase tracking-widest text-[var(--color-stone)]/60 hover:text-[var(--color-paper)] transition-colors"
              onClick={() => setAdvancedOpen((v) => !v)}
            >
              {advancedOpen ? '◂ Friendly editor' : 'Advanced (cron) ▸'}
            </button>

            {/* Live preview */}
            <div className="border-t border-[rgba(163,163,163,0.06)] pt-2 mt-1 text-body">
              {previewError && (
                <p className="text-[var(--color-vermillion)]/80">⚠ {previewError}</p>
              )}
              {!previewError && preview && (
                <div className="flex flex-col gap-1">
                  <p className="text-[var(--color-paper)]">{preview.summary}</p>
                  {preview.next_fires.length > 0 && (
                    <p className="text-caption text-[var(--color-stone)]/70">
                      Next: {new Date(preview.next_fires[0]).toLocaleString()}
                    </p>
                  )}
                  <p className="text-caption text-[var(--color-stone)]/40 font-mono">
                    {preview.schedule_cron}
                  </p>
                </div>
              )}
              {!previewError && !preview && (
                <p className="text-caption text-[var(--color-stone)]/50">Pick a recurrence…</p>
              )}
            </div>
          </div>

          {/* Concurrency */}
          <Field label="If a previous run is still going">
            <div className="flex flex-col gap-1.5">
              {POLICY_OPTIONS.map((opt) => (
                <label
                  key={opt.value}
                  className={cn(
                    'flex items-start gap-2 px-3 py-2 rounded-sm cursor-pointer border transition-colors',
                    policy === opt.value
                      ? 'border-[rgba(163,163,163,0.25)] bg-[rgba(163,163,163,0.05)]'
                      : 'border-[rgba(163,163,163,0.08)] hover:border-[rgba(163,163,163,0.15)]'
                  )}
                >
                  <input
                    type="radio"
                    name="policy"
                    value={opt.value}
                    checked={policy === opt.value}
                    onChange={() => setPolicy(opt.value)}
                    className="mt-1"
                  />
                  <div>
                    <p className="text-body text-[var(--color-paper)]">{opt.label}</p>
                    <p className="text-caption text-[var(--color-stone)]/60">{opt.description}</p>
                  </div>
                </label>
              ))}
            </div>
          </Field>

          {/* Profile + worktree + enabled */}
          <div className="flex gap-2">
            <Field label="Profile" className="flex-1">
              <select
                className={inputClass}
                value={profile}
                onChange={(e) => setProfile(e.target.value)}
              >
                {PROFILE_OPTIONS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </Field>
            <label className="flex-1 flex flex-col gap-1">
              <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60 flex items-center gap-1">
                <GitBranch className="w-3 h-3" /> Worktree
              </span>
              <label className="flex items-center gap-2 px-3 py-2 rounded-sm bg-[var(--color-void)] border border-[rgba(163,163,163,0.1)] cursor-pointer">
                <input
                  type="checkbox"
                  checked={useWorktree}
                  onChange={(e) => setUseWorktree(e.target.checked)}
                />
                <span className="text-body text-[var(--color-paper)]">Isolated</span>
              </label>
            </label>
          </div>

          <label className="flex items-center gap-2 text-body text-[var(--color-paper)]">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            <span>Enabled</span>
            <span className="text-caption text-[var(--color-stone)]/50">
              (you can pause it from the schedules list later)
            </span>
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
              canSubmit && !submitting
                ? 'bg-[var(--color-paper)] text-[var(--color-void)] hover:opacity-90'
                : 'bg-[var(--color-stone)]/20 text-[var(--color-stone)]/50 cursor-not-allowed'
            )}
            onClick={() => void handleSubmit()}
            disabled={!canSubmit || submitting}
          >
            {submitting ? 'Saving…' : isEdit ? 'Save' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  )
}

const inputClass =
  'w-full bg-[var(--color-void)] border border-[rgba(163,163,163,0.1)] rounded-sm px-3 py-2 ' +
  'text-input text-[var(--color-paper)] focus:outline-none focus:border-[rgba(163,163,163,0.2)]'

function Field({
  label,
  children,
  className,
}: {
  label: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <label className={cn('flex flex-col gap-1', className)}>
      <span className="text-caption uppercase tracking-widest text-[var(--color-stone)]/60">
        {label}
      </span>
      {children}
    </label>
  )
}

function PresetChip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      className={cn(
        'px-2.5 py-1 rounded-sm text-caption uppercase tracking-widest transition-colors',
        active
          ? 'bg-[var(--color-paper)] text-[var(--color-void)]'
          : 'bg-[rgba(163,163,163,0.08)] text-[var(--color-stone)] hover:bg-[rgba(163,163,163,0.15)]'
      )}
      onClick={onClick}
    >
      {children}
    </button>
  )
}
