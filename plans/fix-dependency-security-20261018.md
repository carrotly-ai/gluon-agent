# Dependency security remediation — 2026-10-18

## Acceptance criteria
Preserve application behavior, extras, manifests, and security gates. Resolve published, compatible dependency fixes in both Python and web graphs; document upstream or compatibility residue precisely. Use fictional fixtures without live providers. Ship one reviewed feature branch and PR with exact validation evidence.

## Tasks
- [x] Read repository/rules, clone separately, refresh default, alerts, package indexes, and matching PRs.
- [x] Reproduce baseline gates and map production/dev/extra dependency paths.
- [x] Apply targeted compatible dependency updates and add boundary coverage where meaningful.
- [x] Run required gates, full diagnostics, builds, and both ecosystem audits; document residue.
- [x] Review diff, commit, refresh remote head, push, open/link PR #174, and verify clean/pushed state.
- [?] Parent evidence review and final-head GitHub checks (blocked: pending external acceptance gates; PR remains draft).

## Dependency paths and decisions
- Core Python: Claude Agent SDK → MCP → PyJWT[crypto], AnyIO, Starlette, multipart, settings; Authlib → joserfc/cryptography; Anthropic[vertex] → Google auth → pyasn1/urllib3. Keep MCP v1, SDK v0.2, existing FastAPI, all providers, and optional features. Add security floors following the existing manifest pattern.
- Discord/all extras: aiohttp → multidict/yarl. Keep the multidict floor in these extras rather than making Discord libraries mandatory in the base install.
- Dev extra: pre-commit → virtualenv → python-discovery. Update only these implicated packages.
- Web runtime: React Router. Build/dev: Vite/esbuild, Sharp/libvips, Babel/browser mapping, PostCSS/source maps, fast-uri, nanoid, brace-expansion/minimatch. Vite 7.3.7 explicitly accepts esbuild ^0.28; minimatch 10.2.6 explicitly accepts brace-expansion ^5.0.8. No forced incompatible transitive major.
- Sharp moves 0.34 → 0.35 to receive fixed native libraries. Node 24 satisfies its Node >=20.9 requirement. All nine icon resize/PNG operations and both maskable composites pass in memory.
- Preserve npm and Bun locks independently. Selectively patch the existing npm graph. For Bun, export its installed baseline graph through npm, apply only vulnerable-package updates, and import that patched graph back into Bun. This preserves the original React, Biome, and Tailwind pins; 131 existing Bun package entries change, compared with 232 when importing the differently aged npm graph wholesale. Validate every parent constraint in both graphs; hoisting changes do not force incompatible majors. Do not blanket re-resolve Python or migrate MCP to v2.

## Evidence collected
- Live GitHub alerts: 95 (2 critical, 45 high, 36 medium, 12 low). No matching open PR. Base: 1b0bf8b.
- Every live vulnerable range is absent from the updated resolved package versions (95/95 checked in external alert-coverage.json).
- Baseline critical-boundary reproductions: six failures (three PyJWT PEM mutations; AnyIO IDNA hostname; SDK resume token binding with malicious/control values). Patched boundary suite: six passes, no provider process or network calls. AnyIO correctly supplies ASCII bytes; the test was corrected from an initial string expectation.
- Refreshed pip-audit found additional SDK argument injection, MCP session retention, and multidict reference leakage not in the original GitHub snapshot. Include compatible SDK 0.2.121, MCP 1.30.0, and multidict 6.9.1 patches. Final all-extras pip-audit, npm audit, and Bun audit report zero known vulnerabilities.
- Baseline and patched web gates: check passes with the same existing warning/info; 34 tests pass; TypeScript/Vite/PWA build passes. Baseline and patched mypy pass across 96 source files. Ruff passes.
- Original Python 3.13 baseline gate: 458 passed, four warnings, 724.61 seconds. Updated Python 3.13 full suite: 2,493 passed, zero skips, ten warnings, 214.60 seconds using tmpfs temporary fixtures; no test selection or gate severity changed. CI-equivalent Python 3.12 dev/web gate: 464 passed, zero skips, five warnings, 819.35 seconds. Python 3.13 all-extras required gate: 464 passed, five warnings, 829.37 seconds. The redundant disk-backed full Python 3.13 run continues in the background; the complete 2,493-test suite has already passed with tmpfs fixtures, so this is additional diagnostics rather than an unresolved local gate.
- Initial local source-distribution build picked up the nonstandard validation venv directory and failed on its absolute Python symlink. Validate distributions from a clean Git archive so local test tooling cannot enter the artifact. No packaging/source change is warranted for this local artifact.

## Scope boundaries
No application auth/crypto, API/schema, deployment, provider, or security-feature settings changes. CI only adds the critical-boundary test file to the existing backend gate. Original checkout and recovery bytes remain untouched. Live-provider behavior and deployment are not validated by offline fixtures.

## Final review refinements
- Auditing the older Bun graph separately exposed additional compatible fixes in Vitest/mocker, undici, Rollup, ajv, lodash, picomatch, minimatch, and brace-expansion. Add a Vitest >=4.1.11 floor for its mock redirect file-read advisory. Both final web graphs audit clean; final Bun check, 34 tests, and TypeScript/Vite/PWA build pass.
- Validate both root manifests/locks with frozen installations and complete dependency-tree checks. Original React 19.2.0, Biome 2.3.10, and Tailwind 4.1.17 remain pinned in Bun.
- Clean committed-source wheel and sdist builds pass, and their member lists contain no validation venv or node_modules. The later web-only refinement does not change Python source, metadata, or test results.

## Handoff
- PR: https://github.com/carrotly-ai/gluon-agent/pull/174 (draft, linked to T3).
- All local acceptance gates pass. No known vulnerability residue remains in the audited current graphs; the default branch still reports its 95 alerts until merge and GitHub graph refresh.
- Python dependency/source/CI-test evidence applies to implementation commit 3ee0028f72ce2538d7675c41d9d84463692fd300; commit 8ce7be0126b412202b722a3b5d82882495587a6b only narrows the web lock and raises its Vitest floor, with fresh web validation. The final follow-up changes this plan only.
- Full evidence and final status live under /home/mcutler/.claude/session-summaries/2026-10-18-carrotly-remediation/gluon-agent-security/. Parent must independently review the evidence and required GitHub checks before taking the PR out of draft. No merge is authorized.
