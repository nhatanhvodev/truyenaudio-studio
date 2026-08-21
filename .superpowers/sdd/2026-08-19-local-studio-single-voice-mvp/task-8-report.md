# Task 8 Report: Seven-screen UI, fake E2E, real-provider smoke guard

## Status

DONE_WITH_CONCERNS

Implemented the local seven-screen single-voice flow:

- Project + rights screen with evidence upload and rights grants.
- Import screen for paste/TXT-style source snapshot.
- Translation screen using clean offline fake translation and independent approval.
- Voice screen with single fake narrator render.
- Audio screen with independent audio approval.
- Export screen that builds a verified publication bundle.
- Jobs overlay with SSE `EventSource` connection and in-memory fallback display.

Also added CSRF bootstrap/middleware, a RAM-only frontend CSRF client, Playwright E2E, E2E server bootstrap, and a refusal-first real-provider smoke script.

## RED evidence

Command:

```powershell
.\.venv\Scripts\python -m pytest backend\tests\api\test_csrf.py -q
```

Initial output:

```text
FAILED backend\tests\api\test_csrf.py::test_state_change_requires_exact_origin_and_csrf
KeyError: 'csrfToken'
```

Command:

```powershell
cd frontend
npm exec playwright test e2e/single-voice.spec.ts
```

Initial meaningful E2E output after fixing Playwright harness/browser setup:

```text
1) e2e\single-voice.spec.ts:3:1 › fake single narrator reaches verified publication bundle
Error: locator.fill: Test timeout of 30000ms exceeded.
- waiting for getByLabel('Tên truyện')
```

Additional implementation-time failures caught and fixed:

```text
TRANSLATION_QA_BLOCKERS_OPEN
```

Cause: the clean fake translation text was too long for the deterministic length-ratio QA guard.

```text
FileNotFoundError: [WinError 3] The system cannot find the path specified
```

Cause: Playwright E2E was exercising real FFmpeg mastering through the HTTP app. Fixed by making the E2E server opt into `STUDIO_FAKE_AUDIO=1`; full backend tests still cover FFmpeg.

```text
At scripts\smoke-real-providers.ps1:9 char:24
Missing ')' in function parameter list.
```

Cause: Windows PowerShell parsed the non-ASCII default `-Text` poorly. Fixed by making the script ASCII by default while still allowing explicit Qwen Han text via `-Text`.

## GREEN evidence

Command:

```powershell
.\.venv\Scripts\python -m ruff check backend\app\settings\csrf.py backend\app\api\security.py backend\app\api\translation.py backend\app\api\audio.py backend\app\api\exports.py backend\app\providers\fake.py backend\tests\api\test_csrf.py
```

Output:

```text
All checks passed!
```

Command:

```powershell
.\.venv\Scripts\python -m pytest backend\tests\api\test_csrf.py -q
```

Output:

```text
.                                                                        [100%]
1 passed in 0.52s
```

Command:

```powershell
.\.venv\Scripts\python -m pytest backend\tests -q
```

Output:

```text
249 passed, 1 warning in 47.44s
```

Warning:

```text
SAWarning: Cannot correctly sort tables; there are unresolvable cycles between tables
"artifacts, chapters, exports, projects, source_revisions, translation_runs, voice_plans, voice_presets"
```

Command:

```powershell
cd frontend
npm test -- --run
```

Output:

```text
Test Files  3 passed (3)
Tests  7 passed (7)
```

Command:

```powershell
cd frontend
npm run build
```

Output:

```text
✓ 45 modules transformed.
✓ built in 1.30s
```

Command:

```powershell
cd frontend
npm exec playwright test e2e/single-voice.spec.ts
```

Output:

```text
ok 1 e2e\single-voice.spec.ts:3:1 › fake single narrator reaches verified publication bundle (3.2s)
1 passed (11.8s)
```

