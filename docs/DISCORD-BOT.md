# Discord Bot

Run Gluon as a Discord bot with channel-based project mapping, DM support, and real-time task streaming.

## Installation

Discord support is an optional dependency:

```bash
pip install 'gluon-agent[discord]'
# or for all optional features
pip install 'gluon-agent[all]'
```

## Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a New Application
3. Go to "Bot" tab and click "Add Bot"
4. Copy the bot token
5. Enable "MESSAGE CONTENT INTENT" in Bot settings
6. Go to "OAuth2" → "URL Generator"
7. Select scopes: `bot`, `applications.commands`
8. Select permissions: `Send Messages`, `Create Public Threads`, `Read Message History`
9. Copy the generated URL and open it to invite the bot to your server

## Run the Bot

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

## Commands

Mention the bot (`@GluonBot`) in channels or send direct messages:

| Command | Description |
|---------|-------------|
| `@GluonBot link <project>` | Link this channel to a project |
| `@GluonBot projects` | List registered projects |
| `@GluonBot runs` | List your runs |
| `@GluonBot status` | Show overall status |
| `@GluonBot cancel [run_id]` | Cancel a run |
| `@GluonBot <any task>` | Execute task on the linked project |

## Direct Messages (DM Support)

You can interact with Gluon via direct messages. Since DMs aren't associated with a channel, use project specifiers:

```
# Project prefix syntax
project:myapp Fix the login bug
p:myapp Add user authentication

# Flag syntax
Fix the login bug --project myapp
Fix the login bug -p myapp
```

DM conversations maintain chat mode context, allowing natural follow-up questions without repeating the project name.

## Model Selection

Specify model tier using the `--model` or `-m` flag in your prompt:

```
@GluonBot Fix the bug --model opus
@GluonBot Quick fix --model haiku
@GluonBot Implement feature -m sonnet
```

| Model | Best For |
|-------|----------|
| `haiku` | Quick fixes, simple tasks |
| `sonnet` | Most coding tasks (default) |
| `opus` | Complex refactoring, architecture |

## Channel Topic Configuration

Configure default project and model by adding markers to your channel topic:

```
Project: myapp | Model: opus
```

Supported formats:
- `Project: myapp` or `project:myapp`
- `Model: opus` or `model:opus`

This allows the channel to auto-link to a project and use a specific model without explicit flags.

## Channel-Project Mapping

Discord channels map to projects in multiple ways:

1. **Channel topic** - Set `project:myapp` in the channel topic (see above)
2. **Auto-match** - Channel name matches project name (e.g., `#myapp` → `myapp` project)
3. **Explicit link** - Use `@GluonBot link <project>` to bind any channel

Once linked, all @mentions execute tasks on that project automatically.

```mermaid
flowchart TD
    subgraph "Channel Resolution"
        MSG[Incoming Message<br/>#myapp channel]
        MSG --> CHECK0{Channel topic<br/>has project?}

        CHECK0 -->|Yes| FOUND[Use mapped project]
        CHECK0 -->|No| CHECK1{Explicit mapping<br/>in DB?}

        CHECK1 -->|Yes| FOUND
        CHECK1 -->|No| CHECK2{Channel name<br/>matches project?}

        CHECK2 -->|Yes| FOUND
        CHECK2 -->|No| PROMPT[Prompt user to link]
    end

    subgraph "Link Command"
        LINK["@GluonBot link myproject"]
        LINK --> SAVE[Save to channel_mappings table]
        SAVE --> CONFIRM["✅ Channel linked"]
    end

    style FOUND fill:#c8e6c9
    style PROMPT fill:#ffecb3
```

## Message-Based Resume

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

## Bot Flow

```mermaid
sequenceDiagram
    participant User
    participant Discord
    participant DiscordTransport
    participant BotCore
    participant Store

    User->>Discord: @GluonBot Fix the bug
    Discord->>DiscordTransport: on_message event

    DiscordTransport->>DiscordTransport: Check @mention
    DiscordTransport->>DiscordTransport: Check authorization
    DiscordTransport->>DiscordTransport: Resolve project from channel

    alt No project linked
        DiscordTransport-->>Discord: "Link channel with @GluonBot link <project>"
        Discord-->>User: Prompt to link
    else Project found
        DiscordTransport->>Store: create_run(project_id, prompt)
        Store-->>DiscordTransport: ExecutionRun

        DiscordTransport-->>Discord: "🚀 Starting task..."
        Discord-->>User: Status message

        DiscordTransport->>BotCore: execute_task()

        loop Streaming Progress
            BotCore-->>DiscordTransport: AgentMessage
            DiscordTransport-->>Discord: Send progress
        end

        BotCore-->>DiscordTransport: AgentResult
        DiscordTransport->>Store: update_run(status)
        DiscordTransport->>Discord: Edit message: "✅ Complete"
        Discord-->>User: Summary + "Reply to continue"
    end

    Note over User,Discord: Later - Resume via reply
    User->>Discord: Reply: "Also add tests"
    Discord->>DiscordTransport: on_message (with reference)
    DiscordTransport->>BotCore: execute_task(session_id)
    Note over BotCore: Resumes previous Claude session
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GLUON_DISCORD_TOKEN` | Discord bot token (required) |
| `GLUON_DISCORD_GUILD` | Discord guild (server) ID (required) |
| `GLUON_DISCORD_USERS` | Comma-separated list of allowed user IDs |

## Real-Time Tool Call Display

During task execution, the bot displays agent tool calls in real-time:

```
🔄 Running task on myapp...
🔧 Bash: Running tests
🔧 Edit: Fixing test file
🔧 Bash: Running tests again
✅ Task completed!
```

This provides visibility into what the agent is doing without overwhelming the chat with full output.

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

### Features

- **Shared project, session, and run state** - All transports read/write to the same SQLite database
- **Shared git background sync** - Single GitManager instance fetches for all transports
- **Shared concurrency limits** - Global semaphore prevents overload across all platforms
- **Cross-platform run visibility** - Users on any platform can see runs from other platforms
