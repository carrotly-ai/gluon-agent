# Gluon Web UI

React + TypeScript web dashboard for Gluon Agent. Provides a Kanban board, real-time log streaming, task management, and usage analytics.

## Tech Stack

- **React 19** with TypeScript 5.9
- **Vite 7** with SWC for fast builds
- **Tailwind CSS 4** for styling
- **Radix UI** for accessible dialog, tabs, tooltip, and scroll area primitives
- **DnD Kit** for drag-and-drop Kanban columns
- **React Router 7** for URL-based routing
- **React Virtual** for virtualized log rendering
- **React Markdown** with GFM for rendered output
- **Sonner** for toast notifications
- **Lucide React** for icons
- **Biome** for linting and formatting
- **vite-plugin-pwa** for Progressive Web App support

## Development

```bash
# Install dependencies
bun install

# Start dev server (proxies API to localhost:45866)
bun dev

# Build for production
bun build

# Lint & format
bun check
```

The dev server proxies `/api` requests to the Gluon backend at `http://localhost:45866`. Start the backend first with `gluon web` or `docker compose up`.

## Features

### Kanban Board
Drag-and-drop columns: Queue, Running, Review, Done. Task cards show prompt, project, model, cost, loop progress, and duration.

### Task Detail View
Tabbed interface with:
- **Messages** - Live message stream with tool call visualization, filterable by Tools/Text/Errors
- **Output** - Rendered markdown output
- **Errors** - Error messages and stack traces
- **Commits** - Git commits with per-file additions/deletions
- **Files** - Changed files with inline unified diffs
- **Images** - Attached screenshots and diagrams
- **Loop** - Ralph Loop iteration table with cost, tokens, and circuit state

### Task Actions
- Create new tasks with slash command and file autocomplete
- Resume/follow-up on completed tasks
- Cancel running tasks, stop Ralph Loops
- Create PRs, merge branches, archive runs
- Queue follow-up messages for running tasks
- Answer pending agent questions via modal

### Usage Analytics
Cost and token tracking with:
- Summary stats (today, week, month, avg/run, avg/day)
- Per-project cost breakdown
- Daily trend charts
- Per-run cost table sorted by cost/date/tokens

### Settings
- **Workspaces** - Add/remove workspaces, scan for projects, view git status
- **Projects** - List all projects with workspace labels and git branches
- **Preferences** - Git worktree default, author identity, sandbox isolation

### Progressive Web App
- Installable on mobile and desktop
- Service worker with offline caching
- Pull-to-refresh gesture
- Animated offline indicator
- Update banner when new version available

### Real-Time Updates
WebSocket connection to `/api/ws` for:
- Run status transitions
- Live log streaming
- Pending question notifications
- Connectivity detection with offline mode

## Project Structure

```
src/
├── components/         # React components
│   ├── ui/             # Shared UI primitives (Radix-based)
│   ├── KanbanBoard.tsx
│   ├── RunCard.tsx
│   ├── RunDetailPage.tsx
│   ├── RunDetailDialog.tsx
│   ├── CreateTaskDialog.tsx
│   ├── StreamingLogViewer.tsx
│   ├── LoopProgressTab.tsx
│   ├── QuestionModal.tsx
│   ├── UsagePage.tsx
│   ├── SettingsPage.tsx
│   └── ...
├── hooks/              # Custom React hooks
│   ├── useWebSocket.ts
│   └── ...
├── lib/                # Utilities
├── App.tsx             # Root component with routing
└── main.tsx            # Entry point
```

## Build & Deploy

The production build is served by the FastAPI backend. Running `bun build` outputs to `dist/`, which is embedded into the Python package and served as static files by `gluon web`.

For Docker deployments, the web UI is built during `docker compose build`.

## Related Documentation

- [Web Dashboard](../docs/WEB-DASHBOARD.md) - Feature documentation
- [API Reference](../docs/API.md) - REST and WebSocket API
- [Screenshots](../docs/SCREENSHOTS.md) - UI gallery
