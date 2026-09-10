# Task 26 / U08 report — part 1 (PARTIAL)

Status: PARTIAL — model catalog UI (filters/provenance/stale/unknown-price) shipped and mounted in Settings; provider credential editor, quality/quote/consent stage controls remain.

## Delivered (part 1)

- `features/models/ModelCatalog.tsx`:
  - fetches `/api/models` with the server maximum page size (`limit=100`, so ≤100 mounted rows) plus `cursor`, `pricing`, `contextMin`, `benchmarked`, and optional `profileId`/`providerId`;
  - filters UI (price all/free/paid, minimum context, benchmark-only) driving the query; "Tải thêm" appends the next page and disables at the end of the list;
  - honest labelling: pricing class `unknown` renders "Chưa rõ giá" (never "Miễn phí"), availability `unknown` renders "Chưa xác minh", a stale banner (`role="status"`) warns the snapshot may be old, provenance is the provider `sourceUrl` link, and a benchmark badge appears only when `benchmarkRef` exists (no reference => no badge);
  - load failure surfaces via `role="alert"`; empty result states "Không có model khớp filter"; no credential is read or stored (no localStorage).
- Mounted as the real content of Settings → Models (`/settings/models`) in place of the pending panel.
- Tests (6): query builder (default limit-only, filters + cursor), rendering with provenance/stale/unknown-price/unknown-availability, pagination append + exhaustion, unknown class not-free, load-error alert.

## Remaining for U08 acceptance (later parts)

- Provider profile CRUD with masked credential + validate/status, quality picker (Free/Paid/Recommended/Fast/High Quality/Long Context/Translation Optimized/Cloud) and per-stage quote/ceiling/consent controls; model change invalidating an existing quote; combobox/filter E2E and credential rotate tests.

## Validation (part 1)

- `npx vitest run src/features/models` — 6 passed.
- Full frontend suite: `npm test -- --run` — 59 passed / 18 files; production build `npm run build` PASS.
- No backend/API change in this part.
