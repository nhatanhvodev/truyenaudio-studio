# Task 5 Report: Local voice catalog, preview, VieNeu and Piper adapters

## Status

DONE_WITH_CONCERNS

## RED Evidence

Command:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m pytest backend\tests\providers\test_local_tts_contract.py -q
```

Observed failing output before implementation:

```text
ModuleNotFoundError: No module named 'app.modules.voices'
ERROR backend\tests\providers\test_local_tts_contract.py
```

Additional RED during self-review:

```text
FAILED backend\tests\providers\test_local_tts_contract.py::test_catalog_marks_piper_active_when_vieneu_is_not_available
AssertionError: ('piper-vais1000', False) != ('piper-vais1000', True)
```

Command:

```powershell
cd D:\truyenaudio-studio\frontend
npm test -- --run src/features/voices/VoiceBrowser.test.tsx
```

Observed failing output before implementation:

```text
Error: Failed to resolve import "./VoiceBrowser" from "src/features/voices/VoiceBrowser.test.tsx". Does the file exist?
Test Files  1 failed (1)
```

## GREEN Evidence

Command:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m pytest backend\tests\providers\test_local_tts_contract.py -q
```

Output:

```text
.........                                                                [100%]
9 passed in 1.14s
```

Command:

```powershell
cd D:\truyenaudio-studio\frontend
npm test -- --run src/features/voices/VoiceBrowser.test.tsx
```

Output:

```text
✓ src/features/voices/VoiceBrowser.test.tsx (2 tests) 129ms
Test Files  1 passed (1)
Tests  2 passed (2)
```

## Additional Verification

Command:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m ruff check backend\app\modules\voices\catalog.py backend\app\providers\vieneu.py backend\app\providers\piper.py backend\app\api\voices.py backend\tests\providers\test_local_tts_contract.py backend\app\main.py
```

Output:

```text
All checks passed!
```

Command:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m pytest backend\tests -q
```

Output:

```text
228 passed, 1 warning in 36.14s
```

Warning:

```text
tests/db/test_schema.py::test_frozen_migration_matches_orm_metadata_server_defaults
SAWarning: Cannot correctly sort tables; there are unresolvable cycles between tables "artifacts, chapters, exports, projects, source_revisions, translation_runs, voice_plans, voice_presets"
```

Command:

```powershell
cd D:\truyenaudio-studio\frontend
npm test -- --run
```

Output:

```text
Test Files  3 passed (3)
Tests  7 passed (7)
```

Command:

```powershell
cd D:\truyenaudio-studio\frontend
npm run build
```

Output:

```text
✓ 28 modules transformed.
✓ built in 921ms
```

## Files Changed

- `backend/app/modules/voices/catalog.py`
- `backend/app/providers/vieneu.py`
- `backend/app/providers/piper.py`
- `backend/app/api/voices.py`
- `backend/app/main.py`
- `backend/tests/providers/test_local_tts_contract.py`
- `frontend/src/features/voices/VoiceBrowser.tsx`
- `frontend/src/features/voices/VoiceBrowser.test.tsx`
- `.superpowers/sdd/2026-08-19-local-studio-single-voice-mvp/task-5-report.md`

## Implementation Notes

- Added a local-only `VoiceCatalog` with verified model/license activation gates, VieNeu default selection only when available and POC-passed, Piper fallback, local missing-model guidance, and preview cache keys that include text, preset, settings hash, model hash, and pronunciation hash.
- Added preview-only `/api/voices` endpoints. The preview endpoint returns a queued `PREVIEW_TTS` `VOICE_PREVIEW` job view and does not synthesize audio or use cloud fallback.
- Added VieNeu and Piper adapters that use subprocess argv lists with `shell=False`, injected process factories for tests, timeout/cancel termination followed by kill, redacted failure streams, `*.partial` output, WAV validation, and atomic commit.
- Added the `VoiceBrowser` UI for local catalog browsing and preview queueing with missing-model guidance and no cloud fallback path.

## Self-Review

- Confirmed tests exercise the real catalog/adapters/UI behavior with fake subprocess contracts only.
- Confirmed adapters do not import or load provider model libraries into the API process.
- Confirmed failed subprocess output is not exposed beyond byte counts.
- Confirmed output WAV constraints cover RIFF, mono, expected sample rate, nonzero frames, 16-bit PCM, and 0.5-120 second duration.

## Concerns

- `VoiceCatalog.local_defaults()` currently requires future verification metadata to set real model/license hashes; without those verified snapshots the default API catalog intentionally reports local voices as unavailable.
- The broader backend test suite has an existing SQLAlchemy table-cycle warning unrelated to Task 5.

## Round 1 Review Fix

### RED Evidence

Command:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m pytest backend\tests\providers\test_local_tts_contract.py -q
```

Observed failing output after adding regressions:

```text
FAILED backend\tests\providers\test_local_tts_contract.py::test_local_defaults_loads_verified_manifest_and_api_can_queue_preview
AssertionError: API returned hardcoded unavailable defaults instead of the verified manifest preset

FAILED backend\tests\providers\test_local_tts_contract.py::test_common_preview_text_estimates_between_20_and_60_seconds
AssertionError: assert 20 <= 13.2
2 failed, 9 passed in 1.61s
```

### GREEN Evidence

Command:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m pytest backend\tests\providers\test_local_tts_contract.py -q
```

Output:

```text
...........                                                              [100%]
11 passed in 1.40s
```

Command:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m ruff check backend\app\modules\voices\catalog.py backend\tests\providers\test_local_tts_contract.py
```

Output:

```text
All checks passed!
```

Command:

```powershell
cd D:\truyenaudio-studio\frontend
npm test -- --run src/features/voices/VoiceBrowser.test.tsx
```

Output:

```text
✓ src/features/voices/VoiceBrowser.test.tsx (2 tests) 145ms
Test Files  1 passed (1)
Tests  2 passed (2)
```

Command:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m pytest backend\tests -q
```

Output:

```text
230 passed, 1 warning in 32.00s
```

Warning remains:

```text
tests/db/test_schema.py::test_frozen_migration_matches_orm_metadata_server_defaults
SAWarning: Cannot correctly sort tables; there are unresolvable cycles between tables "artifacts, chapters, exports, projects, source_revisions, translation_runs, voice_plans, voice_presets"
```

### Changes

- `VoiceCatalog.local_defaults()` now reads verified local preset metadata from `data_root/models/voices/voice-presets.manifest.json` using schema `truyenaudio-studio.voice-presets.v1`.
- Manifest presets include model path, license snapshot path, model hash, license snapshot hash, model/license verification flags, VieNeu POC status, settings hash, and pronunciation hash.
- Manifest paths must be relative and stay under `data_root`; invalid entries are skipped fail-closed.
- Availability still requires verified booleans plus matching model and license snapshot hashes; missing metadata still falls back to unavailable local defaults with guidance.
- Expanded the common preview text to a Vietnamese narration passage with an estimated 20-60 second duration and varied punctuation/rhythm.

### Self-Review

- Confirmed verified Piper metadata can make the catalog/API mark a local voice available and queue a preview job without spawning a real model.
- Confirmed the preview passage is protected by a words-per-second heuristic and punctuation check.
- Confirmed no auto-download, cloud fallback, or real provider spawn was added.
