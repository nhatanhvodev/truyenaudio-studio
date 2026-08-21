# Task 1 Report: EPUB, DOCX and Folder Import Sandbox

Status: complete.

RED evidence:
- `.\.venv\Scripts\python -m pytest backend/tests/sources/test_secure_imports.py -q`
- Result after test correction: `ModuleNotFoundError: No module named 'app.modules.sources.archive_guard'`.

GREEN evidence:
- `.\.venv\Scripts\python -m pip install -e ".\backend"`: passed after adding `tool.setuptools.packages.find` for `app*`; installed `EbookLib==0.19`, `python-docx==1.2.0`, `defusedxml==0.7.1`, `psutil==7.0.0`.
- `.\.venv\Scripts\python -m pytest backend/tests/sources -q`: `11 passed in 0.33s`.
- `.\.venv\Scripts\python -m ruff check backend/app/modules/sources/archive_guard.py backend/app/modules/sources/epub.py backend/app/modules/sources/docx.py backend/app/modules/sources/folder.py backend/tests/sources/test_secure_imports.py`: `All checks passed!`.
- `.\.venv\Scripts\python -m pytest backend/tests -q`: `276 passed, 1 warning in 47.84s`.
- `npm test -- --run src/features/import/ImportPreview.test.tsx`: `1 passed`.
- `npm test -- --run`: `12 passed`.
- `npm run build`: passed, Vite built production bundle.

Files changed:
- `backend/pyproject.toml`
- `backend/app/modules/sources/archive_guard.py`
- `backend/app/modules/sources/epub.py`
- `backend/app/modules/sources/docx.py`
- `backend/app/modules/sources/folder.py`
- `backend/tests/sources/test_secure_imports.py`
- `backend/tests/fixtures/imports/README.md`
- `frontend/src/features/import/ImportPreview.tsx`
- `frontend/src/features/import/ImportPreview.test.tsx`

Self-review:
- Archive import now scans ZIP metadata before any parser reads content: ZIP magic, 50 MB archive cap, 200 MB uncompressed cap, 10,000 entry cap, compression ratio cap, path traversal/absolute Windows paths, encrypted entries, symlinks, macro/script/executable member names.
- EPUB parsing reads container/OPF/spine XHTML directly through `defusedxml`, rejects inline script/style, and never extracts to disk.
- DOCX parsing runs after archive guard and only collects body paragraph/table text in document order.
- Folder import resolves selected paths inside the chosen root, rejects symlink/reparse points before reading, reads only `.txt`, natural-sorts filenames, and returns preview warnings for empty, missing ordinal, and duplicate ordinal candidates.
- `ProjectWorkflow.import_chapters` was not expanded; this task remains preview-only and performs no DB writes.

Concerns:
- Symlink test skips if the Windows account cannot create symlinks.
- Editable install created `backend/truyenaudio_studio_backend.egg-info`; local policy blocked recursive deletion, so it was left untracked and unstaged.
