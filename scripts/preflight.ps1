<#
.SYNOPSIS
    Fail-fast environment check for the local studio.

.DESCRIPTION
    Checks, in order: Python 3.12, Node 24, FFmpeg/ffprobe (unless -SkipFfmpeg), a
    writable data root, and the migration state of that data root. Every failure
    prints "PREFLIGHT FAIL: <reason>" and exits 1 - the script never continues with a
    half-satisfied environment and never reports success silently.

    The migration state is read from the alembic chain itself (currently 18
    migrations, head 0018), so this file does not carry a hard-coded count that can
    drift when a migration is added.

.PARAMETER VerifyDatabase
    Also require the data root database to be exactly at head. Use this after
    scripts/migrate.ps1 (run-studio.bat does) or before starting the studio on an
    existing data root. Without it, the state is reported but not enforced, so a
    brand-new data root can still be created by the migrate step.

.PARAMETER SkipFfmpeg
    Skip the FFmpeg/ffprobe check (FFmpeg is only needed once real audio is rendered).
#>
param(
    [switch]$SkipFfmpeg,
    [switch]$VerifyDatabase
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

function Fail {
    param([string]$Message)
    Write-Host "PREFLIGHT FAIL: $Message" -ForegroundColor Red
    exit 1
}

function Get-PythonCommand {
    if (Test-Path -LiteralPath $venvPython) {
        $version = & $venvPython -c "import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version.Trim() -eq "3.12") {
            return @{ FilePath = $venvPython; Arguments = @() }
        }
        Fail "the repo .venv does not run Python 3.12 (found $($version.Trim())). Recreate it with Python 3.12; do not run the studio on the wrong interpreter."
    }

    $pyVersion = & py -3.12 -c "import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))" 2>$null
    if ($LASTEXITCODE -eq 0 -and $pyVersion.Trim() -eq "3.12") {
        return @{ FilePath = "py"; Arguments = @("-3.12") }
    }

    Fail "Python 3.12 was not found in the repo .venv or via py -3.12."
}

$pythonCommand = Get-PythonCommand
Write-Host "Python 3.12 OK"

$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if ($null -eq $nodeCommand) {
    Fail "Node was not found on PATH; Node 24 is required."
}
try {
    $nodeVersion = (& node --version 2>$null)
}
catch {
    Fail "node is on PATH ($($nodeCommand.Source)) but could not be executed. Install Node 24."
}
if ($LASTEXITCODE -ne 0 -or $null -eq $nodeVersion) {
    Fail "node --version failed with exit code $LASTEXITCODE; Node 24 is required."
}
$nodeVersion = $nodeVersion.Trim()
if ($nodeVersion -notmatch "^v?(\d+)\.") {
    Fail "could not read the Node version from "$nodeVersion"."
}
if ([int]$Matches[1] -ne 24) {
    Fail "Node 24 is required; found $nodeVersion."
}
Write-Host "Node 24 OK ($nodeVersion)"

if (-not $SkipFfmpeg) {
    foreach ($tool in @("ffmpeg", "ffprobe")) {
        $command = Get-Command $tool -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            Fail "$tool was not found on PATH. Install FFmpeg, or run with -SkipFfmpeg when no audio rendering is needed."
        }
        try {
            & $tool -version >$null 2>$null
        }
        catch {
            Fail "$tool is on PATH ($($command.Source)) but could not be executed. Install a working FFmpeg, or run with -SkipFfmpeg when no audio rendering is needed."
        }
        if ($LASTEXITCODE -ne 0) {
            Fail "$tool -version failed with exit code $LASTEXITCODE."
        }
    }
    Write-Host "FFmpeg tools OK"
}

$dataRoot = if ($env:STUDIO_DATA_ROOT) { $env:STUDIO_DATA_ROOT } else { Join-Path $repoRoot "data" }
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
$dataRoot = (Resolve-Path -LiteralPath $dataRoot).Path
$probePath = Join-Path $dataRoot ([System.IO.Path]::GetRandomFileName())
try {
    Set-Content -LiteralPath $probePath -Value "ok" -Encoding ASCII
    Remove-Item -LiteralPath $probePath -Force
}
catch {
    Fail "data root is not writable: $dataRoot"
}
Write-Host "Data root writable ($dataRoot)"

$statusProbe = @"
import os
import sys
from pathlib import Path

repo = Path(os.environ["STUDIO_REPO_ROOT"])
sys.path.insert(0, str(repo / "backend"))

from app.db.migration_status import migration_status  # noqa: E402

status = migration_status(Path(os.environ["STUDIO_DATA_ROOT"]) / "studio.sqlite3")
print(
    "PYTHONPROBE migrations=%d head=%s db=%s state=%s"
    % (status.migration_count, status.head_revision, status.database_revision or "none", status.detail)
)
"@

$env:STUDIO_REPO_ROOT = $repoRoot
$env:STUDIO_DATA_ROOT = $dataRoot
$probeArguments = @($pythonCommand.Arguments) + @("-")
$probeOutput = $statusProbe | & $pythonCommand.FilePath @probeArguments
if ($LASTEXITCODE -ne 0) {
    Fail "could not read the migration state of $dataRoot (exit code $LASTEXITCODE)."
}

$stateLine = $probeOutput | Where-Object { $_ -like "PYTHONPROBE*" } | Select-Object -First 1
if ($null -eq $stateLine) {
    Fail "the migration state probe produced no result."
}
if ($stateLine -notmatch "migrations=(\d+)") { Fail "unreadable migration probe: $stateLine" }
$migrationCount = [int]$Matches[1]
if ($stateLine -notmatch "head=(\S+)") { Fail "unreadable migration probe: $stateLine" }
$headRevision = $Matches[1]
if ($stateLine -notmatch "db=(\S+)") { Fail "unreadable migration probe: $stateLine" }
$databaseRevision = $Matches[1]
if ($stateLine -notmatch "state=(\S+)") { Fail "unreadable migration probe: $stateLine" }
$migrationState = $Matches[1]

Write-Host "Migrations: $migrationCount (head $headRevision); database at $databaseRevision; state $migrationState"

if ($VerifyDatabase) {
    switch ($migrationState) {
        "OK" { Write-Host "Database is at head ($headRevision)" }
        "DATABASE_MISSING" { Fail "no database at $dataRoot\studio.sqlite3. Run scripts/migrate.ps1 (or run-studio.bat) to create it." }
        "REVISION_MISSING" { Fail "the database at $dataRoot\studio.sqlite3 has no alembic revision. It was not created by this repo migrations; do not point the studio at it." }
        "MIGRATION_INCOMPLETE" { Fail "the database is at $databaseRevision but head is ${headRevision}: a migration is pending or was interrupted. Run scripts/migrate.ps1. If an upgrade failed mid-way, restore the verified backup instead of re-running the migration (SQLite DDL is not transactional)." }
        default { Fail "unknown database state "$migrationState"." }
    }
}

Write-Host "PREFLIGHT OK"
