# Task 19 / U01 report — part 1 (PARTIAL)

Status: PARTIAL — tokens + Button/Input/Select shipped; remaining primitives in later parts.

## Delivered (part 1)

- `frontend/src/shared/ui/tokens.ts`: semantic color tokens for `light`/`dark` (surface, text, primary, danger, focus ring, success) chosen to meet WCAG (body text ≥4.5:1, UI/focus ≥3:1), spacing/radius/font scales.
- `frontend/src/shared/ui/Button.tsx`: variants primary/secondary/danger/ghost, theme light/dark, `loading` (disabled + `aria-busy`, label preserved), keyboard focus ring, `type="button"` default so it never submits accidentally.
- `frontend/src/shared/ui/Input.tsx`: label + `htmlFor` via `useId`, `aria-invalid`, `aria-describedby` wiring to error (`role="alert"`) and hint, required marker, focus ring.
- `frontend/src/shared/ui/Select.tsx`: labelled select with options and the same error/aria contract.
- Barrel `index.ts` export + `ui.test.tsx` (5 RTL tests).

## Remaining for U01 acceptance (later parts)

- Combobox/Modal/Drawer/Tooltip/Toast/Tabs/Table/Tree/Progress primitives; global stylesheet wiring; full component interaction + contrast + keyboard/focus-return + light/dark audit at U01 close (visual check per acceptance).

## Validation (part 1)

- `npx vitest run src/shared/ui` — 5 passed.
- Full frontend suite: `npm test -- --run` — 10 files / 27 tests passed.
- Production build `npm run build` — tsc + Vite PASS (dist emitted).
- No backend or provider/cloud change in this part.
