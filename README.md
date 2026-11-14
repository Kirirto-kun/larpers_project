# Campus Edge Fabric

The coordinator accepts jobs, schedules compatible devices and publishes verified results. The user portal, pool console and Go runner integrate over the versioned HTTP API.

## Start the coordinator

Install Docker with Compose and Python 3.12. From the repository root, copy `.env.example` to `.env`. Run one of these commands.

```sh
./infra/start.sh
```

```powershell
./infra/start.ps1
```

Initialize the pool once. Keep the one-time invitation private.

```sh
docker compose --env-file .env -f infra/compose.yaml exec api python -m campus.admin initialize
```

Set up the CA trust for this installation. Export its public certificate and check the API using that CA.

```sh
docker compose --env-file .env -f infra/compose.yaml exec -T gateway cat /data/caddy/pki/authorities/local/root.crt > campus-local-ca.pem
curl --cacert campus-local-ca.pem https://localhost:8443/api/v1/health
```

For a browser, import the public CA through the approved trust mechanism on each device. When using an issued campus certificate, configure the gateway with its certificate and key. Set `CAMPUS_HOST`, `CAMPUS_BIND`, `PUBLIC_ORIGIN` and the firewall to the authorized LAN address before testing from other devices. Test TLS, upload, download and reconnect from every participating machine on the intended SSID. If routing is blocked, use a reachable campus-authorized coordinator address. For a runner that supports certificate pinning, distribute the SHA-256 fingerprint through the trusted invitation channel and set `TLS_FINGERPRINT_SHA256`. The gateway's managed leaf certificate can rotate.

When the UI builds are present at `apps/portal/dist` and `apps/console/dist`, the coordinator serves them at `/` and `/admin/`. Interactive API docs are at `/api/v1/docs`. A pool owner can use `python -m campus.admin owner-login` in the API container to obtain a new one-time login or `owner-cli` to issue a revocable CLI credential.

## Run the offline checksum smoke

Use an isolated test pool. Set `CATALOG_FILE=/catalog/smoke.json` in its environment and start the coordinator. Prepare the pinned Python workload image once, on the device that will execute the smoke. Run the script from that device with a trusted CA and the test pool's owner CLI credential.

```sh
docker pull python@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
umask 077
docker compose --env-file .env -f infra/compose.yaml exec -T api python -m campus.admin owner-cli > campus-smoke-credential.json
python3 infra/smoke.py --ca campus-local-ca.pem --credential-file campus-smoke-credential.json
```

Add `--ai` to exercise the configured live LLM before execution. Without it, the script uploads new data, creates a prepared plan, registers a device, downloads its assigned input, computes the digest in an offline container, uploads the output, waits for acceptance and verifies the downloaded result and completion replay. This test records one computing device. The formal benchmark uses the workload adapters and the physical inventory from the runner integration.

## Configure AI planning and workload adapters

Set `LLM_PROVIDER=model-studio`, `deepseek` or `openai-compatible`, the exact HTTPS `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` and a supported `LLM_JSON_MODE`. Keep the key in deployment configuration. Preview creation returns immediately. Workers continue to claim and report existing jobs when the planner is unavailable. Project excerpts require explicit file selection and consent. The API inspects uploaded ZIP data and validates plan references before assigning work.

For DeepSeek, the verified configuration is `LLM_PROVIDER=deepseek`, `LLM_BASE_URL=https://api.deepseek.com`, `LLM_MODEL=deepseek-flash`, `LLM_JSON_MODE=json_object`. The adapter disables thinking for this structured planning call and validates the result with Pydantic. The live capability check and complete HTTPS/Docker run passed on 2026-11-14. See [DeepSeek JSON mode](https://api-docs.deepseek.com/guides/json_mode/). Keep the credential only in the ignored root `.env`; startup scripts pass that file explicitly to Compose.

Set `CATALOG_FILE` to a trusted catalog with immutable OCI profiles, adapter versions and validation policies. The smoke catalog provides a minimal example. Production workload adapters use the JSON request/response protocol and a bounded runtime with read-only access to the required artifacts. The coordinator uses subprocess timeouts and output limits. Configure the heavy adapter command with its own sandbox and resource limits. The smoke validator is trusted Python shipped with the coordinator and reads a bounded result from its own volume. Model Studio output modes vary by selected endpoint and model. [Provider documentation](https://help.aliyun.com/en/model-studio/qwen-structured-output)

Checkpoint-capable adapters must declare `checkpoint_files` in the catalog. Upload one ZIP containing `campus-checkpoint.json` and every state file. The manifest uses [checkpoint-manifest.schema.json](contracts/schemas/checkpoint-manifest.schema.json): compatibility digest plus each file's relative path, size and SHA-256. The server checks the exact archive contents and all required files before making an artifact available. Publish its pointer with the current attempt proof and increasing sequence. Worker code is responsible for capturing a consistent full training state.

Adapters that can execute any assigned item subset may set `adaptive_batches=true`, `initial_batch_units` and `max_batch_units`. Only never-leased work tasks are split. The server uses accepted durations on the same node/runtime/parameters for a 10-second target and shortens the tail. A worker may supply `cached_artifact_digests` and its measured `transfer_bytes_per_second` in capabilities. Among fresh compatible tasks belonging to the next fair owner, the scheduler considers missing input bytes and accepted compute rates. After 30 seconds queued work keeps FIFO priority. These hints grant no artifact access and never replace checksum validation. Issued item IDs stay fixed across retries. Benchmark batches remain fixed; benchmark `concurrency` is a limit **per node**, matching standalone runs.

## Develop and test

```sh
docker compose --env-file .env -f infra/compose.yaml up -d db
uv sync --locked --project server
uv run --project server alembic -c server/alembic.ini upgrade head
uv run --project server pytest server/tests -q
uv run --project server python contracts/check.py
uv run --project server ruff check server/campus server/tests contracts infra
python3 contracts/generate.py --target all
```

The test suite creates a fresh schema on real PostgreSQL and drops that schema when done. Set `TEST_DATABASE_URL` to override its connection. Generate a single client with `--target portal`, `--target console`, `--target go` or `--target python`. After API changes regenerate the committed contract with `uv run --project server python contracts/generate.py --export`.

```sh
CAMPUS_FIXTURE=retrying uv run --project server uvicorn --app-dir contracts mock_server:app --port 8001
```

Fixture scenarios include `ready`, `offline`, `retrying`, `failed`, `needs_input`, `partial` and `empty`.

## Back up and restore

Choose a private destination on a separate device. Set the source Compose project name from your deployment. The script stops the API briefly while capturing a PostgreSQL dump and the artifact volume, records hashes, and starts the API again. Restore to a new project name and unused database port. Use the manifest timestamp as the recovery point. To preserve gateway identity across host loss, retain the TLS volume or issued certificate/key through the deployment's existing secure certificate backup process.

```sh
python3 infra/snapshot.py backup /mounted-backup/campus-snapshot --project infra
POSTGRES_PORT=55434 python3 infra/snapshot.py restore /mounted-backup/campus-snapshot --project campus-restore
```

PowerShell wrappers are available in `infra/`. The restore performs artifact reconciliation before the recovered API is started. A pool has a single writable coordinator.

## Project material

- [Implementation plan](docs/superpowers/plans/2026-11-14-campus-pool-implementation.md)
- [System design and failure matrix](docs/superpowers/specs/2026-11-14-campus-pool-design.md)
- [Control-plane analysis and verification](server/CONTROL_PLANE.md)
- [HTTP contract](contracts/openapi-v1.yaml)
- [Official brief](docs/briefs/D_Campus_Edge_Fabric_EN.pdf)
