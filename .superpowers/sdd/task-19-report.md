# Task 19 / U01 report — parts 1–5

Status: DONE (code complete) — visual/zoom/screen-reader audit NOT_RUN (no browser harness this session).

## Delivered (parts 1–5)

- `frontend/src/shared/ui/tokens.ts`: light/dark semantic tokens chosen for WCAG (body text ≥4.5:1, UI/focus ≥3:1); dark primary `#2563eb`/danger `#dc2626` keep white text ≥4.5; spacing/radius/font scales.
- Primitives (each with keyboard/focus/ARIA contract + RTL tests):
  - `Button` (4 variants, loading `aria-busy`, focus ring, type=button default)
  - `Input` / `Select` (label+useId, `aria-invalid`/`aria-describedby`, error `role=alert`, required)
  - `Combobox` (ARIA 1.2: filter, activedescendant, Arrow/Home/End/Enter/Escape, mouse)
  - `Modal` / `Drawer` (aria-modal labelled dialog, Escape/overlay/close, focus in + focus return)
  - `Tabs` (tablist/tab/tabpanel, roving tabindex, arrow keys), `Tooltip` (clone trigger + aria-describedby, role=tooltip)
  - `Toast` (role=status aria-live=polite), `Progress` (progressbar + valuenow), `Table` (caption/scope headers), `Tree` (tree/treeitem/group, expand/collapse + selection)
- Automated WCAG contrast test over tokens for light & dark (`tokens-contrast.test.ts`).
- Barrel `index.ts` exports; shared/ui RTL tests total 24 (parts 1–5 + contrast).

## NOT_RUN / residual (environment)

- Light/dark **visual** pass, 200% zoom reflow and real screen-reader/focus-return audit require a browser/visual harness and were not run this session (NOT_RUN per plan G-UX).
- Global stylesheet wiring of tokens is deferred to the app-shell task (U02) where the theme hook lives.
- These must be recorded before calling U01 fully "verified"; the component/code contract paths are covered by the tests above.

## Validation

- `npx vitest run src/shared/ui` — 24 passed.
- Full frontend suite: `npm test -- --run` — 46 tests passed across 15 files; production build `npm run build` PASS.
- No backend or provider/cloud change in these parts.
