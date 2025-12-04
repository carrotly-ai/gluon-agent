# Gap Analysis: Gluon-Agent vs Vibe-Kanban

**Date**: 2025-12-04
**Purpose**: Identify feature gaps where vibe-kanban has capabilities that gluon-agent lacks

---

## Executive Summary

After thorough analysis of both codebases, I've identified **23 significant feature gaps** across 6 major categories. The most critical gaps are in **attachment handling**, **real-time updates**, **multi-agent support**, and **collaboration features**.

---

## 1. Attachment & Image Handling (CRITICAL GAP)

### Vibe-Kanban Has:
- **Image upload API** with three endpoints:
  - `/api/images/upload` - General image upload
  - `/api/images/task/{taskId}/upload` - Task-specific upload
  - `/api/task-attempts/{attemptId}/images/upload` - Attempt-specific upload
- **Image metadata tracking** (`ImageResponse` type):
  - `id`, `file_path`, `original_name`, `mime_type`, `size_bytes`, `hash`
- **Image-to-markdown conversion**: Converts uploads to markdown syntax
- **Automatic copy to worktree**: Images are immediately copied to worktree after upload for agent visibility
- **Image retrieval by task**: Get all images for a task
- **Image deletion**: Remove uploaded images
- **WYSIWYG editor** with image transformer for rich text editing with inline images

### Gluon-Agent Lacks:
- No image/file upload capability
- No attachment storage mechanism
- No way to provide visual context to AI agents
- No image metadata tracking
- No file management API

### Impact: **HIGH**
Users cannot attach screenshots, designs, or reference images to task prompts.

---

## 2. Real-Time Updates & WebSocket Streaming (SIGNIFICANT GAP)

### Vibe-Kanban Has:
- **RFC6902 JSON Patch WebSocket streaming** (`useJsonPatchWsStream` hook):
  - Incremental state updates via JSON patches
  - Deep cloning before mutation
  - Patch deduplication
  - Exponential backoff reconnection (1s → 2s → 4s → 8s cap)
  - Terminal state detection (`finished: true`)
- **Raw log streaming** (`useLogStream` hook):
  - Separate STDOUT/STDERR extraction
  - Log accumulation
  - 6-attempt retry limit
- **Process-level streaming** (`streamJsonPatchEntries`):
  - Subscriber pattern with immediate state push
  - Last-write-wins deduplication
  - Connection state tracking
- **Diff streaming** for live code changes
- **Task streaming** for real-time kanban updates

### Gluon-Agent Has:
- Basic WebSocket with simple message types
- Run created/updated events
- Log line streaming (basic)
- No patch-based updates
- No sophisticated reconnection logic

### Gap:
- No JSON Patch protocol for efficient incremental updates
- No sophisticated retry/reconnection with exponential backoff
- No diff streaming for code changes
- Limited real-time collaboration features

### Impact: **MEDIUM-HIGH**
Less efficient updates, potential data inconsistencies, limited live collaboration.

---

## 3. Multi-Agent & Executor Support (CRITICAL GAP)

### Vibe-Kanban Has:
- **9 supported coding agents**:
  - `CLAUDE_CODE` - Anthropic's Claude Code
  - `AMP` - Amp agent
  - `GEMINI` - Google's Gemini CLI
  - `CODEX` - OpenAI Codex
  - `OPENCODE` - OpenCode agent
  - `CURSOR_AGENT` - Cursor Agent
  - `QWEN_CODE` - Qwen Code
  - `COPILOT` - GitHub Copilot
  - `DROID` - Droid agent
- **Pluggable executor architecture** with per-agent configuration
- **Executor profiles** with variant support (e.g., `PLAN`, `ROUTER` modes)
- **Agent capability detection** (`SESSION_FORK`, `SETUP_HELPER`)
- **Per-agent MCP configuration**
- **Agent availability checking**
- **Agent setup wizard** with guided installation

### Gluon-Agent Has:
- Claude Code only (hardcoded)
- No multi-agent support
- No pluggable executor system
- No variant/profile configuration

### Impact: **HIGH**
Users locked into single AI agent, cannot leverage best agent for each task.

---

## 4. Task Management & Organization (MODERATE GAP)

### Vibe-Kanban Has:
- **Tags/Labels system**:
  - Global tags with CRUD API
  - Tag search with autocomplete
  - Tag-to-task assignment
- **Task sharing & collaboration**:
  - Share tasks to organization
  - Assignee management
  - Remote project linking
- **Scratch/Draft system**:
  - Draft task saving
  - Draft follow-up saving
  - Real-time draft streaming
- **Task relationships**:
  - Parent task attempts
  - Child tasks
  - Task hierarchies
- **Task status**: `todo`, `inprogress`, `inreview`, `done`, `cancelled`

### Gluon-Agent Has:
- Runs (not "tasks")
- Basic status: `pending`, `running`, `completed`, `failed`, `cancelled`
- No tagging system
- No task sharing
- No draft saving
- No task hierarchies

### Gap:
- No label/tag organization
- No task categorization beyond status
- No collaborative task assignment
- No draft auto-save

### Impact: **MEDIUM**
Limited organization for large task volumes.

---

## 5. Git Operations & Branch Management (MODERATE GAP)

### Vibe-Kanban Has:
- **Advanced rebase support**:
  - Rebase task attempts
  - Conflict detection (`is_rebase_in_progress`)
  - Conflict abort
  - Conflicted files listing
- **Branch renaming**
- **Target branch changing**
- **Force push** with confirmation
- **Commit comparison** (ahead/behind target)
- **Merge operations** with conflict awareness
- **Git operation error types**:
  - `merge_conflicts`
  - `rebase_in_progress`
  - `force_push_required`
