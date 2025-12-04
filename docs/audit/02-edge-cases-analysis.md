# Edge Cases & Error Handling Analysis

**Date**: 2025-12-04
**Purpose**: Document edge cases handled by vibe-kanban that gluon-agent should consider

---

## 1. Image/File Upload Edge Cases

### Vibe-Kanban Handles:

#### Size Limits
```rust
ImageError::TooLarge(size, max)
```
- User-friendly message with sizes formatted in MB
- Returns HTTP 413 (Payload Too Large)
- Message: "Image is too large: {size}MB. Maximum allowed: {max}MB"

#### Invalid Format
```rust
ImageError::InvalidFormat
```
- Returns HTTP 400 (Bad Request)
- Lists supported formats in error message
- Prevents processing of non-image files

#### Path Traversal Prevention
```rust
if query.path.contains("..") {
    return Err(ApiError::BadRequest("Invalid path"));
}
```
- Blocks directory traversal attacks
- Only allows paths starting with `.vibe-images/`

#### Missing Files
```rust
ImageError::NotFound
```
- Returns HTTP 404
- Graceful handling when referenced images are deleted

### Gluon-Agent Should Add:
- [ ] File size validation before upload
- [ ] MIME type validation
- [ ] Path sanitization for any file operations
- [ ] Graceful handling of missing referenced files

---

## 2. Git Operation Edge Cases

### Vibe-Kanban Handles:

#### Merge Conflicts
```rust
GitOperationError::MergeConflicts { message, op }
```
- Detects when merge/rebase/cherry-pick/revert has conflicts
- Returns HTTP 409 (Conflict)
- Provides list of conflicted files
- Tracks conflict operation type: `rebase`, `merge`, `cherry_pick`, `revert`

#### Rebase In Progress
```rust
GitOperationError::RebaseInProgress
```
- Blocks new operations during active rebase
- Returns HTTP 409
- Message: "A rebase is already in progress. Resolve conflicts or abort..."

#### Force Push Required
```rust
PushError::ForcePushRequired
```
- Detects when history has diverged
- Returns specific error type
- UI provides explicit force-push option with confirmation

#### Branch Not Found
```typescript
CreatePrError::TargetBranchNotFound { branch }
```
- Returns which branch was not found
- Allows user to select different target

#### Git CLI Not Installed
```typescript
CreatePrError::GitHubCliNotInstalled
CreatePrError::GitHubCliNotLoggedIn
CreatePrError::GitCliNotInstalled
CreatePrError::GitCliNotLoggedIn
```
- Separate error types for each failure mode
- Provides setup wizard to fix

### Gluon-Agent Current State:
- Basic error handling
- No conflict detection
- No rebase state awareness

### Gluon-Agent Should Add:
- [ ] Detect merge conflicts before they cause failures
- [ ] Check for rebase-in-progress state
- [ ] Distinguish between push rejection reasons
- [ ] GitHub CLI availability checking with setup guidance

---

## 3. WebSocket Connection Edge Cases

### Vibe-Kanban Handles:

#### Exponential Backoff Reconnection
```typescript
const delay = Math.min(8000, 1000 * Math.pow(2, attempt));
```
- Starts at 1 second
- Doubles each attempt: 1s → 2s → 4s → 8s
- Caps at 8 seconds max
- Resets on successful connection

#### Terminal State Detection
```typescript
if ('finished' in msg) {
  finishedRef.current = true;
  ws.close(1000, 'finished');
  // Do NOT reconnect
}
```
- Recognizes when stream is complete
- Prevents infinite reconnection loops
- Clean close with code 1000

#### Clean vs Unexpected Closure
```typescript
if (finishedRef.current || (evt?.code === 1000 && evt?.wasClean)) {
  return; // Don't reconnect
}
// Otherwise reconnect
```
- Distinguishes intentional vs error closures
- Only reconnects on unexpected disconnects

#### Retry Limits
```typescript
// Log streaming: max 6 attempts
const delay = Math.min(1500, 250 * 2 ** (next - 1));
```
- Different limits for different stream types
- Faster retry for logs (250ms base)

#### Event Handler Cleanup
```typescript
ws.onopen = null;
ws.onmessage = null;
ws.onerror = null;
ws.onclose = null;
ws.close();
```
- Clears handlers before close
- Prevents callbacks after cleanup

