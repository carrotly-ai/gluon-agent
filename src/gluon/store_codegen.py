"""Codegen / drift-guard helpers for the ``execution_runs`` persistence (#166).

The ``execution_runs`` round-trip is hand-maintained across four places — the
``CREATE TABLE`` + 76 append-only ``ALTER TABLE ... ADD COLUMN`` migrations, the
``create_run`` INSERT column list, the ``update_run`` UPDATE column list, and
``_row_to_run`` — so a model field can silently drift out of sync with the
schema (a new field with no column never persists; a migration column with no
field is dead weight). This module derives the canonical column list from the
``ExecutionRun`` model so the drift-guard tests can assert the schema, writes,
and reads all agree.

**Why the full INSERT/UPDATE/_row_to_run code-generation was NOT done here.**
The owner greenlit "build codegen tooling"; on grounding, the 86 columns use 7+
*bespoke* per-column transforms (plain; ``RunStatus``/``CircuitState``/
``ApprovalPolicy`` enums, each with its own fallback default; ``_parse_datetime``;
bool-from-int with per-column defaults — ``use_worktree`` False vs
``auto_resume_enabled`` True; ints with varying defaults 0/50/100; ``Path``-wrap;
``json.loads``→dict; ``json.loads``→model / model-list). A generic
serialize/deserialize that *value-matches* the explicit ``_row_to_run`` for every
column would encode all ~86 transforms+defaults into a ``dict[str, Callable]``
registry — relocating the complexity rather than reducing it, type-checking
worse than the explicit constructor in this mypy-checked, corruption-sensitive
path. So the runtime swap is deferred (see issue #166); the drift-guard below
delivers the actual maintenance benefit (drift becomes a failing test) without
that risk.
"""

from __future__ import annotations

from gluon.models import ExecutionRun

# DB columns that exist (from old migrations) but are no longer part of the
# ExecutionRun model or its round-trip — vestigial and harmless, left in place
# because the MIGRATIONS list is append-only. A NEW orphan (a migration column
# with no model field) or a MISSING column (a model field with no column) makes
# ``test_execution_runs_schema_matches_model`` fail.
KNOWN_LEGACY_DB_COLUMNS: frozenset[str] = frozenset({"queued_followup", "queued_followup_at"})


def execution_run_columns() -> list[str]:
    """The canonical persisted column list, derived from the ExecutionRun model.

    Every model field maps 1:1 to an ``execution_runs`` column (asserted by the
    drift-guard tests). This is the single source of truth a future codegen of
    the INSERT/UPDATE/_row_to_run bodies would build on.
    """
    return list(ExecutionRun.model_fields.keys())
