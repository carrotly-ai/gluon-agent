# Gluon Agent

AI orchestrator for managing multiple Claude Code agents across projects. Features a web dashboard with Kanban board, git worktree isolation for parallel tasks, PR integration, real-time WebSocket updates, and multi-platform bot interfaces (Telegram, Discord).

## Features

- **Web Dashboard** - React-based Kanban board with real-time updates
- **Git Worktree Isolation** - Run tasks in isolated branches without affecting main
- **PR Integration** - Create PRs, detect conflicts, merge directly from dashboard
- **Image Attachments** - Attach screenshots/diagrams to tasks for AI context
- **Usage Tracking** - Monitor costs, tokens, and usage per project
- **Multi-Platform Bots** - Telegram and Discord interfaces with natural language
- **Session Resume** - Continue Claude sessions with follow-up prompts

## Installation

```bash
uv venv
uv pip install -e '.[dev]'
```

## Quick Start

```bash
# Register a project
gluon project add myapp /path/to/myapp

# Run a task
gluon run myapp 'Fix the authentication bug'

# Resume session
gluon resume myapp 'Also add logging'
```

## Commands

### Project Management

- `gluon project add <name> <path>` - Register project
- `gluon project list` - List projects
- `gluon project remove <name>` - Remove project

### Workspace Management

- `gluon workspace add <name> <path>` - Register workspace and auto-discover projects
- `gluon workspace list` - List workspaces
- `gluon workspace scan [name]` - Scan workspace(s) for new projects
- `gluon workspace projects <name>` - List projects in workspace
- `gluon workspace remove <name>` - Remove workspace

### Task Execution

- `gluon run <project> <prompt>` - Execute task (foreground)
- `gluon run <project> <prompt> --background` - Execute task in background
- `gluon resume <project> [prompt]` - Resume last session
- `gluon sessions [project]` - List sessions
- `gluon status` - Show status

### Background Runs

- `gluon runs` - List all background execution runs
- `gluon runs --active` - Show only active runs
- `gluon logs <run_id>` - View logs for a run
- `gluon logs <run_id> --follow` - Tail logs in real-time
- `gluon cancel <run_id>` - Cancel a running task

### Git Management

- `gluon git status [project]` - Show git status for project(s)
- `gluon git fetch [project]` - Fetch latest from remote
- `gluon git sync <project>` - Commit, fetch, and fast-forward
- `gluon git push <project>` - Commit and push changes

### Bot Interfaces

- `gluon bot` - Run Telegram bot interface
- `gluon discord` - Run Discord bot interface
- `gluon serve --telegram --discord` - Run multiple transports concurrently

## Web Dashboard

Gluon includes a full-featured web dashboard built with FastAPI + React, providing a Kanban board view of all tasks with real-time WebSocket updates.

### Quick Start

```bash
# Start the web server (port 45866)
gluon web

# Or run in development mode
cd web-ui && npm run dev  # Terminal 1: Vite dev server
uvicorn gluon.web.api:app --reload --port 45866  # Terminal 2: FastAPI
```

Open http://localhost:45866 to access the dashboard.

### Dashboard Features

- **Kanban Board** - Drag-and-drop task management across columns (Queued, Running, Review, Completed, Failed)
- **Real-time Updates** - WebSocket-powered live status updates
- **Project Filtering** - Filter tasks by project or workspace
- **Run Details Modal** - View logs, commits, file changes, PR status
- **Image Attachments** - Upload screenshots for AI context (paste with ⌘V)
- **PR Integration** - Create PRs, view merge status, resolve conflicts
- **Usage Dashboard** - Track costs and token usage by project/day

### Web Dashboard Architecture

```mermaid
graph TB
    subgraph "Web Dashboard"
        REACT[React SPA<br/>Kanban Board]
        WS_CLIENT[WebSocket Client<br/>Real-time Updates]
    end

    subgraph "FastAPI Backend"
        API[REST API<br/>/api/*]
        WS_SERVER[WebSocket Server<br/>/api/ws]
        POLLING[Background Polling<br/>Status Updates]
    end

    subgraph "Core Services"
        RUNNER[TaskRunner]
        GIT[GitManager]
        IMG[ImageStorage]
        STORE[(SQLite)]
    end

    REACT -->|fetch/POST| API
    WS_CLIENT <-->|subscribe| WS_SERVER

    API --> RUNNER
    API --> GIT
    API --> IMG
    API --> STORE

    POLLING --> STORE
    POLLING --> WS_SERVER

    WS_SERVER -->|broadcast| WS_CLIENT
```

