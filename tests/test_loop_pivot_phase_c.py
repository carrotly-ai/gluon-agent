"""Tests for loop-first pivot Phase C (docs/design/loop-first-pivot.md).

Three seams derived from the ClaudeDevs loop taxonomy gaps:

- N1 — intra-loop model routing: the surveyor / verifier / continuations run on
  the JUDGMENT model (``loop.model``); mechanical agent-authored fan-out tasks
  (``source == "agent"``) run on the cheaper ``loop.executor_model`` when set.
- N2 — event-reactive ("watch") loops: a loop that would otherwise stall on an
  empty queue instead runs ``watch_cmd`` and, on exit 0, re-seeds a surveyor
  cycle carrying the command's output. Exit != 0 falls through to stall bounds.
- N3 — verification-skill convention: a shipped ``verify-loop-work`` skill plus
  prompt guidance so iterations verify before claiming completion.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gluon.loop_manager import LoopManager
from gluon.models import AgentLoop, LoopStatus, RunStatus, resolve_loop_iteration_model, resolve_task_options
from gluon.store import GluonStore
from gluon.work_queue import WorkQueueManager


def _project(store: GluonStore, path: Path, name: str = "p"):
    ws = store.create_workspace(f"w-{name}", path.parent)
    return store.create_project(name=name, path=path, workspace_id=ws.id)


def _make_loop(store: GluonStore, project_id: str, **kwargs) -> AgentLoop:
    return LoopManager(store).create_loop(project_id=project_id, objective="Ship the campaign", **kwargs)


def _loop_items(store: GluonStore, loop: AgentLoop, status: str | None = None):
    return [
        i for i in store.list_work_items(project_id=loop.project_id, status=status, limit=200) if i.loop_id == loop.id
    ]


# ===========================================================================
# N1 — intra-loop model routing
# ===========================================================================


def test_executor_tasks_route_to_executor_model() -> None:
    loop = AgentLoop(project_id="p", objective="x", model="claude-opus-4-8", executor_model="claude-haiku-4-5")
    # Agent-authored fan-out = mechanical → cheaper executor model.
    assert resolve_loop_iteration_model(loop, "agent", "standard") == "claude-haiku-4-5"


@pytest.mark.parametrize("source", ["seed", "verifier", "continuation", None])
def test_judgment_sources_always_use_judgment_model(source: str | None) -> None:
    loop = AgentLoop(project_id="p", objective="x", model="claude-opus-4-8", executor_model="claude-haiku-4-5")
    # Surveyor, verifier, harness continuations, and unknown/None sources are
    # judgment work — never demoted to the executor tier.
    assert resolve_loop_iteration_model(loop, source, "standard") == "claude-opus-4-8"


def test_executor_falls_back_to_judgment_when_unset() -> None:
    loop = AgentLoop(project_id="p", objective="x", model="claude-opus-4-8", executor_model=None)
    assert resolve_loop_iteration_model(loop, "agent", "standard") == "claude-opus-4-8"


def test_judgment_falls_back_to_profile_default_when_model_unset() -> None:
    loop = AgentLoop(project_id="p", objective="x", model=None, executor_model="claude-haiku-4-5")
    default = resolve_task_options(profile="standard")["model"]
    # Judgment work with no loop.model → the profile default (NOT the executor).
    assert resolve_loop_iteration_model(loop, "seed", "standard") == default
    # But executor work still routes to the executor tier.
    assert resolve_loop_iteration_model(loop, "agent", "standard") == "claude-haiku-4-5"


def test_executor_model_round_trips_through_store(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "proj")
    loop = _make_loop(temp_store, proj.id, model="claude-opus-4-8", executor_model="claude-haiku-4-5")
    reloaded = temp_store.get_agent_loop(loop.id)
    assert reloaded is not None
    assert reloaded.executor_model == "claude-haiku-4-5"


def test_blank_executor_model_normalizes_to_none(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "proj")
    loop = _make_loop(temp_store, proj.id, executor_model="   ")
    assert loop.executor_model is None


# ===========================================================================
# N2 — event-reactive (watch) loops
# ===========================================================================


def test_watch_cmd_round_trips_through_store(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "proj")
    loop = _make_loop(temp_store, proj.id, watch_cmd="gh pr list")
    reloaded = temp_store.get_agent_loop(loop.id)
    assert reloaded is not None
    assert reloaded.watch_cmd == "gh pr list"


def test_watch_reseed_enqueues_seed_when_command_reports_work(temp_store: GluonStore, tmp_path: Path) -> None:
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    proj = _project(temp_store, proj_dir)
    # `echo` exits 0 with output → "there is work".
    loop = _make_loop(temp_store, proj.id, watch_cmd="echo NEW-PR-123")
    mgr = LoopManager(temp_store)

    seeded = mgr._watch_reseed(loop)

    assert seeded is True
    fresh_seeds = [i for i in _loop_items(temp_store, loop, status="pending") if i.source == "seed"]
    # The original create_loop seed + the new watch-triggered seed.
    assert len(fresh_seeds) == 2
    watch_seed = max(fresh_seeds, key=lambda i: i.created_at)
    assert "WATCH TRIGGER" in watch_seed.prompt
    assert "NEW-PR-123" in watch_seed.prompt


def test_watch_reseed_noops_when_command_reports_no_work(temp_store: GluonStore, tmp_path: Path) -> None:
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    proj = _project(temp_store, proj_dir)
    loop = _make_loop(temp_store, proj.id, watch_cmd="false")  # exit 1 → no work
    mgr = LoopManager(temp_store)

    seeded = mgr._watch_reseed(loop)

    assert seeded is False
    seeds = [i for i in _loop_items(temp_store, loop, status="pending") if i.source == "seed"]
    assert len(seeds) == 1  # only the original create_loop seed


def _finish_run(store: GluonStore, loop: AgentLoop, initiator: str) -> None:
    """Run the loop's currently-pending item to COMPLETED, then advance."""
    run = store.create_run(project_id=loop.project_id, prompt="iter", loop_id=loop.id, initiator=initiator)
    run.status = RunStatus.COMPLETED
    run.cost_usd = 0.05
    store.update_run(run)
    asyncio.run(LoopManager(store).on_run_completed(run))


