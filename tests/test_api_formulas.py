"""Integration tests for the LIVE formula API routes.

Locks the behavior of GET /api/formulas (list) and POST /api/formulas/{name}/run
before the two internally-dead routes (GET /api/formulas/{name},
POST /api/formulas/validate) are removed (audit item 11).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def test_list_formulas_returns_list(api_client):
    client, _ = api_client
    resp = client.get("/api/formulas")
    assert resp.status_code == 200
    body = resp.json()
    assert "formulas" in body
    assert isinstance(body["formulas"], list)


def test_run_formula_not_found_404(api_client):
    client, _ = api_client
    with patch("gluon.formulas.FormulaLoader.load", return_value=None):
        resp = client.post("/api/formulas/does-not-exist/run", json={"project_id": "p", "variables": {}})
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_run_formula_success(api_client):
    client, _ = api_client
    template = SimpleNamespace(steps=[object(), object()])  # step_count == 2
    with (
        patch("gluon.formulas.FormulaLoader.load", return_value=template),
        patch("gluon.formula_executor.FormulaExecutor.execute", new=AsyncMock(return_value="chain-xyz")),
    ):
        resp = client.post("/api/formulas/my-formula/run", json={"project_id": "p", "variables": {"k": "v"}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chain_id"] == "chain-xyz"
    assert body["step_count"] == 2
