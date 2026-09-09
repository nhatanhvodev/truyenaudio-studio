# Task 5 / S02 report

Status: DONE

## Delivered

- Gemini and Qwen inference routes now require an explicit `profileId`, cloud-consent ID, and budget-authorization ID. Their Pydantic models reject raw credential fields. Route composition resolves exactly that enabled translator profile, checks its adapter kind and model preference, and obtains the credential only from its existing keyring reference.
- Gemini no longer selects fallback models automatically. Provider and transport failures map to a short stable public-code allowlist; no provider response body, URL, or credential value is returned.
- The production API middleware accepts only the exact `Host: 127.0.0.1:8765`. State-changing API requests additionally require exact loopback Origin and CSRF token. An API request must fully match a registered API route; encoded, traversal, secret-query, unknown, localhost, IPv6, and other-port variants are rejected before the SPA fallback.
- The production validation handler returns `INVALID_REQUEST`, preventing Pydantic invalid-input echoes from returning a submitted credential.
- Diagnostics recursively redact structured secret fields and secret-bearing query parameters.
- The translation screen deletes the legacy `gemini_api_key` entry without reading it. Credentials exist only in transient component state, are sent solely to `PUT /api/cloud-profiles/{profileId}/credential`, and the input is cleared after the request. Inference sends profile and guard/model identifiers only. Non-secret model preference remains localStorage-backed.

## TDD and validation

1. RED: `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend\tests\security\test_api_boundary.py -q`
   - Initially 3 failures: non-canonical Host reached handlers, state changes accepted a wrong Host, and Gemini did not require `profileId`.
2. Focused backend: `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend\tests\security\test_api_boundary.py backend\tests\security\test_credentials.py backend\tests\translation\test_workflow.py backend\tests\providers\test_gemini_mt_security.py backend\tests\providers\test_qwen_mt_contract.py backend\tests\diagnostics\test_redaction.py backend\tests\api\test_csrf.py -q`
   - 57 passed in 19.35s.
3. Frontend: `npm test -- --run`
   - 9 files, 20 tests passed. `App.test.tsx` captures provisioning and inference bodies and asserts legacy storage removal plus transient-field clearing.
4. Build: `npm run build`
   - Passed.
5. Ruff and whitespace review: `D:\truyenaudio-studio\.venv\Scripts\python.exe -m ruff check ...`; `git diff --check`
   - Passed.

## Concerns

- Paid-cloud smoke was deliberately not run. Its evidence state remains `NOT_RUN`.
- This task does not create quote/budget/consent authorization or catalog snapshots; S03 and later tasks own those controls. S02 only requires callers to submit the explicit IDs and never synthesizes them.
- Review remediation clears the transient credential after both successful and failed provisioning, rejects generic key/credential/token query parameters, and redacts Gemini `AIza...` credentials in diagnostic text.
- Remediation validation: backend focused 62 passed, frontend 21 passed, frontend build passed, and Ruff passed.

## S02 round 2 remediation

- App bootstrap now removes the legacy `gemini_api_key` without reading it, so cleanup runs on `/` and every other initial route. The frontend test preloads that key at `/` and confirms removal.
- Gemini and Qwen inference request bodies accept profile and guard IDs only. A profile supplies the model; both routes reject an old raw `model` field with the fixed sanitized `INVALID_REQUEST` response. The Gemini model selector and local storage seam were removed from the frontend.
- Qwen profile config accepts an `endpoint` only when it exactly equals `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation`. Endpoint aliases (`baseUrl`, `base_url`, `url`, and `apiBase`) fail with `QWEN_ENDPOINT_INVALID`; evidence keys such as `provider`, `policy_sha256`, and `quota` remain available. The adapter applies the same exact check, and dispatch revalidates legacy stored config before resolving a credential or creating a network-capable adapter.
- API tests cover rejected create/PATCH payloads without echoing an attacker URL or bearer secret, and adapter/legacy-dispatch tests prove no attacker endpoint receives a request.

