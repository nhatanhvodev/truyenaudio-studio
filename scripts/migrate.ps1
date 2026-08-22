$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$dataRoot = if ($env:STUDIO_DATA_ROOT) { $env:STUDIO_DATA_ROOT } else { Join-Path $repoRoot "data" }

function Get-PythonCommand {
    if (Test-Path -LiteralPath $venvPython) {
        $version = & $venvPython -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))'
        if ($LASTEXITCODE -eq 0 -and $version.Trim() -eq "3.12") {
            return @{
                FilePath = $venvPython
                Arguments = @()
            }
        }
    }

    $pyVersion = & py -3.12 -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))' 2>$null
    if ($LASTEXITCODE -eq 0 -and $pyVersion.Trim() -eq "3.12") {
        return @{
            FilePath = "py"
            Arguments = @("-3.12")
        }
    }

    throw "Python 3.12 was not found in the repo .venv or via py -3.12."
}

New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null

$migration = @'
from pathlib import Path
import os
import sys

from alembic import command
from alembic.config import Config

repo = Path(os.environ["STUDIO_REPO_ROOT"])
data_root = Path(os.environ["STUDIO_DATA_ROOT"])
db_path = data_root / "studio.sqlite3"

sys.path.insert(0, str(repo / "backend"))
config = Config(str(repo / "backend" / "alembic.ini"))
config.set_main_option("script_location", str(repo / "backend" / "migrations"))
config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
command.upgrade(config, "head")
'@

$env:STUDIO_REPO_ROOT = $repoRoot
$env:STUDIO_DATA_ROOT = (Resolve-Path -LiteralPath $dataRoot).Path
$pythonCommand = Get-PythonCommand
$arguments = @($pythonCommand.Arguments) + @("-")
$migration | & $pythonCommand.FilePath @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Database migration failed with exit code $LASTEXITCODE."
}

Write-Host "Database migrations applied"
