# Task 13 / C01 report

Status: DONE

## Delivered

- New `translation_styles` table (additive migration `0009`, index parity with ORM): id, project_id nullable, revision_no, supersedes_id, name, genre, tone, source_language, target_language, user_instruction, prompt_template_version.
- `style_profile` module: versioned style profiles chained by `supersedes_id` (glossary-style revision semantics). Upsert by (project, name) creates a new revision + new canonical `sha256` on any semantic change and is a no-op otherwise. Validation: `STYLE_NAME_REQUIRED`, `STYLE_GENRE_INVALID:<genre>`, `STYLE_TONE_INVALID:<tone>`, `STYLE_LANGUAGE_REQUIRED`, `STYLE_INSTRUCTION_TOO_LONG` (4 000 char cap).
- Genre/tone allowlists and 10 curated presets (sát nghĩa, tự nhiên, văn học, web novel, light novel, cổ trang, hiện đại, fantasy, wuxia, xianxia) as configuration with Vietnamese default instructions — versioned config, never a quality badge. Editing a preset for a project creates a project-scoped copy-on-write (contract C03 semantics); a project may hold several active named styles.
- New `/api/projects/{id}/styles` router (GET active styles, GET presets, POST upsert) registered in the app.
- `PromptBuilder` kept for chat envelopes; source stays untrusted data (tests: malicious source text cannot restructure instruction blocks; expected segment order preserved; Unicode/Han/Vietnamese round-trip). Added `NativeMtPromptBuilder`/`NativeMtEnvelope` producing a single-segment envelope for native-MT providers: languages + glossary/TM references + segment text, with **no** chat `system`/`user` instruction fields (mirrors the Qwen-MT no-system-message constraint from P05); raises `NATIVE_SINGLE_SEGMENT_REQUIRED` for multi-segment input.

## Design notes / residual (visible follow-ups)

- Wiring a project style/profile into runs, cache keys and provider calls (the PromptEnvelope -> workflow/cache path) is deferred to the C05 context/snapshot work and the U09 style-management UI, matching the milestone decomposition (this task covers profile data/API and the chat/native envelope builders).
- `translation_styles` keeps `user_instruction` free-form; preset copy-on-write is materialized on first project upsert of a preset-named style.
- Ruff on new/changed modules passes; `app/main.py` carries the repo-wide pre-existing E402 pattern (one new import line adds the same category that already existed 28x at HEAD).

## Validation

- `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests/translation/test_style_profile.py backend/tests/translation/test_prompt_builder.py backend/tests/db/test_schema.py -q` — 28 passed (includes 8 new C01 tests).
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 581 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed; migration 0009 exercised by schema round-trip tests.

## Notes

- Contract/fixture validation only; no provider/cloud call added.
