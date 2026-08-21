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
