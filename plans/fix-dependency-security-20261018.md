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

## Handoff (round 1)
- PR: https://github.com/carrotly-ai/gluon-agent/pull/174 (draft, linked to T3).
- All local acceptance gates pass. No known vulnerability residue remains in the audited current graphs; the default branch still reports its 95 alerts until merge and GitHub graph refresh.
- Python dependency/source/CI-test evidence applies to implementation commit 3ee0028f72ce2538d7675c41d9d84463692fd300; commit 8ce7be0126b412202b722a3b5d82882495587a6b only narrows the web lock and raises its Vitest floor, with fresh web validation. The final follow-up changes this plan only.
- Full evidence and final status live under /home/mcutler/.claude/session-summaries/2026-10-18-carrotly-remediation/gluon-agent-security/. Parent must independently review the evidence and required GitHub checks before taking the PR out of draft. No merge is authorized.

## Round 2: isolated container validation

Acceptance: build the actual Dockerfile using only clean committed source; start only an owned Compose project with no credentials, host accounts, external network, or production services. Compare the installed production Python inventory with the all-extra frozen lock, audit it, and exercise CLI, HTTP health, built web assets, and critical offline dependency boundaries. Preserve previous fixtures/processes and round-one evidence.

- [x] Read round-one results, parent disposition, current PR and repository/tool/security rules; confirm exclusive clean clone and unchanged remote head.
- [x] Reproduce current image dependency resolution and compare its actual inventory with uv.lock (41 changed versions, missing distro, and residual packages; baseline pip check/audit alone still pass).
- [x] Enforce the existing frozen lock with CI-pinned uv 0.11.28, all runtime extras, and an owned gluon virtualenv; preserve editable source, CLI symlink, entrypoint, and existing global Python tools.
- [x] Run final owned Compose build/deploy/smoke, actual image inventory/constraints/audits, six focused boundary tests, and Ruff at original severity. All executable checks pass; image global audit remains nonzero for the documented Vercel gate.
- [x] Correct the two Vite coverage entries and review the full source/configuration diff; verify all lockfiles equal the parent-reviewed head. Ship on the existing PR #174 branch with exact evidence and check URLs.
- [?] Parent reviews final evidence and final-head hosted checks (blocked: parent acceptance).

### Image inventory and newly exposed global tooling

- Locked candidate image: 73 third-party runtime distributions exactly match the Linux/Python 3.12 all-extra production export, plus gluon-agent itself. No missing, extra, or changed runtime distributions. Runtime and system Python tooling together: 77 exact-version distributions, zero known vulnerabilities.
- The baseline image resolves Anthropic 1.15.0, SDK 0.2.168, Redis 8.1.0, and 38 other unreviewed versions instead of the committed graph. Its fallback leaves MCP-2-only packages behind even after final MCP v1 installation. Frozen installation removes that drift, rather than asserting a clean lock audit proves the old pip image.
- Actual global CLI inventory initially contains 433 installed paths and 37 affected packages (1 critical, 26 high, 10 moderate). This is a separate graph from the clean web locks.
- A same-major npm 11.19.1 → 11.21.1 candidate removes all advisories beneath npm's own bundled tree, including its undici path, brace-expansion, http-cache-semantics, ip-address, and postcss-selector-parser. Node 24 satisfies its engine requirement. Include this bounded image-tool dependency patch.
- Residual Vercel 64.5.0: 33 affected packages (1 critical, 24 high, 8 moderate, including inherited/metavulnerability entries). Exact upstream pins include tar 7.5.11, undici 5.28.4/5.29.0, path-to-regexp 6.1.0/8.3.0, ajv 8.6.3, js-yaml 4.1.1, smol-toml 1.5.2, and minimatch 10.1.1. No newer stable Vercel 64 release is available in the checked compatible range.
- Ordinary targeted npm updates in the owned candidate fail on unavailable upstream development metadata despite --omit=dev: npm requests @npmcli/docs@^1.0.0; Vercel requests @vercel-internals/builder-orchestration@1.0.23. No access grants, scan bypasses, forced majors, package removals, or unreviewed bundle rewrites are applied.
- [?] Parent decides the global Vercel dependency policy before full security acceptance (blocked: exact upstream pins and unavailable internal metadata). Proposal: approve an isolated locked global-tool installation with explicit scoped overrides, reviewing out-of-range undici/busboy upgrades and testing the tool APIs; alternatively retain the draft while obtaining an upstream patched bundle. Removing Vercel or changing deployment architecture needs separate authorization.
- OS packages are inventoried; no OS-CVE scanner (Trivy, Grype, or Docker Scout) is available here. Python/global npm audits do not certify OS or embedded Claude binaries. Live provider calls, agents, production deployment, Mac/device behavior, and provider-backed CLI actions remain untested.
- Corrected external alert-coverage.json entries 103/104 to actual Vite 7.3.7 only (Vitest 4.1.18 was accidentally included). All 95 exclusions remain valid; the original artifact and round-one results are preserved.

