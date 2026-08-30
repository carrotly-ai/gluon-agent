"""Tests for workflow formulas."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gluon.formulas import (
    FormulaLoader,
    FormulaStepDef,
    FormulaTemplate,
    FormulaVariable,
    render_prompt,
    resolve_variables,
    validate_formula,
)


def test_load_builtin_feature_template():
    templates = FormulaLoader.discover()
    names = [t.name for t in templates]
    assert "feature" in names
    feature = next(t for t in templates if t.name == "feature")
    assert len(feature.steps) == 4
    assert feature.steps[0].id == "plan"


def test_load_builtin_bugfix_template():
    templates = FormulaLoader.discover()
    names = [t.name for t in templates]
    assert "bugfix" in names
    bugfix = next(t for t in templates if t.name == "bugfix")
    assert len(bugfix.steps) == 3


def test_variable_resolution_with_defaults():
    template = FormulaTemplate(
        name="test",
        variables=[
            FormulaVariable(name="foo", required=True),
            FormulaVariable(name="bar", default="baz"),
        ],
        steps=[FormulaStepDef(id="s1", name="S1", prompt="do {{foo}} with {{bar}}")],
    )
    resolved = resolve_variables(template, {"foo": "hello"})
    assert resolved == {"foo": "hello", "bar": "baz"}


def test_variable_resolution_missing_required():
    template = FormulaTemplate(
        name="test",
        variables=[FormulaVariable(name="foo", required=True)],
        steps=[FormulaStepDef(id="s1", name="S1", prompt="do {{foo}}")],
    )
    with pytest.raises(ValueError, match="Required variable 'foo'"):
        resolve_variables(template, {})


def test_render_prompt_substitution():
    result = render_prompt("Build {{feature}} with {{framework}}", {"feature": "auth", "framework": "FastAPI"})
    assert result == "Build auth with FastAPI"


def test_render_prompt_removes_unresolved():
    result = render_prompt("Build {{feature}} with {{optional}}", {"feature": "auth"})
    assert result == "Build auth with"


def test_validate_cycle_detection():
    template = FormulaTemplate(
        name="test",
        steps=[
            FormulaStepDef(id="a", name="A", prompt="x", depends_on=["b"]),
            FormulaStepDef(id="b", name="B", prompt="x", depends_on=["a"]),
        ],
    )
    errors = validate_formula(template)
    assert any("cycle" in e.lower() for e in errors)


def test_validate_missing_dependency():
    template = FormulaTemplate(
        name="test",
        steps=[
            FormulaStepDef(id="a", name="A", prompt="x", depends_on=["nonexistent"]),
        ],
    )
    errors = validate_formula(template)
    assert any("unknown step" in e.lower() for e in errors)


def test_validate_valid_formula():
    template = FormulaTemplate(
        name="test",
        steps=[
            FormulaStepDef(id="a", name="A", prompt="x"),
            FormulaStepDef(id="b", name="B", prompt="x", depends_on=["a"]),
        ],
    )
    errors = validate_formula(template)
    assert errors == []


@pytest.mark.anyio
async def test_formula_executor_creates_chain(tmp_path):
    from gluon.formula_executor import FormulaExecutor
    from gluon.store import GluonStore

    store = GluonStore(db_path=tmp_path / "test.db")

    # Create a test project
    project = store.create_project(name="test-proj", path=tmp_path)

    # Mock chain executor
    chain_executor = MagicMock()
    chain_executor.start_chain = AsyncMock()

    executor = FormulaExecutor(store, chain_executor)

    template = FormulaTemplate(
        name="test",
        variables=[FormulaVariable(name="desc", required=True)],
        steps=[
            FormulaStepDef(id="s1", name="Step1", prompt="Do {{desc}}"),
            FormulaStepDef(id="s2", name="Step2", prompt="Review", depends_on=["s1"]),
        ],
    )

    outcome = await executor.execute(
        template=template,
        project_id=project.id,
        variables={"desc": "test feature"},
        initiator="test",
    )

    assert outcome.kind == "workflow"
    assert outcome.chain_id
    assert outcome.step_count == 2
    chain_executor.start_chain.assert_called_once_with(outcome.chain_id)

    # Verify steps were created
    steps = store.list_steps(outcome.chain_id)
    assert len(steps) == 2
    assert steps[0].prompt == "Do test feature"


@pytest.mark.anyio
async def test_loop_formula_renders_watch_and_executor_model(tmp_path):
    """Phase C: a loop formula's templated watch_cmd/executor_model render to the
    resolved values, and an unset (default-empty) executor_model collapses to
    None rather than a literal '{{executor_model}}' masquerading as a model id."""
    from gluon.formula_executor import FormulaExecutor
    from gluon.store import GluonStore

    store = GluonStore(db_path=tmp_path / "test.db")
    project = store.create_project(name="test-proj", path=tmp_path)
    executor = FormulaExecutor(store, MagicMock())

    template = FormulaTemplate(
        name="watcher",
        kind="loop",
        objective="Keep {{scope}} green",
        watch_cmd="gh pr list --repo {{repo}} --json number --jq '.[].number' | grep -q .",
        executor_model="{{executor_model}}",  # templated; unset below → ""
        variables=[
            FormulaVariable(name="scope", default="all PRs"),
            FormulaVariable(name="repo", default="acme/widgets"),
            FormulaVariable(name="executor_model", default=""),
        ],
    )

    outcome = await executor.execute(
        template=template,
        project_id=project.id,
        variables={},  # take all defaults
        initiator="test",
    )

    assert outcome.kind == "loop"
    loop = store.get_agent_loop(outcome.loop_id)
    assert loop is not None
    assert loop.watch_cmd == "gh pr list --repo acme/widgets --json number --jq '.[].number' | grep -q ."
    assert loop.executor_model is None  # "{{executor_model}}" → "" → None, not a literal

    # And when provided, the executor model is threaded through.
    outcome2 = await executor.execute(
        template=template,
        project_id=project.id,
        variables={"executor_model": "claude-haiku-4-5"},
        initiator="test",
    )
    loop2 = store.get_agent_loop(outcome2.loop_id)
    assert loop2 is not None
    assert loop2.executor_model == "claude-haiku-4-5"