## Architecture

### High-Level Overview

```mermaid
graph TB
    subgraph Interfaces
        WEB[Web Dashboard]
        CLI[CLI - gluon]
        TG[Telegram Bot]
        DC[Discord Bot]
    end

    subgraph Core
        BOTCORE[GluonBotCore]
        ORCH[Orchestrator]
        RUNNER[TaskRunner]
        STORE[GluonStore]
        GIT[GitManager]
        WORKTREE[WorktreeManager]
        CHAT[ChatAgent]
        IMG[ImageStorage]
    end

    subgraph Execution
        AGENT[GluonAgent]
        SDK[Claude Agent SDK]
        CLAUDE[Claude CLI]
    end

    subgraph Storage
        DB[(SQLite DB)]
        LOGS[Log Files]
        IMAGES[Image Store]
    end

    subgraph External
        BEDROCK[AWS Bedrock]
        REMOTE[Git Remote]
        GITHUB[GitHub API]
        WEB_EXT[Web/URLs]
    end

    WEB --> RUNNER
    WEB --> GIT
    WEB --> IMG
    CLI --> ORCH
    CLI --> RUNNER
    CLI --> GIT
    TG --> BOTCORE
    DC --> BOTCORE
    BOTCORE --> ORCH
    BOTCORE --> RUNNER
    BOTCORE --> CHAT

    CHAT --> ORCH
    CHAT --> SDK
    CHAT -.->|WebSearch/Fetch| WEB_EXT

    ORCH --> STORE
    ORCH --> AGENT
    ORCH --> GIT
    RUNNER --> STORE
    RUNNER --> AGENT
    RUNNER --> WORKTREE
    RUNNER --> IMG

    GIT --> STORE
    GIT -.->|fetch/push| REMOTE
    GIT -.->|PR/merge| GITHUB

    WORKTREE --> GIT

    AGENT --> SDK
    SDK --> CLAUDE

    STORE --> DB
    RUNNER --> LOGS
    IMG --> IMAGES

    CLAUDE -.->|spawns| BEDROCK
```

### Data Model

```mermaid
erDiagram
    Workspace ||--o{ Project : contains
    Project ||--o{ Session : has
    Project ||--o{ ExecutionRun : has
    Project ||--o{ ChannelMapping : "mapped by"
    Session ||--o| ExecutionRun : "created by"

    Workspace {
        string id PK
        string name UK
        string path
        int scan_depth
        bool auto_discover
    }

    Project {
        string id PK
        string name UK
        string path
        string workspace_id FK
        bool git_is_repo
        string git_branch
        string git_remote
        int git_commits_ahead
        int git_commits_behind
    }

    Session {
        string id PK
        string project_id FK
        string claude_session_id
        string status
        float total_cost_usd
        int total_turns
    }

    ExecutionRun {
        string id PK
        string project_id FK
        string session_id FK
        int pid
        string status
        string prompt
        string initiator
        string log_path
        bool use_worktree
        string branch_name
        string worktree_path
        string pr_url
        string pr_status
        float cost_usd
        int input_tokens
        int output_tokens
    }

    ImageAttachment {
        string id PK
        string file_path
        string original_name
        string mime_type
        int size_bytes
        string hash
    }

    ExecutionRun ||--o{ ImageAttachment : has

    ChannelMapping {
        string id PK
        string transport
        string channel_id UK
        string project_id FK
        string project_name
    }
```

### CLI Execution Flow

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant Orchestrator
    participant GluonAgent
    participant ClaudeSDK
    participant ClaudeCLI

    User->>CLI: gluon run myapp "Fix bug"
    CLI->>Orchestrator: execute(project, prompt)
    Orchestrator->>Orchestrator: Get/Create Session
    Orchestrator->>GluonAgent: execute(working_dir, prompt)
    GluonAgent->>ClaudeSDK: ClaudeSDKClient(options)
    ClaudeSDK->>ClaudeCLI: spawn subprocess
    GluonAgent->>ClaudeSDK: client.query(prompt)

    loop Streaming Response
        ClaudeCLI-->>ClaudeSDK: messages
        ClaudeSDK-->>GluonAgent: AssistantMessage/ResultMessage
        GluonAgent-->>Orchestrator: AgentMessage
        Orchestrator-->>CLI: AgentMessage
        CLI-->>User: Display output
    end

    ClaudeSDK-->>GluonAgent: ResultMessage (final)
    GluonAgent-->>Orchestrator: AgentResult
    Orchestrator->>Orchestrator: Update Session
    Orchestrator-->>CLI: AgentResult
    CLI-->>User: Display summary
