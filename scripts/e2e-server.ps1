$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$dataRoot = Join-Path $repoRoot 'frontend\e2e\.tmp-data'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    $python = 'py'
}

if (Test-Path -LiteralPath $dataRoot) {
    $resolvedDataRoot = (Resolve-Path -LiteralPath $dataRoot).Path
    $expectedRoot = (Resolve-Path -LiteralPath (Join-Path $repoRoot 'frontend\e2e')).Path
    if (-not $resolvedDataRoot.StartsWith($expectedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove unexpected E2E data root: $resolvedDataRoot"
    }
    Remove-Item -LiteralPath $resolvedDataRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null

Push-Location (Join-Path $repoRoot 'frontend')
try {
    $env:VITE_STUDIO_FAKE_AUDIO = '1'
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "frontend build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$env:STUDIO_DATA_ROOT = $dataRoot
$env:STUDIO_FAKE_AUDIO = '1'
$env:E2E_REPO_ROOT = $repoRoot
$env:E2E_VOICE_PRESET_ID = '018f0000-0000-7000-8000-000000000001'

$seed = @'
from pathlib import Path
import os

from alembic import command
from alembic.config import Config

from app.contracts import VoiceOrigin
from app.db.base import create_engine_for, session_factory
from app.db.models import VoicePreset

repo = Path(os.environ["E2E_REPO_ROOT"])
data_root = Path(os.environ["STUDIO_DATA_ROOT"])
db_path = data_root / "studio.sqlite3"

config = Config(str(repo / "backend" / "alembic.ini"))
config.set_main_option("script_location", str(repo / "backend" / "migrations"))
config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
command.upgrade(config, "head")

engine = create_engine_for(db_path)
with session_factory(engine)() as session:
    preset_id = os.environ["E2E_VOICE_PRESET_ID"]
    if session.get(VoicePreset, preset_id) is None:
        session.add(
            VoicePreset(
                id=preset_id,
                name="Fake offline narrator",
                provider_voice_id="fake-vi-narrator",
                locale="vi-VN",
                origin=VoiceOrigin.BUILT_IN.value,
                gender_label="neutral",
                region_label="local",
                speed="1.0",
                pitch="0",
                style=None,
                sample_rate=44_100,
                settings_json={"engine": "fake"},
                model_snapshot_hash=None,
                active=True,
            )
        )
        session.commit()
engine.dispose()
'@

$seed | & $python -
if ($LASTEXITCODE -ne 0) {
    throw "E2E database setup failed with exit code $LASTEXITCODE"
}
& $python -m uvicorn app.main:app --app-dir (Join-Path $repoRoot 'backend') --host 127.0.0.1 --port 8765
