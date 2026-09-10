# Task 26 / U08 report — parts 1–2 (PARTIAL)

Status: PARTIAL — model catalog + provider profile/credential editor shipped in Settings; quality picker and per-stage quote/ceiling/consent controls remain.

## Delivered (part 2)

- `features/providers/ProfileEditor.tsx` mounted as Settings → AI Providers:
  - lists profiles from `/api/cloud-profiles` showing adapter/model/region, a **masked credential state** ("Đã cấu hình" / "Chưa có"), revision status, and never any secret value;
  - credential entry is a `type="password"` `autoComplete="off"` field whose value is held only in component state and sent once in the `PUT .../credential` body, then cleared; empty input is refused client-side ("Cần nhập credential") without calling the API; `DELETE .../credential` revokes; `POST .../validate` reports the backend health status;
  - create-profile form sends providerKind/adapterName/displayName/model/region to `POST /api/cloud-profiles` and refreshes the list; API errors surface via `role="alert"`.
- Tests (5, with a fetch mock that also serves `/api/security/bootstrap` for the CSRF header): masked status rendering, credential send-once + field cleared + **localStorage stays empty**, empty-credential refusal, validate + delete flows, create-profile payload + API error alert.
- Note: tests must stub the CSRF bootstrap endpoint — `apiJson` performs a state-changing request only after fetching a CSRF token, which the earlier mock lacked.

## Delivered (part 1)

- `features/modelCatalog/ModelCatalog.tsx` (thư mục đặt tên `modelCatalog` vì `.gitignore` của repo chặn pattern `models` dành cho model AI local):
  - fetches `/api/models` with the server maximum page size (`limit=100`, so ≤100 mounted rows) plus `cursor`, `pricing`, `contextMin`, `benchmarked`, and optional `profileId`/`providerId`;
  - filters UI (price all/free/paid, minimum context, benchmark-only) driving the query; "Tải thêm" appends the next page and disables at the end of the list;
  - honest labelling: pricing class `unknown` renders "Chưa rõ giá" (never "Miễn phí"), availability `unknown` renders "Chưa xác minh", a stale banner (`role="status"`) warns the snapshot may be old, provenance is the provider `sourceUrl` link, and a benchmark badge appears only when `benchmarkRef` exists (no reference => no badge);
  - load failure surfaces via `role="alert"`; empty result states "Không có model khớp filter"; no credential is read or stored (no localStorage).
- Mounted as the real content of Settings → Models (`/settings/models`) in place of the pending panel.
- Tests (6): query builder (default limit-only, filters + cursor), rendering with provenance/stale/unknown-price/unknown-availability, pagination append + exhaustion, unknown class not-free, load-error alert.

## Remaining for U08 acceptance (later parts)

- Provider quality picker (Free/Paid/Recommended/Fast/High Quality/Long Context/Translation Optimized/Cloud) and per-stage quote/ceiling/consent controls; model change invalidating an existing quote; combobox/filter and credential-rotate E2E.

## Validation (parts 1–2)

- `npx vitest run src/features/modelCatalog src/features/providers` — 11 passed (6 catalog + 5 profile).
- Full frontend suite: `npm test -- --run` — 64 passed / 19 files; production build `npm run build` PASS.
- No backend/API change in these parts.
