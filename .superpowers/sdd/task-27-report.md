# Task 27 / U09 report — part 1 (PARTIAL)

Status: PARTIAL — style manager (preset + revision/hash) shipped and reachable from Settings → Translation; glossary/character/memory managers remain.

## Delivered (part 1)

- `features/settings/StyleManager.tsx` (uses the C01 backend API):
  - loads active styles (`GET /api/projects/{id}/styles`) and curated presets (`GET /api/projects/{id}/styles/presets`);
  - applying a preset fills name/genre/tone/custom instruction (presets are versioned configuration, not a quality badge — stated in the panel copy);
  - saving posts name/genre/tone/languages/custom instruction and reports the new **revision + truncated content hash**, then reloads the active list; the active-style table shows name/genre/tone/revision/short hash;
  - loading/empty/error states (`role="alert"`), client-side guard for a missing name, saving state on the button.
- `features/settings/ProjectScopedPanel.tsx`: explicit project-ID gate for project-scoped managers (value kept in component state only — nothing persisted in the browser), used to host the style manager inside global Settings until nested project routes (U02 next part) provide the project from the URL.
- Settings → Translation now renders the project-scoped style manager instead of a pending panel.
- Tests (4): presets + active styles with revision/hash rendering, preset fill + POST payload/status, missing-name guard + API error alert, load failure alert.

## Remaining for U09 acceptance (later parts)

- Glossary manager (import/export/scope/locked/conflict), character/alias/relationship manager, candidate-summary review with evidence/approve; preview stale scope before apply; mount inside nested project routes so the project comes from the route instead of a manual ID.

## Validation (part 1)

- `npx vitest run src/features/settings/StyleManager.test.tsx` — 4 passed.
- Full frontend suite: `npm test -- --run` — 74 passed / 21 files; production build `npm run build` PASS.
- No backend/API change in this part.
