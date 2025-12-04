# Feature Inspiration: Patterns from Vibe-Kanban

**Date**: 2025-12-04
**Purpose**: Document specific patterns and implementations from vibe-kanban that could inspire gluon-agent improvements

---

## 1. Image Attachment System (TOP PRIORITY)

### Inspiration: Complete Image Workflow

Vibe-kanban implements a complete image handling pipeline:

#### API Design Pattern
```typescript
// Three-tier upload endpoints
/api/images/upload                              // General upload
/api/images/task/{taskId}/upload               // Task-specific
/api/task-attempts/{attemptId}/images/upload   // Attempt-specific + copy to worktree
```

#### Image Response Model
```typescript
export type ImageResponse = {
  id: string,
  file_path: string,          // Path for markdown reference
  original_name: string,      // User's filename
  mime_type: string | null,
  size_bytes: bigint,
  hash: string,               // Content hash for dedup
  created_at: string,
  updated_at: string,
};
```

#### Markdown Integration
```typescript
export function imageToMarkdown(image: ImageResponse): string {
  return `![${image.original_name}](${image.file_path})`;
}

export function appendImageMarkdown(prev: string, image: ImageResponse): string {
  const markdownText = imageToMarkdown(image);
  if (prev.trim() === '') return markdownText + '\n';
  const needsNewline = !prev.endsWith('\n');
  return prev + (needsNewline ? '\n' : '') + markdownText + '\n';
}
```

#### Worktree Copy Pattern
When uploading to a task attempt, images are **immediately copied to the worktree** so the AI agent can see them:
```rust
deployment
    .image()
    .copy_images_by_ids_to_worktree(&worktree_path, &[image_response.id])
    .await?;
```

### Recommended Implementation for Gluon:

1. **Database Schema**:
```sql
CREATE TABLE images (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    original_name TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER NOT NULL,
    hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE run_images (
    run_id TEXT NOT NULL,
    image_id TEXT NOT NULL,
    PRIMARY KEY (run_id, image_id),
    FOREIGN KEY (run_id) REFERENCES execution_runs(id),
    FOREIGN KEY (image_id) REFERENCES images(id)
);
```

2. **Storage Path**: `~/.gluon/images/{hash}.{ext}`

3. **API Endpoints**:
   - `POST /api/images/upload` - Upload image
   - `POST /api/runs/{run_id}/images` - Attach to run
   - `GET /api/runs/{run_id}/images` - List attachments
   - `GET /api/images/{id}/file` - Serve image

---

## 2. JSON Patch WebSocket Protocol

### Inspiration: Efficient State Updates

Instead of sending full state on every update, vibe-kanban uses RFC6902 JSON Patch:

#### Hook Pattern
```typescript
export const useJsonPatchWsStream = <T extends object>(
  endpoint: string | undefined,
  enabled: boolean,
  initialData: () => T,
  options?: UseJsonPatchStreamOptions<T>
): UseJsonPatchStreamResult<T> => {
  // ...

  ws.onmessage = (event) => {
    const msg: WsMsg = JSON.parse(event.data);

    if ('JsonPatch' in msg) {
      const patches: Operation[] = msg.JsonPatch;
      const filtered = deduplicatePatches ? deduplicatePatches(patches) : patches;

      // Deep clone before mutation
      const next = structuredClone(current);

      // Apply patch (mutates the clone)
      applyPatch(next, filtered);

      dataRef.current = next;
      setData(next);
    }

    // Terminal state
    if ('finished' in msg) {
      finishedRef.current = true;
      ws.close(1000, 'finished');
    }
  };
};
```

#### Message Format
```typescript
type WsJsonPatchMsg = { JsonPatch: Operation[] };
type WsFinishedMsg = { finished: boolean };
type WsMsg = WsJsonPatchMsg | WsFinishedMsg;
```

#### Reconnection with Backoff
```typescript
const delay = Math.min(8000, 1000 * Math.pow(2, attempt));
// 1s, 2s, 4s, 8s (cap)
```

### Recommended Implementation for Gluon:

1. **Use rfc6902 library** for JSON Patch
2. **Add message types**:
```typescript
type GluonWsMsg =
  | { type: 'patch', patches: Operation[] }
  | { type: 'finished', run_id: string }
  | { type: 'error', message: string };
```
3. **Implement backoff** in WebSocket hook

---

## 3. Multi-Agent Executor Architecture

### Inspiration: Pluggable Agent System

Vibe-kanban supports 9 different AI agents through a pluggable architecture:

