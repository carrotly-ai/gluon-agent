# Vercel CLI runtime installation

The Docker image installs this private wrapper at `/opt/gluon-vercel` with npm 11.21.1 and `npm ci --include=optional`. Both `vercel` and `vc` remain on PATH. The standard Node CLI, builders, commands and platform optional dependencies are retained; native execution remains upstream opt-in.

Vercel 64.5.0 is the maintained stable release checked on 2026-10-19 SGT. Its exact transitive pins retain published vulnerabilities. Installing it beneath a private package allows a reproducible scoped override lock without resolving Vercel's unavailable development metadata. Overrides apply only beneath `vercel@64.5.0`; other global tools retain their own graphs.

The parent-approved maintenance decision accepts 13 same-major exact-pin relaxations and two Undici 5→6 dependency edges. The package manifest lists the nine consumed selectors; the lock preserves all upstream platform packages. An unused Busboy selector from the prototype is intentionally omitted because Undici 6 uses its own form-data parser. All non-root lock entries equal the reviewed prototype graph.

This is a partial dependency repair. The complete actual image npm graph still audits nonzero: Braces 3.0.3 is the sole direct residual advisory, with inherited records throughout the dependency tree. Its bounded fixture passing does not establish safety. Native offline builds fail in both baseline and candidate. Python/npm audits and published-byte verification do not certify OS packages, statically embedded modules, provider-backed deployment, or full orchestration lifecycle.

For an update, first check for a maintained upstream release that removes these overrides. Preserve CLI aliases, builders and optional platforms. Verify actual consumer resolution and installed bytes after an independent empty-cache frozen install; check original raw ranges separately from npm's override-aware tree. Run the offline CLI/static/Node build and HTTP/parser fixtures, application/container contracts, repository gates and complete actual-image audits at their original severity. Do not infer installed versions from lock text alone or remove remaining gates because a bounded test passes.

An independent Trivy v0.76.0 OS scan reports the same 3,072 Debian package-advisory records in baseline and candidate, including eight critical and 315 high records. Their package inventories are identical. OS acceptance remains failing; this bounded Node dependency repair does not clear it.

Decision and final evidence: `plans/fix-dependency-security-20261018.md`, round four. The PR remains draft for parent review and independent security/provider/production acceptance.
