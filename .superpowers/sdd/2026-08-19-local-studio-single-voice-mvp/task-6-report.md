# Task 6 Report: Single narrator SpeechWorkflow, FFmpeg mastering, audio approval

## Status

DONE

## RED evidence

Command:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m pytest backend/tests/speech backend/tests/audio/test_ffmpeg.py -q
```

Output:

```text
ERROR backend\tests\speech\test_single_narrator.py
ModuleNotFoundError: No module named 'app.modules.speech'
ERROR backend\tests\audio\test_ffmpeg.py
ModuleNotFoundError: No module named 'app.providers.ffmpeg_audio'
2 errors in 0.33s
```

## GREEN evidence

Focused speech/audio suite:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/speech backend/tests/audio -q
```

```text
......                                                                   [100%]
6 passed in 1.17s
```

Ruff touched Python files:

```powershell
.\.venv\Scripts\python -m ruff check backend/app/modules/speech/narration.py backend/app/modules/speech/workflow.py backend/app/providers/ffmpeg_audio.py backend/app/modules/audio/qa.py backend/app/api/audio.py backend/app/main.py backend/tests/speech/test_single_narrator.py backend/tests/audio/test_ffmpeg.py
```

```text
All checks passed!
```

Broader backend suite:

```powershell
.\.venv\Scripts\python -m pytest backend/tests -q
```

```text
236 passed, 1 warning in 28.10s
```

Frontend build:

```powershell
cd D:\truyenaudio-studio\frontend
npm run build
```

```text
✓ built in 1.05s
```

Frontend tests:

```powershell
npm test -- --run
```

```text
Test Files  3 passed (3)
Tests  7 passed (7)
```

## Files changed

- `backend/app/modules/speech/narration.py`
- `backend/app/modules/speech/workflow.py`
- `backend/app/providers/ffmpeg_audio.py`
- `backend/app/modules/audio/qa.py`
- `backend/app/api/audio.py`
- `backend/app/main.py`
- `backend/tests/speech/test_single_narrator.py`
- `backend/tests/audio/test_ffmpeg.py`
- `frontend/src/features/audio/AudioReview.tsx`

## Implementation notes

- `SpeechWorkflow.configure_single` requires an approved translation run, creates `VoiceMode.SINGLE_NARRATOR`, exactly one narrator role, derives narration separately from translation text, stores separate narration hashes, and moves the chapter to `VOICE_CONFIGURED`.
- Rendering computes TTS cache keys from narration, provider, model, voice, settings, and pronunciation hashes. Cache hits verify artifact checksums before reuse; explicit regeneration supersedes the affected cached segment artifact and reuses the other segment artifacts.
- FFmpeg mastering uses a concat list file, generated silence WAV entries for pauses, two-pass loudnorm target `I=-16:TP=-1.5:LRA=11`, mono 44.1 kHz MP3, `128k`, and ID3v2.3 options.
- Audio approval is separate from translation approval and requires the requested master artifact, checksum verification, a successful probe, and no open major/critical audio QA blockers.

## Self-review

- Scope stayed within Task 6 modules/tests plus router registration and the requested frontend component.
- No QQ fetching, browser automation, cloud TTS, export bundles, upload flow, or paid network behavior was added.
- Existing schema already had the required voice/audio tables, so no migration was added.

## Concerns

- The new `AudioReview` component is standalone and build-verified, but it is not wired into `App.tsx`; the brief only required creating the component.
- The deterministic workflow tests inject fake TTS/mastering boundaries; real FFmpeg behavior is covered at the provider command/probe seam without invoking paid or network services.

---

## Review Fix Round 1

### Status

DONE

### RED evidence

Command:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m pytest backend/tests/speech backend/tests/audio -q
```

Output:

```text
FAILED backend\tests\speech\test_single_narrator.py::test_premaster_audio_qa_generates_blocking_issue_before_approval[clipped-premaster-segment]
FAILED backend\tests\speech\test_single_narrator.py::test_premaster_audio_qa_generates_blocking_issue_before_approval[long-silence-premaster-segment]
FAILED backend\tests\audio\test_ffmpeg.py::test_ffmpeg_silence_temp_file_name_is_safe_for_windows_operation_id
3 failed, 6 passed in 2.37s
```

### GREEN evidence

Focused speech/audio suite:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/speech backend/tests/audio -q
```

```text
.........                                                                [100%]
9 passed in 3.36s
```

Ruff touched Python files:

```powershell
.\.venv\Scripts\python -m ruff check backend/app/modules/speech/workflow.py backend/app/providers/ffmpeg_audio.py backend/app/modules/audio/qa.py backend/tests/speech/test_single_narrator.py backend/tests/audio/test_ffmpeg.py
```

```text
All checks passed!
```

Broader backend suite:

```powershell
.\.venv\Scripts\python -m pytest backend/tests -q
```

```text
239 passed, 1 warning in 36.74s
```

### Files changed

- `backend/app/modules/audio/qa.py`
- `backend/app/modules/speech/workflow.py`
- `backend/app/providers/ffmpeg_audio.py`
- `backend/tests/audio/test_ffmpeg.py`
- `backend/tests/speech/test_single_narrator.py`

### Self-review

- Fixed Windows-unsafe FFmpeg silence filenames by deriving a sanitized temp stem with a hash suffix from `operation_id`; concat-list and two-pass loudnorm behavior are preserved.
- Added pre-master WAV analysis for source/TTS segment peaks over `-1 dBFS` and non-scene-break silence longer than 8 seconds, generating open Major `CLIPPING` or `SILENCE` issues before approval.
- `SpeechWorkflow.approve_audio` already blocks open Major/Critical audio categories, so generated pre-master issues now block approval without broadening export, cloud TTS, or upload scope.

### Concerns

- Pre-master QA currently analyzes readable 16-bit PCM WAV segment files and skips unreadable/non-WAV fake files; the real local TTS adapters already validate WAV output.

---

## Review Fix Round 2

### Status

DONE

### RED evidence

Command:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m pytest backend/tests/speech backend/tests/audio -q
```

Output:

```text
FAILED backend\tests\speech\test_single_narrator.py::test_long_pause_generates_blocking_silence_issue_before_approval
1 failed, 9 passed in 3.63s
```

### GREEN evidence

Focused speech/audio suite:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/speech backend/tests/audio -q
```

```text
..........                                                               [100%]
10 passed in 3.67s
```

Ruff touched Python files:

```powershell
.\.venv\Scripts\python -m ruff check backend/app/modules/audio/qa.py backend/app/modules/speech/workflow.py backend/tests/speech/test_single_narrator.py
```

```text
All checks passed!
```

Broader backend suite:

```powershell
.\.venv\Scripts\python -m pytest backend/tests -q
```

```text
240 passed, 1 warning in 34.79s
```

### Files changed

- `backend/app/modules/audio/qa.py`
- `backend/app/modules/speech/workflow.py`
- `backend/tests/speech/test_single_narrator.py`

### Self-review

- Added a regression where a normal TTS WAV followed by `pause_after_ms=8500` creates an open Major `SILENCE` issue and blocks audio approval.
- Wired workflow pause durations into pre-master QA and added fail-closed pause analysis for pauses over 8 seconds unless an explicit scene-break exemption exists.
- Preserved FFmpeg concat-list/two-pass mastering behavior; this fix does not introduce Python byte concatenation or export/cloud/upload scope.

### Concerns

- No existing scene-break signal is present in the Task 6 schema/workflow, so all pauses over 8 seconds are treated as non-scene-break and blocking.
