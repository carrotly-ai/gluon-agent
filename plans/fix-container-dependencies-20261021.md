# Bounded container dependency refresh

Acceptance: retain Python3.12/Trixie and all runtime capabilities; use supported patched GitHub CLI; compare actual inventories and complete unsuppressed HIGH/CRITICAL scans using identical scanner/database; never publish or deploy an unqualified image.

- [x] Inspect issue189, current default Dockerfile/gates, registry metadata and upstream fixes.
- [x] Start a fresh branch from default, preserving PR174 and all old refs.
- [x] Build a secret-excluded tracked-source candidate with the newer Python3.12 base and GitHub CLI 2.102.1.
- [x] Compare actual package inventories and same-database full baseline/candidate Trivy reports; run offline consumer and relevant source gates.
- [x] Review, commit, push and open a draft PR with partial reduction and explicit residuals.
- [?] Pass the complete image release gate and qualify both architectures (blocked: unpatched Trixie criticals, pip-vendored highs and braces high; no publication or deployment in scope).

## Current evidence

The same Trivy 0.76.0 database (SHA256 `50b0d49617d6efa3a1883b1f84b07c321c7f31bc1a4cda43575df3093fc6f897`) scans the existing local qualified baseline `53cafc7ba196...` at 5 critical / 207 high and new amd64 candidate `d5749d85f236...` at 3 critical / 203 high. Removed records are two critical certifi versions and four high GitHub CLI Go components; no high/critical records were added. The older hosted October20 report's3critical/199high is a different database checkpoint and is not used as the reduction denominator.

The base remains Python 3.12.15/Trixie. GitHub CLI 2.102.1 embeds Go 1.27.3/x-net 0.60.0. All 74 installed application distributions match the final lock under PEP503 normalization, with no dev tools. Source hashes match. Network-none/read-only isolated API readiness 200, authentication rejection 401 and static serving, certifi SSL trust, gh/gluon help and Chromium local-page rendering pass. Fresh universal 98 registry-pair Python, web Bun and web npm audits return zero. Scoped Vercel npm audit has 21 affected high package nodes from the single unpatched braces advisory GHSA-vfj7-8cjw-p6xm.

Residuals: 3 critical / 198 high OS records, four pip-vendored highs and one braces high; no suppression or gate changes. Full publication gate still fails. Only amd64 was built/tested. Neither ARM64 nor production/provider/publication/deployment acceptance is claimed. Local fixtures exclude all environment/secret/key/credential files. No original compose services or credential mounts were used.

Web check, 34 tests and build pass with Node 24.18.0. Initial shell PATH selected older system Node and failed ESM worker startup; corrected runtime-only rerun passes. Fresh required backend gate on the final certifi graph passed: 568 tests, no skips or failures, six visible warnings, Ruff passed with two existing invalid-noqa warnings, and locked all-extras sync passed on Python 3.12. The earlier pre-certifi run is retained separately and is not credited as final-graph evidence. All logs and complete inventories/scans reside at `/home/mcutler/.claude/session-summaries/2026-10-21-dependency-remediation/gluon-agent/image-refresh/`.

## OS completeness

The remaining 201 OS records (3 critical and 198 high) have zero nonempty Trivy FixedVersion values. An exclusively owned read-only image container refreshed signed apt metadata into temporary filesystems only: all 54 affected installed packages equal their configured Trixie apt candidates, and the complete image has no upgradable packages. No host apt command, package installation, or shared service was used. Exact policy/version evidence is retained in `apt-candidates.log` and `apt-candidates.json`.

## Final handoff

Draft PR: https://github.com/carrotly-ai/gluon-agent/pull/190. The final graph passes the complete required offline PR baseline locally: 568 backend tests, Ruff, frozen web install, Biome check with one existing warning, 34 web tests, TypeScript/production build, and full application lock audits. The independent Vercel tooling graph retains one unpatched braces advisory affecting 21 npm package nodes. System CA and certifi trust probes pass. The full image release gate remains honestly blocked at 3 critical and 203 high records; no image publication or deployment occurred. Only plan documentation changed after the tested source commit; exact tree/head and one hosted checkpoint are recorded in the external result.
