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
