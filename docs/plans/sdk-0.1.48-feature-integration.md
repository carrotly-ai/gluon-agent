# Plan: SDK 0.1.48 Feature Integration

**Status**: Ready to implement
**Date**: 2026-03-08
**SDK Version**: claude-agent-sdk 0.1.48 (upgraded from 0.1.45)
**Branch**: `feat/sdk-0.1.48-features`

## Overview

Three features from the 0.1.46–0.1.48 SDK releases to integrate into Gluon:

| # | Feature | Priority | Effort | Depends on |
|---|---------|----------|--------|------------|
| 1 | Agent ID in tool call messages | High | S | — |
| 2 | Claude session explorer API + UI | Medium | M | — |
| 3 | Runtime MCP server management | Low | L | — |

---

## Feature 1: Agent ID in Tool Call Messages

### Problem

When agent teams are enabled, tool calls in the web UI message viewer show no indication of *which* agent (main or subagent) made the call. The `agent_id` and `agent_type` fields are now available on hook inputs (SDK 0.1.46) and we already log them in `agent_hooks.py`, but they don't flow to `messages.jsonl` or the web UI.

### Approach

Propagate `agent_id` from PostToolUse hooks into the message stream, then display a badge in the `ToolCallMessage` component. Only visible when agent teams are active (non-null `agent_id`).

### Implementation

#### Step 1: Capture agent_id in hook → message callback

**File**: `src/gluon/agent_hooks.py`

The `log_post_tool_use` function currently returns `{}`. Instead, capture `agent_id`/`agent_type` and pass to a new callback. But since `log_post_tool_use` is a standalone function (not bound to a closure with state), the cleanest path is to enhance `_make_screenshot_interceptor` and `_make_todo_mirror_hook` patterns — create a new `_make_tool_annotator` hook that writes agent context to the message callback.

```python
def _make_tool_annotator(message_callback: Callable[[dict[str, Any]], None]):
    """PostToolUse hook that emits agent_id metadata into the message stream."""

    async def on_post_tool_use(
        input_data: PostToolUseHookInput | HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput | AsyncHookJSONOutput:
        agent_id = input_data.get("agent_id")
        if not agent_id:
            return {}

        tool_name = input_data.get("tool_name", "unknown")
        message_callback({
            "timestamp": datetime.now(UTC).isoformat(),
            "type": "tool_agent_annotation",
            "content": tool_name,
            "metadata": {
                "tool_use_id": tool_use_id or input_data.get("tool_use_id"),
                "agent_id": agent_id,
                "agent_type": input_data.get("agent_type", "unknown"),
            },
        })
        return {}

    return on_post_tool_use
```

Update `build_hooks()` to accept and wire this hook when `message_callback` is provided.

#### Step 2: Alternative — Inline in agent.py (simpler)

Instead of a new hook, attach `agent_id` directly on the `tool_use` AgentMessage. But `ToolUseBlock` from the SDK stream doesn't carry `agent_id` — only hooks do. So we'd need to correlate by `tool_use_id`.

**Decision**: Go with Step 1 (separate message type). The web UI can merge `tool_agent_annotation` with the preceding `tool_use` message by `tool_use_id`. This avoids coupling hooks with the streaming loop.

#### Step 3: Web UI — Display agent badge

**File**: `web-ui/src/components/StreamingLogViewer.tsx`

In the `ToolCallMessage` component (~line 494), after the tool name span:

```tsx
{msg.metadata?.agent_id && (
  <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-sky)]/10 text-[var(--color-sky)]/70 font-mono">
    {msg.metadata.agent_type === 'main' ? 'main' : msg.metadata.agent_id.slice(0, 6)}
  </span>
)}
```

**File**: `web-ui/src/lib/types.ts`

Add `agent_id?: string` and `agent_type?: string` to the `AgentMessageData.metadata` type.

#### Step 4: Merge annotations in log stream hook

**File**: `web-ui/src/hooks/useRunLogStream.ts`

When a `tool_agent_annotation` message arrives, find the most recent `tool_use` message with matching `tool_use_id` and merge `agent_id`/`agent_type` into its metadata. Don't render `tool_agent_annotation` as a standalone message.

### Tests

- [ ] Unit test: `_make_tool_annotator` emits message only when `agent_id` is present
- [ ] Unit test: `build_hooks` includes annotator when `message_callback` is provided
- [ ] Existing hook tests still pass (no regressions)

### Acceptance Criteria

```
- Tool calls from subagents show a short agent ID badge in the web UI
- Tool calls from main agent (or non-team runs) show no badge
- No visual change when agent_teams_enabled is false
- Structured logs include agent_id/agent_type (already done in v0.1.48 upgrade)
```

---

## Feature 2: Claude Session Explorer

### Problem

