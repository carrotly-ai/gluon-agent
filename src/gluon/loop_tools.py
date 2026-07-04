"""The ``gluon-loop`` MCP server: how a running iteration authors future work.

Injected into a worker agent's session (agent.py) only when its run belongs to
an AgentLoop. Workers execute as ``python -m gluon.runner`` subprocesses with
store access, so these in-process SDK tools write straight to SQLite — no HTTP
callback into the Gluon server is needed.

This is the loop-engineering Phase 2 write-path: the agent enqueues the next
iteration's task(s) (fan-out = multiple calls) and requests completion; the
harness (loop_manager.py) keeps authority over verification, budgets, and
stopping. Guards enforced *here*, at enqueue time:

- loop must be RUNNING,
- per-loop pending cap (``max_fanout``) — bounds runaway fan-out,
- normalized-prompt dedup — an agent cannot re-enqueue work the loop has
  already seen (the classic self-looping failure mode).

Plain-async ``_*_impl`` functions hold the logic (unit-testable without an MCP
session); the ``@tool`` wrappers are thin. docs/design/agent-loops.md
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from gluon.models import LoopStatus, RunStatus, normalize_prompt_hash

if TYPE_CHECKING:
    from claude_agent_sdk.types import McpSdkServerConfig

    from gluon.store import GluonStore

logger = logging.getLogger(__name__)

# Tool names as the agent sees them (server "gluon-loop").
LOOP_TOOL_NAMES = [
    "mcp__gluon-loop__loop_enqueue_task",
    "mcp__gluon-loop__loop_complete",
    "mcp__gluon-loop__loop_status",
]


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


async def _enqueue_task_impl(store: GluonStore, loop_id: str, run_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Enqueue one agent-authored follow-up task, with all guards applied."""
    prompt = (args.get("prompt") or "").strip()
    priority = args.get("priority")
    if not prompt:
        return _text("Error: prompt is required")
    if len(prompt) < 20:
        return _text(
            "Error: task prompt too short — each task must be self-contained "
            "(the next agent has no memory of this session)"
        )

    loop = store.get_agent_loop(loop_id)
    if loop is None:
        return _text(f"Error: loop {loop_id} not found")
    if loop.status != LoopStatus.RUNNING:
        return _text(
            f"Error: loop is {loop.status.value} ({loop.status_reason or 'no reason recorded'}) — no new tasks accepted"
        )

    pending = store.count_pending_loop_items(loop.id)
    if pending >= loop.max_fanout:
        return _text(
            f"Error: fan-out cap reached — {pending} tasks already pending (max_fanout={loop.max_fanout}). "
            "Let queued work drain before enqueueing more."
        )

    prompt_hash = normalize_prompt_hash(prompt)
    if store.loop_prompt_seen(loop.id, prompt_hash):
        return _text(
            "Error: duplicate task rejected — this loop has already seen an identical prompt. "
            "Check loop_status for pending/completed work; author genuinely new work, not a rewording."
        )

    from gluon.loop_manager import LOOP_TASK_PRIORITY

    item = store.enqueue_work(
        project_id=loop.project_id,
        prompt=prompt,
        profile=loop.profile,
        priority=priority if isinstance(priority, int) else LOOP_TASK_PRIORITY,
        loop_id=loop.id,
        source="agent",
        prompt_hash=prompt_hash,
    )
    logger.info("Loop %s: run %s enqueued task %s", loop.id[:8], run_id[:8], item.id[:8])
    return _text(f"Task enqueued (id {item.id}, priority {item.priority}). Loop now has {pending + 1} pending task(s).")