#### Executor Profile Pattern
```typescript
export type ExecutorProfileId = {
  executor: BaseCodingAgent,  // e.g., "CLAUDE_CODE", "GEMINI"
  variant: string | null,      // e.g., "PLAN", "ROUTER"
};
```

#### Per-Agent Configuration
```typescript
export type ClaudeCode = {
  append_prompt: AppendPrompt,
  claude_code_router?: boolean | null,
  plan?: boolean | null,
  approvals?: boolean | null,
  model?: string | null,
  dangerously_skip_permissions?: boolean | null,
  disable_api_key?: boolean | null,
  base_command_override?: string | null,
  additional_params?: Array<string> | null,
};

export type Gemini = {
  append_prompt: AppendPrompt,
  model?: string | null,
  yolo?: boolean | null,
  base_command_override?: string | null,
  additional_params?: Array<string> | null,
};
```

#### Capability Detection
```typescript
export enum BaseAgentCapability {
  SESSION_FORK = "SESSION_FORK",
  SETUP_HELPER = "SETUP_HELPER"
}

// In user system info
capabilities: { [key in string]?: Array<BaseAgentCapability> }
```

### Recommended Implementation for Gluon:

1. **Create executor interface**:
```python
class Executor(ABC):
    @abstractmethod
    def get_command(self, prompt: str) -> List[str]: ...

    @abstractmethod
    def parse_output(self, output: str) -> AgentMessage: ...

    @property
    @abstractmethod
    def capabilities(self) -> Set[str]: ...
```

2. **Start with existing agents**:
   - `ClaudeCodeExecutor` (current)
   - `GeminiExecutor` (gemini-cli)
   - `AmpExecutor` (amp)

---

## 4. Follow-Up Queue System

### Inspiration: Async Message Queuing

Vibe-kanban allows queuing follow-up messages during execution:

#### Queue API
```typescript
export const queueApi = {
  queue: async (attemptId: string, data: { message: string; variant: string | null }) => {
    // Queue message to execute after current task completes
  },

  cancel: async (attemptId: string) => {
    // Cancel queued message
  },

  getStatus: async (attemptId: string): Promise<QueueStatus> => {
    // Check if message is queued
  },
};
```

#### Queue Status Types
```typescript
export type QueueStatus =
  | { "status": "empty" }
  | { "status": "queued", message: QueuedMessage };
```

### Recommended Implementation for Gluon:

1. **Add queued_prompt column** to execution_runs table
2. **Check queue on completion** - auto-resume with queued prompt
3. **API endpoints**:
   - `POST /api/runs/{id}/queue` - Queue follow-up
   - `DELETE /api/runs/{id}/queue` - Cancel queue
   - `GET /api/runs/{id}/queue` - Get status

---

## 5. Draft/Scratch System

### Inspiration: Auto-Save Drafts

Vibe-kanban auto-saves drafts as users type:

#### Scratch Types
```typescript
export type ScratchPayload =
  | { "type": "DRAFT_TASK", "data": string }
  | { "type": "DRAFT_FOLLOW_UP", "data": DraftFollowUpData };

export enum ScratchType {
  DRAFT_TASK = "DRAFT_TASK",
  DRAFT_FOLLOW_UP = "DRAFT_FOLLOW_UP"
}
```

#### Scratch API
```typescript
export const scratchApi = {
  create: async (scratchType, id, data) => { ... },
  get: async (scratchType, id) => { ... },
  update: async (scratchType, id, data) => { ... },
  delete: async (scratchType, id) => { ... },
  getStreamUrl: (scratchType, id) => `/api/scratch/${scratchType}/${id}/stream/ws`,
};
```

### Recommended Implementation for Gluon:

1. **localStorage for drafts** (simpler):
```typescript
const DRAFT_KEY = 'gluon.draft.task';
const [draft, setDraft] = useLocalStorage(DRAFT_KEY, '');
```

2. **Debounced save** to avoid excessive writes

---

## 6. Tagging System

### Inspiration: Global Tags

Vibe-kanban has a comprehensive tagging system:

#### Tag Model
```typescript
export type Tag = {
  id: string,
  tag_name: string,
  content: string,       // Description/metadata
  created_at: string,
  updated_at: string,
};
```

#### Task-Tag Association
```typescript
export type CreateTask = {
  // ...
  image_ids: Array<string> | null,  // Note: same pattern as images
  // Tags would be similar
};
```

### Recommended Implementation for Gluon:

