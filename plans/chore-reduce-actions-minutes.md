# Reduce GitHub Actions minutes

Branch: `chore/reduce-actions-minutes`

## Cost drivers

- The latest 100 runs include 20 CI PR runs, 21 Docker PR builds, seven CI main pushes and seven Docker main builds. CI spawned three jobs, including coverage and a web build; Docker built two architectures.

## Acceptance criteria

- [x] PRs run one fast Ruff check with concurrency cancellation and a five minute limit.
- [x] Full tests, type checks, and web build move to local quality gates before push.
- [x] Multi architecture image publishing runs on version tags or a manual main branch dispatch, retaining the `latest` image tag.
- [x] Validate workflows with actionlint and run the PR command locally.

Local gate: `uvx ruff check src tests && uv run --all-extras pytest tests -q`; in `web-ui`, run `bun install --frozen-lockfile && bun run check && bun run build` for UI changes.

Validation for this change: actionlint and Ruff passed. The full local suite exceeded 180 seconds and was stopped before completion; no application code changed.

## Baseline PR checkpoint

## Acceptance criteria

- Every PR emits `pr-check`; applicable validation failures, cancellations and unexpected skips fail.
- Frozen installs, existing offline checks, no production services/credentials.
- Hosted lightweight/public jobs; private heavier suites on Linux, Apple builds on mini.
- Preserve existing release/deployment/security jobs and related local CI work.

## Tasks

- [x] Inspect default branch, related CI PR, manifests and instructions.
- [x] Implement repository-specific baseline and runner routing.
- [x] Validate workflow and run supported local checks; document failures.
- [x] Prepare and ship one feature-branch commit and PR.

- [x] Preserve main-branch Docker publication and default-branch-only latest tags; run380foundation and78API-auth/security assertions on PRs, full2487suite manual.

## Baseline validation evidence

- actionlint v1.7.7 and `git diff --check` passed.
- `uv sync --locked --extra dev --extra web` — passed.
- `uv run --locked --extra dev --extra web ruff check src tests` — passed.
- `uv run --locked --extra dev --extra web pytest tests/test_auth.py tests/test_models.py tests/test_agent_config.py tests/test_llm_provider.py tests/test_security_phase3.py tests/test_workspace_env_isolation.py tests/test_store.py tests/test_git_manager.py -q` — passed.
- `uv run --locked --extra dev --extra web pytest tests/test_api_auth.py tests/test_api_authz.py -q` — passed.
- `bun install --frozen-lockfile` — passed.
- `bun run check` — passed.
- `bun run test` — passed.
- `bun run build` — passed.

- [x] Curated foundation380, API-auth/security78, and UI34tests passed. The nonrequired full2487-test diagnostic remains running in the background; its status is independent of the PR checkpoint.
