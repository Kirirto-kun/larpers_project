# Runner implementation and integration record

Implements part 4 of `docs/superpowers/plans/2026-11-14-campus-pool-implementation.md` on `codex/runner-cli`, starting at `f1914dd`.

## Analysis

The repository initially contains the design, implementation plan and brief only. There is no server, contract generator, OpenAPI document, shared fixture API, portal or console implementation. Part 4 owns only `runner/`, `bootstrap/`, `environments/`, `workloads/`, and `sdk/python/`. This branch must not invent or overwrite the control-plane owner's foundation.

The implementation uses the documented v1 routes, snake_case, SHA-256, durations in milliseconds, server-fenced attempts, explicit GPU admission, and offline execution. Until foundation lands, runner-owned wire structs and executable HTTP integration fixtures describe the proposed detailed payloads. They are not generated DTOs. A real control-plane compatibility check remains a release gate.

## Tasks

1. Safe project snapshots, artifact capture and checkpoint primitives: Unicode paths, secret exclusions, symlink/traversal rejection, quotas and atomic publication.
2. Go protocol client, resumable artifacts, session identity/locking, discovery and shared resource admission.
3. Isolated Docker execution, independent PID1 lease guard, output/checkpoint transport, worker lifecycle, bounded telemetry.
4. Registered embedding/training/generic adapters, offline model handling, full-reference validation, safe training/resume/evaluation.
5. CLI, Python SDK, verified bootstrap, pinned profiles, executable integration tests and operator documentation.
6. Full build/test, independent review, fix regressions, and document the exact hardware/integration gates still requiring external resources.

For each task: write meaningful boundary/failure tests, observe failure, implement, run its suite, record evidence, commit owned paths. No changes to shared root configuration or contracts.

## Decisions

- Use the user's requested separate branch in the clean existing checkout; no additional worktree is necessary.
- Continue implementation despite missing foundation using explicit runner-owned wire types and boundary tests. Cost: detailed DTOs may require an adaptation when the foundation is published.
- Physical multi-node, CUDA and campus-network measurements cannot be inferred from local tests. They remain acceptance gates until measured on the target laptops.

## Validation

Commands and observed outcomes are recorded in `VALIDATION.md`. Do not interpret an engineering target, a fixture response, or a test double as measured pool performance.