### Final round-two validation

- Built image: `sha256:95df867ae3a70c5471d2318cac28ccf90e7aa86b322b49dd2dcb15489f6d4f7a`, Python 3.12.15. The clean archive plus Dockerfile overlay matches the committed source hash recorded in round2-final-source.json.
- Owned project `gluon-pr174-round2-final` is healthy. No mounts, volumes, published ports, environment files, or external runtime network. Prior own candidate fixture is also preserved. No pre-existing process/container/checkout was changed or killed.
- CLI help, live HTTP /api/health, built dashboard/assets, PID 1 uid 1000, six critical boundaries, and stale-lock rejection pass. Application lifespan is deliberately off: this is packaging/liveness validation, not orchestration/provider readiness. `uv pip check`: 74 distributions compatible, including the local project. Node/npm/Vercel/agent-browser/Biome version probes succeed offline.
- All 73 runtime third-party versions exactly equal the Linux/Python 3.12 frozen production export. Combined runtime and separate global Python tooling audit: 77 packages, zero advisories. Actual installed global npm audit: 433 package paths, 33 residual affected Vercel package entries; exit status remains 1 with no ignored advisories or reduced severity.
- Required Python 3.12/3.13 suites, full suite, API/auth subset, and 34 web tests from round one/parent remain applicable because no application source, lock, manifest, or workflow changes in this round. Do not repeat the 14-minute suite without a source or dependency-graph change. New hosted checks at the final commit remain for parent/T3 watch.
- Evidence and proposal: round2-final-*.json/log/tsv, round2-global-tooling-proposal.md, round2-global-npm-parent-constraints.json, round2-compatible-tooling-build-attempt*.log. Original round-one result/status/coverage are preserved as round1-* artifacts.

## Round 3: inert global Vercel tooling prototype

Acceptance: preserve parent-reviewed `c13f379` and production tooling policy. Check maintained upstream releases first, reproduce the actual installed advisory graph, then prepare an inert scoped lock/override diff and disposable image. Keep exact-pin relaxations and undici 5→6 / busboy 2→3 candidates outside product configuration. Exercise offline CLI/help, fictional local builds and HTTP/parser boundaries with runtime networking disabled. Inventory and audit every installed version/path, including duplicates; explicitly identify OS and unversioned bundle coverage gaps. Ship evidence only on this same draft PR for parent review.

- [x] Confirm exclusive clean clone, current instructions, unchanged remote PR head and parent evidence. The supplied container-validation evidence directory is absent; round-two artifacts are in the original security evidence directory.
- [x] Refresh upstream CLI/bundle and dependency metadata, reproduce baseline actual-image inventory/audit and offline behavior.
- [x] Construct and execute owned inert scoped-lock/override and image prototypes; record exact metadata gaps and observed alternatives.
- [x] Compare complete actual-image inventories/audits and HTTP/parser/local-build results; document a concrete parent decision with unresolved gates.
- [x] Review the evidence-only diff, commit/push the same branch, update/link draft PR and return exact head/pending checks without polling.

### Round-three executable findings

