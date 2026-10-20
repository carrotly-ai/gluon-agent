"""HTTP contracts for the intentionally retained public surfaces in issue #159."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from gluon.auth import SESSION_COOKIE_NAME
from gluon.models import (
    CircuitState,
    RunStatus,
    SupervisionConfig,
    SupervisionDecision,
    SupervisionPolicy,
    UserRole,
    utc_now,
)


@pytest.fixture(autouse=True)
def isolated_provider_and_auth(monkeypatch, temp_store):
    """Keep provider resolution on the fixture DB, never the user's store."""
    monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")
    monkeypatch.setenv("GLUON_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(
        "gluon.llm_provider._read_provider_setting",
        lambda: temp_store.get_setting("llm_provider"),
    )


def _authenticate(client, store, role: UserRole) -> None:
    user = store.create_user(
        username=role.value,
        display_name=f"Test {role.value}",
        auth_subject=f"test:{role.value}",
        role=role,
    )
    session = store.create_user_session(user.id, expires_at=utc_now() + timedelta(hours=1))
    client.cookies.set(SESSION_COOKIE_NAME, session.id)


@pytest.fixture
def public_resources(temp_store, tmp_path, api_client, monkeypatch):
    workspace = temp_store.create_workspace("public-contracts", tmp_path, daily_budget_usd=10, monthly_budget_usd=100)
    project = temp_store.create_project("contract-project", tmp_path / "project", workspace_id=workspace.id)
    run = temp_store.create_run(project.id, "A retained HTTP contract")
    run.status = RunStatus.REVIEW
    run.cost_usd = 2.5
    run.supervision_config = SupervisionConfig(
        policy=SupervisionPolicy.MANUAL,
        max_auto_resumes=3,
        min_time_between_resumes=120,
    )
    run.supervision_auto_resume_count = 2
    temp_store.update_run(run)
    client, _ = api_client
    monkeypatch.setattr(
        client.app.state.runner,
        "resume_in_place",
        AsyncMock(side_effect=AssertionError("Manual-policy contract tests must never launch an agent")),
    )
    return workspace, run


@pytest.mark.parametrize(
    ("environment", "stored", "provider", "name", "source"),
    [
        ("anthropic", "bedrock", "anthropic", "Anthropic", "environment variable"),
        (None, "anthropic", "anthropic", "Anthropic", "database setting"),
        (None, None, "bedrock", "AWS Bedrock", "default"),
    ],
)
def test_provider_reports_configuration_and_precedence(
    api_client, temp_store, monkeypatch, environment, stored, provider, name, source
):
    client, _ = api_client
    _authenticate(client, temp_store, UserRole.VIEWER)
    if environment is None:
        monkeypatch.delenv("GLUON_LLM_PROVIDER")
    else:
        monkeypatch.setenv("GLUON_LLM_PROVIDER", environment)
    if stored is not None:
        temp_store.set_setting("llm_provider", stored)

    response = client.get("/api/provider")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"provider", "name", "supports_cost_tracking", "source", "models"}
    assert body["provider"] == provider
    assert body["name"] == name
    assert body["source"] == source
    assert body["supports_cost_tracking"] is True
    assert {"haiku", "sonnet"} <= body["models"].keys()
    assert all(isinstance(model, str) and model for model in body["models"].values())


