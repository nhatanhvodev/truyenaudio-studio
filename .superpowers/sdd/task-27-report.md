# Task 27 / U09 report — parts 1–3 (PARTIAL)

Status: PARTIAL — style, glossary and character/relationship managers shipped in Settings → Translation; memory-candidate review remains.

## Delivered (part 1 — style)

- `features/settings/StyleManager.tsx` (C01 API): loads active styles (`GET /api/projects/{id}/styles`) and curated presets (`/styles/presets`); applying a preset fills name/genre/tone/custom instruction (copy states presets are versioned configuration, not a quality badge); saving posts name/genre/tone/languages/instruction and reports the new **revision + truncated content hash**, then reloads the list; table shows revision/hash; loading/empty/error states, missing-name guard.
- `features/settings/ProjectScopedPanel.tsx`: explicit project-ID gate for project-scoped managers (component state only — nothing persisted in the browser) until nested project routes (U02 later part) supply the project from the URL.

## Delivered (part 2 — glossary)

- `features/glossary/GlossaryManager.tsx` (C02 API): active table with locked flag, **chapter scope** and forbidden forms plus revision hash; entry form for reading/category/locked/description/forbidden forms/scope range/evidence; local guards mirroring backend invariants (`GLOSSARY_*_REQUIRED`, `GLOSSARY_SCOPE_INVALID`, `GLOSSARY_CONFLICT_INTERNAL`) that **never call the API when invalid**; post-save report of the new revision hash plus **affected segment** and **stale run** counts; loading/empty/error states.
- Helpers exported for tests: `parseForbiddenForms`, `validateDraft`, `buildEntryPayload`.

## Delivered (part 3 — characters & addressing)

- `features/characters/CharacterManager.tsx` (C04 API): table of characters per revision with aliases, **gender "chưa rõ" when null (never guessed)** and candidate/approved status; candidate creation (aliases/role/optional gender); **approve requires evidence** — refuses locally (`CHARACTER_EVIDENCE_REQUIRED`) with no API call until a source revision id and ≥1 segment id are provided; directed relationships (from/to ids, ordinal range with open upper bound, `key=value` addressing lines) and a chapter-ordinal query listing active relationships with addressing; loading/empty/error states.
- Helpers exported for tests: `parseList`, `parseAddressing`.

Settings → Translation renders Style + Glossary + Character managers for the selected project.

## Remaining for U09 acceptance (later parts)

- Memory candidate review (list candidates, approve with run+segment evidence, reject) and stale-scope preview before applying; nested project routes so the project comes from the URL instead of the manual ID gate.

## Validation (parts 1–3)

- `npx vitest run src/features/settings/StyleManager.test.tsx src/features/glossary src/features/characters` — 16 passed.
- Full frontend suite: `npm test -- --run` — 86 passed / 23 files; production build `npm run build` PASS.
- No backend/API change in these parts.
