# Bounded container dependency refresh

Acceptance: retain Python3.12/Trixie and all runtime capabilities; use supported patched GitHub CLI; compare actual inventories and complete unsuppressed HIGH/CRITICAL scans using identical scanner/database; never publish or deploy an unqualified image.

- [x] Inspect issue189, current default Dockerfile/gates, registry metadata and upstream fixes.
- [x] Start a fresh branch from default, preserving PR174 and all old refs.
- [x] Build a secret-excluded tracked-source candidate with the newer Python3.12 base and GitHub CLI2.102.1.
- [ ] Compare actual package inventories and same-database full baseline/candidate Trivy reports; run offline consumer and relevant source gates.
- [ ] Review, commit, push and open a draft PR with partial reduction and explicit residuals.
- [?] Pass the complete image release gate and qualify both architectures (blocked: unpatched Trixie criticals, pip-vendored highs and braces high; no publication or deployment in scope).

## Current evidence

The same Trivy0.76.0 database (SHA256 `50b0d49617d6efa3a1883b1f84b07c321c7f31bc1a4cda43575df3093fc6f897`) scans the existing local qualified baseline `53cafc7ba196...` at5critical/207high and new amd64 candidate `d5749d85f236...` at3critical/203high. Removed records are two critical certifi versions and four high GitHub CLI Go components; no high/critical records were added. The older hosted October20 report's3critical/199high is a different database checkpoint and is not used as the reduction denominator.

The base remains Python3.12.15/Trixie. GitHub CLI2.102.1 embeds Go1.27.3/x-net0.60.0. All74 installed application distributions match the final lock under PEP503 normalization, with no dev tools. Source hashes match. Network-none/read-only isolated API200readiness/401auth/static, certifi SSL trust, gh/gluon help and Chromium local-page rendering pass. Fresh universal98registry Python, webBun and webnpm audits return zero. Scoped Vercel npm audit has21affected high package nodes from the single unpatched braces advisory GHSA-vfj7-8cjw-p6xm.

Residuals:3critical/198high OS records, four pip-vendored highs and one braces high; no suppression or gate changes. Full publication gate still fails. Only amd64 was built/tested. Neither ARM64 nor production/provider/publication/deployment acceptance is claimed. Local fixtures exclude all environment/secret/key/credential files. No original compose services or credential mounts were used.

Webcheck,34tests andbuild pass with Node24.18.0. Initial shell PATH selected older systemNode and failed ESM worker startup; corrected runtime-only rerun passes. Fresh required backend gate on final certifi graph is still running; its result will be recorded before handoff. All logs and complete inventories/scans reside at `/home/mcutler/.claude/session-summaries/2026-10-21-dependency-remediation/gluon-agent/image-refresh/`.