1. **Simple labels table**:
```sql
CREATE TABLE labels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    color TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE run_labels (
    run_id TEXT NOT NULL,
    label_id TEXT NOT NULL,
    PRIMARY KEY (run_id, label_id)
);
```

2. **Filter by label** in UI

---

## 7. Approval/Human-in-the-Loop System

### Inspiration: Tool Approval UI

Vibe-kanban provides a way to approve/deny agent tool calls:

#### Approval Model
```typescript
export type ApprovalStatus =
  | { "status": "pending" }
  | { "status": "approved" }
  | { "status": "denied", reason?: string }
  | { "status": "timed_out" };
```

#### Tool Status with Approval
```typescript
export type ToolStatus =
  | { "status": "created" }
  | { "status": "success" }
  | { "status": "failed" }
  | { "status": "denied", reason: string | null }
  | { "status": "pending_approval", approval_id: string, requested_at: string, timeout_at: string }
  | { "status": "timed_out" };
```

### Recommended Implementation for Gluon:

This would require integration with Claude Code's approval system. Could be useful for:
- High-risk operations (delete files, system commands)
- Budget controls (expensive API calls)

---

## 8. Normalized Log Entries

### Inspiration: Structured Log Types

Vibe-kanban normalizes agent output into typed entries:

```typescript
export type NormalizedEntry = {
  timestamp: string | null,
  entry_type: NormalizedEntryType,
  content: string,
};

export type NormalizedEntryType =
  | { "type": "user_message" }
  | { "type": "user_feedback", denied_tool: string }
  | { "type": "assistant_message" }
  | { "type": "tool_use", tool_name: string, action_type: ActionType, status: ToolStatus }
  | { "type": "system_message" }
  | { "type": "error_message", error_type: NormalizedEntryError }
  | { "type": "thinking" }
  | { "type": "loading" }
  | { "type": "next_action", failed: boolean, execution_processes: number, needs_setup: boolean };
```

### Recommended Implementation for Gluon:

Gluon already has `messages.jsonl` - could enhance with structured parsing:
1. Parse Claude Code's JSONL output
2. Create typed message objects
3. Enable filtering by type in UI

---

## 9. Editor Integration

### Inspiration: Deep Editor Links

Vibe-kanban generates URLs to open projects in editors:

```typescript
export type EditorConfig = {
  editor_type: EditorType,
  custom_command: string | null,
  remote_ssh_host: string | null,
  remote_ssh_user: string | null,
};

export enum EditorType {
  VS_CODE = "VS_CODE",
  CURSOR = "CURSOR",
  WINDSURF = "WINDSURF",
  INTELLI_J = "INTELLI_J",
  ZED = "ZED",
  XCODE = "XCODE",
  CUSTOM = "CUSTOM"
}
```

#### URL Generation
```typescript
// vscode://vscode-remote/ssh-remote+user@host/path
openEditor: async (id: string, data: OpenEditorRequest) => {
  // Returns { url: string | null }
}
```

### Recommended Implementation for Gluon:

1. **"Open in Editor" button** on RunCard
2. **Settings for editor preference**
3. **URL schemes**:
   - VS Code: `vscode://file/{path}`
   - Cursor: `cursor://file/{path}`

---

## 10. Git Branch Status Details

### Inspiration: Comprehensive Branch Info

```typescript
export type BranchStatus = {
  commits_behind: number | null,
  commits_ahead: number | null,
  has_uncommitted_changes: boolean | null,
  head_oid: string | null,
  uncommitted_count: number | null,
  untracked_count: number | null,
  target_branch_name: string,
  remote_commits_behind: number | null,
  remote_commits_ahead: number | null,
  merges: Array<Merge>,
  is_rebase_in_progress: boolean,
  conflict_op: ConflictOp | null,
  conflicted_files: Array<string>,
};
```

### Recommended Implementation for Gluon:

Enhance `GitStatus` model with:
- `uncommitted_count`
- `untracked_count`
- `remote_ahead` / `remote_behind`
- `has_conflicts`
- `conflict_files`

---

## Priority Summary

| Feature | Effort | Impact | Priority |
|---------|--------|--------|----------|
| Image Attachments | Medium | High | **P1** |
| JSON Patch WebSocket | Medium | Medium | **P2** |
| Multi-Agent Architecture | High | High | **P2** |
| Follow-Up Queue | Low | Medium | **P3** |
| Draft System | Low | Low | **P4** |
| Tagging | Low | Medium | **P3** |
| Editor Integration | Low | Medium | **P3** |
| Branch Status Details | Low | Low | **P4** |