```

### Background Execution Flow

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant TaskRunner
    participant Store
    participant GluonAgent
    participant LogFiles

    User->>CLI: gluon run myapp "Task" --background
    CLI->>Store: create_run(project_id, prompt)
    Store-->>CLI: ExecutionRun (PENDING)
    CLI-->>User: "Task submitted: abc123"

    CLI->>TaskRunner: submit(project_id, prompt)
    TaskRunner->>TaskRunner: asyncio.create_task()

    Note over TaskRunner: Background execution starts

    TaskRunner->>Store: update_run(RUNNING)
    TaskRunner->>GluonAgent: execute(working_dir, prompt)

    loop Execution
        GluonAgent-->>TaskRunner: AgentMessage
        TaskRunner->>LogFiles: Write to stdout.log
        TaskRunner->>LogFiles: Write to messages.jsonl
    end

    GluonAgent-->>TaskRunner: AgentResult
    TaskRunner->>Store: update_run(COMPLETED/FAILED)

    Note over User: Later...
    User->>CLI: gluon logs abc123
    CLI->>TaskRunner: get_logs(run_id)
    TaskRunner->>LogFiles: Read logs
    LogFiles-->>CLI: Log content
    CLI-->>User: Display logs
```

### Git Worktree Isolation Flow

Tasks can run in isolated git worktrees, creating a dedicated branch without affecting the main codebase.

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

### Worktree Directory Structure

```
/tmp/gluon-worktrees/
└── wt-{run_id}/           # Isolated worktree
    ├── .gluon-images/     # Attached images copied here
    ├── .env.local         # Copied from parent repo
    └── ... project files
```

**Benefits:**
- Tasks don't affect the main branch until merged
- Multiple tasks can run in parallel on different branches
- Easy PR review and selective merging
- Rollback by simply not merging

### Telegram Bot Flow

```mermaid
sequenceDiagram
    participant User
    participant Telegram
    participant GluonBot
    participant Store
    participant Orchestrator
    participant GluonAgent

    User->>Telegram: /run myapp "Fix bug"
    Telegram->>GluonBot: Update (command)

    GluonBot->>GluonBot: Check authorization
    GluonBot->>Store: list_active_runs()
    Store-->>GluonBot: active_runs

    alt Concurrency limit reached
        GluonBot-->>Telegram: "Max concurrent runs reached"
        Telegram-->>User: Error message
    else Under limit
        GluonBot->>Store: create_run(project_id, prompt, initiator)
        Store-->>GluonBot: ExecutionRun
        GluonBot-->>Telegram: "Task started: abc123"
        Telegram-->>User: Confirmation

        GluonBot->>GluonBot: asyncio.create_task(_execute_task_with_runner)

        loop Execution with Semaphore
            GluonAgent-->>GluonBot: AgentMessage
            GluonBot-->>Telegram: Progress update
            Telegram-->>User: Status message
        end

        GluonAgent-->>GluonBot: AgentResult
        GluonBot->>Store: update_run(status)
        GluonBot-->>Telegram: "Complete" or "Failed"
        Telegram-->>User: Final result
    end
```

### Bot Startup Recovery

```mermaid
flowchart TD
    A[Transport Starts] --> B[recover_stale_runs]
    B --> C{Any active runs<br/>from this transport?}
    C -->|Yes| D[Mark as FAILED<br/>'Bot restarted']
    C -->|No| E[Continue]
    D --> E
    E --> F[Start GitManager<br/>background sync]
    F --> G[Initialize Transport]
    G --> H[Ready for Messages]
```

### Agent Execution Detail

```mermaid
flowchart LR
    subgraph GluonAgent
        A[execute] --> B[Build Options]
        B --> C[ClaudeSDKClient]
    end

    subgraph ClaudeSDK
        C --> D[query prompt]
        D --> E[receive_response]
    end

    subgraph "Claude CLI Process"
        E --> F[claude CLI]
        F --> G[AWS Bedrock API]
    end

    G --> H{Model}
    H --> I[claude-opus-4.5]
    H --> J[claude-sonnet-4.5]
    H --> K[claude-haiku-4.5]

    style G fill:#ff9900
    style I fill:#6b5b95
    style J fill:#88b04b
    style K fill:#92a8d1
```

