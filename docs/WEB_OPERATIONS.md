# Web product operations

The portal at `/` and administrator console at `/admin/` share the coordinator's
HTTPS origin and authentication. Install Node.js 22 with npm, Docker Engine with
Compose v2 (including `up --wait`), and Python 3.12 for maintenance tools. On
Windows, install and start Docker Desktop manually. Run commands from this checkout.

## Build and start

For a **new installation only**, copy `.env.example` to `.env`, choose a private
database password and configure the host, origin and bind address. Run:

```powershell
./infra/start.ps1
```

```sh
./infra/start.sh
```

Each script runs `npm ci` and `npm run build` in both `apps/portal` and
`apps/console`, then `docker compose ... up -d --build --wait`. Dependency
installation requires registry access or a suitable npm cache. A failed install
or UI build stops before Compose. The API image copies both production builds;
rebuild/restart it after changing frontend code. UI development servers are not
required for production. Never place server credentials in Vite variables or
frontend source: browser bundles are public.

Without a private runtime, the scripts retain Compose's existing project-name
resolution, including `COMPOSE_PROJECT_NAME` and the compose-directory default
(`infra`). Reuse the exact project name of an existing installation; changing it
can create different, empty named volumes.

## Existing private Windows installation

The established runtime stays outside Git at
`%LOCALAPPDATA%/Campus/server-96bfe6d`. When either `runtime.env` or
`live.override.yaml` exists there, PowerShell selects that runtime, requires both
files, and uses project **`campus-live`**. It combines the current checkout's
`infra/compose.yaml` with the private override and passes the private env file
directly to Compose. It does not fall back to `.env` when that runtime is
incomplete. No private file needs to be copied into the repository.

To select another established runtime explicitly:

```powershell
./infra/start.ps1 -RuntimeDirectory 'D:/private/campus-runtime'
# Only when this is the actual existing project's name:
./infra/start.ps1 -RuntimeDirectory 'D:/private/campus-runtime' -ProjectName 'campus-live'
```

```sh
CAMPUS_RUNTIME_DIR=/srv/private/campus-runtime ./infra/start.sh
# Only when this is the actual existing project's name:
CAMPUS_RUNTIME_DIR=/srv/private/campus-runtime CAMPUS_PROJECT_NAME=campus-live ./infra/start.sh
```

PowerShell also accepts `CAMPUS_RUNTIME_DIR`. Bash uses the explicit environment
variable and does not discover a Windows runtime. With a private runtime, the
default project is always `campus-live`, independent of a shell's unrelated
`COMPOSE_PROJECT_NAME`. Explicit project overrides are an operator decision.

Before upgrading an existing deployment, take a coordinated database/artifact
snapshot using its actual runtime and project, and preserve TLS material through
the existing private backup process. Startup applies database migrations. Keep
the same single writable coordinator. These startup scripts do not take backups.

The named `database`, `artifacts`, `tls` and `caddy_config` volumes hold the
database, uploaded inputs/results, Caddy identity and Caddy configuration. With
the established project they resolve to the existing deployment volumes unless
the private override explicitly supplies other volume names. Retain that override
and its dataset/source/catalog bind mounts. The base compose mounts
`infra/catalog` read-only at `/catalog`; additional host datasets/source
directories belong in the private override, with the same container paths used
by the configured catalog. Keep read-only mounts read-only and do not bake
private datasets into images. Review any historical frontend bind mounts before
upgrading: a mount over `/ui/portal` or `/ui/console` hides the new image's UI.

## Accounts, AI and HTTPS

On a fresh installation, initialize the pool once with `python -m campus.admin
initialize` inside the API container using the same Compose files, env and project
as startup. Do not initialize the existing pool again. Self-registration creates
a member; an email address does not grant administrator rights. The existing
owner uses a private one-time owner invitation (`python -m campus.admin
owner-login` inside the API container) to sign in and set account credentials
through `/auth/account`. Keep invitation links, passwords and CLI credentials
private. Existing owner/admin roles and device credentials remain authoritative.

Configure `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` and
`LLM_JSON_MODE` in the private runtime env file for the established deployment,
or ignored `.env` for a new one. A missing/disabled provider leaves AI planning
unavailable; do not claim a successful live provider check from a build alone.
Do not print `docker compose config`, publish runtime files, or push credentials,
certificates, private keys, uploaded datasets or generated owner links to Git.

Keep `PUBLIC_ORIGIN`, `CAMPUS_HOST`, bind address/port and gateway certificates
consistent with the reachable HTTPS URL. Preserve the existing TLS volume or
private certificate mounts. Distribute the public CA certificate through the
approved channel and explicitly install trust on participating devices when
required. The scripts never install an OS CA automatically. Cookies require
HTTPS; access both browser apps through that same origin. Verify health,
registration/login, owner access, uploads/results and a real device reconnect
after an authorized deployment. A successful startup alone does not verify
physical devices, provider access or end-to-end jobs.

## Safe startup checks

```sh
python -m unittest discover -s infra/tests -v
```

These tests use temporary fake npm and Docker commands to check build ordering,
private-runtime selection and failures. They do not start, stop, build or inspect
the live Docker deployment or read its private configuration.