### Gluon-Agent Current State:
- Basic WebSocket
- No exponential backoff
- No terminal state detection

### Gluon-Agent Should Add:
- [ ] Implement exponential backoff
- [ ] Add terminal state messages
- [ ] Distinguish closure types
- [ ] Clean handler cleanup on unmount

---

## 4. State Persistence Edge Cases

### Vibe-Kanban Handles:

#### localStorage Failures
```typescript
function loadSizes(key: string, fallback: SplitSizes): SplitSizes {
  try {
    const saved = localStorage.getItem(key);
    if (!saved) return fallback;
    const parsed = JSON.parse(saved);
    if (Array.isArray(parsed) && parsed.length === 2) return parsed;
    return fallback;
  } catch {
    return fallback;  // Graceful degradation
  }
}

function saveSizes(key: string, sizes: SplitSizes): void {
  try {
    localStorage.setItem(key, JSON.stringify(sizes));
  } catch {
    // Ignore errors - no UI feedback needed
  }
}
```
- Try-catch around all localStorage operations
- Returns sensible defaults on parse errors
- Silently ignores write failures
- Validates data shape before using

#### Versioned Storage Keys
```typescript
const STORAGE_KEYS = {
  KANBAN_ATTEMPT: 'tasksLayout.desktop.v2.kanbanAttempt',
  ATTEMPT_AUX: 'tasksLayout.desktop.v2.attemptAux',
};
```
- Version numbers in keys
- Allows migration without corrupting old data

### Gluon-Agent Should Add:
- [ ] Try-catch all localStorage operations
- [ ] Provide fallback values
- [ ] Version storage keys for future migrations
- [ ] Validate stored data shapes

---

## 5. API Error Edge Cases

### Vibe-Kanban Handles:

#### Structured Error Hierarchy
```rust
pub enum ApiError {
    Project(...),
    TaskAttempt(...),
    GitService(...),
    Image(...),
    Multipart(...),
    Unauthorized,
    BadRequest(String),
    Conflict(String),
    Forbidden(String),
}
```
- Typed errors for each domain
- Consistent HTTP status mapping
- User-friendly messages

#### Two-Tier Error Handling
```typescript
// Check for error_data first (structured errors)
if (result.error_data) {
  throw new ApiError<E>(
    result.message || 'API request failed',
    response.status,
    response,
    result.error_data  // Typed error data
  );
}
```
- HTTP-level errors (status codes)
- Application-level errors (JSON response)
- Preserves structured error data for UI

#### Context-Aware Messages
```rust
// Git operation conflicts
"A rebase is already in progress. Resolve conflicts or abort..."

// Image errors
"Image is too large: {size}MB. Maximum allowed: {max}MB"

// Remote errors
"Remote service timeout. Please try again."
"Please sign in again"
```
- Domain-specific guidance
- Actionable error messages

#### Token Expiration
```rust
TokenExpiration => 401 Unauthorized
```
- Detects expired tokens
- Returns consistent 401
- UI can trigger re-authentication

### Gluon-Agent Current State:
- Basic error handling
- Generic error messages
- No structured error types

### Gluon-Agent Should Add:
- [ ] Create typed error enums
- [ ] Add context to error messages
- [ ] Implement two-tier error handling
- [ ] Handle token/auth expiration gracefully

---

## 6. Process Execution Edge Cases

### Vibe-Kanban Handles:

#### Dropped Processes
```typescript
/**
 * dropped: true if this process is excluded from the current
 * history view (due to restore/trimming). Hidden from logs/timeline;
 * still listed in the Processes tab.
 */
dropped: boolean,
```
- Tracks processes excluded from main view
- Still accessible in detailed view
- Supports history trimming/restoration

#### Before/After Commit Tracking
```typescript
before_head_commit: string | null,
after_head_commit: string | null,
```
- Captures git state before process starts
- Captures state after process ends
- Enables diff calculation

#### Execution Process States
```typescript
enum ExecutionProcessStatus {
  running = "running",
  completed = "completed",
  failed = "failed",
  killed = "killed"
}
```
- Distinguishes "killed" from "failed"
- Clear status for process lifecycle

