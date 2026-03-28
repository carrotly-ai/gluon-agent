# Session Management Exploration

## Background

SDK v0.1.51 introduces three standalone session management functions that operate
directly on session JSONL files:

- `fork_session(session_id, directory, up_to_message_id, title)` → `ForkSessionResult`
- `delete_session(session_id, directory)` → `None`
- `get_session_info(session_id, directory)` → `SDKSessionInfo | None`

These are **file-level operations** — distinct from `ClaudeAgentOptions.fork_session`
(which forks during a live session). They manipulate the JSONL transcript files that
Claude Code stores in `.claude/projects/<hash>/`.

## Current State in Gluon

### How sessions work today

1. **New session**: `Orchestrator.execute()` creates a `Session` in Gluon's SQLite DB.
   The actual Claude session JSONL is created by the SDK when `ClaudeSDKClient` connects.

2. **Resume**: We pass `options.resume = claude_session_id` + `options.fork_session = True`.
   The SDK forks the session on-the-fly during connection, creating an independent branch
   so multiple runs can execute concurrently.

3. **Session ID capture**: The Claude session UUID is captured from `SystemMessage(subtype="init")`
   and `ResultMessage.session_id`, stored in `Session.claude_session_id`.

4. **No cleanup**: Old session JSONL files accumulate on disk. We never delete them.

5. **No visibility**: We don't read session metadata from JSONL — we only track what we
   capture during execution in our SQLite DB.

### Pain points the new APIs could address

| Problem | Current Impact | SDK Solution |
|---------|---------------|-------------|
| Session JSONL files grow unbounded | Disk usage on long-running agents | `delete_session()` |
| Can't inspect sessions without running them | Dashboard shows stale data if agent crashes | `get_session_info()` |
| Can't branch from a specific point | All forks start from the latest message | `fork_session(up_to_message_id=...)` |
| No cross-referencing between SDK sessions and Gluon runs | Hard to debug "which JSONL belongs to which run" | `get_session_info()` + matching by `cwd`/`git_branch` |

## Proposed Integrations

### 1. Session Cleanup (High Value, Low Effort)

**What**: After a run completes and the result is captured, clean up old forked session
files that are no longer needed.

**Why**: Each forked session is a full JSONL copy of the transcript. Long-running agents
with many resumes accumulate dozens of multi-MB files. In production with 50+ projects,
this can consume significant disk space.

**How**:

```python
# In runner.py or core.py, after run completion:
from claude_agent_sdk import delete_session

# Keep the latest session, delete previous forks
for old_session_id in run.previous_session_ids:
    try:
        delete_session(old_session_id, directory=str(working_dir))
    except FileNotFoundError:
        pass  # Already cleaned up
```

**Implementation notes**:
- Add a `previous_session_ids: list[str]` field to `ExecutionRun` metadata to track
  sessions created during the run lifecycle
- Only delete after the run is fully COMPLETED (not REVIEW or FAILED — we may need
  to resume from those)
- Make this configurable via a global setting (`session_cleanup_enabled`)
- Could also add a `gluon sessions cleanup` CLI command for manual batch cleanup

### 2. Session Info Dashboard (Medium Value, Medium Effort)

**What**: Use `get_session_info()` to enrich the dashboard's session browser with
live metadata from JSONL files.

**Why**: Currently, the Session Browser at `/sessions` calls `list_sessions()` from
the SDK which reads all JSONL files. With `get_session_info()`, we can do targeted
lookups for specific sessions and show richer metadata.

**How**:

```python
from claude_agent_sdk import get_session_info

# Enrich run detail view with SDK session metadata
info = get_session_info(run.claude_session_id, directory=str(project.expanded_path))
if info:
    response.sdk_session_summary = info.summary
    response.sdk_session_tag = info.tag
    response.sdk_session_created_at = info.created_at
```