@pytest.mark.parametrize(
    ("role", "read_status", "budget_status", "supervision_status"),
    [
        (None, 401, 401, 401),
        (UserRole.VIEWER, 200, 403, 403),
        (UserRole.OPERATOR, 200, 403, 200),
        (UserRole.ADMIN, 200, 200, 200),
    ],
)
def test_retained_surfaces_enforce_roles(
    api_client, temp_store, public_resources, role, read_status, budget_status, supervision_status
):
    client, _ = api_client
    workspace, run = public_resources
    if role is not None:
        _authenticate(client, temp_store, role)

    assert client.get("/api/provider").status_code == read_status
    assert client.get(f"/api/runs/{run.id}/supervision").status_code == read_status
    assert (
        client.put(f"/api/workspaces/{workspace.id}/budget", json={"daily_budget_usd": 25}).status_code == budget_status
    )
    assert client.post(f"/api/runs/{run.id}/supervision/evaluate").status_code == supervision_status
    assert (
        client.post(f"/api/runs/{run.id}/supervision/disable", json={"reason": "Operator request"}).status_code
        == supervision_status
    )

    persisted_workspace = temp_store.get_workspace(workspace.id)
    assert persisted_workspace.daily_budget_usd == (25 if budget_status == 200 else 10)
    persisted_run = temp_store.get_run(run.id)
    assert persisted_run.supervision_config.enabled is (supervision_status != 200)
    decisions = temp_store.list_supervision_decisions(run.id)
    assert len(decisions) == (2 if supervision_status == 200 else 0)


def test_workspace_budget_updates_persist_and_zero_clears_only_requested_scope(
    api_client, temp_store, public_resources
):
    client, _ = api_client
    workspace, _ = public_resources
    _authenticate(client, temp_store, UserRole.ADMIN)

    response = client.put(
        f"/api/workspaces/{workspace.id}/budget",
        json={"daily_budget_usd": 20, "monthly_budget_usd": 200},
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": workspace.id,
        "name": workspace.name,
        "path": str(workspace.path),
        "project_count": 1,
        "auto_discover": workspace.auto_discover,
        "daily_budget_usd": 20,
        "monthly_budget_usd": 200,
        "daily_spend_usd": 2.5,
        "monthly_spend_usd": 2.5,
    }

    # Names remain accepted by external automation. Null and omitted values
    # preserve caps; an explicit zero clears the selected scope.
    response = client.put(
        f"/api/workspaces/{workspace.name}/budget",
        json={"daily_budget_usd": 0, "monthly_budget_usd": None},
    )
    assert response.status_code == 200
    assert response.json()["daily_budget_usd"] is None
    assert response.json()["monthly_budget_usd"] == 200
    response = client.put(f"/api/workspaces/{workspace.id}/budget", json={"daily_budget_usd": 30})
    assert response.status_code == 200
    persisted = temp_store.get_workspace(workspace.id)
    assert persisted.daily_budget_usd == 30
    assert persisted.monthly_budget_usd == 200
    assert client.get(f"/api/workspaces/{workspace.id}").json() == response.json()


@pytest.mark.parametrize(
    ("path_suffix", "method", "body", "field"),
    [
        ("budget", "put", {"daily_budget_usd": "not-a-number"}, "daily_budget_usd"),
        ("budget", "put", {"monthly_budget_usd": {}}, "monthly_budget_usd"),
        ("supervision/disable", "post", {"reason": ["not", "a", "string"]}, "reason"),
    ],
)
def test_retained_mutations_validate_input_without_changing_state(
    api_client, temp_store, public_resources, path_suffix, method, body, field
):
    client, _ = api_client
    workspace, run = public_resources
    _authenticate(client, temp_store, UserRole.ADMIN)
    resource = f"workspaces/{workspace.id}" if path_suffix == "budget" else f"runs/{run.id}"

    response = client.request(method, f"/api/{resource}/{path_suffix}", json=body)

    assert response.status_code == 422
    assert ["body", field] in [error["loc"] for error in response.json()["detail"]]
    persisted_workspace = temp_store.get_workspace(workspace.id)
    assert persisted_workspace.daily_budget_usd == 10
    assert persisted_workspace.monthly_budget_usd == 100
    assert temp_store.get_run(run.id).supervision_config.enabled is True
    assert temp_store.list_supervision_decisions(run.id) == []


