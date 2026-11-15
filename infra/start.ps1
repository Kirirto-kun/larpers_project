param(
    [string]$RuntimeDirectory = $env:CAMPUS_RUNTIME_DIR,
    [string]$ProjectName = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    # An incomplete existing runtime must fail rather than create a fresh pool.
    if (-not $RuntimeDirectory -and $env:LOCALAPPDATA) {
        $runtimeCandidates = @(Join-Path $env:LOCALAPPDATA 'Campus/server-96bfe6d')
        $packages = Join-Path $env:LOCALAPPDATA 'Packages'
        if (Test-Path -LiteralPath $packages -PathType Container) {
            foreach ($package in Get-ChildItem -LiteralPath $packages -Directory -Filter 'OpenAI.Codex_*') {
                $runtimeCandidates += Join-Path $package.FullName 'LocalCache/Local/Campus/server-96bfe6d'
            }
        }
        foreach ($existing in $runtimeCandidates) {
            if ((Test-Path -LiteralPath (Join-Path $existing 'runtime.env')) -or
                (Test-Path -LiteralPath (Join-Path $existing 'live.override.yaml'))) {
                $RuntimeDirectory = $existing
                break
            }
        }
    }
    $composeArgs = @('compose')
    if ($RuntimeDirectory) {
        $runtimeEnv = Join-Path $RuntimeDirectory 'runtime.env'
        $override = Join-Path $RuntimeDirectory 'live.override.yaml'
        foreach ($file in @($runtimeEnv, $override)) {
            if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
                throw "Private runtime requires both runtime.env and live.override.yaml; missing: $file"
            }
        }
        if (-not $ProjectName) { $ProjectName = 'campus-live' }
        $composeArgs += @('--env-file', $runtimeEnv, '-f', 'infra/compose.yaml', '-f', $override)
    } else {
        if (-not (Test-Path -LiteralPath '.env' -PathType Leaf)) {
            throw 'Create .env from .env.example before starting a fresh installation.'
        }
        $composeArgs += @('--env-file', '.env', '-f', 'infra/compose.yaml')
    }
    if ($ProjectName) { $composeArgs += @('--project-name', $ProjectName) }

    # npm.cmd avoids PowerShell execution-policy failures from npm.ps1 on Windows.
    $npmCommand = if ($env:OS -eq 'Windows_NT') { 'npm.cmd' } else { 'npm' }
    foreach ($app in @('portal', 'console')) {
        Push-Location (Join-Path $root "apps/$app")
        try {
            & $npmCommand ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci failed for $app" }
            & $npmCommand run build
            if ($LASTEXITCODE -ne 0) { throw "UI build failed for $app" }
        } finally {
            Pop-Location
        }
    }
    & docker @composeArgs up -d --build --wait
    if ($LASTEXITCODE -ne 0) { throw "Coordinator startup failed" }
} finally {
    Pop-Location
}
