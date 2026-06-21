"""Regression test for the `gluon formula run` CLI command.

It previously called ``anyio.from_thread.run()`` from a synchronous Typer
command, which raises ``RuntimeError`` (there is no host event-loop thread to
delegate to) — the command was documented but non-functional. It must drive its
async body via ``anyio.run()`` instead.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from gluon.cli import app


def test_formula_run_executes_without_runtime_error() -> None:
    template = SimpleNamespace(name="my-formula", steps=[])
    orchestrator = MagicMock()
    orchestrator.get_project.return_value = SimpleNamespace(id="proj-123")

    formula_executor = MagicMock()
    formula_executor.execute = AsyncMock(return_value="chain-xyz")

    with (
        patch("gluon.formulas.FormulaLoader.load", return_value=template),
        patch("gluon.cli.get_orchestrator", return_value=orchestrator),
        patch("gluon.store.GluonStore"),
        patch("gluon.runner.TaskRunner"),
        patch("gluon.chain_executor.ChainExecutor"),
        patch("gluon.formula_executor.FormulaExecutor", return_value=formula_executor),
    ):
        result = CliRunner().invoke(app, ["formula", "run", "my-formula", "proj"])

    assert result.exit_code == 0, result.output
    assert "chain-xyz" in result.output
    formula_executor.execute.assert_awaited_once()
