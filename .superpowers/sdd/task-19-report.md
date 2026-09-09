# Task 19 / U01 report — parts 1–2 (PARTIAL)

Status: PARTIAL — tokens + Button/Input/Select/Modal/Tabs shipped; remaining primitives in later parts.

## Delivered (part 1)

- `frontend/src/shared/ui/tokens.ts`: semantic color tokens for `light`/`dark` (surface, text, primary, danger, focus ring, success) chosen to meet WCAG (body text ≥4.5:1, UI/focus ≥3:1), spacing/radius/font scales.
- `frontend/src/shared/ui/Button.tsx`: variants primary/secondary/danger/ghost, theme light/dark, `loading` (disabled + `aria-busy`, label preserved), keyboard focus ring, `type="button"` default so it never submits accidentally.
- `frontend/src/shared/ui/Input.tsx`: label + `htmlFor` via `useId`, `aria-invalid`, `aria-describedby` wiring to error (`role="alert"`) and hint, required marker, focus ring.
- `frontend/src/shared/ui/Select.tsx`: labelled select with options and the same error/aria contract.
- Barrel `index.ts` export + `ui.test.tsx` (5 RTL tests).

## Delivered (part 2)

- `Modal.tsx`: `role="dialog"` + `aria-modal` + labelled title, Escape-to-close, overlay click close, close button, focus into panel, focus returns to the previously focused element on close.
- `Tabs.tsx`: `tablist/tab/tabpanel` semantics, `aria-selected`/`aria-controls`/`aria-labelledby`, roving `tabIndex`, ArrowLeft/Right/Home/End keyboard navigation.
- Barrel exports + `modal-tabs.test.tsx` (5 RTL tests: dialog open/close/Escape, tabs selection/keyboard/panel switch).

## Remaining for U01 acceptance (later parts)

- Combobox/Drawer/Tooltip/Toast/Table/Tree/Progress primitives; global stylesheet wiring; full component interaction + contrast + keyboard/focus-return + light/dark audit at U01 close (visual check per acceptance).

## Validation

- `npx vitest run src/shared/ui` — 10 passed (5 + 5).
- Full frontend suite: `npm test -- --run` and production build `npm run build` recorded before commit.
- No backend or provider/cloud change in these parts.
