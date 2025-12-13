# TODO: In-Place Resume Refactoring

Refactor the resume feature to continue existing runs in-place rather than creating new runs.

## Phase 1: Database Schema Changes ✅

- [x] Add `resume_count` column to execution_runs table
- [x] Add `last_resumed_at` column to execution_runs table
- [x] Add migration to store.py MIGRATIONS list
- [x] Update ExecutionRun model in models.py with new fields
- [x] Add `prepare_for_resume()` and `is_resumable` methods to ExecutionRun model

## Phase 2: Runner/TaskRunner Changes ✅

- [x] Add `resume_in_place()` method to TaskRunner
- [x] Implement status reset logic (COMPLETED/FAILED → RUNNING)
- [x] Preserve existing log_path, worktree, branch during resume
- [x] Update log files to append with resume markers (`_write_resume_marker`)
- [x] Update messages.jsonl format to include resume marker messages
- [x] Handle concurrent resume prevention (check `is_resumable` before resume)
- [x] Accumulate costs across resume attempts

## Phase 3: Worktree Lifecycle Changes ✅

- [x] Reuse existing worktree for resumed runs (skip `worktree_manager.create()`)
- [x] Validate worktree exists before allowing resume
- [x] Open log files in append mode for resumed runs

## Phase 4: Web API Changes ✅

- [x] Refactor `/api/runs/{run_id}/resume` endpoint to use `resume_in_place()`
- [x] Change response format from `{new_run_id}` to `{run_id, resume_count}`
- [x] Update WebSocket broadcast to send `run_updated` (not `run_created`)
- [x] Add `resume_count` and `last_resumed_at` to `RunDetailResponse`
- [x] Provide backward compatibility fields in response

## Phase 5: Frontend Changes ✅

- [x] Update `ResumeRunResponse` type to use `run_id` field
- [x] Add `resume_count` and `last_resumed_at` to `RunDetail` type
- [x] Update `handleResume()` in RunDetailDialog.tsx to NOT close dialog
- [x] Refresh run data after resume to show status transition
- [x] Build and verify no TypeScript errors

## Phase 6: Testing & Validation ✅

- [x] Test resume lifecycle: complete → resume → complete
- [x] Test failed run resume (tested via API - validated `is_resumable` check)
- [x] Test worktree persistence across resumes (worktree reused, not recreated)
- [x] Test log appending with markers (`{"type": "system", "subtype": "resume", ...}`)
- [x] Test concurrent resume prevention (`is_resumable` returns false for active runs)
- [x] Test WebSocket updates during resume (`run_updated` broadcast, not `run_created`)

## Phase 7: Cleanup & Documentation

- [x] Implementation summary documented below
- [x] Breaking changes documented below
- [ ] Update CLI reference docs (if needed)
- [ ] Update web dashboard docs (if needed)

---

## Implementation Summary

### Key Changes Made

1. **Database**: Added `resume_count` (INT) and `last_resumed_at` (TEXT) columns
2. **Model**: Added `prepare_for_resume()` method and `is_resumable` property
3. **Runner**: New `resume_in_place()` method that:
   - Validates run can be resumed
   - Validates worktree exists (if worktree run)
   - Writes resume markers to logs
   - Reuses same run ID, worktree, and branch
   - Accumulates costs across attempts
4. **API**: Resume endpoint now uses in-place resume, returns same run_id
5. **Frontend**: Dialog stays open after resume, refreshes to show progress

### Breaking Changes

- API response changed from `{new_run_id}` to `{run_id, resume_count}`
- Backward compatibility fields provided: `original_run_id` and `new_run_id` (both = run_id)