Gluon only knows about sessions *it* created. Users also run `claude` CLI directly, and those sessions are invisible to Gluon. The SDK now exposes `list_sessions()` and `get_session_messages()` (v0.1.46) which read Claude's native `~/.claude/projects/` session data.

### Approach

Add a read-only API endpoint that surfaces Claude sessions for a project. Optionally add a "Session Explorer" panel in the web UI. This is purely additive — no schema changes, no store changes.

### Implementation

#### Step 1: API endpoints

**File**: `src/gluon/web/api.py`

```
GET /api/projects/{project_id}/claude-sessions
GET /api/projects/{project_id}/claude-sessions/{session_id}/messages
```

**File**: `src/gluon/web/models.py`

```python
class ClaudeSessionInfo(BaseModel):
    session_id: str
    summary: str
    last_modified: int  # epoch ms
    file_size: int
    custom_title: str | None = None
    first_prompt: str | None = None
    git_branch: str | None = None
    cwd: str | None = None

class ClaudeSessionListResponse(BaseModel):
    sessions: list[ClaudeSessionInfo]
    project_dir: str

class ClaudeSessionMessageItem(BaseModel):
    type: str        # "user" | "assistant"
    message: str
    timestamp: str | None = None

class ClaudeSessionMessagesResponse(BaseModel):
    session_id: str
    messages: list[ClaudeSessionMessageItem]
```

Implementation:

```python
@app.get("/api/projects/{project_id}/claude-sessions")
async def list_claude_sessions(
    project_id: str,
    limit: int = Query(default=20, le=100),
):
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    from claude_agent_sdk import list_sessions
    sessions = list_sessions(directory=project.path, limit=limit)

    return ClaudeSessionListResponse(
        sessions=[ClaudeSessionInfo(**vars(s)) for s in sessions],
        project_dir=project.path,
    )


@app.get("/api/projects/{project_id}/claude-sessions/{session_id}/messages")
async def get_claude_session_messages(
    project_id: str,
    session_id: str,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    from claude_agent_sdk import get_session_messages
    messages = get_session_messages(
        session_id=session_id,
        directory=project.path,
        limit=limit,
        offset=offset,
    )

    return ClaudeSessionMessagesResponse(
        session_id=session_id,
        messages=[
            ClaudeSessionMessageItem(type=m.type, message=m.message)
            for m in messages
        ],
    )
```

#### Step 2: Web UI — Session Explorer panel

**New file**: `web-ui/src/components/SessionExplorer.tsx`

A collapsible panel on the project detail page showing recent Claude sessions. Each entry shows:
- First prompt (truncated)
- Branch name
- Last modified timestamp
- Click to expand → shows conversation messages in a read-only chat view

API client functions:

**File**: `web-ui/src/lib/api.ts`

```typescript
export async function listClaudeSessions(projectId: string, limit = 20) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/claude-sessions?limit=${limit}`)
  return res.json()
}

