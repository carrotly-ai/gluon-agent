# Implementation Plan: Gluon-Agent Feature Enhancements

**Date**: 2025-12-04
**Based on**: Vibe-Kanban Audit Analysis
**Estimated Duration**: 8-10 weeks
**Last Updated**: 2025-12-04

---

## Progress Summary

| Feature | Priority | Status | Notes |
|---------|----------|--------|-------|
| Image Attachments | P1 | ✅ **COMPLETE** | All 10 steps implemented |
| WebSocket Improvements | P2 | 🔲 Not Started | |
| Tagging/Labels | P3 | 🔲 Not Started | |
| Follow-Up Queue | P3 | 🔲 Not Started | |
| Advanced Git Operations | P3 | 🔲 Not Started | |

---

## Table of Contents

1. [Image Attachments](#1-image-attachments) (P1 - Critical) ✅ COMPLETE
2. [WebSocket Improvements](#2-websocket-improvements) (P2 - High)
3. [Tagging/Labels](#3-tagginglabels) (P3 - Medium)
4. [Follow-Up Queue](#4-follow-up-queue) (P3 - Medium)
5. [Advanced Git Operations](#5-advanced-git-operations) (P3 - Medium)
6. [Implementation Timeline](#6-implementation-timeline)

---

## 1. Image Attachments ✅ COMPLETE

**Priority**: P1 (Critical)
**Effort**: Medium (2 weeks)
**Impact**: High - Users can provide visual context to AI agents
**Status**: ✅ **IMPLEMENTED** (2025-12-04)

### 1.1 Overview

Enable users to attach images (screenshots, designs, diagrams) to runs. Images are copied to worktree so AI agents can reference them.

### 1.2 Database Schema ✅

Implemented in `store.py` MIGRATIONS with enhanced schema:

```sql
-- Images table (content-addressed storage)
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,           -- Relative path: {hash[:2]}/{hash}.{ext}
    original_name TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER NOT NULL,
    hash TEXT NOT NULL UNIQUE,          -- SHA256 for deduplication
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Many-to-many junction table (images can be shared across runs)
CREATE TABLE IF NOT EXISTS run_images (
    run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
    image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, image_id)
);

CREATE INDEX IF NOT EXISTS idx_run_images_run ON run_images(run_id);
CREATE INDEX IF NOT EXISTS idx_run_images_image ON run_images(image_id);
```

### 1.3 Python Models ✅

**File**: `src/gluon/models.py`

```python
class ImageAttachment(BaseModel):
    """Metadata for an uploaded image with content-addressed storage."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    file_path: str  # Relative path within storage
    original_name: str
    mime_type: str | None = None
    size_bytes: int
    hash: str  # SHA256 hash for deduplication
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @property
    def full_path(self) -> Path:
        return Path.home() / ".gluon" / "images" / self.file_path

    def to_markdown(self, base_path: str = ".gluon-images") -> str:
        """Return markdown image reference."""
        return f"![{self.original_name}]({base_path}/{self.original_name})"
```

### 1.4 Storage Service ✅

**Implemented File**: `src/gluon/image_storage.py`

```python
class ImageStorageService:
    """Content-addressed image storage with SHA256 deduplication."""
    STORAGE_DIR = Path.home() / ".gluon" / "images"
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
    ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

    def save_image(self, data: bytes, original_name: str, mime_type: str | None) -> ImageAttachment:
        """Save image with deduplication. Returns existing if hash matches."""

    def save_image_from_file(self, file_path: Path, original_name: str | None = None) -> ImageAttachment:
        """Save image from file path."""

    def get_image(self, image_id: str) -> ImageAttachment:
        """Get image metadata by ID."""

    def get_image_data(self, image_id: str) -> tuple[bytes, ImageAttachment]:
        """Get image file contents and metadata."""

    def delete_image(self, image_id: str) -> bool:
        """Delete image file and metadata."""

    def copy_to_worktree(self, run_id: str, worktree_path: Path) -> list[str]:
        """Copy all images for run to worktree/.gluon-images/ for AI visibility."""

    def get_markdown_references(self, run_id: str) -> str:
        """Get markdown references for all images attached to a run."""

    def attach_to_run(self, run_id: str, image_id: str) -> None:
        """Attach an existing image to a run."""

    def detach_from_run(self, run_id: str, image_id: str) -> bool:
        """Detach an image from a run."""

    def list_images_for_run(self, run_id: str) -> list[ImageAttachment]:
        """List all images attached to a run."""
```

**Custom Exceptions**:
- `ImageStorageError` - Base exception
- `ImageTooLargeError` - File exceeds 50MB limit
- `InvalidImageFormatError` - Unsupported MIME type
- `ImageNotFoundError` - Image ID not found

### 1.5 API Endpoints ✅

**File**: `src/gluon/web/api.py`

| Endpoint | Method | Description | Status |
|----------|--------|-------------|--------|
| `/api/images/upload` | POST | Upload image (multipart) | ✅ |
| `/api/images/{image_id}` | GET | Get image metadata | ✅ |
| `/api/images/{image_id}/file` | GET | Serve image file (with caching) | ✅ |
| `/api/images/{image_id}` | DELETE | Delete image (if no refs) | ✅ |
| `/api/runs/{run_id}/attachments` | GET | List images for run | ✅ |
| `/api/runs/{run_id}/attachments` | POST | Upload and attach image | ✅ |
| `/api/runs/{run_id}/attachments/{image_id}` | DELETE | Detach image from run | ✅ |

### 1.6 Runner Integration ✅

**File**: `src/gluon/runner.py` - Before agent execution:

```python
# Copy images to worktree/working directory for AI visibility
image_paths: list[str] = []
prompt_with_images = run.prompt
try:
    image_paths = self.image_service.copy_to_worktree(run.id, working_dir)
    if image_paths:
        image_markdown = self.image_service.get_markdown_references(run.id)
        prompt_with_images = f"{run.prompt}{image_markdown}"
        logger.info(f"Copied {len(image_paths)} images to {working_dir}/.gluon-images/")
except Exception as e:
    logger.warning(f"Failed to copy images for run {run.id}: {e}")
    # Continue without images if copy fails

# Use prompt_with_images in agent execution
```

**Implementation Details**:
- Images copied to `.gluon-images/` subdirectory in working directory
- Markdown references appended to prompt for AI visibility
- Graceful degradation if copy fails

### 1.7 Frontend Components ✅

**CreateTaskDialog.tsx** ✅:
- Drag-and-drop file input with validation (PNG, JPEG, GIF, WebP)
- Max 50MB per file validation
- Thumbnail previews with remove buttons
- Memory leak prevention (URL.revokeObjectURL on cleanup)
- Upload images after run creation, attach via API

**RunDetailDialog.tsx** ✅:
- New "Images" tab in tab bar
- Lazy loading of attachments on tab switch
- Gallery view with responsive grid (2-4 columns)
- Hover overlay with "View" button (opens in new tab)
- File info footer (name, size)
- Empty state when no images attached

### 1.8 Implementation Steps

| Step | Description | Files | Status |
|------|-------------|-------|--------|
| 1.1 | Add database migration | `store.py` | ✅ |
| 1.2 | Add ImageAttachment model | `models.py` | ✅ |
| 1.3 | Create ImageStorageService | `image_storage.py` (new) | ✅ |
| 1.4 | Add store CRUD methods | `store.py` | ✅ |
| 1.5 | Integrate with runner | `runner.py` | ✅ |
| 1.6 | Add API endpoints | `web/api.py`, `web/models.py` | ✅ |
| 1.7 | Add frontend types | `web-ui/src/lib/types.ts` | ✅ |
| 1.8 | Add frontend API | `web-ui/src/lib/api.ts` | ✅ |
| 1.9 | Update CreateTaskDialog | `CreateTaskDialog.tsx` | ✅ |
| 1.10 | Update RunDetailDialog | `RunDetailDialog.tsx` | ✅ |

**All 10 steps completed on 2025-12-04**

---

## 2. WebSocket Improvements

**Priority**: P2 (High)
**Effort**: Medium (1.5 weeks)
**Impact**: Medium-High - More efficient real-time updates

### 2.1 Overview

Implement RFC6902 JSON Patch protocol for efficient incremental state updates, with exponential backoff reconnection.

### 2.2 New Message Types

**File**: `src/gluon/web/models.py`

```python
class JsonPatchOperation(BaseModel):
    op: Literal['add', 'remove', 'replace', 'move', 'copy', 'test']
    path: str
    value: Any | None = None
    from_: str | None = Field(None, alias='from')

class JsonPatchMessage(BaseModel):
    type: str = "json_patch"
    run_id: str
    operations: list[JsonPatchOperation]

class FinishedMessage(BaseModel):
    type: str = "finished"
    run_id: str
    status: str
    final_state: dict  # Full state for safety
```

### 2.3 Patch Generator

**New File**: `src/gluon/web/patch_generator.py`

```python
class JsonPatchGenerator:
    """Generates RFC6902 JSON Patch operations for run updates."""

    def __init__(self):
        self._states: dict[str, dict[str, Any]] = {}

    def generate_patch(self, run: ExecutionRun) -> list[dict]:
        """Compare current state with last-known state, return patches."""
        run_dict = self._run_to_dict(run)
        last_state = self._states.get(run.id, {})

        operations = []
        for key, new_value in run_dict.items():
            old_value = last_state.get(key)
            if old_value != new_value:
                operations.append({
                    'op': 'replace',
                    'path': f'/{key}',
                    'value': new_value
                })

        self._states[run.id] = run_dict
        return operations
```

### 2.4 Frontend Hook

**File**: `web-ui/src/hooks/useWebSocket.ts`

```typescript
// Exponential backoff: 1s → 2s → 4s → 8s (cap)
const getBackoffDelay = (attempt: number): number => {
  return Math.min(8000, 1000 * Math.pow(2, attempt))
}

// Don't reconnect on:
// - Clean close (code 1000)
// - Finished message received
ws.onclose = (event) => {
  if (event.code === 1000 || finishedRef.current) {
    return // Don't reconnect
  }
  // Schedule reconnect with backoff
  const delay = getBackoffDelay(retryAttempt)
  setTimeout(() => connect(), delay)
}
```

### 2.5 Patch Applier

**New File**: `web-ui/src/lib/patch-applier.ts`

```typescript
export function applyJsonPatch(
  run: Run,
  operations: JsonPatchOperation[]
): Partial<Run> {
  const updated: Partial<Run> = {}
  for (const op of operations) {
    const field = op.path.slice(1) // Remove leading '/'
    if (op.op === 'replace' || op.op === 'add') {
      updated[field as keyof Run] = op.value
    }
  }
  return updated
}
```

### 2.6 Implementation Steps

| Step | Description | Files |
|------|-------------|-------|
| 2.1 | Add message type models | `web/models.py` |
| 2.2 | Create JsonPatchGenerator | `web/patch_generator.py` (new) |
| 2.3 | Update WebSocket manager | `web/websocket.py` |
| 2.4 | Add finished message on run complete | `runner.py` |
| 2.5 | Add patch-applier utility | `web-ui/src/lib/patch-applier.ts` |
| 2.6 | Update useWebSocket hook | `web-ui/src/hooks/useWebSocket.ts` |
| 2.7 | Update message handlers | `web-ui/src/hooks/useRunsWithWebSocket.ts` |
| 2.8 | Add TypeScript types | `web-ui/src/lib/types.ts` |

---

## 3. Tagging/Labels

**Priority**: P3 (Medium)
**Effort**: Low-Medium (1 week)
**Impact**: Medium - Better organization

### 3.1 Overview

Add global labels with colors that can be applied to runs for categorization and filtering.

### 3.2 Database Schema

```sql
CREATE TABLE IF NOT EXISTS labels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_labels (
    run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
    label_id TEXT NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, label_id)
);

CREATE INDEX IF NOT EXISTS idx_run_labels_run ON run_labels(run_id);
CREATE INDEX IF NOT EXISTS idx_run_labels_label ON run_labels(label_id);
```

### 3.3 Python Models

```python
class Label(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    color: str  # e.g., "#FF5733", "red"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

### 3.4 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/labels` | GET | List all labels |
| `/api/labels` | POST | Create label |
| `/api/labels/{id}` | PUT | Update label |
| `/api/labels/{id}` | DELETE | Delete label |
| `/api/runs/{run_id}/labels/{label_id}` | POST | Add label to run |
| `/api/runs/{run_id}/labels/{label_id}` | DELETE | Remove label from run |
| `/api/runs/{run_id}/labels` | GET | Get labels for run |
| `/api/labels/{label_id}/runs` | GET | Get runs with label |

### 3.5 Frontend Components

**KanbanBoard.tsx**:
- Add label filter buttons in header
- Show label badges on RunCard

**RunDetailDialog.tsx**:
- Show assigned labels with remove buttons
- "Add Label" dropdown

**SettingsPage.tsx**:
- Label management section (CRUD)

### 3.6 Implementation Steps

| Step | Description | Files |
|------|-------------|-------|
| 3.1 | Add database migrations | `store.py` |
| 3.2 | Add Label model | `models.py` |
| 3.3 | Add store CRUD methods | `store.py` |
| 3.4 | Add API endpoints | `web/api.py` |
| 3.5 | Add frontend types/API | `types.ts`, `api.ts` |
| 3.6 | Add label badges to RunCard | `RunCard.tsx` |
| 3.7 | Add label filter to KanbanBoard | `KanbanBoard.tsx` |
| 3.8 | Add label section to RunDetailDialog | `RunDetailDialog.tsx` |
| 3.9 | Add label management to SettingsPage | `SettingsPage.tsx` |

---

## 4. Follow-Up Queue

**Priority**: P3 (Medium)
**Effort**: Low-Medium (1 week)
**Impact**: Medium - Better workflow

### 4.1 Overview

Allow users to queue a follow-up prompt while a run is executing. Auto-resume when current run completes.

### 4.2 Database Schema

```sql
CREATE TABLE IF NOT EXISTS follow_up_queue (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    variant TEXT DEFAULT 'default',
    status TEXT DEFAULT 'queued',
    created_at TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    executed_at TEXT,
    cancelled_at TEXT,
    UNIQUE(run_id)
);

CREATE INDEX IF NOT EXISTS idx_queue_run ON follow_up_queue(run_id);
CREATE INDEX IF NOT EXISTS idx_queue_status ON follow_up_queue(status);
```

### 4.3 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/runs/{run_id}/queue` | POST | Queue follow-up message |
| `/api/runs/{run_id}/queue` | GET | Get queue status |
| `/api/runs/{run_id}/queue` | DELETE | Cancel queued message |

### 4.4 Queue Status Response

```python
# Empty queue
{"status": "empty"}

# Queued message
{
    "status": "queued",
    "message": {
        "task_attempt_id": "abc-1234",
        "data": {"message": "Now add tests", "variant": "default"},
        "queued_at": "2025-12-04T10:30:00Z"
    }
}
```

### 4.5 Runner Integration

**File**: `src/gluon/runner.py` - After run completion:

```python
# Check for queued follow-up
queue_status = store.get_queue_status(run.id)
if queue_status.get("status") == "queued":
    queued_message = queue_status["message"]
    follow_up_prompt = queued_message["data"]["message"]

    # Auto-submit follow-up with same session
    follow_up_run = await runner.submit(
        project_id=run.project_id,
        prompt=follow_up_prompt,
        initiator=f"follow_up:{run.id}",
        claude_session_id=run.claude_session_id,
        use_worktree=run.use_worktree,
    )

    store.mark_queue_executed(run.id)
```

### 4.6 Frontend Components

**RunDetailDialog.tsx** (for running tasks):
- If queue empty: Show "Queue follow-up" input
- If queued: Show message + "Cancel" button
- Real-time status via WebSocket

### 4.7 Implementation Steps

| Step | Description | Files |
|------|-------------|-------|
| 4.1 | Add database migration | `store.py` |
| 4.2 | Add FollowUpQueue model | `models.py` |
| 4.3 | Add store CRUD methods | `store.py` |
| 4.4 | Add API endpoints | `web/api.py` |
| 4.5 | Integrate with runner | `runner.py` |
| 4.6 | Add frontend types/API | `types.ts`, `api.ts` |
| 4.7 | Add queue UI to RunDetailDialog | `RunDetailDialog.tsx` |
| 4.8 | Add WebSocket queue_update messages | `websocket.py` |

---

## 5. Advanced Git Operations

**Priority**: P3 (Medium)
**Effort**: Medium-High (2 weeks)
**Impact**: Medium - Power-user workflows

### 5.1 Overview

Add rebase support, conflict detection, force push controls, and branch management.

### 5.2 Exception Hierarchy

**File**: `src/gluon/core.py`

```python
class GitOperationError(Exception):
    """Base exception for git operations."""
    pass

class GitMergeConflict(GitOperationError):
    def __init__(self, files: list[str], operation: str = "merge"):
        self.files = files
        self.operation = operation

class GitRebaseInProgress(GitOperationError):
    pass

class GitForcePushRequired(GitOperationError):
    def __init__(self, branch: str, commits_to_delete: int):
        self.branch = branch
        self.commits_to_delete = commits_to_delete
```

### 5.3 Enhanced GitStatus Model

```python
class GitStatus(BaseModel):
    # ... existing fields ...

    # Conflict/rebase state
    is_rebase_in_progress: bool = False
    is_merge_in_progress: bool = False
    conflict_operation: str | None = None  # "rebase", "merge", "cherry_pick"
    conflicted_files: list[str] = Field(default_factory=list)
    rebase_current_step: int | None = None
    rebase_total_steps: int | None = None
```

### 5.4 GitManager Methods

**File**: `src/gluon/git_manager.py`

```python
# Rebase Operations
async def rebase_branch(self, path: Path, onto_branch: str) -> dict
async def rebase_abort(self, path: Path) -> dict
async def rebase_continue(self, path: Path) -> dict
async def rebase_skip(self, path: Path) -> dict

# Conflict Detection
async def detect_conflicts(self, path: Path) -> list[dict]
async def get_conflict_diff(self, path: Path, file_path: str) -> dict
async def resolve_conflict(self, path: Path, file_path: str, resolution: str) -> dict

# Force Push
async def check_force_push_needed(self, path: Path, branch: str) -> dict
async def force_push(self, path: Path, branch: str, force_with_lease: bool) -> dict

# Branch Management
async def rename_branch(self, path: Path, old_name: str, new_name: str) -> dict
async def change_base_branch(self, path: Path, feature: str, new_base: str) -> dict
async def list_branches(self, path: Path, remote: bool) -> list[dict]
async def delete_branch(self, path: Path, branch: str, force: bool) -> dict
```

### 5.5 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/projects/{id}/conflicts` | GET | Detect conflicts |
| `/api/projects/{id}/conflicts/{file}` | GET | Get conflict diff |
| `/api/projects/{id}/conflicts/resolve` | POST | Resolve conflict |
| `/api/projects/{id}/rebase` | POST | Start rebase |
| `/api/projects/{id}/rebase/continue` | POST | Continue rebase |
| `/api/projects/{id}/rebase/abort` | POST | Abort rebase |
| `/api/projects/{id}/rebase/skip` | POST | Skip commit |
| `/api/projects/{id}/force-push-check` | GET | Check if needed |
| `/api/projects/{id}/force-push` | POST | Execute force push |
| `/api/projects/{id}/branches` | GET | List branches |
| `/api/projects/{id}/branches/rename` | POST | Rename branch |
| `/api/projects/{id}/branches/change-base` | POST | Change base |
| `/api/projects/{id}/branches/{name}` | DELETE | Delete branch |

### 5.6 Frontend Components

**ConflictResolutionDialog.tsx** (new):
- List conflicted files
- 3-way diff view (base, ours, theirs)
- Resolution options (ours/theirs/manual)
- Continue/Abort/Skip buttons

**RunDetailDialog.tsx**:
- Show conflict warning badge
- "Resolve Conflicts" button when detected

**KanbanBoard.tsx**:
- Visual indicator on cards with conflicts

### 5.7 Implementation Steps

| Step | Description | Files |
|------|-------------|-------|
| 5.1 | Add exception classes | `core.py` |
| 5.2 | Extend GitStatus model | `models.py` |
| 5.3 | Add database migrations | `store.py` |
| 5.4 | Implement detect_conflicts | `git_manager.py` |
| 5.5 | Implement rebase operations | `git_manager.py` |
| 5.6 | Implement force push | `git_manager.py` |
| 5.7 | Implement branch management | `git_manager.py` |
| 5.8 | Add API endpoints | `web/api.py` |
| 5.9 | Add web models | `web/models.py` |
| 5.10 | Add frontend types | `types.ts` |
| 5.11 | Create ConflictResolutionDialog | `ConflictResolutionDialog.tsx` |
| 5.12 | Update RunDetailDialog | `RunDetailDialog.tsx` |

---

## 6. Implementation Timeline

### Week 1-2: Image Attachments (P1)

| Day | Tasks |
|-----|-------|
| 1-2 | Database schema, models, ImageStorageService |
| 3-4 | Store CRUD, runner integration |
| 5 | API endpoints |
| 6-7 | Frontend: CreateTaskDialog upload |
| 8-9 | Frontend: RunDetailDialog gallery |
| 10 | Testing, edge cases |

### Week 3-4: WebSocket Improvements (P2)

| Day | Tasks |
|-----|-------|
| 1-2 | Message types, JsonPatchGenerator |
| 3-4 | WebSocket manager updates, runner integration |
| 5-6 | Frontend hook with exponential backoff |
| 7-8 | Patch applier, message handlers |
| 9-10 | Testing, backward compatibility |

### Week 5: Tagging/Labels (P3)

| Day | Tasks |
|-----|-------|
| 1 | Database schema, models |
| 2 | Store CRUD, API endpoints |
| 3 | Frontend types/API |
| 4 | RunCard badges, KanbanBoard filter |
| 5 | RunDetailDialog, SettingsPage |

### Week 6: Follow-Up Queue (P3)

| Day | Tasks |
|-----|-------|
| 1 | Database schema, models |
| 2 | Store CRUD, API endpoints |
| 3 | Runner integration (auto-resume) |
| 4 | Frontend queue UI |
| 5 | WebSocket updates, testing |

### Week 7-8: Advanced Git Operations (P3)

| Day | Tasks |
|-----|-------|
| 1-2 | Exception classes, GitStatus model |
| 3-4 | Conflict detection, rebase operations |
| 5-6 | Force push, branch management |
| 7-8 | API endpoints |
| 9-10 | ConflictResolutionDialog frontend |

### Week 9-10: Integration & Polish

| Day | Tasks |
|-----|-------|
| 1-3 | End-to-end testing |
| 4-5 | Bug fixes, edge cases |
| 6-7 | Documentation updates |
| 8-10 | Performance optimization, monitoring |

---

## Critical Files Summary

### Backend (Python)

| File | Changes |
|------|---------|
| `src/gluon/models.py` | ImageResponse, Label, FollowUpQueue, GitStatus enhancements |
| `src/gluon/store.py` | CRUD for images, labels, queue; migrations |
| `src/gluon/core.py` | Git exception classes |
| `src/gluon/image_storage.py` | **NEW** - Image storage service |
| `src/gluon/git_manager.py` | Rebase, conflict, force push, branch ops |
| `src/gluon/runner.py` | Image copy, queue auto-resume |
| `src/gluon/web/api.py` | All new endpoints |
| `src/gluon/web/models.py` | Request/response models |
| `src/gluon/web/websocket.py` | JSON Patch, finished messages |
| `src/gluon/web/patch_generator.py` | **NEW** - JSON Patch generation |

### Frontend (TypeScript/React)

| File | Changes |
|------|---------|
| `web-ui/src/lib/types.ts` | All new types |
| `web-ui/src/lib/api.ts` | All new API functions |
| `web-ui/src/lib/patch-applier.ts` | **NEW** - JSON Patch application |
| `web-ui/src/hooks/useWebSocket.ts` | Exponential backoff, finished handling |
| `web-ui/src/components/CreateTaskDialog.tsx` | Image upload |
| `web-ui/src/components/RunDetailDialog.tsx` | Attachments, labels, queue |
| `web-ui/src/components/RunCard.tsx` | Label badges, queue indicator |
| `web-ui/src/components/KanbanBoard.tsx` | Label filtering |
| `web-ui/src/components/SettingsPage.tsx` | Label management |
| `web-ui/src/components/ConflictResolutionDialog.tsx` | **NEW** - Git conflicts UI |

---

## Success Metrics

| Feature | Metric | Target |
|---------|--------|--------|
| Image Attachments | Upload success rate | >99% |
| Image Attachments | Avg upload time | <2s for 5MB |
| WebSocket | Reconnection success | >95% within 30s |
| WebSocket | Patch size vs full update | <20% bandwidth |
| Labels | Filter response time | <100ms |
| Queue | Auto-resume success | >99% |
| Git Ops | Conflict detection accuracy | 100% |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Image storage fills disk | Add cleanup job for orphaned images |
| WebSocket patch conflicts | Send full state in `finished` message |
| Queue race conditions | Use database transactions, UNIQUE constraint |
| Git operation hangs | Add timeouts to all git commands |
| Large file uploads | Enforce 50MB limit, chunked upload for larger |

---

## Next Steps

1. Review this plan and confirm priorities
2. Create feature branches for each major feature
3. Start with Image Attachments (highest impact)
4. Implement incrementally with testing at each phase
