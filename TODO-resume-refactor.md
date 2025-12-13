# TODO: In-Place Resume Refactoring

Refactor the resume feature to continue existing runs in-place rather than creating new runs.

## Phase 1: Database Schema Changes

- [ ] Add `resume_count` column to execution_runs table
- [ ] Add `last_resumed_at` column to execution_runs table
- [ ] Add migration to store.py MIGRATIONS list
- [ ] Update ExecutionRun model in models.py with new fields
- [ ] Add `increment_resume_count()` method to ExecutionRun model

## Phase 2: Runner/TaskRunner Changes

- [ ] Add `resume_in_place()` method to TaskRunner
- [ ] Implement status reset logic (COMPLETED/FAILED → RUNNING)
- [ ] Preserve existing log_path, worktree, branch during resume
- [ ] Update log files to append with resume markers
- [ ] Update messages.jsonl format to include resume_attempt field
- [ ] Handle concurrent resume prevention (check status before resume)

## Phase 3: Worktree Lifecycle Changes

- [ ] Add `validate_worktree()` method to WorktreeManager
- [ ] Skip worktree cleanup when run is resumable (completed/failed with worktree)
- [ ] Add `worktree_preserved` field to track cleanup state
- [ ] Update cleanup logic to only clean on explicit request or archive

## Phase 4: Web API Changes

- [ ] Refactor `/api/runs/{run_id}/resume` endpoint to use in-place resume
- [ ] Change response format from `{new_run_id}` to `{run_id, resumed: true}`
- [ ] Update WebSocket broadcast to send run_updated (not run_created)
- [ ] Update `/api/runs/{run_id}/session-history` to show resume attempts
- [ ] Add resume validation (check worktree exists, status valid)

## Phase 5: Frontend Changes

- [ ] Update `handleResume()` in RunDetailDialog.tsx to NOT close dialog
- [ ] Handle run status transition (COMPLETED → RUNNING) in UI
- [ ] Update messages parsing to handle resume markers
- [ ] Update session history tab to show resume attempts instead of run chain
- [ ] Add visual separator between resume attempts in Messages tab

## Phase 6: Testing & Validation

- [ ] Test resume lifecycle: complete → resume → complete
- [ ] Test failed run resume
- [ ] Test worktree persistence across resumes
- [ ] Test log appending with markers
- [ ] Test concurrent resume prevention
- [ ] Test WebSocket updates during resume

## Phase 7: Cleanup & Documentation

- [ ] Update CLI reference docs
- [ ] Update web dashboard docs
- [ ] Add migration notes for breaking changes
- [ ] Clean up any deprecated code paths
