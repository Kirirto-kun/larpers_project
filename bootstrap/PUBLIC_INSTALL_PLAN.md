# Public installation and Mac computation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task by task.

**Goal:** Redistributable tiny launchers, cached selected environments and a real independently checked Mac computation.
**Architecture:** Pinned native client/catalog; Go preparation; existing foreground connect; separate scoped server diagnostic compatibility.
**Tech Stack:** Go1.25.6 stdlib, shell/PowerShell5/CMD, stdlib Python maintainer tools and existing Docker/FastAPI coordinator.
**Spec:** `bootstrap/PUBLIC_INSTALL_DESIGN.md`.

## Constraints and review focus

No public invitations/credentials, system CA install, policy bypass, driver
installer, implicit GPU consent or unverified downloads. HTTPS and exact
size/hash bounds precede executable use. CPU selection must never fetch CUDA.
Rehashed cache hits do not download again. Interrupted large downloads resume
only with exact Range validation. Unknown architecture gets a clear failure.
Mac computation proof must distinguish actual task/result from registration.
Retain current Mac and live database credentials; do not reset Docker or peers.

## Parallel task1: downloader/cache (release_downloads)

Own `runner/internal/installer/**`.

- [x] Write failing catalog/security/cache/range tests and observe failures.
- [x] Implement `FetchCatalog(ctx,h,url,sha)`, `(*Catalog).Select(os,arch)` and
  `Prepare(ctx,h,url,sha,catalog,target,gpu,cacheDir,runningBinary)`.
- [x] Return `{Root,ManifestSHA256}` for a verified existing onboarding bundle.
- [x] Verify CPU-only fetches, consent-independent package API, immutable
  metadata, private cache, cancellation/resume and corrupt-cache behavior.
- [x] Run package normal/race/vet checks and hand off exact interfaces.

## Parallel task2: publisher/starters (public_starters)

Own `bootstrap/public/**`, `prepare_public_release.py` and its tests.

- [x] Write failing tests for native target selection, unsafe inputs, secret
  exclusion, pinned argv and Unicode/space shell invocation.
- [x] Build public metadata from native binaries and existing approved gzip
  archives, with no Docker export/pull/build and no private device folder copy.
- [x] Generate small pinned Mac/Linux shell and Windows CMD/PowerShell entry
  points invoking `campus install --catalog-url URL --catalog-sha256 HASH`.
- [x] Verify actual available shells, native headers, archive identities,
  checksums and atomic new output publication.

## Parallel task3: Mac computation compatibility (mac_compute)

Own `bootstrap/control_plane_compat/**` and ignored diagnostic scripts.

- [x] Reproduce exact old-runner/server field and artifact path mismatches.
- [x] Tests first: explicit node/UA allowlist, bounded trusted policy, standard
  API unchanged, strict validation/fencing and result path normalization.
- [x] Prepare reproducible patcher, trusted catalog and ordinary API job
  submit/fetch/independent mathematical verification script.
- [x] Hand off tests/source diff before root deploys/restarts the API.
- [x] Run real Mac task after deployment and retain independent result evidence.

## Root integration

- [x] Tests first: app install routing, GPU consent before CUDA download,
  selected bundle handoff, saved identity, stop/drain and no token echo.
- [x] Add `install` command and strict TLS downloader with bounded metadata
  but sufficient explicit context for multi-GiB environment transfer.
- [x] Review all parallel changes, run meaningful shared regression suites.
- [x] Build six native CGO0/trimpath clients; verify complete public release
  has no private invitations and only sanitized allowed assets.
- [x] Mount sanitized release read-only under current HTTPS gateway; validate
  catalog/range/hash behavior through the live service and test cache reuse.
- [x] Review/apply tested diagnostic server overlay without resetting data;
  configure trusted catalog and verify actual Mac artifact through normal APIs.
- [x] Record what actually passed, limitations and device startup instructions.

## Decisions

- Reuse the existing isolated branch, public CA and approved image archives.
- Keep public download content and private invitation packages in different roots.
- The current deployment supports devices on this LAN; internet-wide joining
  additionally needs a reachable public coordinator address.
- Use the verified `sh Connect.command` Mac entry route. The first Finder kill
  remains unexplained; do not disable OS security or rewrite a pinned binary.

## Final evidence

All implementation/integration tasks above completed. Go normal/race/vet and
native WSL installer tests passed. Full Python 426+one opt-in skip preceded the
last Windows-only launcher fixes; final targeted public 53 passed afterward.
The independent review's Docker archive layer validation and Docker-readiness
cancellation findings were fixed and regression-tested. Real CMD acceptance
also found and fixed inherited module lookup and protected-cache ACL handling.
R3 is hosted read-only with matching catalog/starters and no private invitations.
The actual 4.175GB CUDA download resumed from 8MiB and later prepared successfully
with all network requests denied. It did not use GPU hardware or enroll a node.
The physical Mac completed the independently verified 250000-sample task; see
`control_plane_compat/RESULT.md`. Arbitrary production jobs and real RTX5050
execution remain outside this restricted diagnostic acceptance.