async def _complete_impl(store: GluonStore, loop_id: str, run_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Record the agent's completion request — granted by the harness, not here."""
    summary = (args.get("summary") or "").strip()
    if not summary:
        return _text("Error: summary is required — state what was achieved and how it satisfies the objective")

    loop = store.get_agent_loop(loop_id)
    if loop is None:
        return _text(f"Error: loop {loop_id} not found")
    if loop.status != LoopStatus.RUNNING:
        return _text(f"Error: loop is already {loop.status.value}")

    loop.completion_requested = True
    loop.completion_summary = summary
    store.update_agent_loop(loop)
    logger.info("Loop %s: run %s requested completion", loop.id[:8], run_id[:8])

    if loop.verify_cmd:
        return _text(
            "Completion REQUESTED — not yet granted. The harness will run the verification gate "
            f"(`{loop.verify_cmd}`) when this iteration ends; the loop completes only if it exits 0. "
            "Ensure the gate passes before you finish."
        )
    return _text("Completion requested. This gateless loop will complete when this iteration ends.")


async def _status_impl(store: GluonStore, loop_id: str, run_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Loop state for the agent: objective, budgets, pending tasks, recent runs."""
    loop = store.get_agent_loop(loop_id)
    if loop is None:
        return _text(f"Error: loop {loop_id} not found")

    lines = [
        f"**Agent Loop {loop.id}** — {loop.status.value}",
        f"Objective: {loop.objective}",
        f"Gate: {loop.verify_cmd or '(gateless — completion on agent request)'}",
        f"Iterations: {loop.iteration_count}/{loop.max_iterations}"
        + (
            f" | Cost: ${loop.total_cost_usd:.2f} of ${loop.max_cost_usd:.2f}"
            if loop.max_cost_usd
            else f" | Cost: ${loop.total_cost_usd:.2f}"
        ),
        f"Stalls: {loop.stall_count}/{loop.max_stalls} | Completion requested: {loop.completion_requested}",
    ]

    pending_items = [
        i
        for i in store.list_work_items(project_id=loop.project_id, status="pending", limit=100)
        if i.loop_id == loop.id
    ]
    lines.append(f"\n**Pending tasks ({len(pending_items)}/{loop.max_fanout} fan-out cap):**")
    if pending_items:
        for i in pending_items:
            lines.append(f"- [{i.id}] ({i.source}) {i.prompt[:120]}")
    else:
        lines.append("- (none — enqueue follow-ups or complete the loop before finishing)")

    recent = store.list_runs_for_loop(loop.id, limit=5)
    lines.append("\n**Recent iteration runs (newest first):**")
    if recent:
        for r in recent:
            cost = f"${r.cost_usd:.2f}" if r.cost_usd else "$0.00"
            marker = " (this run)" if r.id == run_id else ""
            lines.append(f"- [{r.id[:8]}] {r.status.value} {cost}{marker} — {(r.custom_title or r.prompt)[:100]}")
    else:
        lines.append("- (none yet)")

    active = [r for r in recent if r.status == RunStatus.RUNNING and r.id != run_id]
    if active:
        lines.append(f"\nNote: {len(active)} sibling iteration(s) currently running — avoid overlapping their work.")

    return _text("\n".join(lines))


def build_loop_mcp_server(store: GluonStore, loop_id: str, run_id: str) -> McpSdkServerConfig:
    """Create the per-run ``gluon-loop`` SDK MCP server bound to one loop + run."""

    @tool(
        "loop_enqueue_task",
        "Enqueue the next task(s) for this agent loop. Call multiple times to fan out "
        "independent work. Each prompt must be self-contained — the executing agent has "
        "no memory of this session. Duplicates and over-fan-out are rejected.",
        {"prompt": str, "priority": int},
    )
    async def loop_enqueue_task(args: dict[str, Any]) -> dict[str, Any]:
        return await _enqueue_task_impl(store, loop_id, run_id, args)

    @tool(
        "loop_complete",
        "Request loop completion — ONLY when the loop objective is fully met. If the loop "
        "has a verification gate, completion is granted only when the gate command exits 0.",
        {"summary": str},
    )
    async def loop_complete(args: dict[str, Any]) -> dict[str, Any]:
        return await _complete_impl(store, loop_id, run_id, args)

    @tool(
        "loop_status",
        "Get the loop's objective, budgets, pending tasks, and recent iterations. Check "
        "this BEFORE enqueueing (avoid duplicates) or completing.",
        {},
    )
    async def loop_status(args: dict[str, Any]) -> dict[str, Any]:
        return await _status_impl(store, loop_id, run_id, args)

    return create_sdk_mcp_server(
        name="gluon-loop",
        version="1.0.0",
        tools=[loop_enqueue_task, loop_complete, loop_status],
    )
