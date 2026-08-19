param(
    [switch]$SkipFfmpeg
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

function Get-Python312 {
    if (Test-Path -LiteralPath $venvPython) {
        $version = & $venvPython -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))'
        if ($LASTEXITCODE -eq 0 -and $version.Trim() -eq "3.12") {
            return $venvPython
        }
    }

    $pyVersion = & py -3.12 -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))' 2>$null
    if ($LASTEXITCODE -eq 0 -and $pyVersion.Trim() -eq "3.12") {
        return "py -3.12"
    }

    throw "Python 3.12 was not found in the repo .venv or via py -3.12."
}

$python312 = Get-Python312
Write-Host "Python 3.12"

$nodeVersion = (& node --version 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch "^v?(\d+)\.") {
    throw "Node 24 was not found."
}
if ([int]$Matches[1] -ne 24) {
    throw "Node 24 is required; found $nodeVersion."
}
Write-Host "Node 24"

if (-not $SkipFfmpeg) {
    & ffmpeg -version >$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "ffmpeg was not found."
    }
    & ffprobe -version >$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "ffprobe was not found."
    }
    Write-Host "FFmpeg tools available"
}

$dataRoot = if ($env:STUDIO_DATA_ROOT) { $env:STUDIO_DATA_ROOT } else { Join-Path $repoRoot "data" }
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
$probePath = Join-Path $dataRoot ([System.IO.Path]::GetRandomFileName())
try {
    Set-Content -LiteralPath $probePath -Value "ok" -Encoding ASCII
    Remove-Item -LiteralPath $probePath -Force
}
catch {
    throw "Data root is not writable: $dataRoot"
}

Write-Host "Data root writable"
