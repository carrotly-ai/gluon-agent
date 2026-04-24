"""Tests for gluon doctor diagnostics and fix functions."""

from pathlib import Path

import pytest

from gluon.doctor import (
    DiagnosticResult,
    check_db_integrity,
    check_log_disk_usage,
    check_orphan_processes,
    check_stale_pending_questions,
    check_stale_runs,
    fix_orphan_processes,
    fix_stale_pending_questions,
    fix_stale_runs,
    run_all_fixes,
    run_diagnostics,
)
from gluon.models import RunStatus
from gluon.store import GluonStore


@pytest.fixture
def project_path(tmp_path: Path) -> Path:
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    return project_dir


# ===================================================================
# DiagnosticResult
# ===================================================================


class TestDiagnosticResult:
    def test_defaults(self):
        r = DiagnosticResult(name="Test", status="ok", message="All good")
        assert r.fixable is False
        assert r.details == []

    def test_with_details(self):
        r = DiagnosticResult(
            name="Test",
            status="error",
            message="Bad",
            fixable=True,
            details=["item1", "item2"],
        )
        assert r.fixable is True
        assert len(r.details) == 2


# ===================================================================
# check_db_integrity
# ===================================================================


class TestCheckDbIntegrity:
    def test_healthy_db(self, store: GluonStore):
        result = check_db_integrity(store)
        assert result.status == "ok"
        assert result.name == "Database Integrity"

    def test_result_is_diagnostic(self, store: GluonStore):
        result = check_db_integrity(store)
        assert isinstance(result, DiagnosticResult)


# ===================================================================
# check_orphan_processes
# ===================================================================


class TestCheckOrphanProcesses:
    def test_no_active_runs(self, store: GluonStore):
        result = check_orphan_processes(store)
        assert result.status == "ok"

    def test_running_with_valid_pid(self, store: GluonStore, project_path: Path):
        import os

        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")
        run.status = RunStatus.RUNNING
        run.pid = os.getpid()  # Current process, guaranteed alive
        store.update_run(run)

        result = check_orphan_processes(store)
        assert result.status == "ok"

    def test_running_with_dead_pid(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")
        run.status = RunStatus.RUNNING
        run.pid = 999999999  # Almost certainly dead
        store.update_run(run)

        result = check_orphan_processes(store)
        assert result.status == "error"
        assert result.fixable is True
        assert len(result.details) == 1

    def test_completed_runs_ignored(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")
        run.status = RunStatus.COMPLETED
        run.pid = 999999999
        store.update_run(run)

        result = check_orphan_processes(store)
        assert result.status == "ok"


# ===================================================================
# check_stale_runs
# ===================================================================


class TestCheckStaleRuns:
    def test_no_stale_runs(self, store: GluonStore):
        result = check_stale_runs(store)
        assert result.status == "ok"

    def test_recent_running_not_stale(self, store: GluonStore, project_path: Path):
        from gluon.models import utc_now

        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")
        run.status = RunStatus.RUNNING
        run.started_at = utc_now()
        store.update_run(run)

        result = check_stale_runs(store)
        assert result.status == "ok"

    def test_old_running_is_stale(self, store: GluonStore, project_path: Path):
        from datetime import timedelta

        from gluon.models import utc_now

        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")
        run.status = RunStatus.RUNNING
        run.started_at = utc_now() - timedelta(hours=5)
        store.update_run(run)

        result = check_stale_runs(store)
        assert result.status == "warn"
        assert result.fixable is True
        assert len(result.details) == 1


# ===================================================================
# check_log_disk_usage
# ===================================================================


class TestCheckLogDiskUsage:
    def test_nonexistent_dir(self, tmp_path: Path):
        result = check_log_disk_usage(tmp_path / "nonexistent")
        assert result.status == "ok"

    def test_small_log_dir(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "test.log").write_text("small log content")

        result = check_log_disk_usage(log_dir)
        assert result.status == "ok"
        assert "MB" in result.message


# ===================================================================
# check_stale_pending_questions
# ===================================================================


class TestCheckStalePendingQuestions:
    def test_no_questions(self, store: GluonStore):
        result = check_stale_pending_questions(store)
        assert result.status == "ok"

    def test_expired_question_detected(self, store: GluonStore, project_path: Path):
        from datetime import timedelta

        from gluon.models import PendingQuestion, utc_now

        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")

        q = PendingQuestion(
            run_id=run.id,
            question_text="What now?",
            header="Choice",
            options=[
                {"label": "a", "description": "Option A"},
                {"label": "b", "description": "Option B"},
            ],
            expires_at=utc_now() - timedelta(hours=1),
        )
        store.create_pending_question(q)

        result = check_stale_pending_questions(store)
        assert result.status == "warn"
        assert result.fixable is True


# ===================================================================
# check_llm_provider_config
# ===================================================================


class TestCheckLlmProviderConfig:
    """Tests for the LLM provider config diagnostic."""

    def test_bedrock_ok_when_region_and_creds_set(self, store: GluonStore, monkeypatch):
        from gluon.doctor import check_llm_provider_config

        monkeypatch.setenv("GLUON_LLM_PROVIDER", "bedrock")
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "token")
        result = check_llm_provider_config(store)
        assert result.status == "ok"
        assert "Bedrock" in result.message

    def test_vertex_errors_without_project_id(self, store: GluonStore, monkeypatch):
        from gluon.doctor import check_llm_provider_config

        monkeypatch.setenv("GLUON_LLM_PROVIDER", "vertex")
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        result = check_llm_provider_config(store)
        assert result.status == "error"
        assert any("ANTHROPIC_VERTEX_PROJECT_ID" in d for d in result.details)

    def test_vertex_ok_with_project_id_and_creds(self, store: GluonStore, monkeypatch, tmp_path):
        from gluon.doctor import check_llm_provider_config

        monkeypatch.setenv("GLUON_LLM_PROVIDER", "vertex")
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-project")
        monkeypatch.setenv("CLOUD_ML_REGION", "global")
        # Simulate a service account key file
        key = tmp_path / "sa.json"
        key.write_text("{}")
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key))
        result = check_llm_provider_config(store)
        assert result.status == "ok"

    def test_foundry_errors_without_endpoint(self, store: GluonStore, monkeypatch):
        from gluon.doctor import check_llm_provider_config

        monkeypatch.setenv("GLUON_LLM_PROVIDER", "foundry")
        monkeypatch.delenv("ANTHROPIC_FOUNDRY_RESOURCE", raising=False)
        monkeypatch.delenv("ANTHROPIC_FOUNDRY_BASE_URL", raising=False)
        monkeypatch.delenv("ANTHROPIC_FOUNDRY_API_KEY", raising=False)
        result = check_llm_provider_config(store)
        assert result.status == "error"

    def test_foundry_ok_with_resource_and_api_key(self, store: GluonStore, monkeypatch):
        from gluon.doctor import check_llm_provider_config

        monkeypatch.setenv("GLUON_LLM_PROVIDER", "foundry")
        monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-res")
        monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "sk-test")
        result = check_llm_provider_config(store)
        assert result.status == "ok"

    def test_anthropic_warns_without_api_key(self, store: GluonStore, monkeypatch):
        from gluon.doctor import check_llm_provider_config

        monkeypatch.setenv("GLUON_LLM_PROVIDER", "anthropic")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = check_llm_provider_config(store)
        # warn, not error — user may have `claude login` session
        assert result.status == "warn"


