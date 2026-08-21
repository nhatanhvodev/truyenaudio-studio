# Final Fix Report: Single-Voice Publication Integrity Gaps

## Status

DONE_WITH_CONCERNS

## Findings addressed

- Closed production fake-audio fallback: `SpeechWorkflow` no longer defaults to `FakeTts`; `/audio/render` only uses fake TTS/MP3 when `STUDIO_FAKE_AUDIO=1`, and normal renders fail closed without a verified local adapter/preset license.
- Added master provenance gates: render requires the active voice plan to match the current approved translation run; audio approval and export reject stale masters from prior translation/voice-plan state.
- Invalidation now clears downstream voice/audio/export pointers when a translation revision supersedes an approved run or a new approved run replaces a prior approved run.
- Added guarded Qwen translation command route requiring cloud consent and budget authorization IDs, passing both through workflow `OperationContext`; default workflow no longer falls back to `FakeTranslator`.
- Wired private archive export UI to `POST /api/chapters/{id}/exports/private` and displays private archive checksum/result separately from publication bundles.
- Hardened Qwen smoke so it validates local DB project/provider profile/consent/policy artifact/budget authorization/rate cards and restricts the endpoint to the configured provider profile before HTTP.

## RED evidence

- Speech regressions failed before implementation: implicit fake render reached mastering instead of `LOCAL_TTS_ADAPTER_REQUIRED`; `allow_fake_tts` did not exist; stale voice-plan/master provenance was accepted.
- Translation regressions failed before implementation: `/translation/qwen` returned 404; `enqueue_translation(... cloud_consent_id, budget_authorization_id ...)` was unsupported; downstream pointers survived revision.
- Export/smoke/UI regressions failed before implementation: stale translation audio exported; Qwen smoke reached endpoint validation without DB context; private archive button did not call the API.

## GREEN evidence

- `.\.venv\Scripts\python -m pytest backend/tests/speech/test_single_narrator.py::test_default_render_refuses_fake_tts_without_explicit_injected_adapter backend/tests/speech/test_single_narrator.py::test_render_rejects_unverified_fake_voice_preset_even_with_fake_e2e_adapter backend/tests/speech/test_single_narrator.py::test_fake_e2e_adapter_still_renders_when_explicitly_allowed backend/tests/speech/test_single_narrator.py::test_render_requires_voice_plan_for_current_approved_translation backend/tests/speech/test_single_narrator.py::test_audio_approval_rejects_master_from_previous_translation_run -q` -> `5 passed`
- `.\.venv\Scripts\python -m pytest backend/tests/translation/test_workflow.py::test_revision_invalidates_voice_audio_and_export_pointers backend/tests/translation/test_workflow.py::test_translation_api_qwen_route_requires_consent_and_authorization backend/tests/translation/test_workflow.py::test_translation_workflow_passes_cloud_context_to_translator -q` -> `3 passed`
- `.\.venv\Scripts\python -m pytest backend/tests/exports/test_bundle_v1.py::test_publication_export_rejects_audio_from_previous_translation_run backend/tests/scripts/test_smoke_real_providers.py::test_qwen_smoke_after_run_requires_local_db_context_before_endpoint -q` -> `2 passed`
- `.\.venv\Scripts\python -m pytest backend/tests/api/test_audio_status.py::test_audio_render_route_refuses_implicit_fake_tts -q` -> `1 passed`
- `npm test -- --run src/App.test.tsx` -> `4 passed`

## Final verification

- `.\.venv\Scripts\python -m pytest backend/tests -q` -> `265 passed, 1 warning in 55.33s`
- `npm test -- --run` -> `10 passed`
- `npm run build` -> success, Vite built `dist/assets/index-BNQduHJ4.js`
- `npm exec playwright test e2e/single-voice.spec.ts` -> `1 passed`
- `.\.venv\Scripts\python -m ruff check ...` -> `All checks passed!`
- `.\.venv\Scripts\python -m ruff format --check ...` -> `11 files already formatted`

## Manual smoke

- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke-real-providers.ps1 -Provider vieneu` -> refused before provider execution because no `RUN` confirmation was supplied. No local TTS command or paid network call was made.

## Concerns

- Full backend still reports the pre-existing SQLAlchemy unresolved FK-cycle warning in `tests/db/test_schema.py`.
- Real VieNeu/Piper execution was not run because executable/model paths and explicit `RUN` confirmation were not provided.
