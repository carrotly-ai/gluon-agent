# External Todo Tracking: Research & Implementation Options

**Status**: Research Phase
**Date**: 2026-02-21
**SDK Version**: claude-agent-sdk 0.1.39

## Objective

Externalise the `TodoWrite` tool so that todo/task state is managed by the Gluon
orchestrator layer rather than being opaque to the system. This gives the
dashboard, bot transports, and runner full visibility into per-run task progress
without relying on message stream heuristics.

---

## 1. How Claude Agent SDK Handles Tool Calls

### 1.1 Tool Call Lifecycle

```
Claude LLM decides to use a tool
        |
        v
  [can_use_tool callback]  ── deny ──> tool blocked, Claude sees denial message
        | allow (+ optional input modification)
        v
  [PreToolUse hooks]  ── deny ──> tool blocked
        | allow (+ optional input/context modification)
        v
  [Tool executes inside Claude Code CLI]
        |
        v
  [PostToolUse hooks]  ── optional output modification
        |
        v
  Claude LLM sees tool result
```

### 1.2 Three Interception Mechanisms

| Mechanism | When | Can Modify Input? | Can Deny? | Can Modify Output? | Already Used by Gluon? |
|-----------|------|-------------------|-----------|--------------------|-----------------------|
| `can_use_tool` callback | Before execution | Yes (`updated_input`) | Yes (`PermissionResultDeny`) | No | Yes (AskUserQuestion, safety guardrails) |
| PreToolUse hook | Before execution | Yes (`updatedInput`) | Yes (`permissionDecision: "deny"`) | No | Yes (logging) |
| PostToolUse hook | After execution | No | No | Yes (`updatedMCPToolOutput`) | Yes (logging, screenshot interception) |

### 1.3 Fourth Mechanism: Tool Replacement via SDK MCP Server

The SDK supports **in-process MCP servers** that can define custom tools:

```python
from claude_agent_sdk import create_sdk_mcp_server, tool

@tool("todo_write", "Write/update the task list", {"todos": list})
async def todo_write(args):
    # Custom implementation — runs in Gluon's process
    return {"content": [{"type": "text", "text": "Todos updated"}]}

server = create_sdk_mcp_server("gluon_tools", tools=[todo_write])

options = ClaudeAgentOptions(
    mcp_servers={"gluon_tools": server},
    disallowed_tools=["TodoWrite"],  # Block the built-in
)
```

Combined with `disallowed_tools=["TodoWrite"]`, this effectively **replaces**
the built-in tool with a Gluon-managed implementation.

---

## 2. Relevant SDK Types (source: `claude_agent_sdk/types.py`)

### 2.1 Permission System

```python
@dataclass
class PermissionResultAllow:
    behavior: Literal["allow"] = "allow"
    updated_input: dict[str, Any] | None = None        # Modify tool input
    updated_permissions: list[PermissionUpdate] | None = None

@dataclass
class PermissionResultDeny:
    behavior: Literal["deny"] = "deny"
    message: str = ""       # Shown to Claude as denial reason
    interrupt: bool = False  # If True, interrupts the session

CanUseTool = Callable[
    [str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResult]
]
```

### 2.2 Hook Input Types

```python
class PreToolUseHookInput(BaseHookInput):
    hook_event_name: Literal["PreToolUse"]
    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str

class PostToolUseHookInput(BaseHookInput):
    hook_event_name: Literal["PostToolUse"]
    tool_name: str
    tool_input: dict[str, Any]
    tool_response: Any         # The tool's output
    tool_use_id: str
```

### 2.3 Hook Output Types (PreToolUse)

```python
class PreToolUseHookSpecificOutput(TypedDict):
    hookEventName: Literal["PreToolUse"]
    permissionDecision: NotRequired[Literal["allow", "deny", "ask"]]
    permissionDecisionReason: NotRequired[str]
    updatedInput: NotRequired[dict[str, Any]]       # Modify input before execution
    additionalContext: NotRequired[str]              # Extra context for Claude
```

### 2.4 Hook Output Types (PostToolUse)

```python
class PostToolUseHookSpecificOutput(TypedDict):
    hookEventName: Literal["PostToolUse"]
    additionalContext: NotRequired[str]
    updatedMCPToolOutput: NotRequired[Any]           # Replace/modify tool output
```