def test_retained_resource_endpoints_return_404_for_unknown_ids(api_client, temp_store):
    client, _ = api_client
    _authenticate(client, temp_store, UserRole.ADMIN)
    requests = [
        ("put", "/api/workspaces/unknown/budget", {"daily_budget_usd": 20}),
        ("get", "/api/runs/unknown/supervision", None),
        ("post", "/api/runs/unknown/supervision/evaluate", None),
        ("post", "/api/runs/unknown/supervision/disable", {}),
    ]

    for method, path, body in requests:
        response = client.request(method, path, json=body)
        assert response.status_code == 404, path
        assert "not found" in response.json()["detail"].lower()


def test_supervision_status_evaluation_and_disable_preserve_audit_history(api_client, temp_store, public_resources):
    client, _ = api_client
    _, run = public_resources
    _authenticate(client, temp_store, UserRole.OPERATOR)
    previous = temp_store.create_supervision_decision(
        SupervisionDecision(
            run_id=run.id,
            decision="hold",
            reason="Awaiting operator",
            trigger="scheduler",
            circuit_state=CircuitState.CLOSED,
            completion_confidence=75,
            auto_resume_count=2,
        )
    )
    # Short run IDs are part of the existing HTTP resource lookup contract.
    path = f"/api/runs/{run.id[:8]}/supervision"
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {
        "run_id": run.id,
        "enabled": True,
        "policy": "manual",
        "max_auto_resumes": 3,
        "auto_resume_count": 2,
        "min_time_between_resumes": 120,
        "last_check_at": None,
        "last_resume_at": None,
        "disabled_reason": None,
        "recent_decisions": [
            {
                "timestamp": previous.timestamp.isoformat(),
                "decision": "hold",
                "reason": "Awaiting operator",
                "trigger": "scheduler",
                "circuit_state": "CLOSED",
                "completion_confidence": 75,
                "auto_resume_count": 2,
            }
        ],
    }

    response = client.post(f"{path}/evaluate")
    assert response.status_code == 200
    assert response.json() == {
        "run_id": run.id,
        "decision": "skip",
        "reason": "Manual policy - no auto-resume",
        "wait_seconds": 0,
    }
    evaluated = client.get(path).json()
    assert datetime.fromisoformat(evaluated["last_check_at"]) >= previous.timestamp
    assert evaluated["recent_decisions"][0]["decision"] == "skip"
    assert evaluated["recent_decisions"][0]["trigger"] == "manual"

    response = client.post(f"{path}/disable", json={"reason": "Release freeze"})
    assert response.status_code == 200
    disabled = response.json()
    assert disabled["enabled"] is False
    assert disabled["disabled_reason"] == "Release freeze"
    assert disabled["policy"] == "manual"
    assert disabled["auto_resume_count"] == 2
    assert [entry["decision"] for entry in disabled["recent_decisions"]] == ["disable", "skip", "hold"]
    assert client.get(path).json() == disabled
    persisted = temp_store.get_run(run.id)
    assert persisted.supervision_config.enabled is False
    assert persisted.supervision_disabled_reason == "Release freeze"
    client.app.state.runner.resume_in_place.assert_not_awaited()


def test_supervision_disable_accepts_default_reason(api_client, temp_store, public_resources):
    client, _ = api_client
    _, run = public_resources
    _authenticate(client, temp_store, UserRole.OPERATOR)

    response = client.post(f"/api/runs/{run.id}/supervision/disable", json={})

    assert response.status_code == 200
    assert response.json()["disabled_reason"] == "Manual disable"
    assert temp_store.get_run(run.id).supervision_disabled_reason == "Manual disable"


def test_supervision_evaluation_failure_is_an_http_error(api_client, temp_store, public_resources, monkeypatch):
    client, _ = api_client
    _, run = public_resources
    _authenticate(client, temp_store, UserRole.OPERATOR)
    monkeypatch.setattr(client.app.state.runner, "evaluate_supervision", AsyncMock(return_value=None))

    response = client.post(f"/api/runs/{run.id}/supervision/evaluate")

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to evaluate supervision"}
    assert temp_store.list_supervision_decisions(run.id) == []
