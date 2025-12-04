# Comprehensive Audit: Gluon-Agent vs Vibe-Kanban

**Date**: 2025-12-04
**Auditor**: Claude Code
**Projects Compared**:
- **Gluon-Agent**: AI orchestrator for managing Claude Code agents (Python + FastAPI + React)
- **Vibe-Kanban**: Multi-agent task orchestration platform (Rust + Axum + React)

---

## Executive Summary

This audit compares gluon-agent with vibe-kanban, an open-source multi-agent orchestration platform. The analysis reveals **23 feature gaps** across **10 categories**, with **4 critical gaps** that significantly impact user experience and capability.

### Key Findings

| Category | Gluon Status | Vibe-Kanban Status | Gap Severity |
|----------|--------------|-------------------|--------------|
| Attachment/Images | **Missing** | Full implementation | CRITICAL |
| Multi-Agent Support | Claude only | 9 agents | CRITICAL |
| Team Collaboration | Single-user | Full org support | CRITICAL |
| Real-Time Updates | Basic WebSocket | JSON Patch + SSE | HIGH |
| Task Organization | Status only | Tags + hierarchies | MEDIUM |
| Git Operations | Basic | Advanced (rebase, conflicts) | MEDIUM |
| Error Handling | Generic | Structured + typed | MEDIUM |
| Configuration | Minimal | Comprehensive | LOW |
| Draft/Queue System | None | Full support | LOW |
| UI/UX Features | Basic | Polished | LOW |

### Critical Gaps Summary

1. **No image/file attachments** - Users cannot provide visual context (screenshots, designs)
2. **Single-agent architecture** - Locked to Claude Code, cannot use Gemini, Codex, etc.
3. **No collaboration features** - Single-user only, no teams or sharing
4. **Limited real-time updates** - No efficient incremental update protocol

---

## Technology Comparison

| Aspect | Gluon-Agent | Vibe-Kanban |
|--------|-------------|-------------|
| Backend Language | Python 3.12+ | Rust |
| Web Framework | FastAPI | Axum |
| Database | SQLite | SQLite (SQLx) |
| Frontend | React + TypeScript | React + TypeScript |
| UI Library | shadcn/ui | shadcn/ui |
| Package Manager | uv | pnpm + Cargo |
| Type Generation | Manual | ts-rs (auto) |
| Deployment | CLI + systemd | npx |

---

## Architecture Comparison

### Gluon-Agent
```
CLI (cli.py) ───────────────────────────────────────────┐
                                                        │
                Transport Layer (transport/)            │
                ┌─────────────┬─────────────┐           │
                ▼             ▼             ▼           ▼
         TelegramTransport  DiscordTransport  ...    Orchestrator
                └─────────────┴─────────────┘           │
                              │                         │
                              ▼                         │
                       GluonBotCore ◄───────────────────┤
                              │                         │
                    ┌─────────┴─────────┐               │
                    ▼                   ▼               ▼
             Chat Agent           Orchestrator ───► Agent (Claude only)
                    │                   │
                    └─────────┬─────────┘
                              ▼
                        Store (SQLite)
```

### Vibe-Kanban
```
                    ┌─────────────────────────────────┐
                    │         Frontend (React)        │
                    │  ┌─────────┐  ┌─────────────┐   │
                    │  │ REST    │  │ WebSocket   │   │
                    │  │ Client  │  │ (JSON Patch)│   │
                    │  └────┬────┘  └──────┬──────┘   │
                    └───────┼──────────────┼──────────┘
                            │              │
                    ┌───────▼──────────────▼──────────┐
                    │         Axum Server             │
                    │  ┌─────────┐  ┌─────────────┐   │
                    │  │ Routes  │  │ WS Handlers │   │
                    │  └────┬────┘  └──────┬──────┘   │
                    │       │              │          │
                    │  ┌────▼──────────────▼─────┐    │
                    │  │      Services Layer      │   │
                    │  │  ┌──────┐ ┌──────────┐  │    │
                    │  │  │Queue │ │ Worktree │  │    │
                    │  │  └──────┘ └──────────┘  │    │
                    │  └────────────┬────────────┘    │
                    │               │                 │
                    │  ┌────────────▼────────────┐    │
                    │  │     Executors Layer     │    │
                    │  │ ┌──────┐ ┌──────┐ ┌───┐│    │
                    │  │ │Claude│ │Gemini│ │...││    │
                    │  │ └──────┘ └──────┘ └───┘│    │
                    │  └─────────────────────────┘    │
                    └─────────────────────────────────┘
```

---

## Detailed Gap Reports

For detailed analysis, see:
1. **[01-gap-analysis.md](./01-gap-analysis.md)** - Complete feature gap inventory
2. **[02-edge-cases-analysis.md](./02-edge-cases-analysis.md)** - Error handling and edge cases
3. **[03-feature-inspiration.md](./03-feature-inspiration.md)** - Implementation patterns to adopt

---

## Feature Comparison Matrix

### Task Management

| Feature | Gluon | Vibe-Kanban |
|---------|-------|-------------|
| Create task | ✅ | ✅ |
| Status tracking | ✅ | ✅ |
| Task description | ✅ (prompt) | ✅ |
| Task title | ❌ | ✅ |
| Task hierarchy | ❌ | ✅ |
| Task sharing | ❌ | ✅ |
| Task assignees | ❌ | ✅ |
| Tags/labels | ❌ | ✅ |
| Image attachments | ❌ | ✅ |
| Draft auto-save | ❌ | ✅ |
| Follow-up queue | ❌ | ✅ |

