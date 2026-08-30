"""Tests for loop-hardening Phase E — trustworthy verification.

- E1 structured, fail-closed verifier verdicts (parse + act).
- E2 ESCALATE_HUMAN → PAUSED.
- E4 cross-family verifier model routing.
- E5 failure-signature stall + attempt-ledger injection.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gluon.loop_manager import (
    VERIFICATION_MARKER,
    LoopManager,
    parse_verifier_verdict,
)
from gluon.models import AgentLoop, LoopStatus, RunStatus, resolve_loop_iteration_model
from gluon.store import GluonStore


def _project(store: GluonStore, path: Path, name: str = "p"):
    ws = store.create_workspace(f"w-{name}", path.parent)
    return store.create_project(name=name, path=path, workspace_id=ws.id)


def _loop(store: GluonStore, project_id: str, **kw) -> AgentLoop:
    return LoopManager(store).create_loop(project_id=project_id, objective="ship the widget", **kw)


def _verifier_run(store: GluonStore, loop: AgentLoop, summary: str):
    """A completed verifier iteration (marker in prompt) reporting `summary`."""
    store.set_loop_completion(loop.id, True, summary)
    run = store.create_run(
        project_id=loop.project_id,
        prompt=f"{VERIFICATION_MARKER} judge the objective",
        loop_id=loop.id,
    )
    run.status = RunStatus.COMPLETED
    run.cost_usd = 0.05
    store.update_run(run)
    return run


# ===========================================================================
# E1 — verdict parser (fail-closed)
# ===========================================================================


def test_parse_pass_verdict() -> None:
    v = parse_verifier_verdict('ok\n```json\n{"verdict": "pass", "confidence": 0.9}\n```')
    assert v.verdict == "pass" and v.parsed and v.confidence == 0.9


def test_parse_revise_with_issues() -> None:
    v = parse_verifier_verdict('```json\n{"verdict": "revise", "blocking_issues": ["no test", "TBD left"]}\n```')
    assert v.verdict == "revise"
    assert v.blocking_issues == ["no test", "TBD left"]


def test_parse_escalate() -> None:
    assert parse_verifier_verdict('```json\n{"verdict":"escalate","notes":"ambiguous"}\n```').verdict == "escalate"


@pytest.mark.parametrize(
    "text",
    [
        "the work looks good to me, shipping it",  # no block at all
        "```json\n{not valid json}\n```",  # unparseable
        '```json\n{"verdict": "approve"}\n```',  # invalid verdict value
        "",  # empty
    ],
)
def test_parse_failcloses_to_revise(text: str) -> None:
    v = parse_verifier_verdict(text)
    assert v.verdict == "revise" and v.parsed is False


def test_parse_uses_last_block() -> None:
    # A thinking-out-loud earlier block must not win over the final verdict.
    text = '```json\n{"verdict":"revise"}\n```\nafter more review:\n```json\n{"verdict":"pass"}\n```'
    assert parse_verifier_verdict(text).verdict == "pass"


def test_parse_bare_trailing_object() -> None:
    assert parse_verifier_verdict('done. {"verdict": "pass"}').verdict == "pass"


# ===========================================================================
# E1/E2 — verifier verdict drives loop lifecycle
# ===========================================================================


def test_verifier_pass_completes_gateless_loop(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "p")
    loop = _loop(temp_store, proj.id, agent_verifier=True)  # gateless
    run = _verifier_run(temp_store, loop, 'verified all criteria.\n```json\n{"verdict":"pass"}\n```')
    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    after = temp_store.get_agent_loop(loop.id)
    assert after is not None and after.status == LoopStatus.COMPLETED
    assert after.last_verdict is not None and '"verdict": "pass"' in after.last_verdict


def test_verifier_revise_denies_and_enqueues_fixes(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "p")
    loop = _loop(temp_store, proj.id, agent_verifier=True)
    run = _verifier_run(
        temp_store,
        loop,
        '```json\n{"verdict":"revise","blocking_issues":["module X has no tests","func Y unimplemented"]}\n```',
    )
    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    after = temp_store.get_agent_loop(loop.id)
    assert after is not None and after.status == LoopStatus.RUNNING  # not completed
    fixes = [
        i
        for i in temp_store.list_work_items(project_id=proj.id, status="pending", limit=200)
        if i.loop_id == loop.id and "VERIFIER REJECTION" in i.prompt
    ]
    assert len(fixes) == 2  # one fix task per blocking issue


def test_verifier_escalate_pauses(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "p")
    loop = _loop(temp_store, proj.id, agent_verifier=True)
    run = _verifier_run(temp_store, loop, '```json\n{"verdict":"escalate","notes":"cannot run the tests here"}\n```')
    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    after = temp_store.get_agent_loop(loop.id)
    assert after is not None and after.status == LoopStatus.PAUSED
    assert "escalated to human" in (after.status_reason or "")


def test_verifier_malformed_verdict_failcloses(temp_store: GluonStore, tmp_path: Path) -> None:
    # A verifier that calls loop_complete with NO structured verdict must NOT
    # complete the loop — the "verifier theater" guard.
    proj = _project(temp_store, tmp_path / "p")
    loop = _loop(temp_store, proj.id, agent_verifier=True)
    run = _verifier_run(temp_store, loop, "looks great, all done here!")
    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    after = temp_store.get_agent_loop(loop.id)
    assert after is not None and after.status == LoopStatus.RUNNING  # NOT completed
    assert after.last_verdict is not None and '"parsed": false' in after.last_verdict


# ===========================================================================
# E4 — cross-family verifier model routing
# ===========================================================================


def test_verifier_source_routes_to_verifier_model() -> None:
    loop = AgentLoop(
        project_id="p",
        objective="x",
        model="claude-opus-4-8",
        agent_verifier_model="qwen3:14b",
    )
    assert resolve_loop_iteration_model(loop, "verifier", "standard") == "qwen3:14b"
    # Non-verifier work is unaffected.
    assert resolve_loop_iteration_model(loop, "seed", "standard") == "claude-opus-4-8"
    assert resolve_loop_iteration_model(loop, "agent", "standard") == "claude-opus-4-8"


def test_verifier_model_implies_verifier_and_round_trips(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "p")
    loop = _loop(temp_store, proj.id, agent_verifier_model="qwen3:14b")  # no explicit agent_verifier
    assert loop.agent_verifier is True
    reloaded = temp_store.get_agent_loop(loop.id)
    assert reloaded is not None and reloaded.agent_verifier_model == "qwen3:14b"


# ===========================================================================
# E5 — failure-signature stall
# ===========================================================================


def test_same_gate_failure_signature_pauses(temp_store: GluonStore, tmp_path: Path) -> None:
    proj_dir = tmp_path / "p"
    proj_dir.mkdir()
    proj = _project(temp_store, proj_dir)
    # A gate that always fails the SAME way. max_stalls=2 → pause on the 2nd
    # identical failure via the signature path (before emptiness-based stall).
    loop = _loop(temp_store, proj.id, verify_cmd="false", max_iterations=20, max_stalls=2)
    mgr = LoopManager(temp_store)
    # Drive repeated completion claims → each runs the gate (fails identically).
    paused = False
    for _ in range(6):
        lp = temp_store.get_agent_loop(loop.id)
        assert lp is not None
        if lp.status != LoopStatus.RUNNING:
            paused = True
            break
        temp_store.set_loop_completion(loop.id, True, "claim done")
        run = temp_store.create_run(project_id=proj.id, prompt="work claiming done", loop_id=loop.id)
        run.status = RunStatus.COMPLETED
        run.cost_usd = 0.01
        temp_store.update_run(run)
        asyncio.run(mgr.on_run_completed(run))
    final = temp_store.get_agent_loop(loop.id)
    assert final is not None
    assert paused and final.status == LoopStatus.PAUSED
    assert "repeating failure" in (final.status_reason or "")


def test_failure_signature_helpers_count_and_reset(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "p")
    loop = _loop(temp_store, proj.id)
    assert temp_store.record_loop_failure_signature(loop.id, "aaa") == 1
    assert temp_store.record_loop_failure_signature(loop.id, "aaa") == 2
    assert temp_store.record_loop_failure_signature(loop.id, "bbb") == 1  # different sig resets streak
    temp_store.reset_loop_failure_signature(loop.id)
    assert temp_store.record_loop_failure_signature(loop.id, "bbb") == 1


# ===========================================================================
# E5 — attempt-ledger injection into continuation prompts
# ===========================================================================


def test_continuation_prompt_includes_attempt_ledger(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "p")
    loop = _loop(temp_store, proj.id)
    # A couple of prior terminal runs to summarize.
    for i, st in enumerate([RunStatus.COMPLETED, RunStatus.FAILED]):
        r = temp_store.create_run(project_id=proj.id, prompt=f"attempt {i} did a thing", loop_id=loop.id)
        r.status = st
        r.cost_usd = 0.1
        if st == RunStatus.FAILED:
            r.error_message = "boom: something specific broke"
        temp_store.update_run(r)
    fresh = temp_store.get_agent_loop(loop.id)
    assert fresh is not None
    LoopManager(temp_store)._enqueue_continuation(fresh)
    conts = [
        i
        for i in temp_store.list_work_items(project_id=proj.id, status="pending", limit=200)
        if i.loop_id == loop.id and "CONTINUATION" in i.prompt
    ]
    assert conts, "a continuation should have been enqueued"
    assert "Prior attempts" in conts[0].prompt
    assert "boom: something specific broke" in conts[0].prompt
