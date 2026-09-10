# Task 27 / U09 report — parts 1–4 (PARTIAL)

Status: PARTIAL — style, glossary, character and memory managers shipped in Settings → Translation; stale-scope preview + nested project routes remain.

## Delivered (part 1 — style)

- `features/settings/StyleManager.tsx` (C01 API): loads active styles (`GET /api/projects/{id}/styles`) and curated presets (`/styles/presets`); applying a preset fills name/genre/tone/custom instruction (copy states presets are versioned configuration, not a quality badge); saving posts name/genre/tone/languages/instruction and reports the new **revision + truncated content hash**, then reloads the list; table shows revision/hash; loading/empty/error states and a missing-name guard.
- `features/settings/ProjectScopedPanel.tsx`: explicit project-ID gate for project-scoped managers (component state only — nothing persisted in the browser) until nested project routes (U02 later part) supply the project from the URL.

## Delivered (part 2 — glossary)

- `features/glossary/GlossaryManager.tsx` (C02 API): active table with locked flag, chapter scope and forbidden forms plus revision hash; entry form for reading/category/locked/description/forbidden forms/scope range/evidence; local guards mirroring backend invariants (`GLOSSARY_*_REQUIRED`, `GLOSSARY_SCOPE_INVALID`, `GLOSSARY_CONFLICT_INTERNAL`) that never call the API when invalid; post-save report of the new revision hash plus affected-segment and stale-run counts; loading/empty/error states. Helpers exported: `parseForbiddenForms`, `validateDraft`, `buildEntryPayload`.

## Delivered (part 3 — characters & addressing)

- `features/characters/CharacterManager.tsx` (C04 API): characters per revision with aliases, gender "chưa rõ" when null (never guessed) and candidate/approved status; candidate creation; **approve requires evidence** (refuses locally with `CHARACTER_EVIDENCE_REQUIRED`, no API call without a source revision + segment ids); directed relationships (from/to ids, ordinal range with open upper bound, `key=value` addressing) and a chapter-ordinal listing; error alerts. Helpers exported: `parseList`, `parseAddressing`.

## Delivered (part 4 — story memory)

- `features/memory/MemoryManager.tsx` (C05 API): candidate list with entity/summary/validity range; approve gated on evidence (run id + ≥1 segment id, otherwise `MEMORY_EVIDENCE_SEGMENTS_REQUIRED` with no API call) and reject; candidate creation with ordinal validity range; approved-context viewer showing only APPROVED entries for a chapter plus the backend revision hash (candidates never enter context; no future chapters); error alerts including `MEMORY_EVIDENCE_RUN_NOT_APPROVED`.
- Settings → Translation renders Style + Glossary + Character + Memory managers for the selected project.

## Remaining for U09 acceptance (later parts)

- Stale-scope preview before applying changes; nested project routes so the project comes from the URL instead of the manual ID gate.

## Validation (parts 1–4)

- `npx vitest run src/features/settings/StyleManager.test.tsx src/features/glossary src/features/characters src/features/memory` — 21 passed.
- Full frontend suite: `npm test -- --run` — 91 passed / 24 files; production build `npm run build` PASS.
- No backend/API change in these parts.