Command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke-real-providers.ps1 -Provider vieneu
```

Output:

```text
{"provider":"vieneu","model":"vieneu-vi-int8","region":"local","han_count":0,"duration_seconds":30,"paid_network":false}
REFUSED: confirmation was not RUN.
```

## Files changed

- `backend/app/api/audio.py`
- `backend/app/api/exports.py`
- `backend/app/api/security.py`
- `backend/app/api/translation.py`
- `backend/app/main.py`
- `backend/app/providers/fake.py`
- `backend/app/settings/csrf.py`
- `backend/tests/api/test_csrf.py`
- `frontend/e2e/.gitignore`
- `frontend/e2e/fixtures/author-permission.txt`
- `frontend/e2e/single-voice.spec.ts`
- `frontend/playwright.config.ts`
- `frontend/src/App.test.tsx`
- `frontend/src/App.tsx`
- `frontend/src/features/exports/ExportGate.tsx`
- `frontend/src/features/jobs/JobProgress.tsx`
- `frontend/src/features/projects/ProjectWizard.tsx`
- `frontend/src/routes/router.tsx`
- `frontend/src/shared/api.ts`
- `frontend/vite.config.ts`
- `scripts/e2e-server.ps1`
- `scripts/smoke-real-providers.ps1`

## Self-review

- CSRF token is generated once per API process by `secrets.token_urlsafe(32)` and held only in process memory.
- State-changing requests require exact `Origin: http://127.0.0.1:8765` and `X-CSRF-Token`; token comparison uses `hmac.compare_digest`.
- Frontend CSRF token is module memory only. It is not stored in cookie, localStorage, sessionStorage, logs, or DB, and the client re-bootstraps after a 403.
- Multipart rights evidence uses the same CSRF wrapper as JSON state changes.
- `ExportGate` now reads `rightsEvaluationHash`, matching the camelized export API DTO.
- E2E uses an isolated SQLite data root and fake audio provider flag, so it avoids paid network and still writes a verified publication bundle.
- Smoke script refuses by default, requires typed `RUN`, redacts full text/secrets, checks Qwen paid flags/UUIDs, and limits Qwen Han input to 500 characters.

## Concerns

- The browser E2E uses `STUDIO_FAKE_AUDIO=1` because the local environment failed on real FFmpeg execution during HTTP E2E. The full backend suite still exercises FFmpeg contract/mastering tests.
- `frontend/e2e/.tmp-data/` remains ignored test output after E2E runs. Recursive removal was blocked by the runtime policy, so it is intentionally not staged.

---

# Task 8 review fix round 1

## Summary

Addressed the review findings:

- Added `/api/jobs/snapshot` and `/api/jobs/events` with persisted job event mapping `{sequenceId, jobId, status, current, total, errorCode}` and Last-Event-ID/query cursor replay.
- Updated the jobs overlay to fetch one snapshot first and keep a stable `EventSource` instance instead of rebuilding on every message.
- Added `/api/chapters/{chapter_id}/audio/status` and made the audio approval screen load the current/pending master artifact from DB on direct navigation/refresh.
- Replaced smoke script post-RUN “manual ready” status with actual guarded Qwen/local execution paths, controlled FAIL reports, Qwen one-segment guard, and local WAV format/duration/hash validation.

## RED evidence

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/api/test_jobs_api.py backend/tests/api/test_audio_status.py backend/tests/scripts/test_smoke_real_providers.py -q
```

Output after adding focused tests and correcting test fixture FK ordering:

```text
FFFFF                                                                    [100%]
FAILED backend\tests\api\test_jobs_api.py::test_jobs_snapshot_maps_persisted_jobs_and_after_cursor - KeyError: 'events'
FAILED backend\tests\api\test_jobs_api.py::test_jobs_events_replays_after_last_event_id_header - assert 404 == 200
FAILED backend\tests\api\test_audio_status.py::test_audio_status_loads_latest_pending_master_from_database - AssertionError: assert {'detail': 'not found'} == ...
FAILED backend\tests\scripts\test_smoke_real_providers.py::test_qwen_smoke_refuses_multi_segment_before_run - assert 'REFUSED: qwen smoke allows exactly one non-empty segment' in ...
FAILED backend\tests\scripts\test_smoke_real_providers.py::test_local_smoke_after_run_writes_controlled_fail_when_executable_missing - AssertionError: assert 0 == 1
5 failed in 2.17s
```

Command:

```powershell
cd frontend
npm test -- --run src/features/jobs/JobProgress.test.tsx src/App.test.tsx
```

Output:

```text
FAIL  src/features/jobs/JobProgress.test.tsx > JobProgress > loads a snapshot once and keeps a stable EventSource after events
TestingLibraryElementError: Unable to find an element with the text: job-1.
FAIL  src/App.test.tsx > App > loads rendered audio approval state on direct navigation
TestingLibraryElementError: Unable to find an element with the text: /Master abc123abc123/.
Test Files  2 failed (2)
Tests  2 failed | 2 passed (4)
```

## GREEN evidence

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/api/test_jobs_api.py backend/tests/api/test_audio_status.py backend/tests/scripts/test_smoke_real_providers.py -q
```

