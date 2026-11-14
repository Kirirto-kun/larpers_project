$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    docker compose -f infra/compose.yaml up -d --build --wait
    if ($LASTEXITCODE -ne 0) { throw "Coordinator startup failed" }
} finally {
    Pop-Location
}
