# Bug: Duplicate Messages During Resume

**Status**: Fixed
**Severity**: Medium (UI/UX issue, no data loss)
**Affected Version**: Current
**Reported**: Mobile UI when resuming tasks

## Bug Summary

When resuming a task on mobile (clicking "Resume" with a follow-up prompt), the messages displayed in the MESSAGES tab appear duplicated - each new line results in two identical lines appearing in the UI.

## Screenshots

The issue manifests as:
- Multiple "=== Resume #1 ===" entries appearing
- Duplicate "init" entries
- Duplicate Task and Glob tool calls appearing twice in sequence
- Same operations (Task, Glob, Read) appearing twice

## Root Cause

The bug occurs due to a **race condition between initial HTTP fetch and WebSocket streaming** when a run is resumed in-place.

### Data Flow Analysis

1. **Initial State**: User views a completed run, `RunDetailPage` loads `initialMessages` via HTTP from `messages.jsonl`

2. **User Resumes**: Clicks resume, which:
   - Calls `resumeRun()` API
   - Backend writes resume marker to `messages.jsonl` (runner.py:244-257)
   - Backend transitions run status to `running`
   - Returns updated run data

3. **After Resume**: `handleRefresh()` is called (RunDetailPage.tsx:468), which:
   - Fetches logs again via HTTP → gets new `initialMessages` including resume marker + any new messages
   - `StreamingLogViewer` receives these as `initialMessages`

4. **WebSocket Streaming**: Meanwhile:
   - `useRunLogStream` hook starts subscribing because `isActive` becomes true
   - Backend's `_poll_log_updates()` (api.py:2156) reads from `messages.jsonl` incrementally
   - **Critical Issue**: The file position tracking `_log_file_positions` starts from position 0 for a newly subscribed run

### The Bug Mechanism

In `_poll_log_updates()` (api.py:2178-2190):

```python
messages_path = log_dir / "messages.jsonl"
if messages_path.exists():
    last_pos = _log_file_positions.get(run_id, 0)  # ← Starts at 0!
    current_size = messages_path.stat().st_size

    if current_size > last_pos:  # ← Always true for new subscription
        with open(messages_path) as f:
            f.seek(last_pos)  # ← Reads from beginning
            for line in f:
                if line.strip():
                    msg = json.loads(line)
                    await ws_manager.stream_agent_message(run_id, msg)  # ← Streams ALL messages
```

**When a client subscribes to log streaming for a resumed run:**
1. `_log_file_positions[run_id]` is 0 (new subscription)
2. The entire `messages.jsonl` file is read and streamed
3. This includes messages that were ALREADY fetched via HTTP as `initialMessages`

**In `StreamingLogViewer.tsx` (line 518-528):**
```typescript
const allMessages: AgentMessage[] = [
    ...initialMessages,        // Messages from HTTP fetch
    ...streamedMessages.map(   // Messages from WebSocket (DUPLICATES!)
```

The messages are simply concatenated without deduplication.

## Why This Mainly Affects Resume (Not Initial Load)

For a **new run** that starts fresh:
- `initialMessages` is empty (no logs yet)
- WebSocket streaming captures messages as they're written
- No overlap

For a **resumed run**:
- `initialMessages` already contains the full history from HTTP fetch
- WebSocket streaming sends the entire file again (starting from position 0)
- **DUPLICATION** occurs

## Other Areas That May Have This Issue

1. **Page refresh while run is active**: If user refreshes the page during an active run, the same pattern would occur
2. **Switching away and back to the MESSAGES tab**: If the component remounts
3. **Network reconnection**: If WebSocket disconnects and reconnects, `_log_file_positions` is cleared but `initialMessages` persists
4. **Opening a run that was resumed by another client**: HTTP fetch has full history, WebSocket may stream overlapping content

## Files Involved

- `web-ui/src/components/StreamingLogViewer.tsx` - Message combination logic
- `web-ui/src/hooks/useRunLogStream.ts` - WebSocket message state management
- `web-ui/src/components/RunDetailPage.tsx` - HTTP fetching and refresh logic
- `src/gluon/web/api.py` - Backend log polling (`_poll_log_updates`, `_log_file_positions`)
- `src/gluon/runner.py` - Resume marker writing (`_write_resume_marker`)

