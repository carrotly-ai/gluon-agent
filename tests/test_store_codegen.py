"""Drift-guard tests for the execution_runs persistence (#166).

These assert the ExecutionRun model, the live DB schema, and the round-trip stay
in sync — so a model field added without a column/migration (or vice versa)
becomes a failing test instead of silent data loss. They also pin a
representative value of each transform type through the real
create_run → update_run → get_run round-trip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from gluon.models import (
    ApprovalPolicy,
    CircuitState,
    ExecutionRun,
    QueuedMessage,
    RunStatus,
    SupervisionConfig,
    SupervisionPolicy,
)
from gluon.store import GluonStore
from gluon.store_codegen import KNOWN_LEGACY_DB_COLUMNS, execution_run_columns


def _store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "codegen.db")


def _project(store: GluonStore, tmp_path: Path):
    p = tmp_path / "proj"
    p.mkdir(exist_ok=True)
    return store.create_project("proj", p)


def test_generated_columns_match_model():
    assert set(execution_run_columns()) == set(ExecutionRun.model_fields.keys())


def test_execution_runs_schema_matches_model(tmp_path):
    """Every model field has a DB column; the only extras are documented orphans."""
    store = _store(tmp_path)
    with store._get_conn() as conn:
        db_cols = {r[1] for r in conn.execute("PRAGMA table_info(execution_runs)").fetchall()}

    model_cols = set(execution_run_columns())

    missing = model_cols - db_cols
    assert not missing, f"model fields with no execution_runs column (missing migration?): {sorted(missing)}"

    extra = db_cols - model_cols
    assert extra == set(KNOWN_LEGACY_DB_COLUMNS), (
        f"unexpected orphan DB columns (add a model field or document as legacy): "
        f"{sorted(extra - set(KNOWN_LEGACY_DB_COLUMNS))}"
    )


def test_representative_transforms_roundtrip(tmp_path):
    """A value of every transform type survives create_run → update_run → get_run."""
    store = _store(tmp_path)
    project = _project(store, tmp_path)

    # Immutable-at-create fields (not updated by update_run) — set via create_run.
    run = store.create_run(
        project_id=project.id,
        prompt="codegen round-trip",
        model="claude-opus-4.8",
        max_loops=99,
        verify_cmd="uv run pytest",
        approval_policy=ApprovalPolicy.CAREFUL,
    )

    dt = datetime(2023, 6, 15, 12, 30, 45, tzinfo=UTC)
    run.status = RunStatus.REVIEW  # RunStatus enum
    run.circuit_state = CircuitState.OPEN  # CircuitState enum
    run.started_at = dt  # datetime
    run.log_path = Path("/tmp/run.log")  # Path
    run.use_worktree = True  # bool-from-int
    run.loop_count = 7  # int
    run.completion_confidence = 42.5  # float
    run.pr_url = "https://github.com/x/y/pull/1"  # plain str
    run.metadata = {"profile": "standard", "n": 3}  # json -> dict
    run.supervision_config = SupervisionConfig(  # json -> model
        policy=SupervisionPolicy.AGGRESSIVE, max_auto_resumes=7
    )
    run.queued_messages = [  # json -> list[model]
        QueuedMessage(message="follow up", queued_at=dt),
    ]
    store.update_run(run)

    reloaded = store.get_run(run.id)
    assert reloaded is not None

    # Immutables persisted via INSERT
    assert reloaded.model == "claude-opus-4.8"
    assert reloaded.max_loops == 99
    assert reloaded.verify_cmd == "uv run pytest"
    # Each transform type round-tripped via UPDATE + _row_to_run
    assert reloaded.status == RunStatus.REVIEW
    assert reloaded.circuit_state == CircuitState.OPEN
    assert reloaded.approval_policy == ApprovalPolicy.CAREFUL
    assert reloaded.started_at == dt
    assert reloaded.log_path == Path("/tmp/run.log")
    assert reloaded.use_worktree is True
    assert reloaded.loop_count == 7
    assert reloaded.completion_confidence == 42.5
    assert reloaded.pr_url == "https://github.com/x/y/pull/1"
    assert reloaded.metadata == {"profile": "standard", "n": 3}
    assert reloaded.supervision_config is not None
    assert reloaded.supervision_config.policy == SupervisionPolicy.AGGRESSIVE
    assert reloaded.supervision_config.max_auto_resumes == 7
    assert len(reloaded.queued_messages) == 1
    assert reloaded.queued_messages[0].message == "follow up"
    assert reloaded.queued_messages[0].queued_at == dt