export async function getClaudeSessionMessages(projectId: string, sessionId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/claude-sessions/${sessionId}/messages`)
  return res.json()
}
```

#### Step 3: Chat agent integration (optional)

**File**: `src/gluon/chat_agent.py`

Add an MCP tool `list_claude_sessions` that the chat agent can use to answer questions like "what did I work on yesterday?" or "show me recent sessions for project X":

```python
@tool("list_claude_sessions", "List recent Claude Code sessions for a project", {
    "project": str,
    "limit": int,
})
async def list_claude_sessions_tool(args: dict[str, Any]) -> dict[str, Any]:
    from claude_agent_sdk import list_sessions
    project = orchestrator.find_project(args["project"])
    sessions = list_sessions(directory=project.path, limit=args.get("limit", 10))
    # Format as text for the chat agent
    ...
```

### Tests

- [ ] API test: `GET /claude-sessions` returns sessions for a project with valid structure
- [ ] API test: `GET /claude-sessions` returns 404 for unknown project
- [ ] API test: `GET /claude-sessions/{id}/messages` returns messages or empty list
- [ ] Mock `claude_agent_sdk.list_sessions` to avoid filesystem dependency in CI

### Acceptance Criteria

```
- GET /api/projects/{id}/claude-sessions returns SDK session data
- GET /api/projects/{id}/claude-sessions/{sid}/messages returns conversation
- Web UI shows session list on project detail page
- Sessions from both Gluon runs and direct CLI usage are visible
- Read-only — no mutations, no schema changes
```

---

## Feature 3: Runtime MCP Server Management

### Problem

MCP servers are currently configured at `ClaudeAgentOptions` build time in `agent.py:474` via `find_mcp_config()`. Once a task starts, its available tools are fixed. The SDK now offers `client.add_mcp_server()` and `client.remove_mcp_server()` for hot-swapping MCP servers on a running session.

### Approach

Expose MCP management as an API endpoint and chat agent tool so users can add/remove MCP servers on active runs. This is a power-user feature with a narrow use case (e.g., "add the scraper MCP to this running task").

### Prerequisites

- Requires access to the live `ClaudeSDKClient` instance for a running task
- The `TaskRunner` currently doesn't expose the SDK client after task creation
- Need a way to send commands to running tasks (similar to the existing queued messages / follow-up system)

### Implementation

#### Step 1: Expose SDK client reference on running tasks

**File**: `src/gluon/runner.py`

The `_run_task()` method creates a `ClaudeSDKClient` inside an `async with` block. Store a reference in a dict keyed by `run_id`:

```python
class TaskRunner:
    def __init__(self, ...):
        ...
        self._active_clients: dict[str, ClaudeSDKClient] = {}

    async def _run_task(self, run: ExecutionRun, ...):
        ...
        async with ClaudeSDKClient(options) as client:
            self._active_clients[run.id] = client
            try:
                ...
            finally:
                self._active_clients.pop(run.id, None)

    def get_active_client(self, run_id: str) -> ClaudeSDKClient | None:
        return self._active_clients.get(run_id)
```

#### Step 2: API endpoints

**File**: `src/gluon/web/api.py`

```
POST /api/runs/{run_id}/mcp-servers       — Add MCP server to running task
DELETE /api/runs/{run_id}/mcp-servers/{name} — Remove MCP server from running task
GET /api/runs/{run_id}/mcp-servers         — List active MCP servers (if SDK supports)
```

**File**: `src/gluon/web/models.py`

```python
class AddMcpServerRequest(BaseModel):
    name: str = Field(description="Server name identifier")
    command: str = Field(description="Command to start the MCP server")
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

class McpServerResponse(BaseModel):
    name: str
    status: str  # "running", "stopped", "error"

class McpServerListResponse(BaseModel):
    run_id: str
    servers: list[McpServerResponse]
```

Implementation:

```python
@app.post("/api/runs/{run_id}/mcp-servers")
async def add_mcp_server(run_id: str, req: AddMcpServerRequest):
    client = runner.get_active_client(run_id)
    if not client:
        raise HTTPException(404, "Run not active or not found")

    from claude_agent_sdk.types import McpStdioServerConfig
    config = McpStdioServerConfig(command=req.command, args=req.args, env=req.env)
    await client.add_mcp_server(req.name, config)

    return McpServerResponse(name=req.name, status="running")
```

#### Step 3: Chat agent tool

**File**: `src/gluon/chat_agent.py`

```python
@tool("add_mcp_server", "Add an MCP server to a running task", {
    "run_id": str,
    "name": str,
    "command": str,
    "args": list,
})
async def add_mcp_server_tool(args: dict[str, Any]) -> dict[str, Any]:
    ...
```

#### Step 4: Web UI (optional, low priority)

A small "MCP Servers" section in the run detail view showing active servers with an "Add Server" button. Given the power-user nature, this could be deferred.

### Risks

- **Client lifecycle**: The SDK client is only alive while `_run_task` runs. Adding a server to a nearly-complete task is wasted effort.
- **Security**: Arbitrary command execution via `add_mcp_server` requires authorization. Should respect the same user allowlist as task creation.
- **Concurrency**: `_active_clients` dict accessed from API thread and runner asyncio loop — needs to be on the same event loop or use thread-safe access.

### Tests

- [ ] Unit test: `get_active_client` returns client during run, None after completion
- [ ] API test: POST /mcp-servers returns 404 for non-active run
- [ ] API test: POST /mcp-servers calls `client.add_mcp_server` with correct config
- [ ] Integration test: verify MCP server actually registers (may need live SDK)

### Acceptance Criteria

```
- POST /api/runs/{run_id}/mcp-servers adds a server to a live task
- DELETE /api/runs/{run_id}/mcp-servers/{name} removes it
- Returns 404 if run is not active
- Chat agent can add MCP servers via natural language
- No impact on existing MCP config at task creation time
```

---

## Execution Order

```
Feature 1 (agent ID badges)      ── Small, self-contained, high visibility
    |
Feature 2 (session explorer)     ── Medium, additive, no schema changes
    |
Feature 3 (runtime MCP)          ── Large, needs client lifecycle work, power-user only
```

Features 1 and 2 are independent and can be parallelized. Feature 3 should come last due to the runner refactoring it requires.

## Files Changed Summary

| Feature | Backend | Frontend | Tests |
|---------|---------|----------|-------|
| 1. Agent ID badges | `agent_hooks.py`, `agent.py` | `StreamingLogViewer.tsx`, `types.ts`, `useRunLogStream.ts` | `test_agent_hooks.py` |
| 2. Session explorer | `web/api.py`, `web/models.py`, optionally `chat_agent.py` | `SessionExplorer.tsx`, `api.ts` | `test_api_sessions.py` |
| 3. Runtime MCP | `runner.py`, `web/api.py`, `web/models.py`, `chat_agent.py` | (deferred) | `test_runner.py`, `test_api_mcp.py` |