## Proposed Solutions

### Solution 1: Deduplication by Timestamp (Frontend) - RECOMMENDED FOR IMMEDIATE FIX

Deduplicate messages in `StreamingLogViewer` based on unique identifiers:

```typescript
const allMessages = useMemo(() => {
    const seen = new Set<string>();
    const combined = [...initialMessages, ...streamedMessages];
    return combined.filter(msg => {
        // Create unique key from timestamp + type + content preview
        const key = `${msg.timestamp}-${msg.type}-${msg.content?.slice(0,50)}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}, [initialMessages, streamedMessages]);
```

**Pros**: Simple, doesn't require backend changes, ships quickly
**Cons**: Relies on unique timestamps, slight memory overhead

### Solution 2: Track File Position Per Subscription (Backend) - RECOMMENDED FOR PROPER FIX

When a client subscribes to log streaming, initialize their file position to the current file size instead of 0:

```python
async def subscribe_logs(self, websocket: WebSocket, run_id: str) -> None:
    """Subscribe a client to log updates for a specific run."""
    async with self._lock:
        if run_id not in self.log_subscriptions:
            self.log_subscriptions[run_id] = set()
        self.log_subscriptions[run_id].add(websocket)

    # Initialize file position to current size (only stream new content)
    if run_id not in _log_file_positions:
        log_dir = runner.get_log_path(run_id)
        if log_dir:
            messages_path = log_dir / "messages.jsonl"
            if messages_path.exists():
                _log_file_positions[run_id] = messages_path.stat().st_size
```

**Pros**: Correct architecture, prevents unnecessary data transfer
**Cons**: Requires backend modification

### Solution 3: Clear Streamed Messages on Initial Load (Frontend)

When `initialMessages` changes significantly, clear `streamedMessages`:

```typescript
useEffect(() => {
    if (initialMessages.length > 0 && streamedMessages.length > 0) {
        clear();  // Clear streamed messages when we get fresh HTTP data
    }
}, [initialMessages.length]);
```

**Pros**: Simple fix
**Cons**: May lose some real-time messages during the transition

### Solution 4: Use Message Sequence Numbers (Backend + Frontend)

Add sequence numbers to messages in `messages.jsonl`:

```python
resume_msg = {
    "seq": self._get_next_seq(run_id),
    "type": "system",
    ...
}
```

Frontend only keeps highest sequence number for each `seq`.

**Pros**: Most robust, handles all edge cases
**Cons**: Requires schema change, migration consideration

## Recommended Implementation Plan

1. **Phase 1 (Immediate)**: Implement Solution 1 (frontend deduplication) to fix the user-facing issue quickly
2. **Phase 2 (Follow-up)**: Implement Solution 2 (backend position tracking) for efficiency and correctness

## Implementation (Completed)

Both Phase 1 and Phase 2 fixes were implemented:

### Frontend Fix (StreamingLogViewer.tsx)
Added message deduplication using `useMemo` with a unique key based on `timestamp + type + content preview`:

```typescript
const allMessages = useMemo((): AgentMessage[] => {
    const seen = new Set<string>()
    const combined: AgentMessage[] = [...initialMessages, ...streamedMessages...]
    return combined.filter((msg) => {
        const contentPreview = msg.content?.slice(0, 100) || ''
        const key = `${msg.timestamp}-${msg.type}-${contentPreview}`
        if (seen.has(key)) return false
        seen.add(key)
        return true
    })
}, [initialMessages, streamedMessages])
```

### Backend Fix (api.py)
Modified `_poll_log_updates()` to initialize file position to current size for new subscriptions:

```python
if run_id not in _log_file_positions:
    _log_file_positions[run_id] = current_size
    logger.debug(f"Initialized log position for {run_id[:8]} at {current_size} bytes")
```

This prevents re-streaming messages that were already fetched via HTTP.

## Testing

After fix, test these scenarios:
1. Resume a completed run → no duplicates in MESSAGES tab
2. Refresh page while run is active → no duplicates
3. Navigate away and back to MESSAGES tab → no duplicates
4. WebSocket reconnection → no duplicates
5. Multiple clients viewing same run → each sees correct messages