### Concurrency Model

```mermaid
flowchart TB
    subgraph "GluonBotCore (shared)"
        SEM[Semaphore<br/>max_concurrent=16]

        subgraph "Active Tasks"
            T1[Task 1<br/>telegram:123]
            T2[Task 2<br/>discord:456]
            T3[Task 3<br/>telegram:789]
        end

        SEM --> T1
        SEM --> T2
        SEM --> T3
    end

    subgraph "SQLite Store"
        DB[(gluon.db)]
        R1[ExecutionRun abc<br/>initiator: telegram:123]
        R2[ExecutionRun def<br/>initiator: discord:456]
        R3[ExecutionRun ghi<br/>initiator: telegram:789]
    end

    T1 -.-> R1
    T2 -.-> R2
    T3 -.-> R3

    subgraph "Log Files"
        L1[~/.gluon/logs/abc/]
        L2[~/.gluon/logs/def/]
        L3[~/.gluon/logs/ghi/]
    end

    T1 --> L1
    T2 --> L2
    T3 --> L3
```

### Transport Layer Architecture

```mermaid
flowchart TB
    subgraph "Transport Layer"
        ABC[Transport ABC<br/>base.py]

        subgraph "Implementations"
            TG[TelegramTransport<br/>telegram.py]
            DC[DiscordTransport<br/>discord.py]
            FUTURE[Future Transports<br/>slack.py, matrix.py...]
        end

        ABC --> TG
        ABC --> DC
        ABC -.-> FUTURE
    end

    subgraph "Core Components"
        CTX[TransportContext<br/>user_id, chat_id, thread_id]
        MSG[TransportMessage<br/>text, metadata]
        RSP[TransportResponse<br/>text, thread_id]
        CAPS[TransportCapabilities<br/>max_length, threading, editing]
    end

    ABC --> CTX
    ABC --> MSG
    ABC --> RSP
    ABC --> CAPS

    subgraph "Shared Logic"
        CORE[GluonBotCore<br/>bot_core.py]
    end

    TG --> CORE
    DC --> CORE

    style ABC fill:#e1bee7
    style CORE fill:#bbdefb
```

### Discord Bot Flow

```mermaid
sequenceDiagram
    participant User
    participant Discord
    participant DiscordTransport
    participant BotCore
    participant Store
    participant Orchestrator

    User->>Discord: @GluonBot Fix the bug
    Discord->>DiscordTransport: on_message event

    DiscordTransport->>DiscordTransport: Check @mention
    DiscordTransport->>DiscordTransport: Check authorization
    DiscordTransport->>DiscordTransport: Resolve project from channel

    alt No project linked
        DiscordTransport-->>Discord: "Link channel with @GluonBot link <project>"
        Discord-->>User: Prompt to link
    else Project found
        DiscordTransport->>Store: create_run(project_id, prompt, initiator)
        Store-->>DiscordTransport: ExecutionRun

        DiscordTransport-->>Discord: "🚀 Starting task on `project`"
        Discord-->>User: Status message

        DiscordTransport->>BotCore: execute_task(ctx, run, project)

        loop Streaming Progress
            BotCore-->>DiscordTransport: AgentMessage
            DiscordTransport-->>Discord: Send progress
            Discord-->>User: Progress messages
        end

        BotCore-->>DiscordTransport: AgentResult
        DiscordTransport->>Store: update_run(status)
        DiscordTransport->>DiscordTransport: Track message for resume
        DiscordTransport->>Discord: Edit status message
        Discord-->>User: ✅ Summary + "Reply to continue"
    end

    Note over User,Discord: Later - Resume via reply
    User->>Discord: Reply to completion: "Also add tests"
    Discord->>DiscordTransport: on_message (with reference)
    DiscordTransport->>DiscordTransport: Lookup run from message map
    DiscordTransport->>BotCore: execute_task(ctx, run, session_id)
    Note over BotCore: Resumes previous Claude session
```

### Discord Channel Mapping