Output:

```text
......                                                                   [100%]
6 passed in 2.50s
```

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/api/test_csrf.py backend/tests/api/test_jobs_api.py backend/tests/api/test_audio_status.py backend/tests/scripts/test_smoke_real_providers.py -q
```

Output:

```text
.......                                                                  [100%]
7 passed in 2.32s
```

Command:

```powershell
.\.venv\Scripts\python -m ruff check backend/app/api/audio.py backend/app/api/jobs.py backend/app/main.py backend/tests/api/test_audio_status.py backend/tests/api/test_jobs_api.py backend/tests/scripts/test_smoke_real_providers.py
```

Output:

```text
All checks passed!
```

Command:

```powershell
cd frontend
npm test -- --run
```

Output:

```text
Test Files  4 passed (4)
Tests  9 passed (9)
Duration  2.39s
```

Command:

```powershell
cd frontend
npm run build
```

Output:

```text
✓ 45 modules transformed.
✓ built in 950ms
```

Command:

```powershell
cd frontend
npm exec playwright test e2e/single-voice.spec.ts
```

Output:

```text
ok 1 e2e\single-voice.spec.ts:3:1 › fake single narrator reaches verified publication bundle (9.3s)
1 passed (22.5s)
```

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests -q
```

Output:

```text
255 passed, 1 warning in 38.80s
```

Command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke-real-providers.ps1 -Provider refuse
```

Output:

```text
{"provider":"refuse","model":"piper-vais1000","region":"local","han_count":0,"duration_seconds":null,"paid_network":false}
REFUSED: choose -Provider qwen, vieneu, or piper and type RUN when prompted.
```

Command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke-real-providers.ps1 -Provider vieneu
```

Output:

```text
{"provider":"vieneu","model":"vieneu-vi-int8","region":"local","han_count":0,"duration_seconds":30,"paid_network":false}
REFUSED: confirmation was not RUN.
```

## Files changed in review round 1

- `backend/app/api/audio.py`
- `backend/app/api/jobs.py`
- `backend/app/main.py`
- `backend/tests/api/test_audio_status.py`
- `backend/tests/api/test_jobs_api.py`
- `backend/tests/scripts/test_smoke_real_providers.py`
- `frontend/src/App.test.tsx`
- `frontend/src/features/jobs/JobProgress.tsx`
- `frontend/src/features/jobs/JobProgress.test.tsx`
- `frontend/src/routes/router.tsx`
- `scripts/smoke-real-providers.ps1`

## Self-review

- Jobs snapshot/SSE are read-only GET routes and use the same SQLite data root as the rest of the app.
- SSE cursor replay accepts both `Last-Event-ID` and `?after=`, and normalizes `+` decoded from ISO timestamps.
- The UI now fetches audio master status from the backend, so refresh/direct navigation does not depend on `location.state`.
- Smoke script default/no-RUN refusal remains non-mutating. After RUN, missing Qwen endpoint or local binaries produce explicit FAIL reports instead of PASS/ready placeholders.
- Smoke reports store hashes, IDs-present booleans, provider/model/region, and validation metadata; they do not store full text or secrets.

## Concerns

- No real local TTS binary/model or Qwen endpoint/auth was available in this environment; real-provider PASS remains a guarded manual smoke path. Automated coverage verifies refusal, one-segment guard, and controlled FAIL report behavior.
- Two ignored smoke reports were generated by the RED run before the old script honored `-ReportRoot`. The runtime blocked exact deletion commands, so they remain local ignored artifacts under `data/smoke-reports/` and are not staged.
