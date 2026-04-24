# Gluon Agent: Stock-Take & Roadmap

**Date**: 2026-04-24
**Version at time of writing**: 0.9.1
**Status**: Theme B/C/D mostly shipped; Theme A + D5 + E remain open.

> **Shipment status banner (2026-04-25):** Themes B (phases 1-4), C (all five
> deliverables), and D (D1-D4) are shipped to `main` plus a bonus Theme G —
> the multi-cloud LLM provider abstraction (Bedrock / Anthropic / Vertex /
> Foundry). What remains open from this document: Theme A (positioning
> content), D5 (multi-user auth), and Theme E (native mobile). See
> [CHANGELOG.md](../../CHANGELOG.md) for the full delta.

## TL;DR

Gluon is at v0.9.1 with a mature, well-architected core. The last ~50 commits
are dominated by SDK chasing (12+ bumps) and stability — running to keep up
rather than building net-new capability. The market got crowded fast:
KingCoding, claude-code-remote-access, Claude Deck, claudectl, Agent-Quest,
Claude Code Scheduled Tasks, Parallel Code, and The Autonomous Stack all
landed in the last 90 days. The two most consequential external signals are:

1. **Anthropic pulling Claude Code off the $20 Pro plan** — strengthens
   Gluon's Bedrock-backed, self-hosted story and is a demand-gen tailwind
   nobody is marketing around.
2. **Industry pivot from "one agent" to "fleets of coordinated agents"** —
   Gluon is positioned for this but hasn't claimed it.

---

## 1. Current state — honest read

### Solid and production-ready

- Core orchestration (`core.py`, `store.py`, `runner.py`) — battle-tested via SDK churn
- Multi-transport: CLI + Telegram + Discord + Web Dashboard (60+ endpoints + WebSocket) — broadest surface of any peer
- Session resume with `fork_session` — sophisticated, hard to replicate
- Git worktree isolation + GC — recent, important, differentiating
- Witness + resume coordinator — unique auto-recovery logic
- Chat agent with MCP tools — natural language interface over own primitives
- Docker-first distribution (dev + production compose files, GHCR images)
- ~70% test-to-source ratio — healthy but gappy on web API

### Planned but unshipped (high-value backlog)

- [`paperclip-patterns-implementation.md`](paperclip-patterns-implementation.md) — Agent Identity, Heartbeats, Task Tracking, Budget Enforcement. Fully designed, zero code.
- [`sdk-0.1.48-feature-integration.md`](sdk-0.1.48-feature-integration.md) — agent-ID badges, session explorer, runtime MCP management. SDK is now at 0.1.65 so some is stale.
- [`../session-management-exploration.md`](../session-management-exploration.md) — session cleanup, point-in-time forking. JSONL files accumulate unbounded on disk.

### Conspicuous gaps

- **No auth layer** — assumes single user, Docker isolation. Blocks team/multi-user use.
- **Cost tracking without enforcement** — `cost_usd` is summed; enforcement is ~20 lines of code.
- **Witness "nudge" suggestions incomplete** (`witness.py`: "not yet implemented").
- **Activity log + work queue tables exist but aren't wired** (flagged in `doctor.py`).
- **Web API has ~60 endpoints but minimal integration test coverage.**
- **No multi-agent coordination primitive** — each run is an island.
- **No mobile PWA** — web dashboard is responsive but not installable.
- **No native iOS/Android app** — despite the "from your phone" positioning.

### Recent commit pattern

Last 50 commits by theme: SDK upgrades ≈44%, stability fixes ≈24%, UI polish
≈16%, docs/misc ≈16%. Zero net-new user-facing capabilities since `b164c60`
("surface SDK v0.1.46 features"). Currently in maintenance mode. Plans
directory shows ambition that hasn't landed.

---

## 2. Market context (2026-04)

### Signals validating the direction

- **r/automation 2026-04-22**: *"Is the real automation shift happening in orchestration, not autonomy?"* — Gluon is an orchestrator. Lean into the framing.
- **r/AI_Agents "The Orchestrator Era: The Great Recalibration"** — validates the category.
- **Anthropic pricing rug-pull** (2255 upvotes on r/ClaudeAI): Pro losing Claude Code, pushing toward $100/mo Max. Every self-hosted Bedrock deployment becomes more attractive.
- **r/ClaudeAI "How do you hand off from Claude chat to Claude Code?"** (53 comments) — friction Gluon's chat agent already addresses.
- **r/ClaudeAI 1stfy3v "Coordinate multiple Claude Code agents so they don't step on each other"** — `claudectl` shipped for exactly this. Demand signal.

### Competitive landscape (last 90 days on Product Hunt)

| Product | Positioning | Overlap with Gluon |
|---|---|---|
| KingCoding | "Your Claude Code & Codex dev, now in your pocket" | Direct — mobile-first remote control |
| claude-code-remote-access | Remote access to Claude Code | Direct |
| Claude Code Scheduled Tasks | Cron for Claude Code | Matches Gluon's planned heartbeats |
| Parallel Code | Parallel agent workflow | Multi-agent coordination |
| Claude Code /ultrareview | "Fleet of parallel agents" for code review | Subagent fleet packaging |
| Agent-Quest (GitHub, 45 upvotes) | Medieval-fantasy visualization | Observability theater |
| Claude Deck (GitHub) | Self-hosted Claude Code dashboard (FastAPI + React + SQLite) | Near-identical shape |
| The Autonomous Stack | "Production-tested architecture for autonomous Claude agents" | Reference architecture |
| Ocean Orchestrator | Agent orchestrator | Same category |
| Baton / SuperHQ | Agent management | Category adjacent |

**Verdict**: Crowded but fragmented. Nobody has all of Gluon's pieces
(multi-transport + self-hosted + Bedrock + session resume + worktree isolation).
Defensibility is *combination*, not any single primitive.

### What leaders converge on

From the Anthropic 2026 Agentic Coding Trends Report + industry chatter:

- Spec-driven multi-agent coordination (shared "living spec" prevents conflicting changes)
- Agent-reviewing-agent quality loops (verifier tier)
- Observability & replay — not "logs," but step-through-able traces
- Budget enforcement (not just tracking)
- Human-in-the-loop escalation on risky ops
- Model flexibility per task tier (Haiku for triage, Opus for architecture)
- Event-triggered autonomy (webhooks, cron, messaging platforms)

---

## 3. Proposed roadmap

