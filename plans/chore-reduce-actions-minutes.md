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