def test_watch_loop_reseeds_instead_of_stalling(temp_store: GluonStore, tmp_path: Path) -> None:
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    proj = _project(temp_store, proj_dir)
    loop = _make_loop(temp_store, proj.id, watch_cmd="echo work-to-do", max_stalls=2)
    # Consume the initial seed item (mark it done) so the queue is empty when
    # on_run_completed runs — the no-progress branch is what watch intercepts.
    seed = _loop_items(temp_store, loop, status="pending")[0]
    wq = WorkQueueManager(temp_store)
    wq.mark_running(seed.id, "r-seed")
    wq.mark_completed(seed.id)

    _finish_run(temp_store, loop, initiator=f"queue:{seed.id}")

    after = temp_store.get_agent_loop(loop.id)
    assert after is not None
    assert after.status == LoopStatus.RUNNING  # did NOT pause/stall
    assert after.stall_count == 0  # watch re-seed resets the stall counter
    # A fresh reactive seed is now pending.
    pending_seeds = [i for i in _loop_items(temp_store, loop, status="pending") if i.source == "seed"]
    assert len(pending_seeds) == 1
    assert "WATCH TRIGGER" in pending_seeds[0].prompt


def test_watch_loop_with_no_work_still_stalls(temp_store: GluonStore, tmp_path: Path) -> None:
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    proj = _project(temp_store, proj_dir)
    loop = _make_loop(temp_store, proj.id, watch_cmd="false", max_stalls=2)
    seed = _loop_items(temp_store, loop, status="pending")[0]
    wq = WorkQueueManager(temp_store)
    wq.mark_running(seed.id, "r-seed")
    wq.mark_completed(seed.id)

    _finish_run(temp_store, loop, initiator=f"queue:{seed.id}")

    after = temp_store.get_agent_loop(loop.id)
    assert after is not None
    # Watch reported nothing → normal stall path: bump stall, enqueue a
    # continuation (not a watch seed).
    assert after.stall_count == 1
    watch_seeds = [i for i in _loop_items(temp_store, loop, status="pending") if "WATCH TRIGGER" in i.prompt]
    assert watch_seeds == []


# ===========================================================================
# N3 — verification-skill convention
# ===========================================================================


def _repo_root() -> Path:
    # tests/ -> repo root
    return Path(__file__).resolve().parents[1]


def test_verify_loop_work_skill_ships_with_frontmatter() -> None:
    skill = _repo_root() / ".claude" / "skills" / "verify-loop-work" / "SKILL.md"
    assert skill.exists(), "verify-loop-work skill must ship in the repo"
    text = skill.read_text()
    assert text.startswith("---")
    assert "name: verify-loop-work" in text
    # The two-tier discipline is the substance, not just a title.
    assert "verify_cmd" in text
    assert "loop_complete" in text


def test_seed_and_continuation_prompts_reference_verification_skill() -> None:
    from gluon.loop_manager import _CONTINUATION_PROMPT_TEMPLATE, _SEED_PROMPT_TEMPLATE

    assert "verify-loop-work" in _SEED_PROMPT_TEMPLATE
    assert "verify-loop-work" in _CONTINUATION_PROMPT_TEMPLATE
