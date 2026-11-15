# Distributed Projects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** One-command project upload and LLM-reviewed distributed map/reduce execution, proven by real Fashion-MNIST training/evaluation on multiple notebooks.

**Architecture:** Extend the existing native CLI and FastAPI preview/DAG scheduler with a bounded project contract. Execute user stages only in existing protected CPU/CUDA images; preserve account ownership, leases, consent and artifact validation.

**Tech Stack:** Go CLI, Python/FastAPI/PostgreSQL coordinator, NumPy/PyTorch workload project, existing React portal and Docker runtimes.

**Spec:** docs/superpowers/specs/2026-11-15-distributed-projects-design.md

## Global Constraints

- Preserve the live LAN service, database, credentials, TLS and native identities.
- LLM sees only selected code/metadata; no arbitrary source execution on API host.
- Validate exact stage semantics and immutable input ownership before scheduling.
- CPU/GPU offers remain locally bounded; no silent GPU enablement.
- Multiple containers on one host do not prove multiple physical devices.
- Count unique accepted work only; use the same numerical reference for baselines.

## Review Focus

- Dataset paths escaping root, case aliases, symlinks, ignored outputs and changes during snapshot.
- Malformed or altered LLM plans, missing code/data bindings and dependent map coverage.
- Cached artifacts from another account, changed coordinator and lost submission acknowledgements.
- Intermediate duplicate names, failed predecessors and late/duplicate result publication.
- CUDA numerical differences, incomplete coverage and benchmarks with mismatched task/resource settings.

### Task 1: Project schema and trusted distributed compiler

**Files:** new server/campus/project_schema.py, project_execution.py, project_adapter.py; server/tests/test_project_execution.py; catalog registrations. Root owns integration edits in existing planner/scheduler files.
**Interfaces:** validated ProjectConfig as in spec; deterministic compile/validate functions producing dto.JobPlan and per-stage specs. Native argv resolver replaces only `{input}` and `{accelerator}`.

- [x] Write failing tests for valid map/reduce, dependent map expansion, invalid paths/DAGs, changed commands, and exact input subsets.
- [x] Implement bounded schema, plan compilation/validation, trusted task preparation and argv/input resolution.
- [x] Run focused tests and give a root integration contract/review report.

### Task 2: CLI project workflow and login

**Files:** runner/internal/app project/login modules and tests, focused client/artifact packaging additions, runner/CLI.md.
**Interfaces:** `campus login`; `campus run` reads campus.json, sends PreviewRequest.project_config with ordinary uploaded project/data bindings. Existing explicit-command path remains.

- [x] Write failing command tests covering discovery, upload/cache ownership, preview validation, selected-code consent, noninteractive mode, resume, wait and download.
- [x] Implement project inventory/configuration, checksummed data reuse, review and safe account-scoped submission/results.
- [x] Verify Go suite/race/vet, build six native targets, report changes and integration needs.

### Task 3: Fashion-MNIST complete project

**Files:** examples/fashion-mnist/ project, official dataset preparation, worker and independent reference, tests and README.
**Interfaces:** campus.json contract; map train writes partial.json; reduce model writes model.json; map evaluate writes predictions.json; final reduce writes result.json. Input globs select dataset/train-*.npz and dataset/test-*.npz; predecessor results under /inputs/stages.

- [x] Write tests for exact partition coverage, class sum/count merge, predictions/reference and duplicate/missing shard rejection.
- [x] Implement official checksummed dataset preparation and bounded CPU/CUDA nearest-centroid stages with one globally trained model.
- [x] Run local full-dataset reference and tests; provide ready upload directory without committing dataset bytes.

### Task 4: Coordinator integration, publishing and live verification

**Files:** server/campus/dto.py, planner, jobs scheduler/adapters/service, auth/main, product catalog/contracts; tools/qa; docs.

- [x] Integrate new schema/adapter/compiler and require exact configuration agreement in LLM previews.
- [x] Add CLI credential login/revoke and owned content-addressed artifact lookup; namespace distributed outputs safely.
- [x] Verify isolated PostgreSQL integration/ownership/retry/fencing and existing suites; regenerate contracts.
- [x] Snapshot live state, build/deploy the API and new client release while preserving public downloads/devices.
- [x] Execute the one-command Fashion-MNIST project across two actual physical devices, verify model/results independently, exercise interruption/recovery and record fair baselines/utilization.
- [x] Perform independent review, fix findings, document verified limitations and commit branch.

## Execution ledger

The user authorized full development/testing and parallel agents. Root owns
existing coordinator integration and live operations. Independent owners do not
edit each other's files or restart the live service without coordination.
