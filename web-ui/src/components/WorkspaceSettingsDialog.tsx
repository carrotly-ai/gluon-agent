import { Eye, EyeOff, Loader2, Plus, RotateCcw, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  deleteWorkspaceEnvVar,
  deleteWorkspaceSetting,
  fetchWorkspaceSettings,
  updateWorkspaceEnvVars,
  updateWorkspaceSettings,
} from '@/lib/api'
import type { WorkspaceSettingsData } from '@/lib/types'
import { cn } from '@/lib/utils'

interface WorkspaceSettingsDialogProps {
  workspaceId: string
  workspaceName: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

const OVERRIDABLE_SETTINGS = [
  { key: 'git_user_name', label: 'Git User Name', type: 'text' as const },
  { key: 'git_user_email', label: 'Git User Email', type: 'text' as const },
  { key: 'auto_create_pr', label: 'Auto-Create PR', type: 'toggle' as const },
  { key: 'extended_context_enabled', label: 'Extended Context', type: 'toggle' as const },
  { key: 'file_checkpointing_enabled', label: 'File Checkpointing', type: 'toggle' as const },
  { key: 'sandbox_enabled', label: 'Sandbox', type: 'toggle' as const },
  { key: 'vercel_cli_enabled', label: 'Vercel CLI', type: 'toggle' as const },
  { key: 'vercel_token', label: 'Vercel Token', type: 'password' as const },
  { key: 'disallowed_tools', label: 'Disallowed Tools (JSON)', type: 'text' as const },
] as const

const ENV_VAR_SUGGESTIONS = ['GH_TOKEN', 'GITHUB_TOKEN', 'AWS_PROFILE']

/** Text input that uses local state while editing and saves on blur. */
function SettingTextInput({
  value: serverValue,
  placeholder,
  type = 'text',
  disabled,
  onSave,
}: {
  value: string
  placeholder: string
  type?: 'text' | 'password'
  disabled?: boolean
  onSave: (value: string) => void
}) {
  const [localValue, setLocalValue] = useState(serverValue)
  const inputRef = useRef<HTMLInputElement>(null)

  // Sync from server when not focused
  useEffect(() => {
    if (document.activeElement !== inputRef.current) {
      setLocalValue(serverValue)
    }
  }, [serverValue])

  return (
    <input
      ref={inputRef}
      type={type}
      className="w-40 text-caption bg-[rgba(163,163,163,0.08)] border border-[rgba(163,163,163,0.12)] rounded px-2 py-1 text-[var(--color-stone)] focus:outline-none focus:border-[var(--color-indigo)]/40"
      value={localValue}
      placeholder={placeholder}
      onChange={(e) => setLocalValue(e.target.value)}
      onBlur={() => {
        if (localValue !== serverValue) {
          onSave(localValue)
        }
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.currentTarget.blur()
        }
      }}
      disabled={disabled}
    />
  )
}

