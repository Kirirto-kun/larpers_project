param(
    [Parameter(Mandatory)][string]$Directory,
    [Parameter(Mandatory)][string]$Project
)
$ErrorActionPreference = 'Stop'
python (Join-Path $PSScriptRoot "snapshot.py") backup $Directory --project $Project
if ($LASTEXITCODE -ne 0) { throw "Backup failed" }