### 2.5 SDK MCP Tool Definition

```python
@dataclass
class SdkMcpTool(Generic[T]):
    name: str
    description: str
    input_schema: type[T] | dict[str, Any]
    handler: Callable[[T], Awaitable[dict[str, Any]]]
    annotations: ToolAnnotations | None = None

def create_sdk_mcp_server(
    name: str,
    version: str = "1.0.0",
    tools: list[SdkMcpTool[Any]] | None = None
) -> McpSdkServerConfig:
    """In-process MCP server — tools run in Gluon's Python process."""
```

---

## 3. Current Gluon Integration Points

### 3.1 `agent.py` — `_can_use_tool` callback

Already intercepts `AskUserQuestion` and dangerous Bash commands. The pattern
for TodoWrite interception would be identical:

```python
# Current pattern (agent.py:390-435)
async def _can_use_tool(self, tool_name, input_data, context):
    if tool_name == "AskUserQuestion" and self.question_handler:
        answers = await self.question_handler(self.run_id, questions)
        return PermissionResultAllow(
            behavior="allow",
            updated_input={"questions": questions, "answers": answers},
        )
    return PermissionResultAllow(behavior="allow", updated_input=input_data)
```

### 3.2 `agent_hooks.py` — Hook registration

`build_hooks()` constructs the hooks dict passed to `ClaudeAgentOptions.hooks`.
Adding a TodoWrite-specific matcher here is straightforward:

```python
# Current pattern (agent_hooks.py:370-382)
hooks = {
    "PreToolUse":  [HookMatcher(hooks=[log_pre_tool_use])],
    "PostToolUse": [HookMatcher(hooks=[log_post_tool_use])],
}
```

### 3.3 `agent.py` — `_build_options`

Options already support `disallowed_tools` (line 548) and `mcp_servers`
(line 484). Both are wired through to the SDK.

### 3.4 Message Stream in `execute()`

`ToolUseBlock` messages for TodoWrite are already yielded as `AgentMessage`
with `type="tool_use"` and `metadata.tool="TodoWrite"` (line 791-799).
The runner/core layer could parse these today, but it's fragile.

---

## 4. Implementation Approaches

### Approach A: Mirror via PostToolUse Hook (Least Invasive)

**How it works**: Let the built-in `TodoWrite` execute normally. A PostToolUse
hook captures the todo data and mirrors it to Gluon's store.

```python
async def mirror_todos(input_data, tool_use_id, context):
    if input_data.get("tool_name") != "TodoWrite":
        return {}
    todos = input_data.get("tool_input", {}).get("todos", [])
    # Write to Gluon store
    await store.update_run_todos(run_id, todos)
    # Emit WebSocket notification
    if notification_callback:
        notification_callback({"type": "todos_updated", "todos": todos})
    return {}
```

| Pros | Cons |
|------|------|
| Zero risk of breaking Claude's internal todo state | Gluon is read-only mirror; can't inject external state |
| Minimal code change (add one hook) | Claude's and Gluon's state could drift |
| Claude still "owns" the todo list | No way to pre-populate todos from orchestrator |

**Complexity**: Low
**Risk**: Low

---

### Approach B: Intercept + Mirror via `can_use_tool` (Moderate)

**How it works**: Intercept TodoWrite in the existing `_can_use_tool` callback.
Capture the data for Gluon, then allow execution with the original (or
modified) input.

```python
async def _can_use_tool(self, tool_name, input_data, context):
    if tool_name == "TodoWrite" and self.todo_handler:
        todos = input_data.get("todos", [])
        # Mirror to Gluon + optionally merge external state
        merged = await self.todo_handler(self.run_id, todos)
        return PermissionResultAllow(
            behavior="allow",
            updated_input={"todos": merged},  # Feed merged state back
        )
    # ... existing handlers ...
```

| Pros | Cons |
|------|------|
| Two-way sync possible (Gluon can inject/merge todos) | More complex state reconciliation |
| Uses existing callback pattern (same as AskUserQuestion) | `updated_input` modifies what Claude "sees" — could confuse it |
| Single interception point | Need to handle first-call vs subsequent-call differently |