#### In-Memory Queue Service
```rust
pub struct QueuedMessageService {
    queue: Arc<DashMap<Uuid, QueuedMessage>>,
}
```
- One message per task attempt
- Atomic operations
- Non-persistent (acknowledged limitation)

### Gluon-Agent Should Add:
- [ ] Track git state before/after execution
- [ ] Distinguish "killed" from "failed" status
- [ ] Consider process history trimming
- [ ] Add queued message support

---

## 7. Authentication Edge Cases

### Vibe-Kanban Handles:

#### OAuth Handoff Flow
```typescript
handoffInit: async (provider, returnTo) => {
  // Returns handoff_id and authorize_url
}
```
- Separate handoff initiation
- Return URL tracking
- Provider-agnostic design

#### Degraded Mode
```typescript
StatusResponse = {
  logged_in: boolean,
  profile: ProfileResponse | null,
  degraded: boolean | null,  // <-- Key field
};
```
- Tracks when auth service is degraded
- UI can show appropriate warnings
- Graceful degradation

#### Token Refresh
```typescript
/** Returns the current access token (auto-refreshes if needed) */
getToken: async (): Promise<TokenResponse | null>
```
- Automatic token refresh
- Returns null on failure (don't throw)

### Gluon-Agent Should Add:
- [ ] Consider authentication for web UI
- [ ] Implement graceful degradation mode
- [ ] Handle token refresh scenarios

---

## 8. UI State Edge Cases

### Vibe-Kanban Handles:

#### Panel State Persistence
```typescript
const [isOpen, setIsOpen] = useState(() => {
  const stored = localStorage.getItem(TODO_PANEL_OPEN_KEY);
  return stored === null ? true : stored === 'true';
});
```
- Lazy initialization from storage
- Default value when not set
- String-to-boolean conversion

#### Split Panel Sizing
```typescript
if (Array.isArray(parsed) && parsed.length === 2) return parsed;
return fallback;
```
- Validates array shape
- Validates length
- Falls back on invalid data

### Gluon-Agent Should Add:
- [ ] Persist UI state (panel open/closed, sizes)
- [ ] Validate stored state shapes
- [ ] Provide sensible defaults

---

## 9. Multipart Upload Edge Cases

### Vibe-Kanban Handles:

#### Multipart Parse Errors
```rust
Multipart(...) => 400 BadRequest
```
- Message: "Failed to upload file. Please ensure the file is valid..."
- Catches malformed uploads

#### Credentials Include
```typescript
const response = await fetch('/api/images/upload', {
  method: 'POST',
  body: formData,
  credentials: 'include',  // <-- Important
});
```
- Includes credentials for authenticated uploads
- Consistent across all upload endpoints

### Gluon-Agent Should Add (when implementing uploads):
- [ ] Handle malformed multipart data
- [ ] Include credentials on upload requests
- [ ] Validate content-type header

---

## 10. Concurrent Operations Edge Cases

### Vibe-Kanban Handles:

#### Process Store Locking
```rust
pub struct LocalContainerService {
    child_store: Arc<RwLock<HashMap<Uuid, Arc<RwLock<AsyncGroupChild>>>>>,
    msg_stores: Arc<RwLock<HashMap<Uuid, Arc<MsgStore>>>>,
}
```
- RwLock for concurrent access
- Per-process locks
- Arc for shared ownership

#### DashMap for Concurrent Access
```rust
queue: Arc<DashMap<Uuid, QueuedMessage>>
```
- Lock-free concurrent hashmap
- Better performance under contention

### Gluon-Agent Should Consider:
- [ ] Review concurrent access patterns
- [ ] Consider lock-free data structures for hot paths
- [ ] Ensure thread-safety in background tasks

---

## Summary: Priority Edge Cases to Implement

### Must Have (P1)
1. **API Error Types** - Structured errors with context
2. **WebSocket Reconnection** - Exponential backoff
3. **localStorage Safety** - Try-catch, validation, fallbacks

### Should Have (P2)
4. **Git Conflict Detection** - Before operations fail
5. **Terminal State Messages** - For WebSocket streams
6. **Token/Auth Handling** - Refresh, expiration

### Nice to Have (P3)
7. **File Upload Validation** - Size, type, path
8. **Process State Tracking** - Before/after commits
9. **Degraded Mode** - Graceful service degradation
