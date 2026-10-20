# Vercel CLI runtime installation

The Docker image installs this private wrapper at `/opt/gluon-vercel` with npm 11.21.1 and `npm ci --include=optional`. Both `vercel` and `vc` remain on PATH. The standard Node CLI, builders, commands and platform optional dependencies are retained; native execution remains upstream opt-in.

Vercel 64.7.0 is the maintained stable release checked on 2026-10-20 SGT against the [official npm registry](https://registry.npmjs.org/vercel/latest). Its exact transitive pins retain published vulnerabilities. Installing it beneath a private package allows a reproducible scoped override lock without resolving Vercel's development metadata. Overrides apply only beneath `vercel@64.7.0`; other global tools retain their own graphs.

The package manifest retains the nine previously qualified selectors, including two Undici 5→6 dependency edges. The lock preserves all upstream platform packages and updates builders to the versions selected by Vercel 64.7.0. Undici 6 uses its own form-data parser. Updating this wrapper does not apply overrides to other global tools.

This is a partial dependency repair. The actual image has 424 npm package records. Braces 3.0.3 remains the sole direct npm advisory, [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm); the wrapper audit has 21 high inherited records. Braces 3.0.3 is still the latest published release. Passing bounded parser fixtures does not establish safety.

The installed Node CLI passes offline static and Node function builds, both CLI aliases and development command loading. The ten HTTP/parser compatibility checks also pass. The upstream opt-in native CLI loads help/version commands but still fails both offline builds with `BUILD_FAILED: Request failed`. These results do not qualify authenticated deployment.

For an update, first check for a maintained upstream release that removes these overrides. Preserve CLI aliases, builders and optional platforms. Verify actual consumer resolution and installed bytes after an independent empty-cache frozen install; check original raw ranges separately from npm's override-aware tree. Run the offline CLI/static/Node build and HTTP/parser fixtures, application/container contracts, repository gates and complete actual-image audits at their original severity. Do not infer installed versions from lock text alone or remove remaining gates because a bounded test passes.

The Python 3.12/Trixie image reduces the earlier Bookworm OS findings from eight critical and 315 high records to three critical and 190 high records with the 2026-10-20 Trivy v0.76.0 database. The remaining critical records affect libxml2, linux-libc-dev and openssh-client; no critical/high record has a vendor fixed version. OS acceptance remains failing.

The expanded image scan also reports four high and two medium Python vendor/SBOM records in the latest pip release, plus four high, thirteen medium, one low and one unknown record in the latest GitHub CLI's embedded Go dependencies. These are separate from the clean audit of 77 installed Python distribution/version records. Pip's vendored setuptools entry represents its pkg_resources subset; the scanner finding is retained without claiming the affected full-setuptools code path is reachable. Metadata-only Python audits do not cover these embedded dependencies.

See `tests/container/README.md` for reproducible offline runtime and tooling qualification, and `plans/fix-dependency-security-20261018.md` for release decisions and evidence. No advisory is suppressed or classified as fixed merely because a smoke check passes.