```mermaid
flowchart TD
    subgraph "Channel Resolution"
        MSG[Incoming Message<br/>#myapp channel]
        MSG --> CHECK1{Explicit mapping<br/>in DB?}

        CHECK1 -->|Yes| FOUND[Use mapped project]
        CHECK1 -->|No| CHECK2{Channel name<br/>matches project?}

        CHECK2 -->|Yes| FOUND
        CHECK2 -->|No| PROMPT[Prompt user to link]
    end

    subgraph "Link Command"
        LINK["@GluonBot link myproject"]
        LINK --> SAVE[Save to channel_mappings table]
        SAVE --> CACHE[Update local cache]
        CACHE --> CONFIRM["✅ Channel linked"]
    end

    style FOUND fill:#c8e6c9
    style PROMPT fill:#ffecb3
```

### Chat Agent Architecture

```mermaid
flowchart TB
    subgraph "Any Transport"
        MSG[User Message]
        HIST[Message History<br/>last 10 per user]
        CTX[TransportContext]
    end

    subgraph "GluonBotCore"
        NL[process_natural_language]
    end

    subgraph "GluonChatAgent"
        CHAT[chat method]
        SDK[Claude Agent SDK<br/>Haiku model]
    end

    subgraph "Available Tools"
        subgraph "Gluon MCP Tools"
            PROJ[list_projects<br/>list_sessions<br/>get_status]
            TASK[run_task<br/>resume_session]
            WS[add_workspace<br/>list_workspaces<br/>scan_workspace]
            RUN[list_runs<br/>cancel_run]
            GIT[get_git_status]
        end

        subgraph "Built-in Tools"
            FILE[Read, Glob, Grep]
            EXEC[Bash, BashOutput]
            WEB[WebSearch, WebFetch]
        end
    end

    MSG --> NL
    HIST --> NL
    CTX --> NL
    NL --> CHAT
    CHAT --> SDK
    SDK --> PROJ
    SDK --> TASK
    SDK --> WS
    SDK --> RUN
    SDK --> GIT
    SDK --> FILE
    SDK --> EXEC
    SDK --> WEB

    style WEB fill:#4a90d9
    style FILE fill:#88b04b
    style EXEC fill:#ff9900
    style NL fill:#bbdefb
```

## Background Execution

Gluon supports running tasks in the background with persistent tracking:

```bash
# Start a background task
gluon run myapp 'Implement user authentication' --background
# Output: Task submitted: abc12345

# Check status
gluon runs
# Shows all runs with status (pending/running/completed/failed)

# View logs
gluon logs abc12345

# Follow logs in real-time
gluon logs abc12345 --follow

# Cancel if needed
gluon cancel abc12345
```

Background runs are tracked in SQLite and persist across restarts. Logs are stored at `~/.gluon/logs/<run_id>/`.

## Telegram Bot

Run Gluon as an always-on daemon via Telegram with support for multiple concurrent tasks.

### Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow instructions to create your bot
3. Copy the token you receive