export function WorkspaceSettingsDialog({
  workspaceId,
  workspaceName,
  open,
  onOpenChange,
}: WorkspaceSettingsDialogProps) {
  const [data, setData] = useState<WorkspaceSettingsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Env var add form
  const [newEnvKey, setNewEnvKey] = useState('')
  const [newEnvValue, setNewEnvValue] = useState('')
  const [showNewEnvValue, setShowNewEnvValue] = useState(false)

  const loadSettings = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetchWorkspaceSettings(workspaceId)
      setData(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load settings')
    } finally {
      setLoading(false)
    }
  }, [workspaceId])

  useEffect(() => {
    if (open) {
      loadSettings()
    }
  }, [open, loadSettings])

  const handleSettingChange = async (key: string, value: string) => {
    setSaving(true)
    try {
      await updateWorkspaceSettings(workspaceId, { [key]: value })
      await loadSettings()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save setting')
    } finally {
      setSaving(false)
    }
  }

  const handleResetSetting = async (key: string) => {
    setSaving(true)
    try {
      await deleteWorkspaceSetting(workspaceId, key)
      await loadSettings()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset setting')
    } finally {
      setSaving(false)
    }
  }

  const handleAddEnvVar = async () => {
    if (!newEnvKey.trim() || !newEnvValue.trim()) return
    setSaving(true)
    try {
      await updateWorkspaceEnvVars(workspaceId, { [newEnvKey.trim()]: newEnvValue })
      setNewEnvKey('')
      setNewEnvValue('')
      setShowNewEnvValue(false)
      await loadSettings()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add env var')
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteEnvVar = async (key: string) => {
    setSaving(true)
    try {
      await deleteWorkspaceEnvVar(workspaceId, key)
      await loadSettings()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete env var')
    } finally {
      setSaving(false)
    }
  }

  const isOverridden = (key: string) => data?.settings[key] !== undefined

  const getEffectiveValue = (key: string, defaultVal: string) => {
    if (data?.settings[key] !== undefined) return data.settings[key]
    if (data?.global_defaults[key] !== undefined) return data.global_defaults[key]
    return defaultVal
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-[var(--color-stone)]">
            Workspace Settings: {workspaceName}
          </DialogTitle>
          <DialogDescription>
            Override global settings for this workspace. Unset overrides inherit from global
            defaults.
          </DialogDescription>
        </DialogHeader>

        {error && (
          <div className="text-caption text-[var(--color-vermillion)] bg-[var(--color-vermillion)]/10 rounded-md px-3 py-2">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-5 h-5 animate-spin text-[var(--color-stone)]/50" />
          </div>
        ) : data ? (
          <div className="space-y-6">
            {/* Settings Overrides Section */}
            <div>
              <h3 className="text-caption text-[var(--color-stone)]/80 uppercase tracking-wider mb-3">
                Setting Overrides
              </h3>
              <div className="space-y-2">
                {OVERRIDABLE_SETTINGS.map((setting) => {
                  const overridden = isOverridden(setting.key)
                  const value = getEffectiveValue(setting.key, '')

                  return (
                    <div
                      key={setting.key}
                      className="flex items-center gap-3 px-3 py-2 rounded-md bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.08)]"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-caption text-[var(--color-stone)]">
                            {setting.label}
                          </span>
                          {overridden ? (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-indigo)]/15 text-[var(--color-indigo)]">
                              overridden
                            </span>
                          ) : (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/50">
                              inherited
                            </span>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        {setting.type === 'toggle' ? (
                          <button
                            type="button"
                            className={cn(
                              'relative w-9 h-5 rounded-full transition-colors',
                              value === 'true'
                                ? 'bg-[var(--color-indigo)]'
                                : 'bg-[rgba(163,163,163,0.2)]'
                            )}
                            onClick={() =>
                              handleSettingChange(setting.key, value === 'true' ? 'false' : 'true')
                            }
                            disabled={saving}
                          >
                            <span
                              className={cn(
                                'absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform',
                                value === 'true' && 'translate-x-4'
                              )}
                            />
                          </button>
                        ) : (
                          <SettingTextInput
                            value={value}
                            type={setting.type === 'password' ? 'password' : 'text'}
                            placeholder={
                              data.global_defaults[setting.key]
                                ? `Global: ${data.global_defaults[setting.key]}`
                                : 'Not set'
                            }
                            onSave={(v) => handleSettingChange(setting.key, v)}
                            disabled={saving}
                          />
                        )}

                        {overridden && (
                          <button
                            type="button"
                            className="p-1 rounded hover:bg-[rgba(163,163,163,0.1)] text-[var(--color-stone)]/50 hover:text-[var(--color-stone)] transition-colors"
                            onClick={() => handleResetSetting(setting.key)}
                            title="Reset to global default"
                            disabled={saving}
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Environment Variables Section */}
            <div>
              <h3 className="text-caption text-[var(--color-stone)]/80 uppercase tracking-wider mb-3">
                Environment Variables
              </h3>
              <p className="text-caption text-[var(--color-stone)]/50 mb-3">
                Injected into task processes for this workspace (e.g., org-specific GH_TOKEN).
              </p>

              {/* Existing env vars */}
              {data.env_var_keys.length > 0 && (
                <div className="space-y-1.5 mb-3">
                  {data.env_var_keys.map((key) => (
                    <div
                      key={key}
                      className="flex items-center justify-between px-3 py-2 rounded-md bg-[rgba(163,163,163,0.04)] border border-[rgba(163,163,163,0.08)]"
                    >
                      <div className="flex items-center gap-2">
                        <code className="text-caption text-[var(--color-indigo)]">{key}</code>
                        <span className="text-caption text-[var(--color-stone)]/40">••••••••</span>
                      </div>
                      <button
                        type="button"
                        className="p-1 rounded hover:bg-[var(--color-vermillion)]/10 text-[var(--color-stone)]/50 hover:text-[var(--color-vermillion)] transition-colors"
                        onClick={() => handleDeleteEnvVar(key)}
                        title={`Remove ${key}`}
                        disabled={saving}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {/* Add new env var */}
              <div className="flex items-end gap-2">
                <div className="flex-1 min-w-0">
                  <label className="text-[10px] text-[var(--color-stone)]/50 uppercase tracking-wider">
                    Key
                  </label>
                  <input
                    type="text"
                    className="w-full text-caption bg-[rgba(163,163,163,0.08)] border border-[rgba(163,163,163,0.12)] rounded px-2 py-1.5 text-[var(--color-stone)] focus:outline-none focus:border-[var(--color-indigo)]/40"
                    value={newEnvKey}
                    onChange={(e) => setNewEnvKey(e.target.value.toUpperCase())}
                    placeholder="GH_TOKEN"
                    list="env-var-suggestions"
                  />
                  <datalist id="env-var-suggestions">
                    {ENV_VAR_SUGGESTIONS.filter((s) => !data.env_var_keys.includes(s)).map((s) => (
                      <option key={s} value={s} />
                    ))}
                  </datalist>
                </div>
                <div className="flex-1 min-w-0 relative">
                  <label className="text-[10px] text-[var(--color-stone)]/50 uppercase tracking-wider">
                    Value
                  </label>
                  <div className="relative">
                    <input
                      type={showNewEnvValue ? 'text' : 'password'}
                      className="w-full text-caption bg-[rgba(163,163,163,0.08)] border border-[rgba(163,163,163,0.12)] rounded px-2 py-1.5 pr-8 text-[var(--color-stone)] focus:outline-none focus:border-[var(--color-indigo)]/40"
                      value={newEnvValue}
                      onChange={(e) => setNewEnvValue(e.target.value)}
                      placeholder="value"
                    />
                    <button
                      type="button"
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 p-0.5 text-[var(--color-stone)]/40 hover:text-[var(--color-stone)] transition-colors"
                      onClick={() => setShowNewEnvValue(!showNewEnvValue)}
                    >
                      {showNewEnvValue ? (
                        <EyeOff className="w-3.5 h-3.5" />
                      ) : (
                        <Eye className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </div>
                </div>
                <button
                  type="button"
                  className="px-3 py-1.5 rounded bg-[var(--color-indigo)] text-white text-caption hover:bg-[var(--color-indigo)]/90 transition-colors disabled:opacity-50"
                  onClick={handleAddEnvVar}
                  disabled={saving || !newEnvKey.trim() || !newEnvValue.trim()}
                >
                  <Plus className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