**Complexity**: Medium
**Risk**: Medium (input modification could cause unexpected Claude behaviour)

---

### Approach C: Full Replacement via SDK MCP Server (Most Powerful)

**How it works**: Block the built-in `TodoWrite` with `disallowed_tools`.
Create an in-process SDK MCP server with a `gluon_todo_write` tool that
Gluon fully controls. Claude calls the replacement tool instead.

```python
from claude_agent_sdk import create_sdk_mcp_server, tool

@tool("todo_write", "Write/update the task list for tracking progress", {
    "todos": list  # Same schema as built-in TodoWrite
})
async def gluon_todo_write(args):
    todos = args.get("todos", [])
    await store.update_run_todos(run_id, todos)
    # Return confirmation that Claude will see
    summary = f"Updated {len(todos)} todo(s)"
    return {"content": [{"type": "text", "text": summary}]}

gluon_mcp = create_sdk_mcp_server("gluon", tools=[gluon_todo_write])

options = ClaudeAgentOptions(
    disallowed_tools=["TodoWrite"],
    mcp_servers={"gluon": gluon_mcp},
)
```

| Pros | Cons |
|------|------|
| Full control — Gluon owns the tool entirely | Claude sees `mcp__gluon__todo_write` not `TodoWrite` |
| Can add extra features (priority, assignee, timestamps) | System prompt must instruct Claude to use the replacement |
| In-process, no IPC overhead | Need to replicate TodoWrite's exact schema/behaviour |
| Clean separation of concerns | More code to maintain |
| Can serve state back to Claude (e.g., show existing todos) | Potential conflicts with MCP config from project `.mcp.json` |

**Complexity**: High
**Risk**: Medium (Claude may not use the tool as naturally as the built-in)

---

### Approach D: Hybrid — Mirror + Selective Replacement

**How it works**: Use Approach A (PostToolUse mirror) as the default.
For runs where the dashboard/bot needs to inject todos, upgrade to
Approach C (full replacement) via a per-run flag.

```python
# In _build_options:
if self.external_todos_enabled:
    # Full replacement mode
    options.disallowed_tools = (options.disallowed_tools or []) + ["TodoWrite"]
    options.mcp_servers["gluon_todos"] = gluon_todo_server
else:
    # Mirror mode — just observe
    hooks["PostToolUse"].append(HookMatcher(
        matcher="TodoWrite",
        hooks=[mirror_todos_hook]
    ))
```

| Pros | Cons |
|------|------|
| Graceful degradation — mirror mode always works | Two code paths to maintain |
| Full replacement only when needed | Feature flag complexity |
| Progressive rollout possible | |

**Complexity**: Medium-High
**Risk**: Low (mirror mode is safe fallback)

---

## 5. Data Model Considerations

### 5.1 Todo Item Schema (matches Claude's built-in)

```python
class TodoItem(TypedDict):
    content: str        # Task description (imperative: "Fix the bug")
    status: str         # "pending" | "in_progress" | "completed"
    activeForm: str     # Present continuous: "Fixing the bug"
```

### 5.2 Proposed Gluon Store Schema

```sql
CREATE TABLE run_todos (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES execution_runs(id),
    content     TEXT NOT NULL,
    active_form TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    ordinal     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE(run_id, content)
);

CREATE INDEX idx_run_todos_run_id ON run_todos(run_id);
```

### 5.3 Store Methods

```python
def update_run_todos(self, run_id: str, todos: list[dict]) -> None:
    """Replace all todos for a run (matches TodoWrite's replace-all semantics)."""

def get_run_todos(self, run_id: str) -> list[dict]:
    """Get current todos for a run."""
```

---

## 6. Key Technical Findings

### 6.1 `updatedInput` in `can_use_tool` is Reliable

The SDK source (`_internal/query.py:242-283`) confirms that when
`PermissionResultAllow.updated_input` is set, it is sent back to the CLI
as the tool's input, replacing the original. This is the same mechanism
Gluon uses for `AskUserQuestion` answer injection.

### 6.2 `updatedMCPToolOutput` in PostToolUse Replaces Output

The `PostToolUseHookSpecificOutput.updatedMCPToolOutput` field can replace
the tool result that Claude sees. This could be used to inject Gluon state
into the response (e.g., showing external todo count).