# ===================================================================
# run_diagnostics
# ===================================================================


class TestRunDiagnostics:
    def test_returns_all_checks(self, store: GluonStore, tmp_path: Path):
        results = run_diagnostics(store, tmp_path / "logs")
        # 7 pre-existing + check_claude_session_disk_usage (C5) + check_llm_provider_config
        assert len(results) == 9
        assert all(isinstance(r, DiagnosticResult) for r in results)

    def test_all_ok_for_clean_store(self, store: GluonStore, tmp_path: Path, monkeypatch):
        # Force the default Bedrock provider with enough env set to pass the
        # new check_llm_provider_config check.
        monkeypatch.delenv("GLUON_LLM_PROVIDER", raising=False)
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
        results = run_diagnostics(store, tmp_path / "logs")
        assert all(r.status == "ok" for r in results), [
            (r.name, r.status, r.message) for r in results if r.status != "ok"
        ]


# ===================================================================
# Fix functions
# ===================================================================


class TestFixOrphanProcesses:
    def test_fixes_dead_pid_run(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")
        run.status = RunStatus.RUNNING
        run.pid = 999999999
        store.update_run(run)

        fixed = fix_orphan_processes(store)
        assert fixed == 1

        updated = store.get_run(run.id)
        assert updated is not None
        assert updated.status == RunStatus.FAILED

    def test_no_orphans_returns_zero(self, store: GluonStore):
        fixed = fix_orphan_processes(store)
        assert fixed == 0


class TestFixStaleRuns:
    def test_fixes_stale_run(self, store: GluonStore, project_path: Path):
        from datetime import timedelta

        from gluon.models import utc_now

        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")
        run.status = RunStatus.RUNNING
        run.started_at = utc_now() - timedelta(hours=5)
        store.update_run(run)

        fixed = fix_stale_runs(store)
        assert fixed == 1

        updated = store.get_run(run.id)
        assert updated is not None
        assert updated.status == RunStatus.FAILED

    def test_recent_run_not_fixed(self, store: GluonStore, project_path: Path):
        from gluon.models import utc_now

        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")
        run.status = RunStatus.RUNNING
        run.started_at = utc_now()
        store.update_run(run)

        fixed = fix_stale_runs(store)
        assert fixed == 0


class TestFixStalePendingQuestions:
    def test_expires_stale_questions(self, store: GluonStore, project_path: Path):
        from datetime import timedelta

        from gluon.models import PendingQuestion, utc_now

        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")

        q = PendingQuestion(
            run_id=run.id,
            question_text="What now?",
            header="Choice",
            options=[
                {"label": "a", "description": "Option A"},
                {"label": "b", "description": "Option B"},
            ],
            expires_at=utc_now() - timedelta(hours=1),
        )
        store.create_pending_question(q)

        fixed = fix_stale_pending_questions(store)
        assert fixed == 1


class TestRunAllFixes:
    def test_returns_dict(self, store: GluonStore):
        result = run_all_fixes(store)
        assert isinstance(result, dict)
        assert "orphan_processes" in result
        assert "stale_runs" in result
        assert "stale_questions" in result

    def test_with_actual_fixable_data(self, store: GluonStore, project_path: Path):
        from datetime import timedelta

        from gluon.models import PendingQuestion, utc_now

        project = store.create_project("test", project_path)

        # Create an orphan run (dead PID)
        orphan_run = store.create_run(project.id, "orphan task")
        orphan_run.status = RunStatus.RUNNING
        orphan_run.pid = 999999999
        store.update_run(orphan_run)

        # Create a stale run (>4h)
        stale_run = store.create_run(project.id, "stale task")
        stale_run.status = RunStatus.RUNNING
        stale_run.started_at = utc_now() - timedelta(hours=5)
        store.update_run(stale_run)

        # Create an expired question
        q = PendingQuestion(
            run_id=stale_run.id,
            question_text="What?",
            header="Choice",
            options=[
                {"label": "a", "description": "A"},
                {"label": "b", "description": "B"},
            ],
            expires_at=utc_now() - timedelta(hours=1),
        )
        store.create_pending_question(q)

        result = run_all_fixes(store)
        assert result["orphan_processes"] >= 1
        assert result["stale_questions"] >= 1


# ===================================================================
# Additional edge cases
# ===================================================================


class TestCheckOrphanProcessesEdgeCases:
    def test_running_with_no_pid(self, store: GluonStore, project_path: Path):
        """Run with status=RUNNING but pid=None should NOT be flagged as orphan."""
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "no-pid task")
        run.status = RunStatus.RUNNING
        run.pid = None
        store.update_run(run)

        result = check_orphan_processes(store)
        assert result.status == "ok"

    def test_multiple_orphans(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)

        for i in range(3):
            run = store.create_run(project.id, f"orphan-{i}")
            run.status = RunStatus.RUNNING
            run.pid = 999999990 + i
            store.update_run(run)

        result = check_orphan_processes(store)
        assert result.status == "error"
        assert len(result.details) == 3
        assert "3 run(s)" in result.message