**Implementation notes**:
- Add fields to `RunDetailResponse` for SDK session metadata
- Call `get_session_info()` in the run detail API endpoint
- Cache results since JSONL stat() is cheap but parsing isn't free
- Could also show session file size to help users understand disk usage

### 3. Point-in-Time Forking (High Value, High Effort)

**What**: Use `fork_session(up_to_message_id=...)` to branch from a specific point
in the conversation, rather than always forking from the end.

**Why**: This enables powerful workflows:
- **Rollback**: If a run goes off track after message N, fork from message N and retry
  with a different prompt
- **A/B Testing**: Fork from the same planning output to try different implementation
  approaches concurrently
- **Recovery**: When context overflow happens at turn 200, fork from turn 150 (before
  the context got too large) instead of starting from scratch

**How this changes resume**:

Currently:
```
Session A: [msg1, msg2, ..., msg200]
                                    └─ Fork → Session B: [msg1..msg200, new_msg]
```

With point-in-time fork:
```
Session A: [msg1, msg2, ..., msg150, ..., msg200]
                             │
                             └─ Fork(up_to=msg150) → Session B: [msg1..msg150, new_msg]
```

**Implementation approach**:

1. **Expose in runner.py**: Add `fork_from_message_id` parameter to `resume_in_place()`
2. **New CLI command**: `gluon fork <run_id> --from-message <msg_id>`
3. **Web UI**: Add "Fork from here" button in the session message viewer
4. **Recovery coordinator**: When context overflow is detected, use point-in-time fork
   to resume from a known-good checkpoint

**Architectural considerations**:
- Need to surface message UUIDs in the messages.jsonl log so users can identify
  fork points
- The forked session gets fresh UUIDs (parentUuid chain is remapped), so Gluon
  needs to track the fork relationship
- `fork_session()` is synchronous (file I/O only) — can be called from the runner
  without blocking the event loop (but should use `asyncio.to_thread()`)

### 4. Session Tagging (Low Value, Low Effort)

**What**: Use session tags to label sessions with Gluon run IDs and project names.

**Why**: Makes it easy to find which SDK session belongs to which Gluon run when
debugging, and enables filtering in the SDK session browser.

**How**: Not directly supported by the new APIs (no `tag_session()` function exposed),
but we could write a custom title via `fork_session(title=...)` when forking.

## Recommendation

**Phase 1 (ship with this upgrade)**: No session management changes — just expose the
new SDK version and `task_budget`. The session management features are valuable but
need design work.

**Phase 2 (next sprint)**: Implement **Session Cleanup** (#1) — it's the highest ROI
with minimal risk. Add a `session_cleanup_enabled` setting and auto-cleanup on run
completion.

**Phase 3 (future)**: Implement **Point-in-Time Forking** (#3) — this is the most
transformative feature but requires UI work and careful state management. Start with
the recovery coordinator use case (fork from checkpoint on context overflow) before
exposing it as a user-facing feature.

## SDK API Reference (for implementers)

```python
from claude_agent_sdk import fork_session, delete_session, get_session_info
from claude_agent_sdk.types import SDKSessionInfo

# Fork a session (creates new JSONL file)
result = fork_session(
    session_id="550e8400-...",
    directory="/path/to/project",      # optional, searches all projects if omitted
    up_to_message_id="660e8400-...",   # optional, forks full transcript if omitted
    title="My fork",                   # optional, derives from original if omitted
)
print(result.session_id)  # New session UUID

# Delete a session (removes JSONL file permanently)
delete_session("550e8400-...", directory="/path/to/project")

# Get session metadata (no full JSONL parse)
info: SDKSessionInfo | None = get_session_info("550e8400-...", directory="/path/to/project")
if info:
    print(info.summary)        # Display title or first prompt
    print(info.last_modified)  # Unix timestamp (ms)
    print(info.file_size)      # Bytes
    print(info.tag)            # User-set tag
    print(info.created_at)     # Unix timestamp (ms)
    print(info.git_branch)     # Branch at end of session
    print(info.cwd)            # Working directory
```
