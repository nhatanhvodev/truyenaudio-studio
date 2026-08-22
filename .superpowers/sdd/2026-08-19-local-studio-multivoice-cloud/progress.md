# 2026-08-19 local studio multivoice cloud

Plan: `D:\tieuthuyetaudio\docs\superpowers\plans\2026-08-19-local-studio-multivoice-cloud.md`

- Baseline: backend `356 passed, 1 warning`; frontend Vitest `16 passed`; build pass.
- Task 1: complete. Commit `1d8c25c` (`feat: add assisted multi-voice role plans`). Verification: `backend/tests/voices backend/tests/speech/test_dialogue_assignments.py` => `4 passed`.
- Task 2: complete. Commit `bd56777` (`feat: render selectively by voice role`). Verification: backend multivoice/invalidation `11 passed`; RoleAssignment Vitest `2 passed`.
- Task 3: complete. Commit `d56c3ec` (`feat: guard cloud processing with consent and quota`). Verification: cloud guard/translation guard `15 passed`.
- Task 4: complete. Commit `c11bb2f` (`feat: add guarded Google TTS adapter`). Verification: Google contract `2 passed`.
- Task 5: complete. Commit `72dc15f` (`feat: add guarded Gemini TTS adapter`). Verification: Gemini contract `2 passed`.
- Task 6: complete. Commit `43377e9` (`feat: add guarded ElevenLabs premium TTS`). Verification: ElevenLabs contract `2 passed`.
- Task 7: complete. Commit `de371c0` (`feat: add optional advisory ASR checks`). Verification: ASR backend `2 passed`; AudioReview Vitest `1 passed`.
- Task 8: complete. Commit `3becbc1` (`feat: complete guarded multi-voice cloud workflow`). Verification: frontend Vitest `19 passed`; build pass; Playwright multivoice-cloud `1 passed`.
- Final verification fix: commit `f384466` (`fix: stabilize multivoice final verification`). Verification: focused speech/API `16 passed`; full backend `376 passed, 1 warning`; frontend Vitest `19 passed`; build pass; Playwright serial 3 specs `3 passed`.
- Review follow-up fixes: pending commit. Changes address review findings:
  - Require at least one extra role for `ASSISTED_MULTI_VOICE`, while keeping narrator default for unresolved segments.
  - Require explicit budget authorization for TTS cloud calls and carry `cloudConsentId`/`budgetAuthorizationId` through the render API/workflow.
  - Preserve `/audio/render` compatibility for POST requests with no JSON body.
  - Commit actual Gemini/ElevenLabs usage to the budget ledger after provider response measurement.
  - Replace duplicated API camel-case serializers with shared `backend/app/api/payload.py`.
  - Move the multi-voice cloud demo route body into `MultiVoiceCloudDemo`.
  - Change `scripts/smoke-cloud-tts.ps1` from fake PASS wording to `NOT_RUN` gated preflight until real provider invocation is wired.
  Verification: targeted backend regression `33 passed`; full backend `377 passed, 1 warning`; frontend Vitest `19 passed`; build pass; Playwright serial 3 specs `3 passed`.

Notes:
- No QQ/Wenku crawler, credential automation, bypass, or auto-upload was added.
- No paid provider smoke was run. `scripts/smoke-cloud-tts.ps1` is hard-gated and writes a redacted `NOT_RUN` preflight report until real provider invocation/profile wiring is configured.
- Playwright is configured with `workers: 1` because E2E uses one local API/SQLite data root.
