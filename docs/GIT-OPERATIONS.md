# Git Operations

Gluon provides comprehensive git integration including automatic synchronization, worktree isolation for parallel tasks, PR integration, and advanced operations like rebase and conflict resolution.

## Automatic Synchronization

Gluon automatically keeps projects synchronized with remote Git repositories to prevent conflicts when multiple Claude instances work on the same codebase.

### Pre-task Sync

Before running a task, Gluon will:
1. Auto-commit any uncommitted changes
2. Fetch from remote
3. Fast-forward if behind (fails if diverged)

```mermaid
flowchart TD
    A[pre_task_sync] --> B{Is git repo?}
    B -->|No| Z[Return success - skip]
    B -->|Yes| C{Has uncommitted?}

    C -->|Yes| D[git add -A]
    D --> E[git commit -m 'gluon: auto-commit']
    C -->|No| F[git fetch origin]
    E --> F

    F --> G{Behind remote?}
    G -->|No| Z2[Return success]
    G -->|Yes| H{Diverged?}
    H -->|Yes| X[FAIL: Manual merge needed]
    H -->|No| I[git pull --ff-only]
    I --> Z2
```

### Post-task Sync

After successful task completion:
1. Stage and commit all changes
2. Push to remote

```mermaid
flowchart TD
    A[post_task_sync] --> B{Is git repo?}
    B -->|No| Z[Return success - skip]
    B -->|Yes| C{Has changes?}

    C -->|No| Z
    C -->|Yes| D[git add -A]
    D --> E[git commit -m message]
    E --> F{Has remote?}

    F -->|No| Z2[Return success - local only]
    F -->|Yes| G[git push]

    G --> H{Push success?}
    H -->|Yes| Z3[Return success]
    H -->|No| I[git pull --rebase]
    I --> J[git push retry]
    J --> K{Success?}
    K -->|Yes| Z3
    K -->|No| X[FAIL: Push rejected]
```

### Background Sync

Bot transports (Telegram, Discord) periodically fetch all projects (every 5 minutes) to maintain status awareness.

```mermaid
flowchart LR
    subgraph "Bot Process"
        LOOP[Background Loop]
        LOOP -->|every 5 min| FETCH
    end

    subgraph "For Each Project"
        FETCH[git fetch] --> STATUS[Update GitStatus]
        STATUS --> DB[(SQLite)]
    end

    STATUS --> WARN{Diverged?}
    WARN -->|Yes| LOG[Log Warning]
```

### Configuration

```bash
# Environment variables
GLUON_GIT_ENABLED=true              # Enable/disable git sync (default: true)
GLUON_GIT_SYNC_INTERVAL=300         # Background fetch interval in seconds
GLUON_GIT_AUTO_COMMIT=true          # Auto-commit before/after tasks
GLUON_GIT_AUTO_PUSH=true            # Auto-push after tasks
GLUON_GIT_COMMIT_PREFIX="gluon:"    # Prefix for auto-commit messages
```

### Error Handling

| Scenario | Behavior |
|----------|----------|
| Not a git repo | Skip git operations, proceed normally |
| No remote configured | Commit locally only |
| Diverged branches | **FAIL** pre-task with error |
| Push rejected | Pull with rebase, retry once |
| Network error (fetch) | **WARN** but proceed |

## Git Worktree Isolation

Tasks can run in isolated git worktrees, creating a dedicated branch without affecting the main codebase.

### How It Works

```mermaid
sequenceDiagram
    participant User
    participant API
    participant Runner
    participant WorktreeManager
    participant GitManager
    participant Agent

    User->>API: Create run (use_worktree=true)
    API->>Runner: submit(project_id, prompt, use_worktree=true)

    Runner->>WorktreeManager: create(run_id)
    WorktreeManager->>WorktreeManager: Create branch gluon-task/{run_id}
    WorktreeManager->>WorktreeManager: git worktree add /tmp/gluon-worktrees/wt-{run_id}
    WorktreeManager-->>Runner: worktree_path

    Runner->>Agent: execute(worktree_path, prompt)

    loop Task Execution
        Agent-->>Runner: AgentMessage
        Runner->>Runner: Write logs
    end

    Agent-->>Runner: AgentResult
    Runner->>GitManager: Commit changes in worktree
    Runner->>GitManager: Push branch to remote
    Runner->>GitManager: Create PR via gh CLI

    GitManager-->>Runner: PR URL, PR number

    Runner->>Runner: Update run with PR info
    Runner-->>API: Run complete with PR
```

### Directory Structure

```
/tmp/gluon-worktrees/
└── wt-{run_id}/           # Isolated worktree
    ├── .gluon-images/     # Attached images copied here
    ├── .env.local         # Copied from parent repo
    └── ... project files
```

### Benefits

- Tasks don't affect the main branch until merged
- Multiple tasks can run in parallel on different branches
- Easy PR review and selective merging
- Rollback by simply not merging

## PR Integration

Gluon integrates with GitHub for PR creation, status tracking, and merging.

### Requirements

- GitHub CLI (`gh`) must be installed and authenticated
- Repository must have a GitHub remote

### PR Workflow

