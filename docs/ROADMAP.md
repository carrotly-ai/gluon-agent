# Gluon Agent Roadmap

## Current Status (v0.1.0)

### Completed Features

- [x] **Project Management**
  - Register/remove individual projects
  - Project path validation
  - Unique naming enforcement

- [x] **Workspace Management**
  - Register workspaces for bulk project discovery
  - Auto-scan for projects using markers
  - Manual scan/refresh capability
  - Configurable ignore patterns

- [x] **Session Management**
  - Session creation and tracking
  - Session resume via Claude session ID
  - Cost and turn tracking
  - Status lifecycle (active → paused → completed/failed)

- [x] **Execution**
  - Claude Agent SDK integration
  - Streaming message output
  - Automatic session resume

- [x] **CLI Interface**
  - Project commands (add, list, remove)
  - Workspace commands (add, list, remove, scan, projects)
  - Session commands (list, status)
  - Run/resume commands

- [x] **Telegram Bot**
  - Slash commands for all operations
  - Natural language processing via Chat Agent
  - User authorization
  - Real-time task updates

---

## Near-Term Enhancements

### 1. Task Queue System
**Priority: High**

Allow queuing multiple tasks across projects for sequential execution.

```python
# Proposed API
gluon queue add myapp "Fix bug A"
gluon queue add backend "Add endpoint"
gluon queue list
gluon queue process  # Execute all queued tasks
gluon queue clear
```

**Implementation:**
- Add `Task` model with status (pending, running, completed, failed)
- Add `tasks` table to store
- Implement queue processing in orchestrator
- Add CLI commands
- Add Telegram `/queue` commands

### 2. Cost Budgets and Limits
**Priority: High**

Set spending limits per project, workspace, or globally.

```python
# Proposed API
gluon config set budget.global 10.00
gluon config set budget.project.myapp 5.00
gluon config set budget.daily 20.00
```

**Implementation:**
- Add `Config` model for settings
- Add budget checking before/during execution
- Send warnings at 80%, 90% thresholds
- Auto-pause when budget exceeded

### 3. Deeper Workspace Scanning
**Priority: Medium**

Currently only scans immediate children. Support deeper scanning.

```python
# Proposed API
gluon workspace add myworkspace /path --depth 3
gluon workspace update myworkspace --depth 2
```

**Implementation:**
- Modify `scan_for_projects()` to use `scan_depth`
- Add recursive directory traversal
- Optimize with early termination on marker detection

### 4. Custom Project Markers
**Priority: Medium**

Allow custom markers for project detection.

```python
# Proposed API
gluon config set markers.add "custom.yaml"
gluon config set markers.remove "setup.py"
```

**Implementation:**
- Add configurable markers in workspace settings
- Support glob patterns

### 5. Session Tags and Search
**Priority: Medium**

Tag sessions for easier organization and search.

```python
# Proposed API
gluon run myapp "Fix bug" --tag bugfix --tag urgent
gluon sessions --tag bugfix
gluon sessions --search "authentication"
```

**Implementation:**
- Add `tags` column to sessions table
- Add full-text search on prompts

---

## Medium-Term Features

### 6. Web Dashboard
**Priority: Medium**

Browser-based UI for monitoring and control.

**Features:**
- Project/workspace overview
- Session history with logs
- Real-time task monitoring
- Cost analytics
- Mobile-responsive

**Tech Stack:**
- FastAPI backend
- React/Next.js frontend
- WebSocket for real-time updates

### 7. Concurrent Execution
**Priority: Medium**

Run multiple agents simultaneously across different projects.

```python
# Proposed API
gluon run --parallel myapp backend frontend "Deploy v2.0"
gluon config set concurrency.max 3
```

**Implementation:**
- Track multiple active sessions
- Resource management
- Progress aggregation

### 8. Project Templates
**Priority: Low**

Pre-configured project settings and prompts.

```python
# Proposed API
gluon template create nextjs-app
gluon template apply nextjs-app myproject
```

### 9. Session Compaction
**Priority: Low**

Summarize long sessions to reduce context size.

```python
# Proposed API
gluon session compact <session-id>
gluon config set auto_compact.enabled true
gluon config set auto_compact.threshold 50  # turns
```

### 10. Export/Import
**Priority: Low**

Backup and restore Gluon data.

```python
# Proposed API
gluon export --output backup.json
gluon import backup.json
```

---

## Long-Term Vision

### 11. Multi-Agent Orchestration

Coordinate multiple Claude instances working on related tasks.

**Use Cases:**
- Parent agent assigns subtasks to child agents
- Agents work on different aspects of same feature
- Review agent validates other agents' work

### 12. Plugin System

Extensible architecture for custom integrations.

```python
# Proposed API
gluon plugin install gluon-github
gluon plugin install gluon-jira
gluon plugin list
```

**Potential Plugins:**
- GitHub PR creation
- Jira ticket updates
- Slack notifications
- Custom MCP servers

### 13. Team Collaboration

Multi-user support with shared workspaces.

**Features:**
- User authentication
- Shared workspaces
- Activity feed
- Permission levels

### 14. Analytics Dashboard

Insights into agent usage and effectiveness.

**Metrics:**
- Cost per project/workspace
- Success rate by task type
- Common failure patterns
- Time to completion trends

### 15. CI/CD Integration

Run Gluon tasks as part of build pipelines.

```yaml
# github-actions example
- name: Run Gluon
  uses: gluon-agent/action@v1
  with:
    project: myapp
    prompt: "Fix failing tests"
```

---

## Technical Debt

### Current Issues to Address

1. **Test Coverage**
   - Add integration tests
   - Mock Claude SDK properly
   - Test bot commands

2. **Error Messages**
   - More descriptive error messages
   - Suggested fixes for common errors

3. **Logging**
   - Structured logging
   - Log levels
   - Log rotation

4. **Configuration**
   - Centralized config management
   - Config file support (gluon.yaml)
   - Environment variable documentation

5. **Documentation**
   - API documentation (auto-generated)
   - Video tutorials
   - Best practices guide

---

## Contributing

### Priority Order for Contributors

1. **High Impact, Low Effort:**
   - Improve error messages
   - Add logging
   - Write tests

2. **High Impact, Medium Effort:**
   - Task queue system
   - Cost budgets
   - Session tags

3. **Medium Impact, High Effort:**
   - Web dashboard
   - Concurrent execution

### Getting Started

1. Fork the repository
2. Create feature branch
3. Follow patterns in DEVELOPMENT.md
4. Write tests
5. Submit PR

### Discussion

Ideas and feedback welcome via:
- GitHub Issues
- Pull Requests
