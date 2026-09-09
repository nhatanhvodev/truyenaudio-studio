# Task 6 / S03 report

Status: DONE

## Delivered

- Added explicit cloud quote/reservation flow for translation. `POST /api/chapters/{chapter_id}/translation/quote` resolves the chapter, selected translator profile, consent, rights, model, secret reference, current rate card, and budget cap before creating a held budget authorization.
- Budget authorizations now persist execution metadata: provider profile ID, provider profile revision, cloud consent ID, stage, immutable plan hash, quote hash, category, and rate-card IDs. Dispatch re-quotes and rejects changed profile revision, changed stage/category/rate card, changed plan hash, expired hold, underfunded hold, or missing authorization.
- Cloud dispatch no longer synthesizes budget authorization during `evaluate`. Cloud calls require an explicit reservation ID and fail closed with `BUDGET_AUTHORIZATION_REQUIRED` when absent.
- Batch cloud authorization validation now binds the budget reservation to the actual job stage, so a translate reservation cannot authorize a different cloud-capable stage.
- Budget reservations are inserted and flushed before cap checks. On SQLite this takes the writer lock before total calculation, then rolls back failed reservations, preventing concurrent holds from exceeding the regular cap.
- Duplicate settlement is idempotent: a second settlement call on an already committed authorization does not create duplicate usage ledger rows. Cloud dispatch still rejects reuse of an already `COMMITTED` authorization, so idempotent settlement cannot be used to resend provider calls.
- Migration `0007_budget_authorization_quote_metadata` adds the quote metadata columns and lookup index.

## Validation

- Focused S03 suite: `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend\tests\budgets\test_guard.py backend\tests\compliance\test_cloud_translation_guard.py backend\tests\compliance\test_cloud_guard.py backend\tests\jobs\test_batch.py backend\tests\db\test_schema.py backend\tests\translation\test_workflow.py::test_translation_approve_rejects_budget_authorization_fields backend\tests\translation\test_workflow.py::test_translation_quote_reserves_budget_and_stale_profile_blocks_dispatch -q`
  - 59 passed, 1 known SQLAlchemy FK-cycle warning.
- Full backend: `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend\tests -q`
  - 513 passed, 1 known SQLAlchemy FK-cycle warning.
- Changed-file Ruff: `D:\truyenaudio-studio\.venv\Scripts\python.exe -m ruff check <S03 changed files>`
  - Passed.

## Notes

- Project-wide Ruff still reports unrelated pre-existing issues in `backend/app/main.py`, `backend/app/modules/translation/hanviet.py`, and a few older tests. The S03 changed files pass Ruff.
- Paid-cloud smoke remains `NOT_RUN`; no paid provider call was executed.

