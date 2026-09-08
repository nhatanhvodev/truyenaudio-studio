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
