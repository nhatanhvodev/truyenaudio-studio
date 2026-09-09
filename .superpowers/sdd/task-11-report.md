# Task 11 / P05 report

Status: DONE (revised after full-suite run and wire re-check)

## Delivered

- Qwen-MT now sends the official DashScope native Generation payload: `model` + `input.messages` (one `user` message with the source text) + `parameters.translation_options` with `source_lang`/`target_lang`, `terms`, `tm_list`, and `result_format: "message"`. Verified against the current Alibaba Model Studio Qwen-MT docs (fetched 2026-09-09, https://www.alibabacloud.com/help/en/model-studio/machine-translation).
- App language tags are mapped to official Qwen-MT codes on the wire (`zh-CN`/`zh-Hans`/`zh` -> `zh`, `vi-VN`/`vi` -> `vi`, `zh-TW`/`zh-Hant` -> `zh_tw`, ...). Unsupported tags fail closed before HTTP (`QWEN_LANGUAGE_UNSUPPORTED:<tag>`); no silent wrong language code is sent.
- Native response parsing: `output.choices[0].message.content` for the translation, top-level `usage.{input_tokens,output_tokens}` and `request_id`. Empty/missing choices raise the shared `ProviderTransportError` (`EMPTY_RESPONSE`/`MALFORMED_RESPONSE`) with `BillingState.UNKNOWN`; the adapter never fabricates a target when the provider returns none.
- No chat-style `system` role/message is ever sent (Qwen-MT docs: system messages not supported). No retry/fallback loop lives in the adapter.
- Qwen dispatch uses shared bearer JSON headers and shared HTTP/malformed/timeout transport normalization. HTTP 400/401/403/404/429/5xx fixtures assert billing state and retryability; timeout/transport after dispatch still raise `ProviderBillingUnknown` and do not retry.
- Existing guards remain: empty source and oversize source fail before HTTP; arbitrary endpoint and unsafe model identifiers fail before dispatch; cloud consent/budget/guard fail before HTTP.
- Aligned the standalone authorized-smoke POC (`scripts/poc/qwen_probe.py` + its test) to the same native payload/response shape so a future smoke exercises the real contract.
- Fixture `backend/tests/fixtures/qwen_translation.json` now pins the native response shape with `wireSource`/`wireFetchedAt`/`modelRevision` metadata per G-PROVIDER.

## Not in scope of this task (visible follow-ups)

- Vietnamese `domain_instruction` (style guide) and `story_memory` have **no documented Qwen-MT wire field**: `translation_options.domains` is English-only and system messages are unsupported. P05 therefore sends only documented MT fields. A native-MT envelope that carries style/memory is the scope of C01/C05 (prompt/context builders) in M3, not P05.
- Endpoint stays the configurable canonical DashScope international text-generation endpoint; regional/WorkspaceId endpoint choice is a deployment/account input (plan §8), not invented here.
- mypy on changed files still reports pre-existing typing noise (`http_client: object` etc.); repo-wide mypy is not a green gate at baseline (140 pre-existing errors in unrelated files). Pytest + ruff are the operative gates for this task.

## Validation

- `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests/providers/test_transport.py backend/tests/providers/test_gemini_mt_security.py backend/tests/providers/test_qwen_mt_contract.py backend/tests/providers/test_openrouter_contract.py backend/tests/translation/test_selective_repair.py backend/tests/poc/test_qwen_probe.py -q`
  - 56 passed.
- `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` (repo root; full backend)
  - 564 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff for provider files, tests, and the POC script: all checks passed.
- Contract/fixture validation only. No Qwen live request or paid-cloud smoke was run.