Five themes, roughly in priority order. Each is shippable as its own arc.
Don't try to run all five in parallel.

### Theme A — Claim the "orchestrator" position (market, not code)

**Rationale**: The Anthropic Pro pricing shift is a gift. Every article
written this month about "Claude Code getting pricey" is a demand-gen moment
for a self-hosted Bedrock-backed alternative. The product is there; the
narrative isn't.

Actions:

- Landing page copy pivot: lead with Bedrock / self-hosted / BYO-key first, transports second
- Comparison matrix vs. Claude Deck, claudectl, KingCoding — own the "complete" quadrant
- One-command Docker quickstart video (pin it on the repo)
- Publish the session resume + worktree GC story — genuinely hard technical differentiators

**Effort**: days. **Leverage**: highest on this list.

---

### Theme B — Multi-agent coordination (the hot new category)

**Rationale**: Every third post on r/ClaudeAI is about agents stepping on
each other. `claudectl` shipped to scratch exactly this itch. Gluon already
has Workspace/Project/Session/Run primitives — missing piece is task-level
atomic checkout so two agents can't claim the same work.

Ship planned [Paperclip patterns](paperclip-patterns-implementation.md) in
adjusted sequence:

1. **Phase 1 — Agent identity** (1-2 weeks) — prerequisite for everything else
2. **Phase 3 — Task tracking with atomic checkout** (2-3 weeks) — prioritize over heartbeats; the hot market pain
3. **Phase 2 — Heartbeats** (2-3 weeks) — enables truly autonomous loops
4. **Phase 4 — Budget enforcement** (3-5 days) — tiny change, huge confidence lift

Layer on top:

- File-level locks during a task ("Agent A has lock on `src/api/*`") — what `claudectl` does
- Inter-agent messaging via comments table on tasks (lift into Phase 3)

**Effort**: 6-10 weeks for all four phases. **Leverage**: high.

---

### Theme C — Observability & replay (retain users)

**Rationale**: Agent-Quest got 45 upvotes for *visualizing agents walking
around a medieval village*. That's a signal — people cannot tell what their
agents are doing. Gluon has `messages.jsonl` per run but the viewer is
plain. Anthropic's trends report flags "agents spend 60% of their time on
context" — showing that breakdown is a feature.

Actions:

