# Task 25 / U02 report — parts 1–2 (PARTIAL)

Status: PARTIAL — Settings route tree (7 groups) + global area nav shipped; nested project routes, per-group loading/empty/error states and real group content continue in later parts.

## Delivered (part 2)

- `features/workspace/GlobalNav.tsx`: global area navigation (Thư viện `/`, Công việc `/jobs`, Cài đặt `/settings`) with `aria-label="Khu vực"` and `NavLink` `aria-current` (Thư viện uses `end` so it is not active on every route); hints via `title`.
- Wired into the existing `Shell` ahead of the legacy workflow nav (kept intact so current flows/tests are unaffected); no route removed or renamed.
- Tests (3): the three areas exist, active area marked (`/settings/providers` -> Cài đặt), library not marked active elsewhere.

## Delivered (part 1)

- `features/settings/SettingsLayout.tsx`: `SETTINGS_GROUPS` (AI Providers, Models, Translation, TTS, Storage, Appearance, Advanced per contract C07) + a labelled nav (`aria-label="Nhóm cài đặt"`) using `NavLink` (automatic `aria-current="page"`), breadcrumb (`Cài đặt / <group>`), and the routed `<Outlet/>`.
- `features/settings/SettingsRoutes.tsx`: additive route tree `/settings` with index redirect to `providers` and one child per group. Existing components cannot be mounted without their required props (`ProviderSettings` needs profile/model/region/policy props, `CleanupPreview` needs a plan + onExecute), so part 1 renders an explicit `SettingsGroupPending` panel for Providers/Models/Translation/TTS/Storage/Appearance naming the task that will fill it (U08/U09/A02/U10), and wires Advanced → `Diagnostics` (no props).
- Wired into the existing router as `...settingsGroupRoutes` so current routes/tests keep working (no route removed).
- Tests (4): seven nav links, active group + breadcrumb + outlet isolation, and the declared route list (`(index)`, providers, models, translation, tts, storage, appearance, advanced).

## Remaining for U02 acceptance (later parts)

- Global IA split (Thư viện / Công việc / Cài đặt) with nested project routes, deep-link/back preserving project + filter, loading/empty/error states per group, and real group content from U08–U10/A02; keyboard navigation route tests.

## Validation (parts 1–2)

- `npx vitest run src/features/settings src/features/workspace` — 7 passed.
- Full frontend suite: `npm test -- --run` — 53 passed / 17 files; production build `npm run build` PASS (tsc + Vite).
- No backend/API change in these parts.