Validation for round 2: focused backend 65 passed, full frontend Vitest 22 passed, frontend build passed, Ruff and `git diff --check` passed. Paid-cloud smoke remains `NOT_RUN`.

## S02 round 3 remediation

- Cloud-profile config now normalizes every field name before classifying it as sensitive. It rejects and omits nested `Authorization`, `credential`, `Bearer`, `x-goog-api-key`, and `google_api_key` fields, including legacy stored values returned through the profile API.
- The API query guard recognizes normalized provider/header forms such as `x-goog-api-key`, `google_api_key`, `api_key`, and `authorization-header`. Its token suffix and sensitive-name checks leave ordinary identifiers such as `cursor` and `id` reachable.
- TestClient regressions prove rejected create/PATCH config responses use the fixed 422 error without echoing submitted secrets, and prove legacy profile responses omit stored secret values.

Validation for round 3: RED exposed one incorrect readiness expectation in the new non-secret-query test (the no-worker fixture returns 503); after correcting the test to assert that it reaches the endpoint, the focused security set passed 49 tests and the expanded S02 backend set passed 77 tests. Ruff and `git diff --check` passed. Frontend was untouched, so frontend tests were not rerun. Paid-cloud smoke remains `NOT_RUN`.

## S02 round 4 remediation

- Diagnostics and profile configuration now share one normalized secret-key classifier. It redacts `credential`, `bearer`, and punctuation/case variants in both JSONL and stream logs, so the live log no longer bypasses the redaction already applied to the file payload.
- Provider profile model identifiers are constrained to 1–255 ASCII path-safe characters: alphanumeric segment starts with dot, underscore, or hyphen allowed after it; slash-separated vendor namespaces are allowed. Queries, fragments, colon schemes, backslashes, percent escapes, whitespace, and traversal-shaped segments are rejected with the fixed `MODEL_IDENTIFIER_INVALID` response.
- Gemini validates the model before resolving a keyring credential. The Gemini workflow also validates legacy DB rows before adapter construction, so an unsafe persisted model never reaches URL construction, credential resolution, or a network-capable client.
- Test coverage includes POST/PATCH non-echo rejection, accepted Gemini/Qwen/OpenRouter-style identifiers, direct Gemini constructor rejection before credential lookup, legacy Gemini profile rejection before dispatch, and structured JSONL/stream redaction variants.

Validation for round 4: RED first produced one diagnostics log failure and 12 model-boundary failures. After the implementation, `pytest tests/diagnostics/test_redaction.py tests/security/test_credentials.py tests/providers/test_gemini_mt_security.py -q` passed 58 tests; targeted Ruff and `git diff --check` passed. Frontend was unchanged and not rerun. Paid-cloud smoke remains `NOT_RUN`.

## S02 round 5 remediation

- API boundary detection now checks both the decoded request path and raw ASGI path, repeatedly percent-decodes with a fixed bound, normalizes backslashes to slashes, and case-folds only for boundary targeting. API-shaped paths such as `/api\health/ready`, encoded/double-encoded backslashes, encoded `api`, and `/API/...` are intercepted by middleware and rejected by the canonical API guard instead of reaching the SPA fallback.
- Secret query detection now rejects parsed query keys that still contain `%`, closing nested percent-encoding bypasses such as `api%254Bey`, `x%252Dgoog%252Dapi%252Dkey`, and `authoriz%2561tion` while leaving non-secret identifiers such as `cursor` and `id` routable.
- Static UI regression coverage proves valid frontend routes still receive the SPA `index.html`, while API-shaped malformed routes receive `404`.

Validation for round 5: RED first produced 4 static fallback failures for API-shaped paths. After the implementation, `pytest backend\tests\api\test_static_ui.py backend\tests\security\test_api_boundary.py -q` passed 25 tests, the expanded S02 backend set passed 105 tests, targeted Ruff passed, and `git diff --check` passed. Paid-cloud smoke remains `NOT_RUN`.
