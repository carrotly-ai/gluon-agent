# Ralph Loop Implementation Plan for Gluon Agent

## Executive Summary

This document analyzes how to apply the "Ralph Loop" autonomous execution pattern to Gluon Agent. After deep analysis of both the [ralph-claude-code](https://github.com/frankbria/ralph-claude-code) implementation and Gluon's current architecture, we find that **Gluon already has most Ralph components implemented** but they are not fully integrated or exposed via CLI/API.

**Current State**: Gluon has CircuitBreaker, CompletionDetector, RateLimiter, RalphManager, and ResumeCoordinator - all the building blocks exist.

**Gap**: These components are not wired together into a cohesive "keep working until complete" loop accessible from CLI, and the supervisor (ResumeCoordinator) is not running by default.

---

## Table of Contents

1. [Ralph Loop Philosophy](#1-ralph-loop-philosophy)
2. [Architecture Comparison](#2-architecture-comparison)
3. [Current Gluon State Analysis](#3-current-gluon-state-analysis)
4. [Gap Analysis](#4-gap-analysis)
5. [Implementation Options](#5-implementation-options)
6. [Recommended Approach](#6-recommended-approach)
7. [Impact Analysis](#7-impact-analysis)
8. [Implementation Phases](#8-implementation-phases)
9. [Risk Assessment](#9-risk-assessment)

---

## 1. Ralph Loop Philosophy

From the article and reference implementation, the Ralph Loop philosophy is:

> "Better to fail predictably than succeed unpredictably."
> — Geoffrey Huntley

**Core Principles:**
1. **Continuous Execution**: Claude works until done (or stopped)
2. **Failure as Data**: Each failed iteration refines the approach
3. **Clear Success Criteria**: Define "done" precisely upfront
4. **Safety Controls**: Circuit breakers, rate limits, cost caps prevent runaway execution

**The fundamental loop:**
```bash
while :; do cat PROMPT.md | claude ; done
```

**What makes it effective:**
- Each iteration sees modified files and git history from previous runs
- Claude can self-correct based on test failures and errors
- Human defines success criteria, agent iterates toward them
- Progress persists via filesystem and git

---

## 2. Architecture Comparison

### 2.1 Ralph-Claude-Code (Reference Implementation)

```
┌─────────────────────────────────────────────────────────────┐
│                    ralph_loop.sh                             │
│  ┌─────────────┬──────────────┬──────────────┬────────────┐ │
│  │ Rate Limiter│Circuit Breaker│Exit Detection│ Tmux UI   │ │
│  └─────────────┴──────────────┴──────────────┴────────────┘ │
│                          │                                   │
│                          ▼                                   │
│              ┌───────────────────────┐                       │
│              │  Claude Code CLI      │                       │
│              │  (stdin: PROMPT.md)   │                       │
│              └───────────────────────┘                       │
│                          │                                   │
│                          ▼                                   │
│              ┌───────────────────────┐                       │
│              │  Response Analyzer    │                       │
│              │  (RALPH_STATUS block) │                       │
│              └───────────────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

**Key Characteristics:**
- Single bash script orchestrator
- Synchronous loop (waits for Claude, analyzes, loops)
- Session continuity via `--continue` flag
- RALPH_STATUS block protocol for completion signaling
- Tmux dashboard for monitoring

### 2.2 Gluon Agent (Current State)

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Gluon Architecture                          │
│                                                                     │
│  ┌────────────┐    ┌──────────────┐    ┌─────────────────────────┐ │
│  │ CLI        │    │ Web API      │    │ Bot (Telegram/Discord)  │ │
│  └─────┬──────┘    └──────┬───────┘    └───────────┬─────────────┘ │
│        │                  │                        │                │
│        └──────────────────┼────────────────────────┘                │
│                           ▼                                         │
│                 ┌──────────────────┐                                │
│                 │   TaskRunner     │                                │
│                 │  (Background)    │                                │
│                 └────────┬─────────┘                                │
│                          │                                          │
│      ┌───────────────────┼───────────────────┐                     │
│      ▼                   ▼                   ▼                     │
│ ┌──────────┐      ┌─────────────┐    ┌────────────────┐            │
│ │ GluonAgent│      │RalphManager │    │ResumeCoordinator│           │
│ │(SDK wrap) │      │(loop orch)  │    │(auto-resume)    │           │
│ └──────────┘      └──────┬──────┘    └────────────────┘            │
│                          │                                          │
│      ┌───────────────────┼───────────────────┐                     │
│      ▼                   ▼                   ▼                     │
│ ┌──────────┐      ┌─────────────┐    ┌────────────────┐            │
│ │CircuitBrkr│      │CompletionDet│    │  RateLimiter   │           │
│ └──────────┘      └─────────────┘    └────────────────┘            │
│                                                                     │
│                    ┌─────────────┐                                  │
│                    │   Store     │                                  │
│                    │  (SQLite)   │                                  │
│                    └─────────────┘                                  │
└─────────────────────────────────────────────────────────────────────┘
```

**Key Characteristics:**
- Python-based, async architecture
- Multiple entry points (CLI, Web, Bots)
- Components exist but integration is incomplete
- Session resume via Claude SDK `fork_session`
- State persisted in SQLite database

---

## 3. Current Gluon State Analysis

### 3.1 What Already Exists

| Component | File | Status | Notes |
|-----------|------|--------|-------|
| Circuit Breaker | `circuit_breaker.py` | ✅ Complete | CLOSED/HALF_OPEN/OPEN states |
| Completion Detector | `completion_detector.py` | ✅ Complete | RALPH_STATUS parsing, confidence scoring |
| Rate Limiter | `rate_limiter.py` | ✅ Complete | Hourly limits, cost caps |
| Ralph Manager | `ralph_manager.py` | ✅ Complete | Loop orchestration |
| Resume Coordinator | `resume_coordinator.py` | ✅ Complete | Polling, policy evaluation |
| Supervision Policies | `policies.py` | ✅ Complete | AGGRESSIVE/CONSERVATIVE/MANUAL |
| ExecutionRun Model | `models.py` | ✅ Complete | Ralph fields (loop_count, circuit_state, etc.) |

### 3.2 Integration Points

**TaskRunner Integration** (`runner.py:366-368`):
```python
# Ralph mode: use RalphManager for autonomous loop execution
if run.ralph_enabled:
    await self._run_ralph_loop(run, working_dir, worktree_manager)
    return
```

**Submit API** (`runner.py:87-143`):
```python
async def submit(
    ...
    ralph_enabled: bool = False,
    max_loops: int = 50,
    max_calls_per_hour: int = 100,
    max_cost_usd: float | None = None,
)
```

**Supervisor Start** (`runner.py:746-769`):
```python
async def start_supervisor(self, poll_interval: int = 30) -> None:
    # Creates and starts ResumeCoordinator
```

### 3.3 Prompt Injection for RALPH_STATUS

In `ralph_manager.py:361-385`, the RALPH_STATUS instruction is injected:
```python
status_instruction = """
**IMPORTANT: At the end of your response, you MUST include a RALPH_STATUS block...**

---RALPH_STATUS---
STATUS: IN_PROGRESS | COMPLETE | BLOCKED
TASKS_COMPLETED_THIS_LOOP: <number>
FILES_MODIFIED: <number>
TESTS_STATUS: PASSING | FAILING | NOT_RUN
WORK_TYPE: IMPLEMENTATION | TESTING | DOCUMENTATION | REFACTORING
EXIT_SIGNAL: false | true
RECOMMENDATION: <one line summary>
---END_RALPH_STATUS---
```

---

## 4. Gap Analysis

### 4.1 Critical Gaps

| Gap | Impact | Severity |
|-----|--------|----------|
| **No CLI command for Ralph mode** | Users can't easily run ralph loops from command line | HIGH |
| **Supervisor not auto-started** | ResumeCoordinator doesn't run unless explicitly started | HIGH |
| **Web API doesn't expose ralph options** | Can't configure ralph mode from UI | MEDIUM |
| **No monitoring dashboard** | Unlike ralph-claude-code's tmux, no real-time loop status | MEDIUM |

### 4.2 Current CLI Commands

From `cli.py`, existing commands:
- `gluon run <project> '<prompt>'` - Has `--background` but no `--ralph`
- `gluon runs` - Lists runs but doesn't show ralph status
- `gluon status` - Shows general status

**Missing:**
- `gluon run <project> '<prompt>' --ralph --max-loops 50`
- `gluon ralph status <run-id>` - Show loop progress
- `gluon ralph resume <run-id>` - Force resume
- `gluon supervisor start/stop/status`

### 4.3 Supervision Flow Issues

The `ResumeCoordinator` is designed to poll REVIEW tasks and auto-resume, but:

1. **Never Started**: `start_supervisor()` exists but isn't called by default
2. **Policies Default to CONSERVATIVE**: Safe but won't aggressively resume
3. **SupervisionConfig not set on runs**: Defaults used, not explicit configuration

### 4.4 Completion Detection Gaps

Current `CompletionDetector`:
- Parses RALPH_STATUS block ✅
- Checks TODO files ✅
- Detects test-only loops ✅

**Missing:**
- No integration with actual test framework results
- No git commit analysis (are commits meaningful?)
- No visual verification (screenshots)

---

## 5. Implementation Options

### Option A: Minimal Integration (1-2 days)

**Approach**: Wire existing components together via CLI flags

**Changes:**
1. Add `--ralph` flag to `gluon run` command
2. Add supervisor start to web server startup
3. Document ralph mode usage

**Pros:**
- Quick to implement
- Uses existing tested components
- Low risk

**Cons:**
- No new monitoring UI
- Limited user control
- Default policies may not suit all use cases

### Option B: Full CLI Integration (3-5 days)

**Approach**: Complete CLI suite for ralph mode

**Changes:**
1. Add ralph command group (`gluon ralph`)
2. Add supervisor commands (`gluon supervisor`)
3. Add progress display during ralph execution
4. Add configuration options (policies, thresholds)

**Pros:**
- Full CLI control
- Better user experience
- Matches ralph-claude-code's workflow

**Cons:**
- More implementation work
- Need to design CLI UX

### Option C: Full Feature Parity (1-2 weeks)

**Approach**: Match ralph-claude-code feature set + Gluon enhancements

**Changes:**
1. Everything in Option B
2. Web UI dashboard for loop monitoring
3. Configurable supervision policies per project
4. Tmux-style terminal dashboard
5. Test framework integration
6. Git progress analysis

**Pros:**
- Full feature set
- Production-ready
- Best user experience

**Cons:**
- Significant development effort
- Risk of over-engineering

---

## 6. Recommended Approach

**Recommendation: Option B (Full CLI Integration)** with incremental enhancement

### Rationale:

1. **Gluon's strength is orchestration** - CLI is the natural interface
2. **Components already exist** - Just need wiring
3. **Web UI can come later** - CLI first, then UI
4. **Matches user expectations** - Similar to ralph-claude-code workflow

### Phase 1 Deliverables (3 days):

1. `gluon run --ralph` flag with options
2. `gluon ralph status <run>` command
3. Auto-start supervisor on `gluon serve`
4. Progress output during execution

### Phase 2 Deliverables (2 days):

1. `gluon supervisor` command group
2. Policy configuration via CLI
3. Ralph status in `gluon runs` output

---

## 7. Impact Analysis

### 7.1 Files to Modify

| File | Changes | Risk |
|------|---------|------|
| `cli.py` | Add ralph commands, --ralph flag | LOW |
| `runner.py` | Start supervisor in serve mode | LOW |
| `web/api.py` | Add ralph options to submit endpoint | LOW |
| `models.py` | No changes needed | NONE |
| `ralph_manager.py` | Minor logging improvements | LOW |
| `resume_coordinator.py` | Add CLI status methods | LOW |

### 7.2 Backward Compatibility

**Breaking Changes**: None expected

**Additive Changes**:
- New CLI commands (ralph, supervisor)
- New flags on existing commands (--ralph)
- New API fields (optional)

### 7.3 Database Impact

**No migrations required** - All ralph fields already exist in ExecutionRun model:
- `ralph_enabled`
- `loop_count`
- `max_loops`
- `circuit_state`
- `completion_signals`
- `supervision_config`
- etc.

### 7.4 Testing Requirements

1. Unit tests for new CLI commands
2. Integration test for ralph loop execution
3. Test supervisor start/stop lifecycle
4. Test policy evaluation

---

## 8. Implementation Phases

### Phase 1: CLI Flag Integration (Priority: HIGH)

**Objective**: Enable ralph mode via `gluon run --ralph`

```bash
# Target usage:
gluon run myproject "implement feature X" --ralph --max-loops 50 --max-cost 10.00
```

**Tasks:**
1. [ ] Add `--ralph` flag to `run` command
2. [ ] Add `--max-loops` option (default: 50)
3. [ ] Add `--max-calls-per-hour` option (default: 100)
4. [ ] Add `--max-cost` option (optional USD cap)
5. [ ] Show loop progress during execution
6. [ ] Update `gluon runs` output to show ralph status

**Files**: `cli.py`

### Phase 2: Supervisor Activation (Priority: HIGH)

**Objective**: Auto-resume capability always available

**Tasks:**
1. [ ] Start supervisor automatically in `gluon serve`
2. [ ] Add `gluon supervisor status` command
3. [ ] Add `gluon supervisor start/stop` commands
4. [ ] Add supervisor status to web UI header

**Files**: `cli.py`, `web/api.py`, `runner.py`

### Phase 3: Ralph Status Commands (Priority: MEDIUM)

**Objective**: Visibility into ralph loop state

```bash
# Target usage:
gluon ralph status abc12345
gluon ralph iterations abc12345
gluon ralph circuit abc12345
```

**Tasks:**
1. [ ] Add `gluon ralph status <run-id>` - Show loop state
2. [ ] Add `gluon ralph iterations <run-id>` - List iteration history
3. [ ] Add `gluon ralph circuit <run-id>` - Show circuit breaker state
4. [ ] Add `gluon ralph reset-circuit <run-id>` - Reset circuit breaker

**Files**: `cli.py`, add `store.py` method for iteration queries

### Phase 4: Policy Configuration (Priority: MEDIUM)

**Objective**: User control over auto-resume behavior

```bash
# Target usage:
gluon run myproject "..." --ralph --policy aggressive
gluon run myproject "..." --ralph --policy conservative
gluon run myproject "..." --ralph --policy manual  # No auto-resume
```

**Tasks:**
1. [ ] Add `--policy` option to `run` command
2. [ ] Add `--max-auto-resumes` option (default: 5)
3. [ ] Document policy behaviors

**Files**: `cli.py`

### Phase 5: Web UI Enhancements (Priority: LOW)

**Objective**: Visual monitoring of ralph loops

**Tasks:**
1. [ ] Add ralph toggle to task submission form
2. [ ] Add loop progress indicator on task card
3. [ ] Add circuit breaker status to task detail
4. [ ] Add iteration history viewer

**Files**: `web-ui/src/components/*.tsx`, `web/api.py`

---

## 9. Risk Assessment

### 9.1 Technical Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Supervisor memory leak | LOW | HIGH | Monitor memory, add cleanup |
| Database lock contention | LOW | MEDIUM | Use WAL mode, optimize queries |
| Runaway cost | MEDIUM | HIGH | Default cost caps, rate limits |
| Circuit breaker false positives | MEDIUM | LOW | Tune thresholds, add manual override |

### 9.2 User Experience Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Confusing CLI options | MEDIUM | MEDIUM | Clear documentation, sensible defaults |
| Unexpected auto-resume | MEDIUM | LOW | Default to CONSERVATIVE policy |
| Silent failures | LOW | HIGH | Log all decisions, add alerts |

### 9.3 Operational Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| API rate limits hit | MEDIUM | MEDIUM | Respect 100/hour limit |
| Cost overruns | MEDIUM | HIGH | Mandatory cost caps in production |
| Long-running tasks blocking | LOW | LOW | Background execution, cancellation |

---

## Appendix A: Code Snippets

### A.1 CLI Changes for Phase 1

```python
# cli.py additions

@app.command("run")
def run(
    project: Annotated[str, typer.Argument(...)],
    prompt: Annotated[str, typer.Argument(...)],
    background: Annotated[bool, typer.Option("--background", "-b")] = False,
    ralph: Annotated[bool, typer.Option("--ralph", "-r", help="Enable ralph loop mode")] = False,
    max_loops: Annotated[int, typer.Option(help="Max loop iterations")] = 50,
    max_calls_per_hour: Annotated[int, typer.Option(help="Max API calls per hour")] = 100,
    max_cost: Annotated[float | None, typer.Option(help="Max cost in USD")] = None,
    policy: Annotated[str, typer.Option(help="Supervision policy")] = "conservative",
    ...
):
    """Run a task on a project."""
    orchestrator = get_orchestrator()

    # Submit with ralph options
    run = asyncio.run(orchestrator.execute(
        project_name=project,
        prompt=prompt,
        wait=not background,
        ralph_enabled=ralph,
        max_loops=max_loops,
        max_calls_per_hour=max_calls_per_hour,
        max_cost_usd=max_cost,
    ))

    if ralph:
        console.print(f"[blue]Ralph mode enabled[/blue]")
        console.print(f"  Max loops: {max_loops}")
        console.print(f"  Max calls/hour: {max_calls_per_hour}")
        if max_cost:
            console.print(f"  Max cost: ${max_cost:.2f}")
```

### A.2 Supervisor Auto-Start

```python
# In web/api.py or separate startup module

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - start supervisor on startup."""
    runner = get_runner()

    # Start supervisor with 30s poll interval
    await runner.start_supervisor(poll_interval=30)
    logger.info("Supervisor started")

    yield

    # Stop supervisor on shutdown
    await runner.stop_supervisor()
    logger.info("Supervisor stopped")
```

---

## Appendix B: Configuration Reference

### Default Ralph Configuration

```python
# From models.py
class SupervisionConfig:
    enabled: bool = True
    policy: SupervisionPolicy = SupervisionPolicy.CONSERVATIVE
    max_auto_resumes: int = 5
    min_time_between_resumes: int = 60  # seconds
    auto_resume_triggers: list[str] = ["incomplete_work", "test_only", "low_confidence"]
```

### Circuit Breaker Thresholds

```python
# From circuit_breaker.py
class CircuitBreakerConfig:
    no_progress_threshold: int = 5  # Open after N no-progress loops
    same_error_threshold: int = 5   # Open after N same-error loops
    half_open_threshold: int = 2    # Enter HALF_OPEN after N no-progress
    half_open_patience: int = 3     # Stay HALF_OPEN for N loops before OPEN
```

### Rate Limiter Defaults

```python
# From rate_limiter.py
class RateLimiterConfig:
    max_calls_per_hour: int = 100
    max_cost_usd: float | None = None  # No cap by default
```

---

## Conclusion

Gluon Agent is **90% ready** for Ralph Loop functionality. The core components are implemented and tested. What remains is:

1. **CLI Integration** - Expose ralph mode via command line
2. **Supervisor Activation** - Auto-start the resume coordinator
3. **User Documentation** - Guide users on ralph mode best practices

The recommended phased approach minimizes risk while delivering value quickly. Phase 1 alone (CLI flag integration) would provide a usable ralph mode within 1-2 days of development.
