# Task 28 / U02 report — part 3 (PARTIAL)

Status: PARTIAL — nested project settings routes now take the project from the URL; global settings groups and per-group content for TTS/Storage/Appearance remain.

## Delivered (part 3)

- `features/settings/ProjectSettingsRoutes.tsx`:
  - route tree `projects/:projectId/settings` with a dedicated shell (`ProjectSettingsShell`) whose nav (`aria-label="Nhóm cài đặt dự án"`) links every group under the project path and marks the active one via `NavLink`, shows the project id, and links back to the project import screen;
  - `translation` renders the four project-scoped managers (Style/Glossary/Character/Memory) with the project **from the URL** — the manual Project-ID gate is no longer needed on this path;
  - `tts`/`storage`/`appearance` render explicit pending panels naming the follow-up task; `advanced` reuses `Diagnostics`; the index redirects to `translation`.
- Wired into the app router as `...projectSettingsRoutes` (additive; nothing removed).
- Tests (3): group links + active marking + project id display, outlet isolation + back link, declared route list (`(index)`, translation, tts, storage, appearance, advanced).

## Remaining for U02 acceptance (later parts)

- Split the global IA into explicit Thư viện / Công việc / Cài đặt sections with nested project routes for library lists (U03) and per-group content for TTS/Storage/Appearance (A02, U10); loading/empty/error states for every group; keyboard navigation route tests.

## Validation (part 3)

- `npx vitest run src/features/settings` — 11 passed (4 route + 3 project-route + 4 style manager).
- Full frontend suite: `npm test -- --run` — 94 passed / 25 files; production build `npm run build` PASS.
- No backend/API change in this part.
