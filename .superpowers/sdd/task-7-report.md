# Task 7 / P01 report

Status: DONE

## Delivered

- ProviderRegistry now resolves model snapshots through the catalog without filtering disabled descriptors first, so disabled providers fail with `PROFILE_DISABLED` instead of being hidden as unavailable.
- Registry authorization validates current profile revision before factory dispatch, supports explicit fallback profile allowlists, checks provider/profile mismatch, and maps required stage to translation or structured capability.
- Unknown and unsupported capabilities fail closed before adapter creation. Descriptor factories still receive the authorization object and tests assert the same boundary is used.
- ProviderCatalog now has a stable descriptor/model lookup plus broader capability filters used by both registry and API code.

## Validation

- `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend\tests\providers\test_registry_catalog.py backend\tests\api\test_models_api.py -q`
  - 19 passed.
- `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend\tests -q`
  - 524 passed, 1 known SQLAlchemy FK-cycle warning.
- Changed-file Ruff for catalog/registry/models API/tests passed.

## Notes

- The legacy translation compatibility routes still instantiate provider adapters directly. The registry boundary is now enforced by adapters and catalog tests, but moving all route adapter construction into an application-level resolver remains tied to the later provider/job orchestration slices.
- Paid-cloud smoke remains `NOT_RUN`.
