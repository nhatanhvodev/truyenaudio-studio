# Task 4 Report: Invalidation graph and verified cache reuse

## Status

Implemented and verified.

## Ruling

Ruling: Spec section 10.2 was not locally available in the repository. I implemented the invalidation matrix from `task-4-brief.md` against the current schema without adding proposal persistence or deleting artifact files.

## Scope

Created:

- `backend/app/modules/projects/invalidation.py`
- `backend/app/modules/artifacts/cache.py`
- `backend/tests/projects/test_invalidation.py`
- `backend/tests/artifacts/test_cache.py`

No existing contracts, schema names, workflow names, cache key columns, or export v1 names were renamed.

## Red Evidence

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/projects/test_invalidation.py backend/tests/artifacts/test_cache.py -q
```

Result before implementation:

```text
ModuleNotFoundError: No module named 'app.modules.projects.invalidation'
ModuleNotFoundError: No module named 'app.modules.artifacts.cache'
2 errors in 0.34s
```

## Implementation Notes

- Added `ChangeKind`, `Change`, `InvalidationPlan`, and `InvalidationGraph`.
- Invalidation updates statuses and active pointers only:
  - stale artifacts move from `READY` to `SUPERSEDED`;
  - exports move from `READY` to `REVOKED` with `revoked_at`;
  - dependent chapter pointers are cleared only where their dependency is stale.
- Target-text, pronunciation, and voice-role changes invalidate only dependent speech audio and downstream master/SRT/export rows.
- Rights-evidence changes revoke exports and supersede export bundle artifacts, while preserving reusable audio/master/SRT artifacts.
- Added `ArtifactCache.lookup(kind, input_hash, settings_hash)`.
- Cache hits require `READY` status, file existence, byte-size match, and SHA-256 match.
- Missing or mismatched files mark the artifact `CORRUPT`, add an `ARTIFACT_CACHE_CORRUPT` audit event, and return no hit.
- Added `canonical_json` and `canonical_sha256` with a golden-vector test to prevent hash drift.

## Verification

Focused Task 4 tests:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/projects/test_invalidation.py backend/tests/artifacts/test_cache.py -q
```

Result:

```text
15 passed in 5.47s
```

Relevant existing export/audio/translation tests:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/exports backend/tests/speech/test_single_narrator.py backend/tests/translation -q
```

Result:

```text
51 passed in 16.45s
```

Ruff on touched backend files:

```powershell
.\.venv\Scripts\python -m ruff check backend/app/modules/projects/invalidation.py backend/app/modules/artifacts/cache.py backend/tests/projects/test_invalidation.py backend/tests/artifacts/test_cache.py
```

Result:

```text
All checks passed!
```

## Concerns

- The full spec section 10.2 was not present locally, so the matrix is intentionally based on the task brief and current schema.
- This task does not wire invalidation into existing workflows; it provides the graph/cache modules and tests requested for Task 4 only.
