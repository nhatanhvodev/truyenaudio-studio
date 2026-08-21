# Task 7 Report: Rights gate and export bundle v1

## Status

DONE

## Files changed

- `backend/app/modules/compliance/rights.py`
- `backend/app/modules/exports/schemas.py`
- `backend/app/modules/exports/workflow.py`
- `backend/app/api/exports.py`
- `backend/app/main.py`
- `backend/tests/exports/test_gate.py`
- `backend/tests/exports/test_bundle_v1.py`
- `frontend/src/features/exports/ExportGate.tsx`

## RED evidence

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/exports -q
```

Initial output:

```text
ERROR backend\tests\exports\test_bundle_v1.py
E   ModuleNotFoundError: No module named 'app.modules.exports'
ERROR backend\tests\exports\test_gate.py
E   ModuleNotFoundError: No module named 'app.modules.exports'
2 errors in 0.32s
```

## GREEN evidence

Focused exports:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/exports -q
```

```text
........                                                                 [100%]
8 passed in 2.19s
```

Ruff touched backend files:

```powershell
.\.venv\Scripts\python -m ruff check backend/app/modules/compliance/rights.py backend/app/modules/exports/schemas.py backend/app/modules/exports/workflow.py backend/app/api/exports.py backend/tests/exports/test_gate.py backend/tests/exports/test_bundle_v1.py
```

```text
All checks passed!
```

Frontend build:

```powershell
npm run build
```

```text
> build
> tsc -b && vite build
vite v7.1.2 building for production...
✓ 28 modules transformed.
✓ built in 777ms
```

Full backend:

```powershell
.\.venv\Scripts\python -m pytest backend/tests -q
```

```text
248 passed, 1 warning in 33.70s
```

Warning was the existing SQLAlchemy unresolved FK-cycle warning in `tests/db/test_schema.py::test_frozen_migration_matches_orm_metadata_server_defaults`.

## Implementation summary

- Added `RightsGate` with fail-closed publication checks for `CLEARED` status, active territory/time-valid grants, required `TRANSLATE_VI`, `CREATE_AUDIO`, `PUBLIC_STREAM`, plus `DOWNLOAD` and `MONETIZE` when requested/used.
- Added export schemas for `PublicationMetadata`, `GateDecision`, and `ExportBundle`.
- Added `ExportWorkflow` private archive and publication bundle builders. Publication bundles include exactly `tap-0001.mp3`, `metadata.json`, `ban-dich.md`, `transcript.srt`, `production-report.json`, `provenance.json`, `THIRD_PARTY_LICENSES.txt`, and `checksums.sha256`.
- Bundle build is contained under `artifacts/exports/builds`, uses a partial directory before atomic rename, writes checksums, verifies checksums, verifies MP3 checksum/metadata/ffprobe before READY, then zips and records `Artifact`/`Export`.
- Private archive includes `PRIVATE_ONLY.txt` and deliberately omits publication `metadata.json`.
- Reports/provenance include hashes, source reference/type, rights result, and evidence display/hash only; no evidence file contents, secrets, or full prompts.
- Added minimal FastAPI export routes and registered the router.
- Added `ExportGate.tsx` for Task 8 to wire later; no final app routing/E2E/CSRF was implemented.

## Self-review

- Scope stayed within Task 7 modules/tests/frontend component plus minimal router registration.
- No QQ fetching, crawler/browser automation, app-main upload, Official Yuewen API, cloud TTS, multi-voice, or auto-upload behavior was added.
- Publication `episodeNumber` is only suggested through `PublicationMetadata.suggested_episode_number`.
- Default workflow uses the existing `FFmpegAudioProcessor` for publication MP3 probing; tests inject a deterministic probe to stay offline.

## Concerns

- Full backend still emits the pre-existing SQLAlchemy FK-cycle warning for the canonical schema comparison.
- The frontend export gate component is intentionally not routed into the main app because final UI routing/E2E/CSRF belongs to Task 8.