- Evidence is confined to /home/mcutler/.claude/session-summaries/2026-10-18-carrotly-remediation/gluon-agent-global-tooling-prototype/. Start with proposal.md, artifact-hashes.json, and the actual inert-scoped-vercel.patch. The patch passes git apply --check and remains unapplied. This repository update changes the plan only.
- Maintained registry latest remains Vercel 64.5.0. Installing it as a dependency of a private wrapper resolves a real upstream lock and override lock without unavailable development metadata. Exact endpoints for @vercel-internals/builder-orchestration/1.0.23 and @npmcli/docs/1.0.0 still return 404; no access change is needed for the wrapper. Missing braces 3.0.4 / smol-toml 1.8.1 are not treated as installed fixes; smol-toml 1.9.1 is actually available/tested.
- Built inert candidate: sha256:8af1d846eeaebaa82c1bf9f87c40e399a12caf534a34f2c3a080f733d19a5964. Of 439 checked tracked c13f379 source files, only the expected external prototype Dockerfile differs. All 288 Vercel dist files and its native executable remain byte-identical. No runtime policy, manifests, locks, middleware, auth/API/schema source, or workflow is changed in the PR.
- Baseline physical global graph: 433 installed paths, audit exit 1 with 33 entries (1 critical, 24 high, 8 moderate). Candidate complete image graph: 425 paths, audit exit 1 with 22 high entries, including one inherited entry for the private wrapper (21 published-package/inherited entries). The bulk advisory endpoint identifies only braces as a direct residual. Every installed nested/duplicate version is audited. All 276 scoped installed paths match the inert lock; the 100 omitted entries are optional platform packages. Raw upstream constraint checking finds no missing required dependency and 15 deliberate deviations: 13 same-major exact-pin relaxations and two undici 5→6 edges. They remain unapproved/inert.
- Node CLI/version/help plus fictional static and Node standalone builds pass offline (7/7); generated HTML/routes/headers and the Node function are consumed. Native CLI/version/help passes, but both native builds fail offline with BUILD_FAILED / Request failed in baseline and candidate. Experimental native execution is not an accepted replacement. All fixtures use owned temporary homes/settings, UID 1000, network_mode:none, no credentials/real mounts/host ports/providers/deployment. Preserve the healthy owned app and exited fixtures; no processes were signalled.
- Ten candidate HTTP/parser checks pass: actual loopback HTTP/JSON, CRLF request rejection, multipart form-data, cookie attribute rejection, numeric-PAX tar extraction, route parser 6/8, YAML aliases, AJV data constraints and TOML comments. Baseline reproduces both undici cookie injections and tar TypeError; candidate rejects/extracts correctly. A bounded braces check completes but does not reproduce or exclude the advisory.
- Separate inert busboy 2→3 alternative: undici 5.29.0 plus busboy 3.2.3 passes all four multipart cases, including previously crashing __proto__/constructor headers and actual Response.formData consumption. Its complete 436-path image still audits with 34 entries because it preserves the original global graph plus the probe; undici 5's cookie issue remains. The undici-6 candidate uses its own formdata-parser and has no external busboy package, so the busboy selector in that candidate has no match and is not claimed as an installed upgrade.
- Owned app health/assets, UID 1000, six existing Python boundaries, stale-lock rejection and pip compatibility pass. All 77 installed Python distribution versions equal the parent inventory after PEP 503 normalization; a fresh 77-package audit is clean. Earlier application suites remain applicable to this plan-only repository update.
- Remaining decision: parent may review adopting the scoped wrapper with the enumerated overrides as a partial repair, or wait for upstream. Neither choice clears unpatched braces 3.0.3 (https://github.com/advisories/GHSA-vfj7-8cjw-p6xm), unchanged opaque/static bundles, native build failures or OS CVE coverage. OS packages and opaque binary hashes are inventoried; Trivy/Grype/Scout are unavailable. No scanner suppression, removal of functionality, bundle rewriting/version relabelling, policy adoption, production operation or merge occurs.
- [?] Parent independently reviews prototype evidence, approves any concrete tooling policy, and decides remaining security gates (blocked: policy and unresolved upstream/coverage gates; retain draft PR #174).


## Round 4: approved scoped Vercel adoption

Parent decision: `gluon-agent-global-tooling-prototype/parent-scoped-adoption-decision.json`, 2026-10-19 SGT. This supersedes the historical owner-choice holds above for this bounded wrapper only. It approves 13 same-major pin relaxations and two Undici 5→6 edges; it does not grant blanket override authority or clear residual security/production gates.

Acceptance: prefer a smaller supported upstream fix if available; otherwise adopt the verified private locked wrapper while preserving all standard Node CLI commands, aliases, builders and platform optional packages. Verify actual final installed bytes, fresh empty-cache consumption, raw ranges, full inventories/audits including duplicates, original CLI/build/parser/app/Python contracts and required backend/web gates. No prototype content enters the production image. Keep draft for parent review, residual advisories and independent provider/production acceptance.

- [x] Refresh same open draft PR #174, exclusive new clone, instructions and original evidence; verify all 31 prior artifact hashes and exact patch SHA256 e61f3d08f3642e1974aa5a833e90531d0917d64548c6e0a67d75a6a52879e773.
- [x] Refresh maintained upstream registry/documentation; Vercel latest remains 64.5.0 and braces latest 3.0.3. The four recorded missing metadata versions still return 404. Reproduce baseline actual inventory and cookie/PAX failures before source edits.
- [x] Adopt production private identity `gluon-vercel-tooling` with coherent lock root metadata. Preserve every non-root lock entry. Omit the unmatched Busboy selector and all prototype-only COPY/files. Install with npm 11.21.1 and npm ci; preserve vercel/vc aliases and default Node execution.
- [x] Build final isolated owned image from exact source and verify fresh-lock installed state, published/SRI bytes, consumer resolution, raw constraints and unchanged dist/native bytes.
- [x] Run full actual npm/Python inventories and audits, original offline CLI/build/parser/app contracts and applicable repository gates; document every residual/coverage limitation.
- [x] Review diff, normal hooks, commit/push existing branch after head refresh, update/link same draft PR with exact evidence, and verify clean/upstream/live equality.
- [?] Parent independently reviews final evidence, residual braces and OS/embedded/native/provider/lifecycle acceptance (blocked: independent acceptance remains required; no deployment or merge).

Maintenance provenance: the prototype patch is inspected, not applied wholesale. Only the Dockerfile install and scoped package metadata/lock are adopted. Official CLI documentation: https://vercel.com/docs/cli. Final evidence is under `/home/mcutler/.claude/session-summaries/2026-10-18-carrotly-remediation/gluon-agent-tooling-approved-adoption/`; prior evidence and fixtures remain untouched.


### Round-four final candidate evidence

- Production candidate image: `sha256:bafee9cc66629df7d65832889e1df26c04a95192bf9d213fea691354f7dfccd7`. Build uses the final Dockerfile/manifest/lock and clean allowed source. No prototype directory exists in the image. The 110 runtime source/metadata files checked equal the context; source provenance records the exact count. All 288 Vercel dist files and the 170,396,864-byte native binary match baseline.
- All 276 installed scoped packages match their frozen lock versions and independently downloaded SRI-verified public tarballs. All 6,636 normalized published file records match installed bytes, accounting for stock esbuild's documented postinstall replacement of its JS launcher with the separately SRI-verified platform binary. Initial byte-check evidence is preserved: tar path normalization and this independently proven stock transform explain the initial fixture mismatches; no installed/vendor bytes were changed.
- A second new image installs into an absent node_modules directory with an empty cache using the final private manifest/lock. Its 276 package byte inventories equal the candidate, actual CLI/Node-builder resolution entries are recorded, and all seven Node CLI/static/Node consumed-build checks pass with build-time fixture networking disabled.
- Complete actual graph: 425 physical npm paths, including all duplicates and the wrapper. Fresh full npm audit exits 1: 22 high entries, one Braces direct-advisory package and 21 inherited records including the wrapper. Baseline: 433 paths, 33 entries (one critical, 24 high, eight moderate). A separate bulk request includes every actual name/version. The nine override selectors are consumed; no external Busboy is installed. There are exactly the 15 previously approved raw range deviations, no missing required dependency, and no runtime engine mismatch.
- Of 376 non-root lock entries, 276 are installed. All 100 omissions are optional: 94 carry platform constraints; six are dependencies/peers exclusively within the omitted WASM platform subgraph. The explicit parent/peer edges are recorded. Other platforms and native mode are preserved, not certified by this Linux/x86_64 validation.
- Original Node CLI/help/static/Node artifact checks pass 7/7 on baseline and final candidate. Ten final HTTP/parser checks pass. Baseline reproduces both cookie injections, numeric-PAX TypeError and, in this stdin invocation, the Braces stack-overflow fixture. Candidate's bounded Braces fixture passes, which neither patches nor clears its advisory. Native CLI help/version pass but both native local builds fail with BUILD_FAILED / Request failed on baseline and candidate; aggregate smoke exit remains 1. No native switch or passing native-build claim.
- Owned application is healthy, runs as UID1000, and passes health/dashboard/assets, six existing Python boundaries, stale-lock refusal and uv pip compatibility. All 77 auditable Python versions match the parent; fresh pip-audit is clean. Lifespan is off; providers and full orchestration lifecycle remain untested. All owned containers use network_mode:none with no mounts/ports/credential files; the healthy app and exited fixtures are preserved without signals or cleanup.
- Repository gates: 464 Python/backend/API tests pass (five existing warnings), Ruff and mypy pass, all 34 web tests/check/build pass, normal all-file pre-commit hooks pass. No workflow source changed, so no Actionlint invocation is required for this round. Earlier full 2,493-test diagnostics remain applicable because no Python/web source or graph changed.
- New independent OS evidence: checksum-verified Trivy v0.76.0, database updated 2026-10-18T13:11:41Z, scans Debian12.15 on Linux/x86_64 at original severity. Baseline and candidate each exit1 with identical 3,072 package-advisory records (eight critical,315 high,1,545 medium,1,194 low,ten unknown); dpkg inventories match and there are no new/removed advisory records. OS acceptance is now known failing rather than unscanned. These pre-existing base-image findings are reported to the parent for separate disposition; this bounded Node repair does not remediate them. Static/native embedded code still lacks complete component coverage.
- Remaining holds: parent evidence review, Braces and OS security acceptance/remediation, native offline failure parity, unverified embedded components/other platforms, live provider and production/full-lifecycle acceptance. Keep the same PR draft. No production operation, source auth/API/schema change, merge, scan bypass or campaign-completion claim.

Round-four adoption shipped in `66bc96b3d33797e412fed8dbdb342877de34836d` on the same draft PR. The final follow-up updates this plan only. Exact final head/base/tree, current hosted metadata, checks/URLs and clean/upstream/live verification are in the final evidence result. Parent evidence/security/production acceptance remains blocked as listed above.


## Round 5: OS security qualification

Acceptance: preserve accepted tooling, Python 3.12/Node 24, all commands and locked graphs. Classify actual critical packages/code using vendor sources; qualify maintained Bookworm before preparing a supported newer-Debian candidate. Parent reviews distro adoption; Michael retains merge/rollout. Preserve raw severities and all independent holds.

- [x] Refresh the same open draft PR, exact head/base and exclusive owned clone; read current instructions and accepted evidence.
- [x] Classify all eight critical records against current Debian sources, actual package contents, symbols, links and hashes.
- [x] Build the maintained Bookworm security refresh and compare complete installed package/filesystem inventories.
- [x] Repeat OS-only scans at unchanged severity; preserve full finding deltas and scanner/export provenance.
- [x] Exercise actual Node CLI/build consumers, HTTP/parser and finite app health/assets/UID/stale-lock contracts; retain native failure parity and separate ecosystem audits.
- [x] Prepare the inert Python 3.12.15/Trixie digest-pinned patch and verify that it applies.
- [?] Build/qualify the actual newer-Debian image, graph, compatibility and OS/npm/Python deltas (blocked: root filesystem reached zero user-available bytes; large-image storage unavailable; candidate is unbuilt/unadopted).
- [x] Review and ship this disposition on the same draft PR with hooks and clean/upstream/live verification.
- [?] Parent independently reviews artifacts and remaining OS/npm/native/embedded/provider/production acceptance.

Evidence: /home/mcutler/.claude/session-summaries/2026-10-18-carrotly-remediation/gluon-agent-os-security-qualification/. Start with result.json, critical-classification.json, os-comparison.json, filesystem-comparison.json and raw scanner .json.gz reports. Large full-filesystem archives/cache are preserved in /dev/shm/gluon-agent-os-security-qualification/ and are volatile across reboot; hashes/provenance and compact raw reports are persisted.

### Bookworm disposition

Maintained python:3.12-slim-bookworm resolves to sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258, exactly the accepted base. Fresh official Bookworm/main, updates and security indexes report **0 upgraded/new/removed/held**. Every critical installed version equals apt's candidate. No same-family patch is available; no production Dockerfile/manifest/lock/runtime change is adopted.

Owned refresh image: sha256:b6923337c319c4cbbe69ea86958f170eada5c71178adbcd47bfaa6f5062e56a5, based on accepted sha256:bafee9cc66629df7d65832889e1df26c04a95192bf9d213fea691354f7dfccd7. Full exported-filesystem comparison finds zero changed/removed entries and 12 added qualification/index metadata paths. Application, scoped Vercel, global Node, Python and OS-package bytes are identical.

Checksum-verified Trivy 0.76.0 with unchanged DB UpdatedAt 2026-10-18T13:11:41Z scans Debian 12.15 on Linux/amd64, OS-only vuln scope, without ignores/severity reductions. Both scans exit 1 with **3072 unchanged records: 8 critical, 315 high, 1545 medium, 1194 low, 10 unknown**; zero new/removed records and complete prior direct-image parity. Docker image-save failed ENOSPC; successful scans use full stopped network-none/no-mount container exports wrapped unchanged as one-layer image archives. This transport workaround provides no npm/Python/embedded/host-kernel certification.

Fresh full actual-inventory npm audit exits 1 with 22 high records (Braces direct plus 21 inherited); fresh 77-package pip-audit exits 0, no findings. Node CLI/build checks pass 7/7, parser checks 10/10 and finite app health/assets/UID 1000/six boundaries/stale-lock pass; native help 5/5 passes but both native builds retain the baseline failures (aggregate smoke exit 1). Actual 425-path npm inventory equals the accepted image. All 276 scoped byte maps and consuming-entry hashes match the parent's independently fresh /tmp/gluon-fresh evidence after path normalization. This job claims no new fresh install. All 77 auditable Python versions match. Original accepted 464 backend/API and 34 web/check/build gates remain applicable because source, graphs and runtime bytes are unchanged; the expensive matrix is not repeated.

### Critical code/vendor classification

All eight raw critical records remain open. Vendor priority and code presence do not authorize suppression or acceptance. Current source URLs and package-level details are in critical-classification.json and vendor-sources.json.

- GLib CVE-2026-58016: libglib2.0-0 / glib2.0 2.74.6-2+deb12u9, Bookworm postponed; installed libgio exports the affected introspection-XML API. Trixie is vendor-fixed; application malformed-input reachability remains unproven.
- SQLite CVE-2025-7458: libsqlite3-0 and sqlite3 / sqlite3 3.40.1-2+deb12u2, Bookworm no-dsa/minor. Python and CLI link the installed library; exact Debian source contains the affected allocator/caller. Trixie is vendor-fixed. No public SQL-injection/exploit claim.
- libxml2 CVE-2026-6653: 2.9.14+dfsg-1.3~deb12u6, Bookworm postponed. XML entrypoints are present; affected internal function is not dynamically exported, so exact internal-path verification is unconfirmed. Trixie's really2.9.14 build remains vendor-vulnerable.
- Linux CVE-2026-43185 / CVE-2026-63825: linux-libc-dev / linux 6.1.190-1, Bookworm/Trixie source affected. Full package list contains UAPI headers without ksmbd/GCOV implementations, relevant Makefile, kernel .c files or a kernel image. This source/header attribution closes neither record. Host kernel/config/reachability unassessed.
- OpenSSH CVE-2026-60002: openssh-client / openssh 1:9.2p1-2+deb12u10, Bookworm postponed and Trixie vulnerable/no-dsa. Actual client bytes hashed; key-reexchange exploitation not reproduced; upstream fix is 10.4p1.
- zlib CVE-2023-45853: zlib1g / zlib 1:1.2.13.dfsg-1, Debian ignores the source record because contrib/minizip is not built. Installed contents/symbols support the distinction; separate libminizip1 / minizip 1.1-8+deb12u1 has the API and is vendor-fixed. Raw critical retained; embedded copies remain unproven.

### Inert newer-Debian proposal

Official Python metadata maintains Python 3.12.15 on supported Debian 13/Trixie. External inert-trixie.patch changes only runtime FROM to python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1. Linux/amd64 manifest: sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe. Patch SHA256: 8bb375bb6ba08bc66e443b0a42ee3bebbd81362c068f230650077ecfaef4e2fe; git apply --check passes. Patch remains unapplied.

Actual image/graph/native requirements, fresh /tmp/gluon-fresh install, full app/tool compatibility, required local gates and three-ecosystem comparisons remain blocked on storage. Vendor fixes do not predict actual candidate counts; libxml2/OpenSSH/Linux vendor holds remain. Resume after storage is available, then obtain parent evidence disposition before adoption. No cleanup, process signals, functionality removal, scan bypass, production operations or merge occurred.

## Release preparation — 2026-10-20

The owner authorized unattended implementation of the repository-health recommendations and all necessary in-scope decisions. This extends the earlier dependency-only scope to readiness, GitHub controls, issue reconciliation, and preparation of version 0.13.0. Historical findings above remain evidence of their respective revisions; their permission and disk-capacity holds are superseded where explicitly resolved below. No merge, production rollout, security suppression, or access to protected credential files is part of this preparation.

### Acceptance criteria

- Keep one branch and PR (#174); preserve existing application features and frozen dependency graphs unless a verified upstream fix requires a change.
- Protect `main` with an enforced PR and passing baseline check; restore CodeQL and automated dependency maintenance without bypassing scans.
- Provide a bounded database readiness probe independent of liveness, with meaningful failure-path tests.
- Refresh dependency and container evidence, adopt only qualified fixes, and retain unresolved security findings as release blockers.
- Reconcile old issues against current code and record remaining, testable scope.
- Prepare 0.13.0 metadata, release notes, validation evidence, and an explicit production acceptance checklist.
- Finish with committed, pushed changes, an updated PR, and a session handoff. Do not describe an unqualified image as production-ready.

### Execution checklist

- [x] Refresh the clean branch and existing PR, register it with this thread, and verify the available disk space (125 GiB).
- [x] Enable and verify GitHub branch controls, CodeQL, and dependency maintenance.
- [x] Add database readiness and validate healthy, unavailable, locked, and authenticated-deployment behavior.
- [x] Refresh all-extra Python and both web dependency audits against current advisories.
- [x] Qualify container fixes, installed dependencies, and OS findings using an isolated image.
- [x] Reconcile the 13 open issues and retain precise follow-up acceptance criteria.
- [x] Fix unsupported merge retries, retain public HTTP contracts, and prevent undeliverable chat questions from being marked delivered.
- [x] Prepare 0.13.0 release metadata, notes, and operational validation instructions.
- [x] Run full backend diagnostics, web checks/tests/build, packaging, and applicable container checks.
- [x] Add a blocking container publication scan that preserves complete findings and rejects high/critical vulnerabilities.
- [x] Review the complete diff and prepare the final PR and session handoff; record final clean/pushed and hosted-check state in that handoff.

### Ownership

The parent owns GitHub configuration, dependency locks, workflow changes, release metadata, the plan, commits, and PR updates. The readiness worker owns its store/probe implementation, related tests, and API documentation. The container worker owns Docker/tooling and container fixtures. A read-only reviewer independently reconciles the backlog and release history.

### Current validation

- `main` now requires a PR, an up-to-date `pr-check` from GitHub Actions (app 15368), CodeQL from GitHub Advanced Security (app 57789), and resolved conversations, including for administrators. Force pushes and deletions are disallowed. Zero mandatory approvals preserves the solo maintainer's ability to merge their own reviewed PR; automatic merging remains disabled.
- CodeQL default setup was re-enabled for Python, JavaScript/TypeScript, and Actions; initial analysis run: 39431233169. Dependabot security PRs are enabled. The committed configuration schedules grouped maintenance; it takes effect on merge.
- The baseline now also requires an application dependency audit (all Python extras and both web locks) and runs readiness, merge-queue, resume-ceiling, and campaign conformance tests. Container tooling and OS acceptance remain separate, explicit release gates.
- Fresh all-extra Python audit: 96 applicable dependency records, zero advisories. Fresh npm and Bun web audits: zero advisories, all three exits 0.
- Database readiness: 25 targeted tests pass; Ruff lint/format and diff checks pass. `/api/ready` uses a bounded, rolled-back write against the existing database, never creates a missing database, and limits stalled workers to one. It does not claim durable commit verification or repair of Docker Desktop bind mounts.

### Final release-preparation evidence

- All **2,525 backend tests pass** in 380 seconds with every optional feature installed. The initial run had 2,524 passes and one setup error because a concurrent Vite build temporarily removed `dist/assets`; the affected 13-test file and then the full suite passed after frontend building completed. Both logs are preserved. Existing deprecation/mock subprocess warnings remain visible (13 warnings); no test was skipped or suppressed.
- All **34 web tests**, Biome, TypeScript/Vite/PWA build, Ruff, mypy (96 source files), and normal all-file pre-commit hooks pass. The existing run-detail effect dependency warning and large bundle warning remain recorded. The Biome schema now matches its pinned CLI.
- Strict npm installation in an isolated directory succeeds; all-extra frozen uv install and uv 0.13.3 lock validation succeed. Workflow and Dependabot schemas validate. Checksum-verified Actionlint 1.7.12 passes. Python wheel and sdist build from a clean archive at `791e756`, with version 0.13.0 and no local `.venv`, `node_modules`, or protected credentials. Those Python artifacts do not bundle the built dashboard; the Docker image does.
- Hosted backend, web, dependency audit, `pr-check`, and all three CodeQL language analyses pass at `791e756`. Fresh open CodeQL and secret-scanning alert counts are zero. Final documentation/workflow commits require their own checks; the session summary records the final hosted state.
- The qualified Linux/amd64 image is `sha256:53cafc7ba196cb77e191b2f26d264866f627879549c6802837e5f889b8b62726`, local tag `gluon-release-trixie:20261020-qualified`. All 15 qualification operations pass: normal lifespan startup/shutdown, UID 1000, liveness/readiness, version and dashboard, six Python dependency boundaries, stale-lock refusal, pip compatibility, Chromium, Vercel static/Node builds, and ten parser fixtures. All three owned containers are stopped with exit 0; no production mounts, credentials, network, or existing processes were used.
- Parent verification confirms all 228 recorded build inputs match committed source at `791e756`. The image metadata records checkpoint `456337a` plus verified overlays; this is a local candidate, not an immutable release built from the final PR commit. Subsequent changes are documentation and workflow controls. Publishing will supply the actual source commit.
- Actual container inventories: 424 npm records; 275 installed scoped versions match the Vercel lock; all 77 auditable Python distribution/version records are clean. The wrapper npm audit retains **21 high entries: one Braces advisory-bearing entry and 20 inherited entries**. This corrects the worker's initial description of all 21 as inherited; raw audit JSON is unchanged.
- Trivy 0.76.0 with database updated 2026-10-20T07:17:35Z retains **3 critical / 190 high / 796 medium / 1,127 low / 49 unknown OS records**. The critical packages are libxml2 (CVE-2026-6653), linux-libc-dev (CVE-2026-93221), and openssh-client (CVE-2026-60002); no critical/high OS record has a vendor fixed version in this scan. Earlier Bookworm evidence had eight critical and 315 high records.
- Expanded image scanning also finds one high Node advisory (Braces), four high/two medium pip vendor/SBOM records, and four high/13 medium/one low/one unknown GitHub CLI embedded records. uv/uvx 0.13.3 clears the earlier Rustls finding. Latest checked pip and gh releases still bundle flagged components; no vendor code was rewritten and no finding suppressed. Pip's setuptools record covers its vendored `pkg_resources` subset; affected-code reachability is unconfirmed.
- The publishing workflow now builds a local amd64 candidate, records all Trivy severities, and rejects high/critical findings before registry login or publication. Its exact gate script rejects the actual candidate report (199 high and three critical across all scopes), accepts a clean fixture, and rejects malformed input. This does not certify ARM64. No workflow was dispatched to publish an image.
- Closed already-completed #155/#157/#158/#164 and declined #160/#166 with explicit compatibility/simplicity rationale. Updated and assigned the seven remaining issues. This PR fixes #156 and #159 on merge; #150 and #163 retain specific storage/live-transport acceptance work, while #161/#162/#165 are nonblocking refactoring follow-ups.
- Independent final reviews of this session's implementation and publication gate found no additional blocking defect or misleading release claim. The new publication workflow itself has not run on GitHub; local equivalent scanning and gate execution passed validation. Remaining holds are upstream container security, native Vercel offline build failures, ARM64, live provider/campaign and chat verification, and the unreproduced Docker Desktop incident. No merge, tag, image publication, production deployment, or claim of full release acceptance occurred.

Durable evidence is under `/home/mcutler/.claude/session-summaries/2026-10-20-gluon-release-evidence/`; the container subdirectory contains raw scanner/audit reports, source hashes, and lifecycle logs. Working evidence and built Python artifacts are also under `/tmp/gluon-release-20261020/` and `/tmp/gluon-release-container-20261020/`. See `docs/releases/0.13.0.md` for upgrade, rollback, and remaining production acceptance.