- Replay viewer — scrub a timeline of tool calls with timestamps and costs per step
- "Why did the agent do X?" — show the message chain leading to each tool call (SDK provides this; expose it)
- Per-run cost breakdown by tool category (aggregate existing tool logs)
- Session explorer from the [SDK 0.1.48 plan](sdk-0.1.48-feature-integration.md) — finally ship it; SDK is at 0.1.65 now so APIs are stable
- Session cleanup job — [session-management #1](../session-management-exploration.md) — JSONL files piling up

**Effort**: 4-6 weeks. **Leverage**: medium-high — deepens moat.

---

### Theme D — Trust & control (enable unsupervised runs)

**Rationale**: Users say they want to let agents run for hours; they don't
because they can't trust it. This turns "remote control from phone" into "I
went to dinner and came back to a merged PR."

Actions:

- Approval gates on risky tool calls (file deletes, `git push`, `npm publish`, `rm -rf`) — prompt via Telegram/Discord; resume on reply. Transports are already two-way.
- Budget enforcement (Phase 4 here if not shipped in Theme B) — hard stop at $X per run / $Y per day
- Cost & step hard-caps per run (`max_tokens`, `max_tool_calls`, `max_duration`) — surface in New Task dialog
- Finish witness nudge system (`witness.py`) — detection works; write the suggestion generator
- Multi-user auth + per-user budgets — opens up "Gluon on a team server"

**Effort**: 4-8 weeks. **Leverage**: high — unlocks the autonomous-use-case narrative.

---

### Theme E — Native mobile experience (defend the positioning) &nbsp;· &nbsp;**Open** ⚪

**Rationale**: KingCoding's tagline is "your Claude Code dev, now in your
pocket." They're coming for Gluon's Telegram-first story. Web dashboard is
responsive but not installable.

Actions:

- PWA manifest + service worker — web dashboard becomes installable. ~1 week.
- Push notifications via Telegram/Discord already work — formalize as first-class notification channels with preference UI
- Voice-to-prompt via Telegram voice notes → Whisper → new task (novel, sticky)
- *Optional/expensive*: native iOS/Android wrapper (React Native shell over existing web). Defer unless PWA metrics justify it.

**Effort**: 2-4 weeks for PWA; months for native. **Leverage**: medium.

---

## 4. What NOT to do

- **Don't add another LLM provider.** CLAUDE.md locks to four Bedrock models; that's a feature. Staying Claude-only keeps depth; every provider added dilutes.
- **Don't build a plugin/marketplace** unless >500 active deployments. Premature.
- **Don't ship more SDK upgrades without user-facing features attached.** 12 in a row; diminishing returns. Skip one cycle and ship from the plans directory.
- **Don't build approval gates in web dashboard first.** Build in Telegram first — that's the channel users are in when agents run unattended.
- **Don't rewrite storage.** Dolt is intriguing (tech radar) but SQLite is fine for now.

---

## 5. Suggested immediate next actions

This week:

1. Ship Phase 1 (Agent model) from the Paperclip plan — fully designed, unblocks everything else
2. Ship budget enforcement — ~20 lines, disproportionate "looks professional" value
3. Write the positioning blog post: *"Why we built Gluon: self-hosted Claude Code orchestration on AWS Bedrock"* — ride the Pro pricing news cycle
4. Add PWA manifest — one file, defensive against KingCoding

---

## Appendix — sources

**Reddit** (via `reddit-tracker` cache, synced 2026-04-24):

- r/ClaudeAI [1srzhd7](https://www.reddit.com/r/ClaudeAI/comments/1srzhd7) — Pro plan no longer lists Claude Code (2255 upvotes)
- r/microsaas [1ss6lqw](https://www.reddit.com/r/microsaas/comments/1ss6lqw) — "RIP Claude Code on Pro" (53 upvotes, 47 comments)
- r/ClaudeAI [1sthldc](https://www.reddit.com/r/ClaudeAI/comments/1sthldc) — Claude chat ↔ Claude Code handoff friction (53 comments)
- r/ClaudeAI [1stfy3v](https://www.reddit.com/r/ClaudeAI/comments/1stfy3v) — claudectl for multi-agent coordination
- r/LocalLLaMA [1stda86](https://www.reddit.com/r/LocalLLaMA/comments/1stda86) — Agent-Quest visualization (45 upvotes)
- r/AI_Agents [1srrh23](https://www.reddit.com/r/AI_Agents/comments/1srrh23) — "The Orchestrator Era"
- r/automation [1ssi7pq](https://www.reddit.com/r/automation/comments/1ssi7pq) — Orchestration vs. autonomy
- r/SideProject [1stsx94](https://www.reddit.com/r/SideProject/comments/1stsx94) — Claude Deck self-hosted dashboard
- r/SaaS [1st1g4f](https://www.reddit.com/r/SaaS/comments/1st1g4f) — passiveagents.com background execution

**Product Hunt** (via `producthunt-tracker` cache):

- [KingCoding](https://www.producthunt.com/products/kingcoding) — "Your Claude Code & Codex dev, now in your pocket"
- [claude-code-remote-access](https://www.producthunt.com/products/claude-code-remote-access)
- [Claude Code Scheduled Tasks](https://www.producthunt.com/products/claude-code-scheduled-tasks)
- [Claude Code /ultrareview](https://www.producthunt.com/products/claude-code) — fleet of parallel review agents
- [Parallel Code](https://www.producthunt.com/products/parallel-code)
- [The Autonomous Stack](https://www.producthunt.com/products/the-autonomous-stack)
- [claude-code-auto-fix-in-the-cloud](https://www.producthunt.com/products/claude-code-auto-fix-in-the-cloud)
- [Ocean Orchestrator](https://www.producthunt.com/products/ocean-orchestrator)
- [Baton](https://www.producthunt.com/products/baton-2)
- [SuperHQ](https://www.producthunt.com/products/superhq)
- [Agent Bar](https://www.producthunt.com/products/agent-bar)

**Industry reports**:

- Anthropic 2026 Agentic Coding Trends Report
- CB Insights Coding AI Market Share Report (Dec 2025)
- Intent / Augment Code "living spec" multi-agent pattern

---

# Part 2 — Implementation Detail (added 2026-04-24)

The original stock-take above made claims at the level of a 5-minute skim. A
deeper read of the code corrects several of those claims. This part documents
what's actually there and what concretely remains.

## 6. Gap re-audit (corrected)

### 6.1 Cost enforcement — partially exists, not absent

**Original claim**: "Cost tracking without enforcement — enforcement is 20 lines of code."

**Actual state**:

- Cost caps exist at three layers:
  - [`policies.py:109`](../../src/gluon/policies.py) — `PolicyContext.max_cost_usd` hard-blocks auto-resume when total spend reaches the cap
  - [`rate_limiter.py:46`](../../src/gluon/rate_limiter.py) — `RateLimiter` refuses further calls when cap reached (Ralph-loop mode)
  - [`agent.py:566`](../../src/gluon/agent.py) — `ClaudeAgentOptions.max_budget_usd` passed to the SDK, which enforces at the LLM call boundary
- `max_cost_usd` is a field on `ExecutionRun` ([`models.py:693`](../../src/gluon/models.py)), plumbed through CLI (`--max-cost`), web API (`RunCreateRequest.max_cost_usd`, override via `max_budget_override`), store migration, and rate limiter
- CLI `gluon runs` displays `$X / $Y cap` ([`cli.py:1930`](../../src/gluon/cli.py))

**What's still missing**:

1. **No daily/workspace/user-level rolling budget**. Caps are per-run only. You can start 100 runs at $5 each and spend $500 without anything stopping you.
2. **No default budget** — new runs have `max_cost_usd = None` unless explicitly set. A novice user with `--dangerously-skip-permissions` on autonomous loops has no safety net.
3. **No alerting when approaching cap** — hitting the cap hard-stops; nothing warns at 80%.

**Concrete plan**: folded into Theme D §9.2. Scope is two new aggregates (daily/workspace) and a default-cap setting, not "20 lines." Revised estimate: **3-5 days**.

### 6.2 Witness NUDGE — genuinely unimplemented

**Original claim**: "Witness 'nudge' suggestions incomplete."

**Actual state**: Confirmed. [`witness.py:195`](../../src/gluon/witness.py) logs `"Witness suggests nudge for run %s (not yet implemented)"` and returns. Only the LOOPING classification maps to NUDGE; RESTART and ESCALATE both work.

**Why NUDGE matters**: When the witness classifies a run as LOOPING (stuck in an error-retry cycle), restarting loses all progress. A nudge — injecting a course-correction message into the live session — preserves state and often breaks the loop at a fraction of the cost.

**Concrete plan**:

1. Add `runner.nudge(run_id: str, message: str) -> bool` method that pushes a message into the run's live message queue ([`runner.py`](../../src/gluon/runner.py) already has `_active_queues: dict[str, asyncio.Queue]` — the plumbing is there)
2. Add witness-specific nudge template:
   ```
   You appear to be stuck in a retry loop on {error_pattern}. Stop and re-plan:
   1. What is the error actually telling you?
   2. Is there a different approach that would sidestep this entirely?
   3. If unsure, write your uncertainty to a file and stop for review.
   ```
3. Wire into [`witness.py:194`](../../src/gluon/witness.py) `execute_action` — when action is NUDGE, call `runner.nudge(run.id, template)` and record the attempt in `WitnessDecision.metadata`
4. Add a cooldown so we don't nudge the same run repeatedly (15 min minimum between nudges per run)
5. Tests: `test_witness.py::test_nudge_injects_message` with a fake runner

**Effort**: 1-2 days. **Acceptance**: a run in LOOPING state receives a nudge message in its `messages.jsonl` within 30 seconds, and the run's `messages.jsonl` shows the injected prompt.

### 6.3 Activity log — already wired (I was wrong)

**Original claim**: "Activity log tables exist but aren't wired."

**Actual state**: Wired in five places:

- [`runner.py:687`](../../src/gluon/runner.py) — `task_started`
- [`runner.py:1383`](../../src/gluon/runner.py) — `task_completed` / `task_failed`
- [`health_monitor.py:126`](../../src/gluon/health_monitor.py) — health monitoring events
- [`chain_executor.py:98,140,191`](../../src/gluon/chain_executor.py) — chain lifecycle events
- [`cli.py:2937`](../../src/gluon/cli.py) — activity query command

Web API endpoints `/api/activity` and `/api/activity/cleanup` exist at [`api.py:3577`](../../src/gluon/web/api.py). `doctor.py` has a size check. Tests in [`test_activity_log.py`](../../tests/test_activity_log.py).

The [`doctor.py:182`](../../src/gluon/doctor.py) message "Activity log table not yet created" only triggers if the table is missing on a fresh install — it's a defensive fallback, not an "unwired" indicator. I read this wrong.

**What's genuinely missing**: more event types. Current events are almost entirely `task_*` lifecycle. A richer stream would include: `question_raised`, `question_answered`, `pr_opened`, `pr_merged`, `merge_conflict_detected`, `budget_warning`. These would make the `/api/activity` feed useful as an audit log.

**Concrete plan**: tag existing emit points with more event types. ~1 day of labeling work, no architecture.

### 6.4 Work queue — wired but invisible

**Original claim**: "Work queue tables exist but aren't wired."

**Actual state**: [`work_queue.py`](../../src/gluon/work_queue.py) is a full `WorkQueueManager` with atomic claim, release, cancel, stale-claim cleanup. It's called from [`runner.py:1396`](../../src/gluon/runner.py) as a **self-propelling queue**: when a run completes, it claims the next queued item and dispatches it. Web API endpoints `/api/queue` (list), `POST /api/queue` (enqueue), `/api/queue/{id}/cancel`, `/api/queue/{id}/release` exist at [`api.py:3617-3729`](../../src/gluon/web/api.py). Store has atomic `claim_work` at [`store.py:3622`](../../src/gluon/store.py).

**What's genuinely missing**:

1. **No CLI command** — `gluon queue list|enqueue|cancel` doesn't exist. Users can only interact via web API.
2. **No UI in the web dashboard** — the endpoints exist but there's no page showing the queue.
3. **No scheduler / heartbeat to drain the queue** — today the queue only drains when *another run completes*. If the queue has items but nothing is running, they sit forever.
4. **No worker assignment** — every queue item goes to the same project's next idle slot. Can't route items to a specific agent/worker (Theme B prerequisite).
5. **No tests for the self-propelling path** — [`test_work_queue.py`](../../tests/test_work_queue.py) covers store CRUD but not the runner.py:1396 dispatch.

**Concrete plan**:

1. Add CLI: `gluon queue list|enqueue|cancel|release` — mirrors the web API. ~4 hours.
2. Add a web UI page `/queue` with the existing endpoints. ~1 day.
3. Fix the "queue drains only on completion" bug: add a periodic drain (every 60s) in `TaskRunner` that calls `claim_next` if `_active_tasks` has capacity. ~2 hours.
4. Test coverage for the dispatch path. ~4 hours.

**Effort**: 2-3 days. This is essentially free — the hard part is already built.

### 6.5 Web API test coverage — actually OK, but gaps in risky areas

**Original claim**: "Web API has ~60 endpoints but minimal integration test coverage."

**Actual state**: 108 endpoints, 8 `test_api_*.py` files, 2,556 lines of API tests. Covered: runs (core), projects, workspaces, files, notifications, queued messages, SDK sessions, stop reason. That's not "minimal."

**What's genuinely uncovered** (in rough order of risk):

- **Git / PR operations**: `/api/projects/*/rebase/*`, `/api/projects/*/force-push`, `/api/runs/*/create-pr`, `/api/runs/*/merge`, `/api/projects/*/branches/*` — destructive, no integration tests
- **Conflict resolution**: `/api/projects/*/conflicts/*` — user-driven merge resolution, no tests
- **Webhooks**: `/api/webhooks/github` (the GitHub webhook receiver) — security-sensitive, untested at API layer
- **Supervision**: `/api/runs/*/supervision/*` — policy evaluation, untested
- **Work queue API**: `/api/queue/*` — enqueue/cancel/release (see 6.4)
- **Activity API**: `/api/activity`, `/api/activity/cleanup`
- **Image uploads**: `/api/images/*`, `/api/runs/*/attachments` — binary handling, worth testing
- **Formulas**: `/api/formulas/*/run` — formula execution

**Concrete plan**: prioritize by blast-radius.

1. **Tier 1** (destructive, security-relevant) — one test file each:
   - `test_api_git_operations.py` — rebase, force-push, branch ops (happy path + auth check + confirmation flows)
   - `test_api_pr_operations.py` — create-pr, merge, pr-status
   - `test_api_webhooks.py` — GitHub webhook signature verification, event parsing
   - `test_api_conflicts.py` — conflict detection, diff, resolve
2. **Tier 2** (important but less risky):
   - `test_api_supervision.py`, `test_api_work_queue.py`, `test_api_activity.py`
3. **Tier 3** (polish):
   - `test_api_images.py`, `test_api_formulas.py`

**Effort**: 1 week for Tier 1, 3 days for Tier 2, 2 days for Tier 3. Total ≈ 2 weeks.

**Recommended**: ship Tier 1 alongside Theme B work — multi-agent coordination will exercise all of these paths and regressions are more likely.

### 6.6 Multi-agent coordination — confirmed absent

**Original claim**: "No multi-agent coordination primitive — each run is an island."

**Actual state**: Confirmed. There is no `Agent` model (only `Worker` as an execution target). There is no task-level lock, no cross-run file lock, no inter-run message channel. The existing `work_queue` is project-level, not agent-level. This is the scope of Theme B §8.

---

## 7. Theme A — Positioning (detailed)

Goal: turn the Anthropic pricing shift into measurable demand for Gluon. Zero
code. This is a one-week sprint of content and site work.

### Deliverable A1 — Landing page rewrite &nbsp;· &nbsp;**Partial** 🟡

README hero rewritten to lead with "self-hosted Claude Code orchestrator, bring your own backend" and multi-provider comparison table added (#69, #70). Landing page itself not yet produced.
- New hero copy leading with **self-hosted / Bedrock / BYO-key**, not transports
- Add a "Why Gluon" section with three pillars:
  1. **Own your agents** — Docker compose, your infrastructure, your data
  2. **Runs on AWS Bedrock** — decoupled from Anthropic subscription pricing
  3. **Control from anywhere** — Telegram, Discord, web, CLI
- New sub-hero block: "**Claude Code just got pricier. Your orchestration shouldn't.**"
- Cross-links to the comparison matrix (A2)

**Files**: wherever the landing page lives (likely external to this repo — check carrotly-ai org). If none, create `README.md` marketing section.

**Effort**: 1 day.

### Deliverable A2 — Comparison matrix &nbsp;· &nbsp;**Partial** 🟡

A small "Gluon vs other Claude Code orchestrators" table is in the README. A dedicated public comparison matrix (outside the README) is still open.
Table comparing Gluon against the five closest competitors:

|  | Gluon | Claude Deck | claudectl | KingCoding | claude-code-remote-access |
|---|---|---|---|---|---|
| Self-hosted | Yes | Yes | Yes | ? | ? |
| Bedrock-backed | Yes | No | No | No | No |
| Multi-transport | CLI+TG+Discord+Web | Web | CLI | Mobile | Mobile |
| Session resume | Fork + worktree | ? | ? | ? | ? |
| Background exec | Yes | ? | ? | ? | ? |
| Multi-agent coord | *Theme B* | No | Yes (basic) | No | No |
| Docker one-liner | Yes | Yes | No | — | — |
| Open source | Yes | Yes | Yes | No | No |

(Research the `?` cells by reading each project's README; don't assume.)

**Files**: `README.md` in this repo, mirrored to landing page.

**Effort**: 1 day.

### Deliverable A3 — 30-second Docker quickstart video &nbsp;· &nbsp;**Open** ⚪
- Record terminal cast: `docker compose up -d`, then `gluon status`, then run a task via Telegram
- Keep it under 30s — no voice-over, captions only
- Host on YouTube unlisted, embed on landing page and pin to GitHub README
- Second, longer version (2 min) for Product Hunt launch when ready

**Files**: `docs/SCREENSHOTS.md` already has stills; add `docs/VIDEOS.md` with links.

**Effort**: 1 day (including reshoots).

### Deliverable A4 — "Why we built Gluon" blog post &nbsp;· &nbsp;**Open** ⚪

`docs/blog/2026-04-self-hosted-claude-code.md` exists as raw material; not yet published as a blog post.
- Angle: the Pro pricing change creates a category of self-hosted orchestrators, here's what one looks like
- Cover technical moats: session resume with `fork_session`, worktree GC, witness auto-recovery
- Link to docs/ARCHITECTURE.md for deep readers
- Target: 1200-1500 words, code screenshots, one diagram
- Post on: Medium/Dev.to + submit to HN + r/ClaudeAI + r/SaaS

**Files**: `docs/blog/2026-04-self-hosted-claude-code.md` in repo, mirrored to external platform.

**Effort**: 1-2 days writing + review cycle.

### Deliverable A5 — Reddit presence &nbsp;· &nbsp;**Open** ⚪
- Don't spam. Pick three threads where Gluon genuinely answers the question:
  - [r/ClaudeAI 1sthldc](https://www.reddit.com/r/ClaudeAI/comments/1sthldc) — Claude chat ↔ Code handoff (Gluon's chat agent solves this)
  - [r/microsaas 1ss6lqw](https://www.reddit.com/r/microsaas/comments/1ss6lqw) — "RIP Claude Code on Pro" (direct pricing relevance)
  - [r/ClaudeAI 1stfy3v](https://www.reddit.com/r/ClaudeAI/comments/1stfy3v) — multi-agent coordination (acknowledge claudectl, note Gluon's different shape)
- Comment with specific, technical answers. Link only if the thread asks for alternatives.

**Effort**: 2 hours.

### Theme A schedule

| Day | Deliverable |
|---|---|
| Mon | A1 landing copy draft |
| Tue | A2 comparison matrix (research + write) |
| Wed | A3 video + A5 Reddit comments |
| Thu | A4 blog post draft |
| Fri | Polish, publish, submit |

**Success metric (two-week watch)**: stargazer growth, `docker compose pull` counts if you have GHCR telemetry, tracked referrals from HN/Reddit/Medium.

---

## 8. Theme B — Multi-agent coordination (detailed)

Goal: let two or more agents work on the same codebase without stepping on
each other. This is the largest theme (6-10 weeks) and the one with the most
market pull.

### Architectural decisions before coding

1. **What is an Agent?** — minimal identity: `id`, `workspace_id`, `name`, `role`, `is_active`, `monthly_budget_usd`, `max_concurrent_runs`. No org chart, no supervisor hierarchy. Detailed design in [paperclip-patterns-implementation.md §Phase 1](paperclip-patterns-implementation.md).
2. **What is a Task?** — project-scoped, atomically checkout-able, workflow states: `backlog → assigned → in_progress → review → done | cancelled`. Detailed design in [paperclip-patterns-implementation.md §Phase 3](paperclip-patterns-implementation.md).
3. **Does an ExecutionRun need an Agent?** — yes, but optional (nullable FK). Preserves backward compatibility for single-agent installs.
4. **How do agents claim tasks?** — DB-level atomic update with a lock TTL. If the lock is held >1h with no progress, it auto-releases. This is what [`work_queue.py`](../../src/gluon/work_queue.py) already does for work items; lift the pattern into tasks.
5. **File-level locks?** — *Don't build these for v1*. Defer to v2 after we see real conflicts. Task-level coordination is the MVP.

### Phase B1 — Agent identity (Week 1-2) &nbsp;· &nbsp;**Shipped** ✅

Shipped as part of the roadmap sprint (#42, #55). `Agent` model + workspace budgets + `gluon agent` CLI.

Follow [paperclip-patterns-implementation.md §Phase 1](paperclip-patterns-implementation.md) verbatim. Key deliverables:

- `Agent` model in [`models.py`](../../src/gluon/models.py)
- Schema migration: `agents` table with unique `(workspace_id, name)`
- Store CRUD: `create_agent`, `get_agent`, `get_agent_by_name`, `list_agents`, `update_agent`, `delete_agent`
- `ExecutionRun.agent_id` FK (nullable)
- CLI: `gluon agent list|create|show|update|delete`
- Web API: `GET/POST/PUT/DELETE /api/agents`
- Web UI: simple Agents page in dashboard
- When workspace has exactly one agent, auto-link new runs; multiple agents require `--agent NAME`

**Acceptance**: `gluon agent create myws researcher --budget 50` works; `gluon run myproj 'fix bug' --agent researcher` links the run; `gluon agent show researcher` shows spend + run count.

**Tests**: `test_agent_model.py`, extend `test_core.py` for agent-linked execution.

### Phase B2 — Task tracking with atomic checkout (Week 3-5) &nbsp;· &nbsp;**Shipped** ✅

Shipped as #56: `OrchestratorTask` model + `checkout_task()` with `BEGIN IMMEDIATE` transaction + `gluon task` CLI + `/api/tasks` endpoints (#62).

Follow [paperclip-patterns-implementation.md §Phase 3](paperclip-patterns-implementation.md). Key adjustments from the plan:

- **Lift task comments into v1** (the plan defers them). A comments table at task creation is cheap and makes the feature feel alive. Agents can post "blocked on X" without creating new tasks.
- **Add `assigned_files: list[str]` to Task** — scope declaration. Even without enforcement, *declaring* which files a task touches lets us detect conflicts between concurrent tasks.

Deliverables:

- `OrchestratorTask` and `TaskComment` models
- `orchestrator_tasks` and `task_comments` tables with indexes
- `store.checkout_task` (atomic), `store.release_task`, `store.get_agent_inbox`
- CLI: `gluon task create|list|show|assign|done|cancel|inbox|comment`
- Web API: full CRUD at `/api/tasks/*`
- Web UI: task board view (kanban) in dashboard
- Heartbeat prompt includes task inbox context (from B3)
- **New**: `gluon task conflicts <project>` — lists tasks whose `assigned_files` overlap and are both in_progress

**Acceptance**: two CLI sessions in parallel both try `gluon task checkout TASK-123` — exactly one succeeds, the other gets `TaskLockedError`. Lock auto-expires after 1h.

**Tests**: `test_tasks.py`, `test_task_concurrency.py` with two processes contending for the same task.

### Phase B3 — Heartbeats (Week 6-8) &nbsp;· &nbsp;**Shipped** ✅

Shipped as #57: `AgentSchedule` + `HeartbeatRun` models + `HeartbeatScheduler` with croniter + coalesce policy + `gluon schedule` and `gluon heartbeat` CLIs.

Follow [paperclip-patterns-implementation.md §Phase 2](paperclip-patterns-implementation.md). Use `croniter` (pure-Python, ~30KB) for cron parsing.

Deliverables:

- `AgentSchedule` and `HeartbeatRun` models
- `scheduler.py` with `HeartbeatScheduler` class (asyncio-based, no APScheduler)
- Coalesce logic: skip if agent already has a heartbeat run within `coalesce_ttl_seconds`
- Circuit breaker: 3 consecutive failures disable the schedule; manual re-enable
- Cheap model for heartbeat check — Haiku via `TaskProfile.QUICK`
- CLI: `gluon schedule create|list|enable|disable|delete`, `gluon heartbeat list|fire`, `gluon scheduler start`
- Web UI: Schedules page in dashboard
- Integrate with `gluon serve` / `gluon bot` lifecycle

**Acceptance**: `gluon schedule create researcher --cron "*/30 * * * *" --prompt "Check inbox and work on highest-priority task"` — the scheduler fires every 30 min, coalesces duplicates, and runs use Haiku.

**Tests**: `test_scheduler.py` (cron parsing, coalesce, circuit breaker), `test_heartbeat_integration.py`.

### Phase B4 — Budget enforcement (Week 9, 3-5 days) &nbsp;· &nbsp;**Shipped** ✅

Shipped as #55 (agent-level monthly budgets) + #63 (workspace-level daily/monthly rolling budgets). `BudgetExceededError` + `WorkspaceBudgetExceededError`.

Trivial once Agent model exists. Follow [paperclip-patterns-implementation.md §Phase 4](paperclip-patterns-implementation.md).

Deliverables:

- `store.get_agent_monthly_spend(agent_id, month_start)` — sum over `execution_runs.cost_usd` WHERE `agent_id = ?` AND `created_at >= ?`
- Enforcement in `Orchestrator.execute()` — raises `BudgetExceededError` before run start
- `gluon agent show` displays spend vs budget with % bar
- Soft warning at 80% budget
- Web API: `/api/agents/{id}/usage` with monthly breakdown

**Acceptance**: agent with `--budget 50` and $49.95 spent this month; next run that would cost >$0.05 is rejected with clear message.

**Tests**: `test_budget.py` (edge cases: no budget, zero budget, exactly at limit, month boundary).

### Theme B total: 9 weeks

Aggressive but doable. Can compress to 7 weeks by parallelizing B2 (tasks) with B3 (heartbeats) after B1 ships — they depend on the Agent model but not on each other.

### What NOT to build in Theme B

- **Org chart / supervisor hierarchy** — not needed. `supervisor_agent_id` can stay off the schema until someone asks for it.
- **Approval gates on agent actions** — that's Theme D.
- **Full issue tracker with labels, milestones, epics** — you're not building Linear. Title + description + priority + status + comments is sufficient.
- **Cross-workspace agents** — an agent belongs to one workspace. Simpler.

---

## 9. Theme C — Observability & replay (detailed)

Goal: users can understand, trust, and debug what their agents did.

### Deliverable C1 — Replay viewer (Week 1-2) &nbsp;· &nbsp;**Shipped** ✅

Shipped as #76 (MVP) + #77 (keyboard shortcuts). New "Timeline" tab on Run Detail dialog with horizontal dot strip, hover tooltip, click-to-focus detail card, prev/next buttons, and `←`/`→`/`Home`/`End` shortcuts. Client-side parse of `messages.jsonl` — no backend changes.

Today the web UI streams logs live but has no scrub-backwards timeline.

- New route `/runs/{run_id}/replay` in the dashboard
- Renders `messages.jsonl` as a horizontal timeline: each tool call is a dot colored by tool type (Read/Write/Bash/etc)
- Hover a dot to see tool input/output preview
- Click to expand inline
- Playback controls: jump to start, jump to end, per-step forward
- Show cumulative cost as a second axis (so you can see expensive moments)

**Files**:
- Frontend: new `ReplayTimeline.tsx` component
- Backend: no change (endpoints already exist at `/api/runs/{run_id}/logs` and messages.jsonl)

**Acceptance**: on a completed run, user scrubs the timeline, sees each tool call with timing and cost.

**Effort**: 1-2 weeks.

### Deliverable C2 — "Why did the agent do X?" (Week 3) &nbsp;· &nbsp;**Shipped** ✅

Shipped as #75. Rolling assistant reasoning/thinking attached to each `tool_use`'s `metadata.reasoning` during the `allMessages` post-processing step in `StreamingLogViewer`. Expanded tool cards gain a "Reasoning" section; collapsed cards get a lightbulb indicator.

The SDK emits assistant reasoning blocks; they're in messages.jsonl but the viewer doesn't surface them adjacent to tool calls.

- Thread the "thinking" block (type `reasoning`) preceding each tool_use block into that tool's hover card
- Add a "Reasoning" tab on each tool call showing what the agent said just before deciding to call it
- Highlight the specific sentence that introduces the tool call (often "I'll read X to understand Y")

**Files**: `useRunLogStream.ts` (thread reasoning to adjacent tool_use), `ToolCallMessage.tsx` (display).

**Acceptance**: open a tool call; the "Reasoning" tab shows the 2-3 sentence rationale from the adjacent assistant message.

**Effort**: 3-5 days.

### Deliverable C3 — Per-run cost breakdown by tool (Week 3) &nbsp;· &nbsp;**Shipped (scope-honest)** ✅

Shipped as #74. New "Tools" tab on Run Detail dialog. **Scope-honest:** the SDK doesn't attribute `$` per tool call, so the tab shows frequency + percentage + timing rather than faking dollar attribution. The footnote explains why. Reuses C3's color palette across tabs.

Already have tool call logs and cost totals. Aggregate.

- On the run detail page, add a pie/bar chart: cost by tool category (Read, Write, Bash, WebFetch, MCP tools, thinking)
- Also: total calls by tool
- Top-5 most expensive tool calls with one-click expand

**Files**: backend aggregation in `/api/runs/{run_id}/cost-breakdown` (new), frontend chart component.

**Acceptance**: a run that spent $1.23 shows the breakdown by tool with percentages summing to 100%.

**Effort**: 2-3 days.

### Deliverable C4 — Session explorer (Week 4) &nbsp;· &nbsp;**Shipped** ✅

Shipped as #66. `GET /api/projects/{id}/claude-sessions` list + show + messages endpoints, plus `gluon claude-sessions` CLI and chat-agent MCP tools.

Follow [`sdk-0.1.48-feature-integration.md` §Feature 2](sdk-0.1.48-feature-integration.md). SDK is at 0.1.65; the APIs (`list_sessions`, `get_session_messages`, `get_session_info`) are stable and available.

Deliverables:

- `GET /api/projects/{id}/claude-sessions` — list all Claude sessions in project dir
- `GET /api/projects/{id}/claude-sessions/{sid}/messages` — read conversation
- Session Explorer panel on project detail page
- Shows sessions from *direct* CLI usage (not spawned via Gluon) — users can see "what did I do yesterday in this project"

**Effort**: 3-5 days (well-designed already, just ship).

### Deliverable C5 — Session cleanup (Week 5) &nbsp;· &nbsp;**Shipped** ✅

Shipped as #61. Opt-in session cleanup on run completion, orphan-session sweep, `gluon sessions-cleanup` CLI with `--dry-run`, doctor check for session disk usage.

Follow [`session-management-exploration.md` §1](../session-management-exploration.md). Critical: long-running deployments will exhaust disk without this.

Deliverables:

- Setting: `session_cleanup_enabled: bool = False` (default off; opt-in)
- Setting: `session_cleanup_keep_latest: int = 5` (keep N most recent forks per claude_session_id)
- On run COMPLETED status, call `delete_session()` for older fork IDs tracked in `run.metadata.previous_session_ids`
- CLI: `gluon sessions cleanup [--dry-run] [--project NAME] [--older-than-days N]`
- Doctor check: `check_session_file_disk_usage` — warn at >10GB

**Acceptance**: after a run with 10 resumes on a completed task, 5 of the 10 fork JSONLs are deleted from `~/.claude/projects/`, the latest 5 remain, and the original session is never touched.

**Effort**: 3-5 days.

### Theme C total: 5 weeks

### Stretch (defer for now)

- **Replay with git diff at each tool call** — show the working-directory state evolving alongside tool calls. Cool, expensive, not v1.
- **Public shareable replay links** — "share this debug view with a teammate." Needs auth first.
- **Visual agent activity view** (Agent-Quest-style) — fun but a distraction. Come back to it once metrics show users want theater over data.

---

## 10. Theme D — Trust & control (detailed)

Goal: convert "I have to watch it" runs into "I can go to dinner" runs.

### Deliverable D1 — Approval gates on risky tool calls (Week 1-3) &nbsp;· &nbsp;**Shipped** ✅

Shipped as #58 (core) + #59 (Telegram approve/deny buttons) + #60 (Discord persistent-view buttons). `PendingApproval` model, `classify_tool_call()` with 25+ destructive patterns, PreToolUse hook, `ApprovalWatcher` polling loop with `ApprovalPoster` Protocol across transports.

The blocking concept: before certain tool calls execute, pause and ask the user. Telegram/Discord/web prompt the user; reply resumes. This is a PreToolUse hook pattern.

Scope (exactly one opt-in policy per run):

- **`permissive`** (default, existing behavior) — no gates
- **`careful`** — gate destructive operations: `rm -rf`, `git push --force`, file deletes, `npm publish`, `gh pr merge`, anything with `--force`
- **`paranoid`** — gate *all* writes and any Bash command

Design:

1. Add `approval_policy: str = "permissive"` to `ExecutionRun`
2. Register PreToolUse hook in [`agent_hooks.py`](../../src/gluon/agent_hooks.py) that:
   - Classifies the tool call (safe | careful | destructive)
   - If policy requires gating, pauses execution by creating a `PendingApproval` record and returning a deny + `{"systemMessage": "Approval pending, ping sent"}`
   - Posts the approval request to the run's notifier (Telegram/Discord/web push)
3. Approval endpoint `POST /api/approvals/{id}/grant|deny` resumes the run via the existing queued-followup pattern
4. Telegram/Discord buttons: "✅ Approve", "❌ Deny", "⏸ Pause for review"

**Acceptance**: a `careful`-policy run tries to `git push --force` → Telegram message with buttons → click Approve → push happens. Click Deny → agent receives "User denied the push; propose an alternative."

**Effort**: 2-3 weeks. The plumbing is non-trivial: PreToolUse hook + pause state + resume flow + three transport UIs.

**File pointers**: [`agent_hooks.py`](../../src/gluon/agent_hooks.py), [`bot_core.py`](../../src/gluon/bot_core.py), [`transport/telegram.py`](../../src/gluon/transport/telegram.py), [`transport/discord.py`](../../src/gluon/transport/discord.py), [`web/api.py`](../../src/gluon/web/api.py).

### Deliverable D2 — Daily/workspace budget caps (Week 3, 3-5 days) &nbsp;· &nbsp;**Shipped** ✅

Shipped as #63. `Workspace.daily_budget_usd` + `monthly_budget_usd`, `get_workspace_spend_since()` rolling aggregation, `_enforce_workspace_budget()` in the orchestrator, `gluon workspace set-budget` CLI.

See §6.1 for full rationale. This is the corrected "20-line fix."

- `Workspace.daily_budget_usd: float | None`
- `Workspace.monthly_budget_usd: float | None`
- Global defaults in settings: `default_run_max_cost_usd`, `default_daily_budget_usd`
- `store.get_workspace_spend_since(ws_id, since)` aggregation
- Enforcement in `Orchestrator.execute()` before spawning: check per-run cap AND workspace daily AND workspace monthly
- CLI: `gluon workspace budget <name> --daily 50 --monthly 1000`
- Web UI: budget section in workspace settings
- Notification: soft warning at 80% daily, hard stop at 100%

**Effort**: 3-5 days.

### Deliverable D3 — Hard step/time caps per run (Week 4, 2-3 days) &nbsp;· &nbsp;**Shipped** ✅

Shipped as #64. `max_tool_calls` and `max_duration_minutes` on `ExecutionRun`, PreToolUse hook that denies at cap, duration watchdog in `runner.py`, `--max-tool-calls` + `--max-duration` CLI flags.

Leverage existing SDK options; surface in UI:

- `max_tokens` (SDK `task_budget` token cap)
- `max_tool_calls` — new, needs PreToolUse hook counter
- `max_duration_minutes` — new, runner-enforced timeout

Surface all three in:

- CLI: `gluon run ... --max-tokens 500000 --max-tool-calls 200 --max-duration 60`
- Web UI: "New Task" advanced section
- Web API: `RunCreateRequest.max_tool_calls`, `max_duration_minutes`

**Effort**: 2-3 days.

### Deliverable D4 — Witness NUDGE implementation (Week 4, 1-2 days) &nbsp;· &nbsp;**Shipped** ✅

Shipped as part of #42 (roadmap sprint). `_send_nudge` injects `LOOPING_NUDGE_PROMPT` into the agent's follow-up queue with a 900s cooldown guard.

See §6.2. Wire the NUDGE action into a live message injection. Write a template library: LOOPING → "stop and re-plan" prompt.

### Deliverable D5 — Multi-user auth (Week 5-8, the big one) &nbsp;· &nbsp;**Open** ⚪

Unchanged from initial plan. Needs a design pass before implementation.

This is the largest unshipped capability. Keep it scoped.

**Scope for v1**:

- Self-hosted auth only. No SaaS, no SSO, no OAuth providers.
- Single auth method: **OIDC / JWT with an external identity provider**. User brings their own (Keycloak, Cognito, Auth0, GitHub OAuth) — Gluon just validates the JWT.
- **Fallback**: basic username/password with bcrypt in SQLite, for no-IDP installs.
- Three roles: `admin` (full), `operator` (create/run/cancel), `viewer` (read-only)
- Per-user budget caps (like per-agent, but scoped to the authenticated user)
- Per-user API keys (for CLI/API access when JWT isn't available)
- Every `ExecutionRun.created_by_user_id` nullable FK

**What does NOT ship in v1**:

- Row-level security ("user A can only see their own runs")
- Fine-grained permissions (tool-level allowlists)
- Organization/team model (that's Workspaces today)
- SAML, LDAP
- Audit logs beyond what the activity log already provides

Deliverables:

- `User` and `ApiKey` models + tables
- `auth.py` module with JWT verification + basic auth
- Middleware on every `/api/*` endpoint
- Login UI in web dashboard
- CLI: `gluon auth login|logout|whoami`, `gluon user list|create|delete`, `gluon apikey create|list|revoke`
- Transport auth: Telegram/Discord bind to a User via invite code

**Acceptance**: fresh `docker compose up`, first user prompt to create admin, subsequent users via invite, all actions logged with user identity.

**Effort**: 3-4 weeks. This is the rate-limiting step for Theme D.

### Theme D total: 8 weeks

### Theme D sequencing

D4 (witness nudge) is tiny; do it alongside D2 for an easy early win. D1 (approval gates) is the user-visible hero feature — ship first. D5 (auth) is last because it's the biggest and doesn't depend on others.

| Week | Deliverables |
|---|---|
| 1-3 | D1 approval gates (Telegram first, then web, then Discord) |
| 3 | D2 budgets + D4 nudge |
| 4 | D3 hard caps + polish |
| 5-8 | D5 auth + per-user budgets |

---

## 11. Combined timeline view

Running all four themes sequentially (no parallelization):

| Weeks | Theme | Headline deliverable |
|---|---|---|
| 0-1 | A | Positioning / pricing-shift content sprint |
| 2-3 | Gap fixes (§6) | Witness nudge, queue CLI, activity events, tier-1 API tests |
| 4-12 | B | Multi-agent coordination (Agent → Tasks → Heartbeats → Budgets) |
| 13-17 | C | Replay, reasoning, session explorer, cleanup |
| 18-25 | D | Approval gates, auth, hard caps |

That's roughly **6 months of sequenced work**. Some streams can run in parallel:

- A can happen *right now* while B1 (Agent model) is being built
- D1 (approval gates) could start in Week 8 alongside B3 (heartbeats) — different subsystems
- Gap fixes (§6) are short and parallelizable with everything else

Realistic calendar: **3-4 months** if you pick carefully, **6 months** to do it all linearly.

## 12. Single-week recommended next sprint

If this week is all you've got:

1. **Mon**: Theme A1 + A2 (landing copy + comparison matrix)
2. **Tue**: Gap 6.2 (witness NUDGE implementation)
3. **Wed**: Gap 6.4.1 (`gluon queue` CLI) + Gap 6.4.3 (periodic queue drain)
4. **Thu**: Gap 6.1 full scope (daily + workspace budgets, default cap setting)
5. **Fri**: Theme A4 blog post + Theme A5 Reddit comments

That lands *visible* improvements across positioning, safety, and operational
polish in five days, without starting anything multi-week that might stall.
Then Week 2 you start Theme B Phase 1 with a clean slate.

