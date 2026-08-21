# Extra Qwen UI Fix Report

## Scope

- Wired the product Translation screen to expose a guarded Qwen translation path next to the explicit fake/test path.
- Kept fake translation on `POST /api/chapters/:chapterId/translation/fake` for offline E2E.
- Qwen now requires `cloudConsentId` and `budgetAuthorizationId` in the UI and blocks before HTTP when either value is missing.
- No backend files changed; existing backend contract already accepts the guarded Qwen DTO.

## RED Evidence

Command:

```powershell
npx vitest run src/App.test.tsx
```

Expected failure before implementation:

```text
FAIL src/App.test.tsx > App > requires guard IDs before calling the Qwen translation route
TestingLibraryElementError: Unable to find role="button" and name "Dich bang Qwen"
```

This proved the product UI exposed only the fake action and could not call the guarded Qwen route.

## GREEN Evidence

Focused frontend:

```powershell
npx vitest run src/App.test.tsx
```

Result:

```text
Test Files  1 passed (1)
Tests  5 passed (5)
```

Full frontend:

```powershell
npx vitest run
```

Result:

```text
Test Files  4 passed (4)
Tests  11 passed (11)
```

Build:

```powershell
npm run build
```

Result:

```text
tsc -b && vite build
45 modules transformed
built in 1.57s
```

Fake-path E2E:

```powershell
npx playwright test e2e/single-voice.spec.ts
```

Result:

```text
1 passed
```

## Files Changed

- `frontend/src/routes/router.tsx`
- `frontend/src/App.test.tsx`
- `.superpowers/sdd/2026-08-19-local-studio-single-voice-mvp/extra-qwen-ui-fix-report.md`

## Self-Review

- Qwen button posts to `/api/chapters/:chapterId/translation/qwen`, not `/translation/fake`.
- Request body uses backend aliases: `cloudConsentId` and `budgetAuthorizationId`.
- Missing IDs set `CLOUD_CONSENT_AND_BUDGET_REQUIRED` and return before CSRF/bootstrap or Qwen HTTP.
- Fake action remains visible as a separate fake/test section and continues to power E2E.
- Backend and Ruff were not run because no Python was changed.

## Concerns

- The UI assumes the operator already created valid consent and budget authorization records elsewhere; this fix only requires and forwards their IDs.
