# larpool

**Turn idle computers into a shared pool for CPU and NVIDIA GPU jobs.**

larpool is our implementation of the **Campus Edge Fabric** project. It connects Windows, macOS and Linux computers to one coordinator, schedules work on compatible devices, and returns downloadable results. Users can contribute a computer, submit Python code and data, or describe a task and review an AI-assisted execution plan. Explicit map/reduce stages let one project use several computers.

This repository contains the complete product: the public landing page, user workspace, administrator console, coordinator/API, native worker CLI, Python SDK, isolated workload environments, and reproducible examples. Internal package names, commands and configuration variables retain the `campus` / `CAMPUS_` names.

**Start here:** [Run the coordinator](#run-the-coordinator) · [Connect a computer](#connect-a-computer) · [Run the example](#run-the-fashion-mnist-example) · [Technology stack](#technology-stack) · [Verification](#verification-and-measured-results)

## What you can do

- **Contribute a computer.** Connect with a native launcher and a separate private invitation. Choose low, medium or maximum contribution; pause, resume or drain the worker from the workspace.
- **Submit code and data.** Upload a Python project through the portal or run `campus run` from a directory containing `campus.json`. Review commands, inputs, outputs and resource limits before submission.
- **Use AI planning.** Prepare a structured plan with a configured LLM provider. Source excerpts are shared only after explicit selection and consent; the server validates the plan before execution.
- **Distribute a project.** Declare map stages over immutable input shards and reduce stages over accepted predecessor outputs. Compatible CPU and CUDA devices can participate in one job.
- **Follow execution.** See job state, task progress and logs; download results with size and SHA-256 checks. Expired worker leases allow unfinished tasks to be retried.
- **Operate the pool.** The administrator console shows devices, jobs, resource information and benchmark measurements, with server-enforced roles.

## Architecture

```mermaid
flowchart LR
    U[User portal] --> G[Caddy HTTPS gateway]
    A[Admin console] --> G
    C[Native CLI] --> G
    G --> S[FastAPI coordinator]
    S --> DB[(PostgreSQL)]
    S --> F[(Artifact storage)]
    S --> L[Configured LLM provider]
    W[Go workers on connected computers] <--> G
    W --> R[Isolated CPU / NVIDIA CUDA containers]
```

The coordinator owns accounts, device registration, plan validation, scheduling, leases, task attempts, artifacts and result acceptance. PostgreSQL stores durable state; a separate volume stores uploaded inputs and outputs. Caddy provides the shared HTTPS origin for both web applications and the API.

Workers prepare approved, immutable Docker images before executing jobs. Workloads run with explicit resource limits and no network access. Task dependencies are resolved by the coordinator; reductions consume accepted outputs. LLM calls help explain and review work, while the declared project contract determines executable commands and dependencies.

## Technology stack

| Layer | Technologies | Purpose |
| --- | --- | --- |
| Web applications | React 19, TypeScript 5.7, Vite 6, React Router 7 | Public landing, user workspace and administrator console |
| Interface | Tailwind CSS 4, shared CSS tokens, Radix Slot, Lucide | Shared larpool styling and UI components |
| Coordinator | Python 3.12, FastAPI, Pydantic 2, Uvicorn | Versioned HTTP API, planning, scheduling and artifact handling |
| Persistence | PostgreSQL 17, SQLAlchemy 2, Alembic, psycopg 3 | Durable job/device state and schema migrations |
| Native CLI / worker | Go 1.25.6 | Authentication, project upload, device enrollment and container execution |
| Workload runtime | Python 3.11, PyTorch 2.9.1, NumPy 2.2.6, Transformers, safetensors | CPU/CUDA execution, registered ML workloads and checkpoint helpers |
| AI integration | httpx, validated JSON responses; DeepSeek, Model Studio or an OpenAI-compatible endpoint | Optional structured planning and project review |
| Deployment | Docker, Docker Compose, Caddy 2 | Coordinator services, HTTPS and isolated execution environments |
| Verification | pytest, Playwright/Chromium, Go tests and vet, Ruff, JSON Schema/OpenAPI checks | API, worker, UI and contract validation |
| Dependency management | npm lockfiles, uv lockfiles, Go modules | Reproducible dependency installation |

Exact dependency versions are recorded in each component's manifest and lockfile.

## Requirements

Requirements depend on the role of the computer:

| Role | Requirements |
| --- | --- |
| Coordinator built from this repository | Git, Node.js **22.12+** with npm, Docker Engine/Desktop with Compose supporting `up --wait` |
| Server development and maintenance | Linux/WSL or a Linux container, Python **3.12** and [uv](https://docs.astral.sh/uv/) |
| Building the native CLI | Go **1.25.6+** |
| Contributing compute | Running Linux-container Docker runtime and the operator's approved CPU/CUDA image; Docker Desktop on Windows/macOS |
| NVIDIA GPU contribution | Compatible NVIDIA GPU, working driver and Docker GPU support; WSL2 on Windows; explicit local GPU consent |
| Preparing the example or running ML tests locally | Python **3.11 or 3.12**, uv and the workload dependencies |
| Submitting an already prepared project with a prebuilt CLI | Native `campus` executable, account, coordinator URL and trusted TLS certificate |

Apple Silicon workers use ARM64 CPU containers. macOS Metal is not an execution target. Building a binary for an architecture does not establish Docker or GPU support on that platform.

Initial installation needs network access for packages and images. Job execution performs no network package/model downloads. When needed, approved cached wheels can be installed offline into a fresh workspace overlay; see the [offline dependency policy](environments/README.md).

## Run the coordinator

Run commands from the repository root. Docker must be running in Linux-container mode.

### 1. Get the source and configure a new installation

```sh
git clone https://github.com/Kirirto-kun/larpers_project.git
cd larpers_project
git switch main
```

Copy the example configuration **for a new pool only**:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```sh
# macOS / Linux
cp .env.example .env
```

Edit `.env` before starting. Its defaults expose a local installation at `https://localhost:8443`.

| Variable | Meaning |
| --- | --- |
| `POSTGRES_PASSWORD` | Database password; replace the example value for a deployment |
| `POSTGRES_PORT` | Host loopback port for PostgreSQL; default `5432` |
| `DATABASE_URL` | Direct host-development database connection; keep its credentials/port consistent with PostgreSQL |
| `PUBLIC_ORIGIN` | Exact public HTTPS origin used by browser authentication |
| `CAMPUS_HOST` | Hostname or IP served by Caddy |
| `CAMPUS_BIND` / `CAMPUS_PORT` | Gateway listening address and host port; defaults `127.0.0.1` / `8443` |
| `CATALOG_FILE` | Trusted execution catalog path **inside the API container**; optional for web/API startup, required for executable plans. `/catalog` mounts `infra/catalog` read-only |
| `LLM_PROVIDER` | `disabled` by default; also supports `deepseek`, `model-studio`, `openai-compatible` |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_JSON_MODE` | Provider endpoint, model, private key and structured-output mode |

Compose supplies the API container's database URL using the internal `db` hostname. `DATABASE_URL` in `.env` is for commands running directly on the host.

For access from other computers, set the host, bind address and origin to a reachable LAN address and allow the configured gateway port through the firewall. `localhost` on another laptop refers to that laptop.

To enable the shipped project adapters and built-in execution profiles, select the included catalog in `.env`:

```dotenv
CATALOG_FILE=/app/server/campus/data/product-catalog.json
```

This catalog pins the prepared release's CPU/CUDA image digests. Workers must have those exact approved images. For custom image builds, prepare a matching trusted catalog under `infra/catalog/`, update its environment digests, and point `CATALOG_FILE` to its `/catalog/...` container path. [Image preparation](runner/README.md#build-and-test) and [worker release preparation](bootstrap/public/README.md) cover the runtime side. An unset catalog starts an empty execution catalog, so it is suitable for web/API setup but cannot schedule the example below.

### 2. Build and start

```powershell
# Windows PowerShell
.\infra\start.ps1
```

```sh
# macOS / Linux
bash ./infra/start.sh
```

The startup script installs the locked npm dependencies, builds both web applications, builds the API image, starts PostgreSQL/API/Caddy and applies database migrations. Production serves the built web apps directly; Vite development servers are unnecessary.

**Existing laptop deployment:** keep its private runtime, Compose project name, volumes and certificates. On Windows, `start.ps1` discovers the established `%LOCALAPPDATA%/Campus/server-96bfe6d` runtime and selects `campus-live`. An explicit path is also supported:

```powershell
.\infra\start.ps1 -RuntimeDirectory 'D:/private/campus-runtime' -ProjectName 'campus-live'
```

The following Compose commands use the **new-installation** `.env` configuration. For an existing private runtime, use its env file, override and actual project name as described in [Web operations](docs/WEB_OPERATIONS.md). Back up an existing pool before an upgrade; the startup scripts do not create backups.

### 3. Trust HTTPS and check health

The default gateway uses a local certificate authority. Export its **public** root certificate:

```sh
docker compose --env-file .env -f infra/compose.yaml cp gateway:/data/caddy/pki/authorities/local/root.crt ./campus-local-ca.pem
curl --cacert campus-local-ca.pem https://localhost:8443/api/v1/health
```

In Windows PowerShell, use `curl.exe` if `curl` resolves to a PowerShell alias. Import the public CA using your operating system/browser's certificate trust settings, and provide the same CA to native clients. An operator can instead configure an issued certificate. See [Web operations](docs/WEB_OPERATIONS.md) for deployment and TLS details.

### 4. Initialize the pool and sign in

For a fresh database, run once:

```sh
docker compose --env-file .env -f infra/compose.yaml exec api python -m campus.admin initialize --pool larpool
```

Open the returned one-time owner invitation privately, then set the owner's account credentials. Ordinary self-registration creates a member account. To issue another owner login for an existing pool:

```sh
docker compose --env-file .env -f infra/compose.yaml exec api python -m campus.admin owner-login
```

| Entry point | Default local URL |
| --- | --- |
| Public landing / signed-in workspace | [https://localhost:8443/](https://localhost:8443/) |
| Administrator console | [https://localhost:8443/admin/](https://localhost:8443/admin/) |
| Interactive API documentation | [https://localhost:8443/api/v1/docs](https://localhost:8443/api/v1/docs) |
| Health | [https://localhost:8443/api/v1/health](https://localhost:8443/api/v1/health) |

Owner/admin authentication is required for administration. Starting the coordinator makes the web/API available; computation additionally requires a connected, compatible worker and approved runtime images.

### Optional: enable AI planning

Configure the deployment's private env file. The previously verified DeepSeek configuration is:

```dotenv
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_JSON_MODE=json_object
LLM_API_KEY=replace-with-your-private-key
```

Rerun the startup script to apply configuration. Keep keys server-side. With `LLM_PROVIDER=disabled`, AI planning/project review is unavailable; existing jobs and prepared built-in examples do not require a successful provider call. Project submission through the CLI includes an LLM review, so configure a provider before following that workflow.

## Connect a computer

1. Sign in and open **Devices** (`/contribute`). Download the small **Connect** launcher for the contributing computer's OS.
2. Install and start Docker Desktop on Windows/macOS, or Docker Engine on Linux. The operator must first prepare and publish the matching approved worker release; see [public release preparation](bootstrap/public/README.md).
3. Launch `Connect.cmd` on Windows or `Connect.command` on macOS/Linux. On macOS, the tested terminal route is `sh ~/Downloads/Connect.command`.
4. Create a separate private device invitation in the portal and enter it in the client. The launcher itself contains no invitation or account credential.
5. Wait for preflight and the server-confirmed ready state. Leave the worker running while contributing. Enable GPU use explicitly on that computer when appropriate.

The **low**, **medium** and **max** modes request 10%, 50% and 90% of locally available CPU/memory, further bounded by Docker capacity and local limits. Browser controls wait for worker acknowledgement. A closed worker cannot be restarted by the browser; Ctrl+C stops local participation.

For source builds and advanced setup:

```sh
# macOS / Linux; output is placed in the repository root
go -C runner build -o ../campus ./cmd/campus
```

```powershell
# Windows; output is placed in the repository root
go -C runner build -o ../campus.exe ./cmd/campus
```

Building the CLI does not prepare execution images. The [CLI guide](runner/CLI.md), [image preparation instructions](runner/README.md#build-and-test), [offline dependency policy](environments/README.md) and [native connection packages](bootstrap/connect/README.md) cover approved image digests, profiles, TLS and `--managed` participation. A custom image must match the coordinator's trusted catalog; an arbitrary Docker image is not an executable pool environment. Current prebuilt download links in the [distributed-project guide](docs/DISTRIBUTED_PROJECTS.md) refer to the recorded LAN installation.

## Run the Fashion-MNIST example

The example trains **one global nearest-centroid classifier** from 60,000 Fashion-MNIST training images and evaluates all 10,000 test images. It declares 24 tasks: 12 training maps, one model reduction, 10 evaluation maps and one final reduction.

Before running, prepare a coordinator with the execution catalog above, a configured project-review provider, an account and at least one compatible worker. The catalog must register `campus.project-map@1` and `campus.project-reduce@1` with matching approved environment digests. This example's `auto` map stages require both CPU and CUDA catalog variants; a physical GPU is optional and the maps can run on CPU workers.

```sh
# Install preparation/reference dependencies from the workload lockfile
uv sync --locked --project workloads --extra cpu
uv run --project workloads --extra cpu python examples/fashion-mnist/prepare.py

# Use a prebuilt campus CLI or the binary built above; put it on PATH
campus login --endpoint https://localhost:8443 --ca-file campus-local-ca.pem
cd examples/fashion-mnist
campus run --wait --to ./results/pool-run
```

For another coordinator, replace the endpoint and public CA. The CLI prompts for account credentials, source-sharing selection and plan approval. `--to` must name a directory that does not already exist. Connect multiple compatible workers to allow map tasks to use multiple computers.

Preparation downloads and checks the pinned official archives, then creates immutable shards and the project manifest. The official uncompressed IDX data is also included under `examples/fashion-mnist/data/`; its provenance is recorded alongside it. The example's preparation command uses its own verified archive cache.

The final output includes `model.json`, `result.json`, predictions, accuracy, a confusion matrix and provenance. Intermediate outputs are collected under stage/task paths. The [example guide](examples/fashion-mnist/README.md) explains the file layout, independent reference check, standalone CPU/CUDA baseline and resource limits.

For your own project, declare commands, data globs, stage dependencies, output paths and resource limits in `campus.json`. See the [complete project workflow and supported contract](docs/DISTRIBUTED_PROJECTS.md).

## Development and checks

### Server

Run this section in **Linux/WSL**, with Python 3.12 and uv available. The adapter subprocess protocol uses POSIX pipe handling; native Windows Python is not a supported coordinator/test runtime. The production coordinator runs in a Linux container on all supported hosts.

Start a development PostgreSQL instance and synchronize the locked Python environment:

```sh
docker compose --env-file .env -f infra/compose.yaml up -d db
uv sync --locked --project server
uv run --project server alembic -c server/alembic.ini upgrade head
uv run --project server pytest server/tests -q
uv run --project server python contracts/check.py
uv run --project server python -m pytest -q contracts/tests
uv run --project server ruff check server/campus server/tests contracts infra
uv run --project server python -m unittest discover -s infra/tests -v
```

Server tests use a real PostgreSQL connection and create/drop an isolated schema. Set `TEST_DATABASE_URL` to the development database URL whenever the credentials or port differ from `postgresql+psycopg://campus:campus@127.0.0.1:5432/campus`; the test fixture does not read `.env`'s `DATABASE_URL`. Migration commands use `DATABASE_URL` from the environment or `.env`; point them at the intended development database.

### Web applications

```sh
npm --prefix apps/portal ci
npm --prefix apps/console ci
npm --prefix apps/portal run build
npm --prefix apps/console run build
npm --prefix apps/portal exec -- playwright install chromium
npm --prefix apps/portal run test:e2e
npm --prefix apps/console run test:e2e
```

For frontend editing, run `npm --prefix apps/portal run dev` and `npm --prefix apps/console run dev` in separate terminals. The default development URLs are [http://127.0.0.1:5173/](http://127.0.0.1:5173/) and [http://127.0.0.1:5174/admin/](http://127.0.0.1:5174/admin/). `/api` proxies to `CAMPUS_API_URL`, defaulting to `http://127.0.0.1:8000`; a real coordinator is required for interactive use. Full authenticated flows use the shared HTTPS deployment. Browser tests supply synthetic HTTP fixtures and verify UI behavior separately from physical execution.

### Runner, SDK and workloads

```sh
go -C runner test ./...
go -C runner vet ./...
uv sync --locked --project workloads --extra cpu
uv run --project workloads --extra cpu python -m pytest -q workloads/tests sdk/python/tests examples/fashion-mnist/tests tools/benchmark/tests tools/qa/tests
```

Run contract-generation tests in the server environment as shown above: the SDK and server both have a Python package named `campus` and are tested separately. The `cuda` workload extra is an alternative to `cpu`; do not combine them. Hardware/container integration checks have separate prerequisites, documented in the component guides. Generate API clients with `python contracts/generate.py --target all`; after an API change, refresh the committed contract with `uv run --project server python contracts/generate.py --export`.

## Verification and measured results

The recorded physical demonstration completed **one 24-task job across three computers**: an RTX laptop, the main Windows computer and an Apple Silicon Mac. Four accepted tasks used physical CUDA. Independent verification checked the global model, full data coverage and every prediction; accuracy was **67.68%** on the official 10,000-image test partition.

A separate repeated service comparison completed **9/9 jobs and 216/216 tasks**. The measured two-node pool throughput was **0.994×** the faster single-node service baseline for this small workload. This demonstrates distributed correctness and service operation; it does not establish a speedup for that benchmark. The recovery experiment also recorded successful retry after stopping an active worker.

These are recorded runs with their own conditions, rather than guarantees for a fresh installation. The current evidence and reproduction details are in:

- [Distributed execution, correctness, performance and recovery](docs/DISTRIBUTED_VERIFICATION.md)
- [Live accounts, AI, device controls and CPU/CUDA jobs](docs/WEB_VERIFICATION.md)
- [Current larpool frontend checks](docs/FRONTEND_VERIFICATION.md)
- [Physical Mac CPU result](bootstrap/control_plane_compat/RESULT.md) and [physical NVIDIA result](bootstrap/control_plane_compat/GPU_RESULT.md)

Supported boundaries matter: projects explicitly declare their parallelism; this system does not pool GPU memory or automatically convert arbitrary training scripts into distributed neural-network training. Output checksums/structural acceptance establish file integrity, while application-level quality requires an independent validator or reference. Custom dependencies must be available in an approved image or its verified offline wheelhouse before execution.

## Repository map

```text
apps/portal/               Public landing, accounts, devices and user jobs
apps/console/              Pool administrator interface
apps/shared/               Shared larpool visual styles
server/                    FastAPI coordinator, migrations and server tests
runner/                    Native Go CLI, worker and runtime supervision
sdk/python/                Python client and checkpoint helpers
contracts/                 OpenAPI, JSON schemas, fixtures and client generation
environments/              CPU/CUDA Dockerfiles, guard and offline preparation
workloads/                 Generic, embeddings and training adapters
examples/fashion-mnist/    Reproducible distributed training/evaluation project
infra/                     Compose, HTTPS gateway, startup, smoke and backups
bootstrap/                 Native launchers, release preparation and handoff
tools/                     Benchmark reporting and reproducible live QA
docs/                      User guides, design, verification and submission notes
```

## Operations and further reading

Persistent Compose volumes hold the database, artifacts and gateway TLS state. Stop a fresh local installation with `docker compose --env-file .env -f infra/compose.yaml stop`; retain its project name and volumes when restarting. The [operations guide](docs/WEB_OPERATIONS.md) covers existing private runtimes and upgrades. Coordinated backup/restore is implemented in `infra/snapshot.py`; use a private destination and preserve TLS identity separately as required by the deployment.

- [User guide in Russian](docs/WEB_PRODUCT.md)
- [Distributed-project guide](docs/DISTRIBUTED_PROJECTS.md)
- [Native CLI reference](runner/CLI.md) and [Python SDK](sdk/python/README.md)
- [Workloads and validation policies](workloads/README.md)
- [Offline environment preparation](environments/README.md)
- [Coordinator internals](server/CONTROL_PLANE.md) and [HTTP contract](contracts/openapi-v1.yaml)
- [Supporting submission materials](docs/submission/README.md): product, business, inventory and demo notes; use the current verification reports above for integrated implementation evidence
- [Original project brief](docs/briefs/D_Campus_Edge_Fabric_EN.pdf)

## License

Project source is distributed under the [Apache License 2.0](LICENSE). The included Fashion-MNIST dataset has its own [license and provenance](examples/fashion-mnist/DATASET_LICENSE.txt).