- **Branch status** with detailed metrics:
  - `commits_behind`, `commits_ahead`
  - `uncommitted_count`, `untracked_count`
  - `remote_commits_behind`, `remote_commits_ahead`

### Gluon-Agent Has:
- Worktree creation
- Basic branch management
- Commit listing
- File change listing
- PR creation
- Merge (basic)

### Gap:
- No rebase support
- No conflict resolution UI
- No branch renaming
- No target branch changing
- No force push controls
- Limited branch status details

### Impact: **MEDIUM**
Less flexible git workflow, users must use CLI for advanced operations.

---

## 6. Organization & Team Collaboration (CRITICAL GAP)

### Vibe-Kanban Has:
- **Organization management**:
  - Create organizations
  - Organization roles (`ADMIN`, `MEMBER`)
  - Member management (add, remove, update role)
- **Invitation system**:
  - Email-based invitations
  - Invitation status tracking (`PENDING`, `ACCEPTED`, `DECLINED`, `EXPIRED`)
  - Revoke invitations
- **Remote projects**:
  - Link local projects to remote
  - Unlink projects
  - Remote project members view
- **GitHub OAuth integration**:
  - OAuth handoff flow
  - Token management
  - Profile linking
- **User profiles**:
  - First name, last name, username
  - Avatar URL
  - Email

### Gluon-Agent Has:
- Single-user mode
- No organizations
- No team features
- No authentication (beyond bot tokens)

### Impact: **HIGH for teams**
Cannot be used in collaborative team environments.

---

## 7. Approval & Human-in-the-Loop (NEW FEATURE GAP)

### Vibe-Kanban Has:
- **Tool approval system**:
  - Pending approval state
  - Approval timeout tracking
  - Approve/deny responses
  - Tool-specific approvals
- **Approval status states**: `pending`, `approved`, `denied`, `timed_out`
- **Request details**: tool name, input, call ID

### Gluon-Agent Has:
- No approval system
- Relies on Claude Code's built-in approval

### Impact: **MEDIUM**
Less granular control over agent actions in web UI.

---

## 8. Configuration & Settings (MODERATE GAP)

### Vibe-Kanban Has:
- **Rich configuration system**:
  - Theme mode (LIGHT, DARK, SYSTEM)
  - Notification settings (sound enabled, push enabled, sound file selection)
  - Editor configuration (VS Code, Cursor, Windsurf, IntelliJ, Zed, Xcode, Custom)
  - Remote SSH configuration
  - Git branch prefix customization
  - Analytics opt-in/out
  - Language selection (EN, JA, ES, KO, BROWSER)
  - Showcase/feature discovery state
- **Per-executor MCP config management**
- **Profiles system** for configuration presets

### Gluon-Agent Has:
- Simple key-value settings
- Auto-create PR toggle
- No theme management in UI
- No notification settings
- No editor integration
- No language selection

### Impact: **LOW-MEDIUM**
Less customizable user experience.

---

## 9. Queue System (MODERATE GAP)

### Vibe-Kanban Has:
- **Follow-up message queuing**:
  - Queue messages during execution
  - Cancel queued messages
  - Queue status checking
  - Auto-execution when current task completes
- **Variant support** for different execution modes

### Gluon-Agent Has:
- No queue system
- Resume requires manual trigger

### Impact: **MEDIUM**
Cannot pre-queue follow-up tasks.

---

## 10. UI/UX Features (VARIOUS GAPS)

### Vibe-Kanban Has:
- **WYSIWYG editor** with markdown support
- **Keyboard shortcuts system** with customization
- **i18n/Internationalization** (4 languages)
- **Sound notifications** with 7 sound options
- **Feature showcases** (onboarding, release notes)
- **VS Code integration** (remote SSH URLs)
- **Local storage persistence** for UI state

### Gluon-Agent Has:
- Basic text editor
- No keyboard shortcuts
- No i18n
- No notifications
- No onboarding
- No editor integration
- Basic localStorage

### Impact: **LOW-MEDIUM**
Less polished user experience.

---

## Gap Priority Matrix

| Gap Area | Severity | Effort | Priority |
|----------|----------|--------|----------|
| Attachment/Image Handling | HIGH | MEDIUM | **P1** |
| Multi-Agent Support | HIGH | HIGH | **P1** |
| Organization/Teams | HIGH | HIGH | **P2** |
| Real-Time Streaming | MEDIUM-HIGH | MEDIUM | **P2** |
| Tags/Labels | MEDIUM | LOW | **P3** |
| Git Advanced Ops | MEDIUM | MEDIUM | **P3** |
| Approval System | MEDIUM | MEDIUM | **P3** |
| Queue System | MEDIUM | LOW | **P3** |
| Configuration/Settings | LOW-MEDIUM | LOW | **P4** |
| UI/UX Enhancements | LOW-MEDIUM | MEDIUM | **P4** |

---

## Recommended Action Items

### Immediate (P1)
1. **Implement image upload API** - Critical for visual context
2. **Design multi-agent architecture** - Even if not implementing all agents

### Short-term (P2)
3. **Enhance WebSocket with JSON Patch** - Better real-time updates
4. **Add tagging system** - Better organization
5. **Research organization/team support** - For future collaborative use

### Medium-term (P3)
6. **Add approval system** - Granular agent control
7. **Implement follow-up queue** - Better workflow
8. **Advanced git operations** - Rebase, branch rename

### Long-term (P4)
9. **Rich configuration** - Theme, notifications, i18n
10. **Editor integration** - VS Code/Cursor deep links
