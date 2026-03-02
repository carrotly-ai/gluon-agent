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

    chain_id = await executor.execute(
        template=template,
        project_id=project.id,
        variables={"desc": "test feature"},
        initiator="test",
    )

    assert chain_id
    chain_executor.start_chain.assert_called_once_with(chain_id)

    # Verify steps were created
    steps = store.list_steps(chain_id)
    assert len(steps) == 2
    assert steps[0].prompt == "Do test feature"