### Execution

| Feature | Gluon | Vibe-Kanban |
|---------|-------|-------------|
| Background execution | ✅ | ✅ |
| Session resume | ✅ | ✅ |
| Log streaming | ✅ | ✅ (JSON Patch) |
| Process cancel | ✅ | ✅ |
| Multiple agents | ❌ | ✅ (9 agents) |
| Agent profiles | ❌ | ✅ |
| Agent capabilities | ❌ | ✅ |
| Setup scripts | ❌ | ✅ |
| Dev server launch | ❌ | ✅ |
| Tool approvals | ❌ | ✅ |

### Git Integration

| Feature | Gluon | Vibe-Kanban |
|---------|-------|-------------|
| Worktree creation | ✅ | ✅ |
| Branch tracking | ✅ | ✅ |
| Commit listing | ✅ | ✅ |
| File changes | ✅ | ✅ |
| PR creation | ✅ | ✅ |
| Merge | ✅ | ✅ |
| Rebase | ❌ | ✅ |
| Conflict detection | ❌ | ✅ |
| Branch rename | ❌ | ✅ |
| Target branch change | ❌ | ✅ |
| Force push | ❌ | ✅ |

### UI/UX

| Feature | Gluon | Vibe-Kanban |
|---------|-------|-------------|
| Kanban board | ✅ | ✅ |
| Drag-and-drop | ✅ | ✅ |
| Real-time updates | ✅ (basic) | ✅ (JSON Patch) |
| Usage dashboard | ✅ | ❓ |
| Settings page | ✅ | ✅ |
| Theme support | ❌ | ✅ |
| i18n | ❌ | ✅ (4 langs) |
| Keyboard shortcuts | ❌ | ✅ |
| Sound notifications | ❌ | ✅ |
| Editor integration | ❌ | ✅ |
| WYSIWYG editor | ❌ | ✅ |

### Infrastructure

| Feature | Gluon | Vibe-Kanban |
|---------|-------|-------------|
| Bot integrations | ✅ (Telegram, Discord) | ❌ |
| MCP server | ❌ | ✅ |
| OAuth | ❌ | ✅ (GitHub) |
| Organizations | ❌ | ✅ |
| Invitations | ❌ | ✅ |
| Remote deployment | ❌ | ✅ |

---

## Strengths: Where Gluon-Agent Excels

Gluon-agent has some features vibe-kanban lacks:

1. **Multi-transport bot support** - Telegram and Discord bots
2. **Usage/cost tracking dashboard** - Detailed cost analytics
3. **Simpler deployment** - Single Python package
4. **CLI-first design** - Great terminal experience

---

## Recommendations

### Phase 1: Quick Wins (Low effort, High impact)

1. **Add image upload to runs**
   - Implement `POST /api/runs/{id}/images`
   - Store in `~/.gluon/images/`
   - Copy to worktree on run start

2. **Improve WebSocket protocol**
   - Add exponential backoff reconnection
   - Add terminal state messages
   - Consider JSON Patch for efficiency

3. **Add draft saving**
   - localStorage-based prompt drafts
   - Debounced auto-save

### Phase 2: Architecture (Medium effort, High impact)

4. **Design multi-agent system**
   - Create `Executor` base class
   - Implement `ClaudeCodeExecutor`, `GeminiExecutor`
   - Add agent selection to UI

5. **Enhance git operations**
   - Add rebase support
   - Add conflict detection
   - Add force push with confirmation

6. **Add tagging/labels**
   - Simple label model
   - Filter by label in UI

### Phase 3: Collaboration (High effort, Varies impact)

7. **Follow-up queue system**
   - Queue prompts during execution
   - Auto-resume on completion

8. **Basic organization support**
   - User authentication
   - Project sharing

### Phase 4: Polish (Low effort, Low impact)

9. **Editor integration**
   - "Open in VS Code" button
   - Editor preference setting

10. **Theme support**
    - Dark/light mode toggle
    - System preference detection

---

## Implementation Roadmap

```
Month 1 (Quick Wins)
├── Week 1-2: Image upload API + storage
├── Week 3: WebSocket improvements
└── Week 4: Draft saving + labels schema

Month 2 (Architecture)
├── Week 1-2: Executor abstraction layer
├── Week 3: Gemini executor implementation
└── Week 4: Agent selection UI

Month 3 (Git + Polish)
├── Week 1: Enhanced git operations
├── Week 2: Conflict detection
├── Week 3: Follow-up queue
└── Week 4: Editor integration + theme
```

---

## Conclusion

Vibe-kanban is a mature, feature-rich platform that serves as excellent inspiration for gluon-agent evolution. The most impactful improvements would be:

1. **Image attachments** - Critical for providing visual context
2. **Multi-agent support** - Future-proofing for agent ecosystem
3. **Better real-time updates** - Improved UX with JSON Patch
4. **Enhanced git operations** - Power-user workflow support

The recommended approach is to adopt vibe-kanban patterns incrementally, starting with high-impact, lower-effort features like image uploads, while planning for larger architectural changes like multi-agent support.

---

## Files Reference

- `docs/audit/00-comprehensive-audit.md` - This file
- `docs/audit/01-gap-analysis.md` - Detailed gap inventory
- `docs/audit/02-edge-cases-analysis.md` - Error handling patterns
- `docs/audit/03-feature-inspiration.md` - Implementation patterns

## Source References

- Vibe-Kanban GitHub: https://github.com/BloopAI/vibe-kanban
- Local clone: `./tmp/vibe-kanban`
