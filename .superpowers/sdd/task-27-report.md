# Task 27 / U09 report — parts 1–2 (PARTIAL)

Status: PARTIAL — style + glossary managers shipped in Settings → Translation; character/alias/relationship and memory-candidate managers remain.

## Delivered (part 1 — style)

- `features/settings/StyleManager.tsx` (C01 API): loads active styles (`GET /api/projects/{id}/styles`) and curated presets (`/styles/presets`); applying a preset fills name/genre/tone/custom instruction (copy states presets are versioned configuration, not a quality badge); saving posts name/genre/tone/languages/instruction and reports the new **revision + truncated content hash**, then reloads the active list; table shows name/genre/tone/revision/short hash; loading/empty/error states and a missing-name guard.
- `features/settings/ProjectScopedPanel.tsx`: explicit project-ID gate for project-scoped managers (component state only — nothing persisted in the browser) until nested project routes (U02 next part) supply the project from the URL.
- Tests (4): presets + active styles with revision/hash, preset fill + POST payload/status, missing-name guard + API error alert, load failure alert.

## Delivered (part 2 — glossary)

- `features/glossary/GlossaryManager.tsx` (C02 API): active table with source→target, locked flag, **chapter scope** (`chương 1–3` / `toàn project`), forbidden forms and the revision hash; entry form for reading/category/locked/description/**forbidden forms**/**scope range**/evidence; local guards mirroring backend invariants (`GLOSSARY_*_REQUIRED`, `GLOSSARY_SCOPE_INVALID`, `GLOSSARY_CONFLICT_INTERNAL`) that **never call the API when invalid**; after saving it reports the new revision hash plus the backend's **affected segment count** and **stale count** (`affected_source_segment_ids` / `invalidated`) so the invalidation scope is visible; loading/empty states and `role="alert"` errors.
- Pure helpers exported for tests: `parseForbiddenForms`, `validateDraft`, `buildEntryPayload`.
- Settings → Translation renders Style + Glossary managers for the selected project.
- Tests (6): helper normalization/conflict, payload with scope+forbidden, table rendering, local validation without API call, POST payload + affected/stale status, backend conflict alert.

## Remaining for U09 acceptance (later parts)

- Character/alias/relationship manager (evidence required to approve, directed addressing), candidate-summary review with evidence + approve/reject, stale-scope preview before applying, and nested project routes so the project comes from the URL instead of the manual ID gate.

## Validation (parts 1–2)

- `npx vitest run src/features/settings/StyleManager.test.tsx src/features/glossary` — 10 passed.
- Full frontend suite: `npm test -- --run` — 80 passed / 22 files; production build `npm run build` PASS.
- No backend/API change in these parts.
