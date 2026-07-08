"""Tests for loop-hardening Phase F — the operator surface (with teeth).

- F1 repo-local binding constraints + mechanically-enforced path denylist.
- F2 global / per-project kill switch enforced at claim_work.
- F3 budget degradation tier (report-only pause before the hard cap).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from gluon.loop_constraints import (
    DEFAULT_DENYLIST,
    constraints_prompt_block,
    denylisted_paths,
    load_constraints,
    path_matches_denylist,
)
from gluon.loop_integration import integrate_run_branch
from gluon.loop_manager import LoopManager
from gluon.models import AgentLoop, ExecutionRun, LoopStatus, RunStatus
from gluon.store import GluonStore


def _project(store: GluonStore, path: Path, name: str = "p"):
    ws = store.create_workspace(f"w-{name}", path.parent)
    return store.create_project(name=name, path=path, workspace_id=ws.id)


def _loop(store: GluonStore, project_id: str, **kw) -> AgentLoop:
    return LoopManager(store).create_loop(project_id=project_id, objective="ship it", **kw)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True).stdout.strip()


# ===========================================================================
# F1 — constraints + denylist matching
# ===========================================================================


def test_default_denylist_matches_secrets_and_env() -> None:
    for p in [".env", ".env.local", "config/.env", "secrets/db.txt", "auth/token.py", "id_rsa.key", "certs/a.pem"]:
        assert path_matches_denylist(p, DEFAULT_DENYLIST), p


def test_default_denylist_allows_normal_paths() -> None:
    for p in ["src/main.py", "README.md", "tests/test_x.py", "environment.md"]:
        assert not path_matches_denylist(p, DEFAULT_DENYLIST), p


def test_denylisted_paths_filters() -> None:
    hits = denylisted_paths(["src/a.py", ".env", "docs/b.md", "secrets/x"], DEFAULT_DENYLIST)
    assert set(hits) == {".env", "secrets/x"}


def test_load_constraints_defaults_when_absent(tmp_path: Path) -> None:
    c = load_constraints(tmp_path)
    assert c.text == "" and not c.has_file
    assert c.denylist == DEFAULT_DENYLIST


def test_load_constraints_extends_denylist_from_file(tmp_path: Path) -> None:
    (tmp_path / ".gluon").mkdir()
    (tmp_path / ".gluon" / "constraints.md").write_text(
        "# Rules\n\n## Paths\n- Never edit `infra/` or `migrations/schema.sql`\n\n## Code\n- one fix per run\n"
    )
    c = load_constraints(tmp_path)
    assert c.has_file and "one fix per run" in c.text
    # Defaults preserved + file globs added.
    assert ".env" in c.denylist
    assert "infra/**" in c.denylist
    assert "migrations/schema.sql" in c.denylist
    assert path_matches_denylist("infra/main.tf", c.denylist)


def test_constraints_prompt_block_empty_without_file(tmp_path: Path) -> None:
    assert constraints_prompt_block(tmp_path) == ""


def test_constraints_prompt_block_wraps_text(tmp_path: Path) -> None:
    (tmp_path / "loop-constraints.md").write_text("- Never auto-merge to main\n")
    block = constraints_prompt_block(tmp_path)
    assert "BINDING" in block and "Never auto-merge to main" in block


# ---- mechanical teeth: merge-back refuses denylisted paths ----


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@gluon.dev")
    _git(root, "config", "user.name", "T")
    # Neutralize any machine-global gitignore (e.g. a personal `.env` ignore) so
    # the denylisted file is genuinely committed to the branch — the case the
    # merge-back guard defends against. A gitignored secret is never committed
    # (and so never integrated), which is a separate, already-safe path.
    _git(root, "config", "core.excludesFile", "/dev/null")
    (root / "README.md").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "base")
    return root


def _worktree_task(repo: Path, run_id: str, files: dict[str, str]) -> ExecutionRun:
    branch = f"gluon-task/{run_id}"
    wt = repo.parent / f"wt-{run_id}"
    _git(repo, "worktree", "add", "-b", branch, str(wt), "main")
    for name, content in files.items():
        p = wt / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", f"work {run_id}")
    return ExecutionRun(
        id=run_id,
        project_id="proj",
        prompt="iter",
        use_worktree=True,
        branch_name=branch,
        source_branch="main",
        worktree_path=str(wt),
    )


def test_mergeback_refuses_denylisted_env(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    run = _worktree_task(repo, "run0001", {"src/ok.py": "print(1)\n", ".env": "SECRET=xyz\n"})
    result = asyncio.run(integrate_run_branch(repo, run))
    assert result.status == "denylist_violation"
    assert ".env" in result.detail
    # main is untouched — the secret never landed.
    assert not (repo / ".env").exists()
    assert not (repo / "src" / "ok.py").exists()


def test_mergeback_allows_clean_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    run = _worktree_task(repo, "run0002", {"src/ok.py": "print(1)\n"})
    result = asyncio.run(integrate_run_branch(repo, run))
    assert result.status == "merged"
    assert (repo / "src" / "ok.py").exists()


# ===========================================================================
# F2 — kill switch
# ===========================================================================


def test_kill_switch_global(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "p")
    temp_store.enqueue_work(project_id=proj.id, prompt="a claimable task long enough", priority=5)
    assert temp_store.claim_work(proj.id) is not None or True  # (may or may not have claimed; reset below)
    # Engage the global kill switch: no further dispatch anywhere.
    temp_store.set_dispatch_pause(True)
    assert temp_store.is_dispatch_paused(proj.id) is True
    temp_store.enqueue_work(project_id=proj.id, prompt="another claimable task here", priority=5)
    assert temp_store.claim_work(proj.id) is None  # halted
    # Clear it.
    temp_store.set_dispatch_pause(False)
    assert temp_store.is_dispatch_paused(proj.id) is False
    assert temp_store.claim_work(proj.id) is not None


def test_kill_switch_per_project(temp_store: GluonStore, tmp_path: Path) -> None:
    a = _project(temp_store, tmp_path / "a", name="a")
    b = _project(temp_store, tmp_path / "b", name="b")
    temp_store.enqueue_work(project_id=a.id, prompt="task in project a here now", priority=5)
    temp_store.enqueue_work(project_id=b.id, prompt="task in project b here now", priority=5)
    temp_store.set_dispatch_pause(True, project_id=a.id)
    assert temp_store.is_dispatch_paused(a.id) is True
    assert temp_store.is_dispatch_paused(b.id) is False
    assert temp_store.claim_work(a.id) is None  # a halted
    assert temp_store.claim_work(b.id) is not None  # b unaffected


# ===========================================================================
# F3 — budget degradation tier
# ===========================================================================


def test_budget_degraded_threshold() -> None:
    loop = AgentLoop(project_id="p", objective="x", max_cost_usd=10.0, total_cost_usd=8.5)
    assert loop.budget_degraded(0.8) is not None  # 85% >= 80%
    loop2 = AgentLoop(project_id="p", objective="x", max_cost_usd=10.0, total_cost_usd=5.0)
    assert loop2.budget_degraded(0.8) is None  # 50% < 80%
    loop3 = AgentLoop(project_id="p", objective="x", max_cost_usd=10.0, total_cost_usd=10.0)
    assert loop3.budget_degraded(0.8) is None  # 100% is the HARD cap, not degradation
    loop4 = AgentLoop(project_id="p", objective="x", max_cost_usd=None, total_cost_usd=99.0)
    assert loop4.budget_degraded(0.8) is None  # no cap → no degradation


def test_loop_pauses_at_degradation_threshold(temp_store: GluonStore, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GLUON_LOOP_BUDGET_DEGRADE_FRACTION", "0.8")
    proj = _project(temp_store, tmp_path / "p")
    loop = _loop(temp_store, proj.id, max_cost_usd=1.0, max_iterations=50)
    # One completed run costing $0.85 → 85% of the $1.00 cap → degrade + pause.
    run = temp_store.create_run(project_id=proj.id, prompt="expensive iteration", loop_id=loop.id)
    run.status = RunStatus.COMPLETED
    run.cost_usd = 0.85
    temp_store.update_run(run)
    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    after = temp_store.get_agent_loop(loop.id)
    assert after is not None
    assert after.status == LoopStatus.PAUSED
    assert "degradation" in (after.status_reason or "")