class TestFixOrphanProcessesEdgeCases:
    def test_error_message_content(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "check-msg")
        run.status = RunStatus.RUNNING
        run.pid = 999999999
        store.update_run(run)

        fix_orphan_processes(store)

        updated = store.get_run(run.id)
        assert updated is not None
        assert "gluon doctor" in (updated.error_message or "")


class TestFixStaleRunsEdgeCases:
    def test_error_message_content(self, store: GluonStore, project_path: Path):
        from datetime import timedelta

        from gluon.models import utc_now

        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "stale-msg")
        run.status = RunStatus.RUNNING
        run.started_at = utc_now() - timedelta(hours=6)
        store.update_run(run)

        fix_stale_runs(store)

        updated = store.get_run(run.id)
        assert updated is not None
        assert "gluon doctor" in (updated.error_message or "")
        assert "hours" in (updated.error_message or "")


class TestCheckStalePendingQuestionsEdgeCases:
    def test_non_expired_question_ok(self, store: GluonStore, project_path: Path):
        from datetime import timedelta

        from gluon.models import PendingQuestion, utc_now

        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "future q")

        q = PendingQuestion(
            run_id=run.id,
            question_text="What?",
            header="Choice",
            options=[
                {"label": "a", "description": "A"},
                {"label": "b", "description": "B"},
            ],
            expires_at=utc_now() + timedelta(hours=1),  # Future — not expired
        )
        store.create_pending_question(q)

        result = check_stale_pending_questions(store)
        assert result.status == "ok"


class TestCheckStaleRunsEdgeCases:
    def test_running_with_no_started_at(self, store: GluonStore, project_path: Path):
        """Run with status=RUNNING but no started_at should NOT be flagged."""
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "no-start")
        run.status = RunStatus.RUNNING
        run.started_at = None
        store.update_run(run)

        result = check_stale_runs(store)
        assert result.status == "ok"
