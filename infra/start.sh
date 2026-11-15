#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

compose_args=(compose)
if [[ -n "${CAMPUS_RUNTIME_DIR:-}" ]]; then
    runtime_env="$CAMPUS_RUNTIME_DIR/runtime.env"
    override="$CAMPUS_RUNTIME_DIR/live.override.yaml"
    for file in "$runtime_env" "$override"; do
        if [[ ! -f "$file" ]]; then
            printf 'Private runtime requires both runtime.env and live.override.yaml; missing: %s\n' "$file" >&2
            exit 1
        fi
    done
    compose_args+=(--env-file "$runtime_env" -f infra/compose.yaml -f "$override")
    compose_args+=(--project-name "${CAMPUS_PROJECT_NAME:-campus-live}")
else
    if [[ ! -f .env ]]; then
        printf 'Create .env from .env.example before starting a fresh installation.\n' >&2
        exit 1
    fi
    compose_args+=(--env-file .env -f infra/compose.yaml)
    if [[ -n "${CAMPUS_PROJECT_NAME:-}" ]]; then
        compose_args+=(--project-name "$CAMPUS_PROJECT_NAME")
    fi
fi

for app in portal console; do
    (cd "apps/$app" && npm ci && npm run build)
done
exec docker "${compose_args[@]}" up -d --build --wait