### 6.3 `disallowed_tools` is Already Wired

Gluon already supports `disallowed_tools` in `agent.py:548-549` and
`runner.py:680-684`, sourced from the settings store. Blocking `TodoWrite`
requires only a settings change — no code modification.

### 6.4 SDK MCP Servers Run In-Process

`create_sdk_mcp_server` creates tools that execute in the same Python process
as Gluon. This means the tool handler has direct access to the `GluonStore`,
the run context, and can emit WebSocket notifications synchronously.

### 6.5 MCP Tool Naming Convention

SDK MCP tools are exposed to Claude as `mcp__<server_name>__<tool_name>`.
A tool named `todo_write` on server `gluon` becomes `mcp__gluon__todo_write`.
The system prompt would need to instruct Claude to use this tool for task
tracking. Alternatively, keeping the system prompt reference to `TodoWrite`
and using the `can_use_tool` callback to redirect may be cleaner.

### 6.6 Hook Matcher Supports Tool-Specific Filtering

`HookMatcher(matcher="TodoWrite", hooks=[...])` will only fire for
`TodoWrite` tool calls. No need to filter inside the callback.

### 6.7 PreToolUse Cannot Provide a Custom Result

PreToolUse hooks can deny or modify input, but they **cannot** short-circuit
execution and return a custom result. Only the `PostToolUse` hook's
`updatedMCPToolOutput` can modify what Claude sees as the result. This means
a "pure intercept without execution" approach would require the MCP
replacement strategy (Approach C), not just hooks.

---

## 7. Recommendation

**Start with Approach A (PostToolUse mirror)** for immediate dashboard visibility,
then evaluate whether full replacement (Approach C) is needed based on whether
the orchestrator needs to inject/manage todos externally.

### Phase 1: Mirror Mode (Approach A)
- Add a `TodoWrite`-specific PostToolUse hook in `agent_hooks.py`
- Store todos in `run_todos` table
- Stream updates via WebSocket to dashboard
- **No impact on Claude's behaviour** — purely observational

### Phase 2: Full Replacement (Approach C) — if needed
- Create `gluon_todo_server` using `create_sdk_mcp_server`
- Add `TodoWrite` to `disallowed_tools`
- Update system prompt to reference the replacement tool
- Enable two-way state management (orchestrator can set initial todos)

---

## 8. Open Questions

1. **Should the orchestrator be able to pre-populate todos?** If yes, Approach A
   alone is insufficient — need Approach B or C.

2. **Should todos survive session resume?** The mirror store would persist
   across resumes, but Claude's internal state resets. Need to decide if
   Gluon should re-inject previous todos on resume.

3. **Multiple concurrent agents on the same run** — how to handle todo
   conflicts if agent teams are enabled?

4. **Schema compatibility** — should Gluon extend the todo schema (e.g., add
   `priority`, `assigned_agent`, `parent_task_id`) or keep it minimal to
   match Claude's built-in format?

5. **MCP config conflicts** — when a project has its own `.mcp.json`, Gluon
   currently sets `allowed_tools=None` (line 467 of `agent.py`). Need to
   ensure the `gluon` MCP server is merged correctly with project configs.

---

## Appendix: SDK Source Paths

| File | Purpose |
|------|---------|
| `/home/gluon/.local/lib/python3.12/site-packages/claude_agent_sdk/types.py` | All type definitions (hooks, permissions, options) |
| `/home/gluon/.local/lib/python3.12/site-packages/claude_agent_sdk/__init__.py` | `create_sdk_mcp_server`, `@tool` decorator, exports |
| `/home/gluon/.local/lib/python3.12/site-packages/claude_agent_sdk/_internal/query.py` | Hook processing, `can_use_tool` handler, control protocol |
| `/home/gluon/.local/lib/python3.12/site-packages/claude_agent_sdk/_internal/message_parser.py` | ToolUseBlock/ToolResultBlock parsing |
| `/tmp/gluon-worktrees/wt-70aea07b/src/gluon/agent.py` | GluonAgent — `_can_use_tool`, `_build_options` |
| `/tmp/gluon-worktrees/wt-70aea07b/src/gluon/agent_hooks.py` | `build_hooks()`, PreToolUse/PostToolUse logging |