```mermaid
flowchart TD
    subgraph "Task Completion"
        RUN[Run completes in worktree]
        PUSH[Push branch to remote]
        CREATE[Create PR via gh CLI]
    end

    subgraph "Review Phase"
        PR[PR Open on GitHub]
        STATUS[Poll PR status]
        MERGE_CHECK{Mergeable?}
    end

    subgraph "Actions"
        MERGE[Merge locally + push]
        RESOLVE[AI resolves conflicts]
        CLOSE[PR auto-closed]
    end

    RUN --> PUSH --> CREATE --> PR
    PR --> STATUS --> MERGE_CHECK
    MERGE_CHECK -->|Yes| MERGE --> CLOSE
    MERGE_CHECK -->|Conflicts| RESOLVE --> PUSH
```

### Dashboard PR Features

| Feature | Description |
|---------|-------------|
| **PR Badge** | Shows PR number, status (open/merged/closed), conflict indicator |
| **Create PR** | Button to manually create PR for worktree runs |
| **Merge** | Merge branch locally and push (GitHub auto-closes PR) |
| **Resolve Conflicts** | One-click to resume task with conflict resolution prompt |

### Conflict Resolution

When a PR has merge conflicts, the dashboard shows a "Resolve" button that:

1. Pre-fills a prompt instructing Claude to rebase and resolve conflicts
2. Resumes the session in the existing worktree
3. Claude rebases onto main and intelligently merges changes
4. Force-pushes the resolved branch

## Advanced Git Operations

Gluon provides a comprehensive API for advanced git operations.

### Rebase Operations

```mermaid
sequenceDiagram
    participant UI as Dashboard
    participant API as Gluon API
    participant Git as Git Manager
    participant Repo as Repository

    UI->>API: POST /api/projects/{id}/rebase
    API->>Git: rebase_branch(path, "main")
    Git->>Repo: git rebase main

    alt Rebase Success
        Repo-->>Git: Success
        Git-->>API: {status: "success"}
        API-->>UI: Rebase complete
    else Conflicts Detected
        Repo-->>Git: Conflicts
        Git-->>API: {status: "conflict", files: [...]}
        API-->>UI: Show conflict files

        loop For Each Conflict
            UI->>API: GET /api/projects/{id}/conflicts/{file}
            API-->>UI: 3-way diff (ours/theirs/base)

            UI->>API: POST /api/projects/{id}/conflicts/resolve
            Note over API: User chooses resolution
        end

        UI->>API: POST /api/projects/{id}/rebase/continue
        API->>Git: rebase_continue()
        Git->>Repo: git rebase --continue
    end
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/projects/{id}/rebase` | POST | Start rebase onto target branch |
| `/api/projects/{id}/rebase/continue` | POST | Continue after resolving conflicts |
| `/api/projects/{id}/rebase/abort` | POST | Abort rebase and restore state |
| `/api/projects/{id}/rebase/skip` | POST | Skip current commit during rebase |
| `/api/projects/{id}/conflicts` | GET | List files with conflicts |
| `/api/projects/{id}/conflicts/{file}` | GET | Get 3-way diff for conflict |
| `/api/projects/{id}/conflicts/resolve` | POST | Mark conflict as resolved |
| `/api/projects/{id}/force-push-check` | GET | Check if force push is needed |
| `/api/projects/{id}/force-push` | POST | Force push with lease (safe) |

### Branch Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/projects/{id}/branches` | GET | List local and remote branches |
| `/api/projects/{id}/branches/rename` | POST | Rename a branch |
| `/api/projects/{id}/branches/change-base` | POST | Change base branch for PR |
| `/api/projects/{id}/branches/{name}` | DELETE | Delete a branch |

### Conflict Detection

```mermaid
flowchart TD
    subgraph "Detection"
        PR[PR Open on GitHub]
        POLL[Poll PR Status]
        CHECK{mergeable?}
    end

    subgraph "States"
        CLEAN[MERGEABLE<br/>✅ Ready to merge]
        CONFLICT[CONFLICTING<br/>⚠️ Has conflicts]
        UNKNOWN[UNKNOWN<br/>🔄 Checking...]
    end

    subgraph "Resolution"
        RESOLVE_BTN[Resolve Button]
        PROMPT[Pre-filled prompt:<br/>'Rebase and resolve conflicts']
        AI[Claude resolves conflicts]
        FORCE[Force push branch]
    end

    PR --> POLL --> CHECK
    CHECK -->|yes| CLEAN
    CHECK -->|no| CONFLICT
    CHECK -->|null| UNKNOWN

    CONFLICT --> RESOLVE_BTN
    RESOLVE_BTN --> PROMPT --> AI --> FORCE
    FORCE --> PR

    style CLEAN fill:#c8e6c9
    style CONFLICT fill:#ffcdd2
    style UNKNOWN fill:#fff9c4
```

### Force Push Safety

Force push uses `--force-with-lease` for safety:

```mermaid
flowchart LR
    CHECK[Check if force push needed]
    CHECK --> AHEAD{Ahead of remote?}
    AHEAD -->|No| NORMAL[Normal push]
    AHEAD -->|Yes| DIVERGED{Diverged?}
    DIVERGED -->|No| NORMAL
    DIVERGED -->|Yes| FORCE[Force push with lease]
    FORCE --> VERIFY{Remote unchanged?}
    VERIFY -->|Yes| SUCCESS[Push succeeds]
    VERIFY -->|No| FAIL[Push rejected<br/>Remote was updated]

    style SUCCESS fill:#c8e6c9
    style FAIL fill:#ffcdd2
```

## CLI Commands

```bash
gluon git status [project]    # Show git status for project(s)
gluon git fetch [project]     # Fetch latest from remote
gluon git sync <project>      # Commit, fetch, fast-forward
gluon git push <project>      # Commit and push changes
```