4. (Optional) Get your user ID from [@userinfobot](https://t.me/userinfobot)

### Run the Bot

```bash
# Set token via environment variable
export GLUON_TELEGRAM_TOKEN="your-bot-token"

# Optional: restrict to specific users (comma-separated IDs)
export GLUON_TELEGRAM_USERS="123456789,987654321"

# Start the bot
gluon bot

# Or pass token directly
gluon bot --token "your-bot-token" --users "123456789"
```

### Bot Commands

- `/projects` - List registered projects
- `/sessions [project]` - List sessions
- `/run <project> <prompt>` - Run a task
- `/resume <project> [session_id] [prompt]` - Resume session (latest or specific)
- `/runs` - List your background runs
- `/runs all` - List all runs
- `/status` - Show overall status
- `/cancel` - Cancel your latest active run
- `/cancel <run_id>` - Cancel specific run
- `/clear` - Clear chat history
- `/help` - Show help

### Features

- **Multiple concurrent tasks**: Run tasks across multiple projects simultaneously
- **Persistent tracking**: All runs are tracked in SQLite and survive bot restarts
- **Global concurrency limit**: Configurable limit (default: 16) prevents resource exhaustion
- **Natural language support**: Chat naturally instead of using commands
- **Conversation context**: Bot remembers recent messages for follow-up questions
- **Reply to resume**: Reply to a completion message to automatically resume that session
- **Real-time updates**: Get progress updates as tasks execute

### Natural Language Examples

Instead of commands, you can chat naturally:

| What you say | What happens |
|--------------|--------------|
| "Show me my projects" | Lists all registered projects |
| "What's the git status of myapp?" | Shows git branch, uncommitted changes, ahead/behind |
| "Run a task on myapp to fix the login bug" | Starts a coding task with Claude |
| "What tasks are running?" | Lists active background runs |
| "Cancel the last task" | Cancels most recent active run |
| "Search the web for React best practices" | Performs web search |
| "Read the README in myapp" | Reads file contents from project |
| "Find all Python files in myapp" | Searches for files by pattern |
| *(reply to completion)* "Also add tests" | Resumes the session with follow-up prompt |

The chat agent has access to:
- **Gluon tools**: Project management, task execution, run monitoring, git status
- **File tools**: Read, Glob, Grep for exploring project code
- **Shell tools**: Bash, BashOutput for running commands
- **Web tools**: WebSearch, WebFetch for internet lookups

## Discord Bot

Run Gluon as a Discord bot with channel-based project mapping and threaded task output.

### Installation

Discord support is an optional dependency:

```bash
pip install 'gluon-agent[discord]'
# or for all optional features
pip install 'gluon-agent[all]'
```

### Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a New Application
3. Go to "Bot" tab and click "Add Bot"
4. Copy the bot token
5. Enable "MESSAGE CONTENT INTENT" in Bot settings
6. Go to "OAuth2" → "URL Generator"
7. Select scopes: `bot`, `applications.commands`
8. Select permissions: `Send Messages`, `Create Public Threads`, `Read Message History`
9. Copy the generated URL and open it to invite the bot to your server

### Run the Bot

```bash
# Set required environment variables
export GLUON_DISCORD_TOKEN="your-bot-token"
export GLUON_DISCORD_GUILD="your-guild-id"

# Optional: restrict to specific users (comma-separated Discord user IDs)
export GLUON_DISCORD_USERS="123456789,987654321"

# Start the bot
gluon discord

# Or pass options directly
gluon discord --token "token" --guild 123456789
```

### Bot Commands

Mention the bot (`@GluonBot`) with a command:

- `@GluonBot link <project>` - Link this channel to a project
- `@GluonBot projects` - List registered projects
- `@GluonBot runs` - List your runs
- `@GluonBot status` - Show overall status
- `@GluonBot cancel [run_id]` - Cancel a run
- `@GluonBot <any task>` - Execute task on the linked project

### Channel-Project Mapping

Discord channels map to projects in two ways:

1. **Auto-match**: Channel name matches project name (e.g., `#myapp` → `myapp` project)
2. **Explicit link**: Use `@GluonBot link <project>` to bind any channel

Once linked, all @mentions execute tasks on that project automatically.

### Message-Based Resume

Task execution uses Discord's reply feature for session continuity:
- Initial message shows task status and run ID
- Progress updates sent as follow-up messages
- Completion message is edited with final status and "💬 Reply to continue" hint
- **Reply to any completion message to resume that session**

```mermaid
flowchart TD
    subgraph "Discord Channel: #myapp"
        MSG1["🚀 Starting task on myapp<br/>Run: abc12345<br/>Status: Running..."]
        MSG1 -->|progress| P1["Agent output..."]
        P1 -->|edited| MSG2["✅ myapp - abc12345<br/><i>Fix the login bug...</i><br/>💬 Reply to continue"]

        subgraph "Resume Flow"
            MSG2 -->|user replies| REPLY["↩️ Also add tests"]
            REPLY --> MSG3["🔄 Resuming session on myapp<br/>Run: def67890"]
            MSG3 -->|edited| MSG4["✅ myapp - def67890<br/><i>Also add tests</i><br/>💬 Reply to continue"]
        end
    end

    style MSG1 fill:#fff9c4
    style MSG2 fill:#c8e6c9
    style MSG4 fill:#c8e6c9
    style REPLY fill:#e3f2fd
```

## Multi-Transport Mode

Run both Telegram and Discord simultaneously with a shared bot core:

```bash
# Set all required environment variables
export GLUON_TELEGRAM_TOKEN="telegram-token"
export GLUON_TELEGRAM_USERS="123456789"
export GLUON_DISCORD_TOKEN="discord-token"
export GLUON_DISCORD_GUILD="987654321"
export GLUON_DISCORD_USERS="111222333"

# Run both transports
gluon serve --telegram --discord
```

### Multi-Transport Architecture

```mermaid
flowchart TB
    subgraph "gluon serve Process"
        CLI[CLI: gluon serve]

        subgraph "Transports (Concurrent)"
            TG[TelegramTransport]
            DC[DiscordTransport]
        end

        subgraph "Shared State"
            CORE[GluonBotCore]
            STORE[(SQLite Store)]
            GIT[GitManager]
            SEM[Semaphore<br/>max_concurrent=16]
        end

        CLI --> TG
        CLI --> DC

        TG --> CORE
        DC --> CORE

        CORE --> STORE
        CORE --> GIT
        CORE --> SEM
    end

    subgraph "External Services"
        TGAPI[Telegram API]
        DCAPI[Discord API]
        BEDROCK[AWS Bedrock]
    end

    TG <--> TGAPI
    DC <--> DCAPI
    CORE -.-> BEDROCK

    style CORE fill:#bbdefb
    style STORE fill:#fff9c4
    style SEM fill:#ffccbc
```

### Cross-Platform Visibility

```mermaid
sequenceDiagram
    participant TGUser as Telegram User
    participant TG as Telegram Transport
    participant Core as GluonBotCore
    participant DC as Discord Transport
    participant DCUser as Discord User

    TGUser->>TG: /run myapp "Fix bug"
    TG->>Core: execute_task(initiator="telegram:123")
    Core->>Core: Store run in SQLite

    Note over Core: Run executes...

    DCUser->>DC: @GluonBot runs
    DC->>Core: format_runs_list()
    Core-->>DC: All runs (including telegram:123)
    DC-->>DCUser: Shows run from Telegram user

    Note over Core: Run completes

    Core-->>TG: AgentResult
    TG-->>TGUser: ✅ Complete
```

### Features

- **Shared project, session, and run state** - All transports read/write to the same SQLite database
- **Shared git background sync** - Single GitManager instance fetches for all transports
- **Shared concurrency limits** - Global semaphore prevents overload across all platforms
- **Cross-platform run visibility** - Users on any platform can see runs from other platforms

## Configuration

### Environment Variables

**Telegram:**
- `GLUON_TELEGRAM_TOKEN` - Telegram bot token
- `GLUON_TELEGRAM_USERS` - Comma-separated list of allowed Telegram user IDs

**Discord:**
- `GLUON_DISCORD_TOKEN` - Discord bot token
- `GLUON_DISCORD_GUILD` - Discord guild (server) ID
- `GLUON_DISCORD_USERS` - Comma-separated list of allowed Discord user IDs

**AWS Bedrock:**
- AWS credentials for Bedrock models (see `.env.local.example`)

### Model Selection

Gluon uses Claude models via AWS Bedrock:

| Tier | Model | Use Case |
|------|-------|----------|
| `haiku` | claude-haiku-4.5 | Fast, economical tasks |
| `sonnet` | claude-sonnet-4.5 | Balanced performance (default) |
| `opus` | claude-opus-4.5 | Complex, demanding tasks |

```bash
gluon run myapp 'Fix bug' --model haiku
gluon run myapp 'Complex refactor' --model sonnet
gluon run myapp 'Architecture review' --model opus
```

## Git Synchronization

Gluon automatically keeps projects synchronized with remote Git repositories to prevent conflicts when multiple Claude instances work on the same codebase.

### Pre-task Sync Flow

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

### Post-task Sync Flow

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

```mermaid
flowchart LR
    subgraph "Bot Process (any transport)"
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

### Automatic Behavior

- **Pre-task**: Before running a task, Gluon will:
  1. Auto-commit any uncommitted changes
  2. Fetch from remote
  3. Fast-forward if behind (fails if diverged)

- **Post-task**: After successful task completion:
  1. Stage and commit all changes
  2. Push to remote

- **Background**: Bot transports (Telegram, Discord) periodically fetch all projects (every 5 minutes) to maintain status awareness

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

## Image Attachments

Attach images (screenshots, diagrams, mockups) to tasks to provide visual context to the AI agent.

### Upload Methods

1. **Web Dashboard** - Paste with ⌘V in the task creation dialog or resume textarea
2. **API** - POST multipart form to `/api/runs/{run_id}/attachments`

### How It Works

```mermaid
flowchart LR
    subgraph "Upload"
        USER[User pastes image]
        UPLOAD[Upload API]
        HASH[SHA256 Hash]
    end

    subgraph "Storage"
        DEDUP{Duplicate?}
        STORE[~/.gluon/images/]
        DB[(run_images table)]
    end

    subgraph "Task Execution"
        COPY[Copy to worktree]
        AI[Claude Agent sees images]
    end

    USER --> UPLOAD
    UPLOAD --> HASH
    HASH --> DEDUP
    DEDUP -->|No| STORE
    DEDUP -->|Yes| DB
    STORE --> DB
    DB --> COPY
    COPY --> AI
```

### Features

- **Deduplication** - Same image uploaded twice only stored once (SHA256)
- **Worktree Copy** - Images copied to `.gluon-images/` in worktree for AI visibility
- **Gallery View** - View all images attached to a run in the dashboard
- **Supported Formats** - PNG, JPEG, GIF, WebP (max 50MB)

## PR Integration

Gluon integrates with GitHub for PR creation, status tracking, and merging.

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

## Usage Tracking

Monitor costs and token usage across all runs.

### Dashboard View

The Usage page (`/usage`) shows:

- **Today's Cost** - Total spend today
- **Weekly Cost** - 7-day rolling total
- **Cost by Project** - Breakdown per project
- **Daily Chart** - Visual cost/token trends
- **Run List** - Sortable by cost, tokens, date

### API Endpoints

```
GET /api/usage/summary      # Today/week totals
GET /api/usage/by-project   # Cost per project
GET /api/usage/by-day       # Daily aggregates
GET /api/usage/runs         # Runs with cost data
```

### Tracked Metrics

| Metric | Description |
|--------|-------------|
| `cost_usd` | Total API cost for the run |
| `input_tokens` | Tokens sent to Claude |
| `output_tokens` | Tokens received from Claude |
| `model_used` | Model tier (haiku/sonnet/opus) |

## File Structure

```
~/.gluon/
├── gluon.db          # SQLite database (projects, sessions, runs)
├── images/           # Image attachments (content-addressed)
│   └── {hash[:2]}/
│       └── {hash}.{ext}
└── logs/
    └── <run_id>/     # Per-run log directories
        ├── stdout.log
        ├── stderr.log
        └── messages.jsonl

/tmp/gluon-worktrees/   # Temporary worktrees for isolated tasks
└── wt-{run_id}/
    └── .gluon-images/  # Images copied for AI visibility
```

## Development

```bash
# Run tests
pytest

# Run linter
ruff check src/ tests/

# Format code
ruff format src/ tests/
```

## Web API Reference

The web dashboard exposes a REST API at `/api/*` and WebSocket at `/api/ws`.

### Core Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/runs` | GET | List runs (filter by project, status, archived) |
| `/api/runs` | POST | Create new run |
| `/api/runs/{id}` | GET | Get run details |
| `/api/runs/{id}/cancel` | POST | Cancel running task |
| `/api/runs/{id}/resume` | POST | Resume with follow-up prompt |
| `/api/runs/{id}/logs` | GET | Get stdout/stderr/messages |
| `/api/runs/{id}/archive` | POST | Archive run (hide from board) |

### Git/PR Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/runs/{id}/commits` | GET | Commits on run's branch |
| `/api/runs/{id}/files` | GET | Files changed on branch |
| `/api/runs/{id}/create-pr` | POST | Create PR for worktree run |
| `/api/runs/{id}/merge` | POST | Merge branch locally |
| `/api/projects/{id}/conflicts` | GET | Detect merge conflicts |
| `/api/projects/{id}/rebase` | POST | Start rebase operation |

### Image Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/images/upload` | POST | Upload image (multipart) |
| `/api/images/{id}/file` | GET | Serve image file |
| `/api/runs/{id}/attachments` | GET | List attached images |
| `/api/runs/{id}/attachments` | POST | Attach image to run |

### WebSocket Events

Connect to `/api/ws` for real-time updates:

```json
{"type": "run_created", "run": {...}}
{"type": "run_updated", "run": {...}}
{"type": "log_line", "run_id": "...", "stream": "stdout", "line": "..."}
```

## Limitations

- **No interactive STDIN**: Once a task starts, you cannot send additional input to the running Claude process. To continue work, wait for completion and use `resume` with a new prompt.
- **Single-process concurrency**: The semaphore only limits concurrency within a single process. Multiple CLI `--background` invocations each run in separate processes. However, `gluon serve` runs all transports in a single process with shared concurrency limits.
- **Fire-and-forget execution**: The Claude Agent SDK sends the prompt once via `client.query()` and then only receives responses.
- **Discord requires optional dependency**: Install with `pip install 'gluon-agent[discord]'` to enable Discord support.
- **GitHub CLI required for PRs**: The `gh` CLI must be installed and authenticated for PR creation/merge features.
