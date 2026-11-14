# Run Campus Pool interfaces

## Frontend development

Requirements: Node.js >=22.12 (tested here with Node 25), npm, and Python 3. Both applications have separate pinned dependencies and lockfiles. From the repository root:

```sh
npm --prefix apps/portal ci
npm --prefix apps/console ci
npm --prefix apps/portal run dev
```

In another terminal: `npm --prefix apps/console run dev`. Portal: http://127.0.0.1:5173/ ; console: http://127.0.0.1:5174/admin/ . Both proxy /api to http://127.0.0.1:8000. Override with CAMPUS_API_URL when starting Vite. The browser uses same-origin /api/v1 and secure-session cookies/CSRF; do not embed credentials in client code.

A real backend is not included in our owned paths. Use the fixture instructions in apps/portal/README.md for an explicitly labelled local preview. Test fixtures are synthetic and are never bundled into production JavaScript. Frontend models are provisional pending generated foundation DTOs.

```sh
npm --prefix apps/portal run build
npm --prefix apps/console run build
npm --prefix apps/portal exec -- playwright install chromium
npm --prefix apps/portal run test:e2e
npm --prefix apps/console run test:e2e
python3 -m unittest discover -s tools/benchmark/tests -v
```

Integration target: serve portal dist at /, console dist at /admin/, API at /api/v1. Each app needs SPA history fallback within its own prefix. The control-plane owner configures the shared origin, HTTPS and generated DTO pipeline.

## User journey: searchable documents

Sign in using a member invitation. Choose Run a task, describe the search outcome (Unicode EN/ZH supported), select document-search recipe and JSONL/TXT inputs. Review included file sizes, excluded secrets and any selected text fragments for external AI. Prepare the plan, answer clarification questions, and review CPU/GPU, outputs and recovery. Run only the ready revision. JobDetails shows accepted and failed units, retries, logs and files. Search an accepted index to get excerpts with source labels. Search may wait for compatible capacity.

## User journey: own-code training

Choose the training recipe or upload train.py, permitted requirements and a non-sensitive labelled dataset. Review data split, environment and execution plan. Collect model.pt, config and training/validation metrics from Results. Files become downloadable only after publication. For ordinary code without a registered validator, execution completion does not establish quality. Resume is only available when the recipe/code implements compatible complete checkpointing; otherwise it restarts or is unsupported.

## Contribute and observe

Contribute compute requires the device owner's consent. Choose OS and CPU budget, optionally opt in to verified GPU use, and generate a short-lived command. Run it on the contributing laptop and inspect preflight; missing Docker/CUDA is an actionable error. Choose CPU and memory budgets; GPU use is separate opt-in. Use Drain/Stop to control participation. Device credentials do not grant user/admin privileges.

Open the console for devices, attempts and measured benchmarks. Only authorized owners/admins see control actions and experiment creation; server authorization remains authoritative. A disconnected event stream triggers snapshot refresh and reconnection. Reloading JobDetails observes the existing job rather than starting another one.
