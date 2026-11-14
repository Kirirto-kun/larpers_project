# Web product verification — 2026-11-15

The integrated product is on `codex/web-platform`, incorporating control-plane
`5b2ce70` and portal-console `874e052` while retaining native runner history.
The existing LAN deployment, database, artifacts, device identities and TLS
volumes were preserved. Private configuration and recovery snapshots remain
outside Git. The API image includes both production UI builds; the canonical
private startup override now matches the verified deployment.

## Automated checks

| Check | Observed result |
| --- | --- |
| Full server suite on isolated PostgreSQL | 106 passed, 42.95 seconds |
| Portal Playwright journeys | 14 passed |
| Administrator console Playwright journeys | 5 passed |
| Complete Go runner suite | Passed |
| Managed worker/app race checks and Go vet | Passed during native release validation |
| Production portal and console builds | Passed; included in the deployed image |
| OpenAPI, JSON schemas and response fixtures | Passed |
| Ruff: server, tests, contracts and infrastructure | Passed |
| Startup script boundary tests | 8 passed |
| Public starter Windows/POSIX regression checks | 7 targeted tests passed |

Browser fixture tests check contracts and UI behavior; they are separate from
the actual deployed flows below. A final full-suite run exposed a test-only
preview leak: its database schema persisted while subsequent tests used a
different artifact directory. The context test now cancels its unfinished
preview in `finally`; the complete 106-test suite passed afterward. An earlier
one-second test lease also expired during concurrent heavy test runs; the final
serialized server run passed without weakening lease validation.

## Real deployed flows

| Requirement | Evidence |
| --- | --- |
| Account registration, password login/logout, member isolation | Actual browser flow, repeat login, member statistics and administrator API denial; separate server ownership/CSRF tests |
| Public tiny starter and private invitation | Actual Windows starter download with attachment disposition, 33,167 bytes and SHA-256 verification; ordinary member created and redeemed a separate private device invitation |
| Remote device controls | Physical RTX 5050 acknowledged pause/resume and low/max/medium revisions; member-owned CPU client acknowledged pause/resume and low/medium revisions |
| NVIDIA computation | Job `cefb3184-ad45-4d35-b5bd-93a95e3c1dfd` succeeded on the physical RTX 5050; downloaded output matched metadata and all 65,536 matrix elements matched an independent integer CPU reference exactly |
| Own uploaded code with AI | Actual DeepSeek preview for an uploaded ZIP, explicitly sharing only `main.py`, visible plan review and job `a15b3d61-2f23-4b76-8a7a-4552e6624d55`; physical native execution and authenticated result download |
| Independent own-code result check | Downloaded result reports the sum of squares for 0 through 49,999 as `41665416675000`, matching the independent closed-form formula |
| Ordinary member contribution | Job `384f3053-0ffd-4c16-be2d-9f4fcaadc2af` ran on that member's own enrolled node, not an administrator test node; output checksum and exact rational numerical reference passed |
| Real personal statistics | After the member contribution: one owned device, one accepted contribution and two successful own jobs, with real result/history entries |
| Administrator interface | Actual owner browser session saw pool totals, physical hardware, acknowledged settings, jobs and results; CSRF-protected idempotent control passed |
| Responsive/error behavior | Mobile layout, keyboard/reduced-motion forms, clipboard fallback, unavailable/old devices, stale preview revisions and server failures covered in browser checks |
| Local stop takes precedence | Managed runner tests cover local stop during pause/work; the real member test ended with web pause, local leave and native exit 0 |
| HTTPS and deployment continuity | Health and protocol returned 200 with independently verified local CA/hostname; startup Compose configuration validated without printing secrets |

Downloaded artifact SHA-256 values:

- RTX output: `c071df4dd76f38cf1331c28234c5912882f23054c42ba422846d70a52d630f6f`.
- AI own-code output: `8d5aadd49902f7e58fce419a997086f8b924098ab6724543e57ac9654a378d5d`.
- Member CPU output: `ae869918d00ead9829eddc6504295b769034e4ac725f57ae627d49fda5316e9d`.

Reproducible live QA sources are in `tools/qa/`. Local reports, screenshots,
independently checked results and review reports are retained under the ignored
`.git/campus-runner/web-product/` directory. Runtime credentials, invitation
tokens, provider keys, private configuration and generated activation links
are not part of this report or the public release.

## Practical boundaries

- Native clients were built for Windows, macOS and Linux on amd64/arm64. This
  release's complete managed web flow was physically exercised on Windows CPU
  devices and the RTX 5050 laptop. An earlier physical Apple M2 CPU diagnostic
  and reconnect passed; the new managed Mac build was not physically retested.
- macOS contributes CPU through Docker. Metal/Apple GPU computation is not
  provided. NVIDIA CUDA requires a compatible GPU/runtime and local GPU consent.
- Contribution modes select 10/50/90% of currently available CPU/RAM within local
  and Docker limits. They do not promise a matching GPU utilization percentage.
- The native client must remain running and the computer awake/reachable. A web
  command cannot start a closed process. Desired and acknowledged state remain
  distinct; disconnected computers are not presented as ready.
- Generic Python jobs use the trusted pinned offline environments and structural
  output verification. Users must assess application-specific result quality;
  the numerical checks above independently verify only these actual test jobs.
- The LAN HTTPS deployment uses its existing local CA. Browser QA used isolated
  contexts accepting that certificate; strict CA/hostname verification was also
  performed separately. No automatic OS trust change was made.

See [the user guide](WEB_PRODUCT.md) and [operator instructions](WEB_OPERATIONS.md).
