# Phase 2 — Nghiệm thu và bật từng mở rộng (task X07)

Ngày chạy: 2026-09-10 (18:01–18:06 UTC) · Nhánh `codex/implement-upgrade-plan` · Commit tham chiếu `37a6381`
(commit chứa 5 adapter X01–X05). Cây làm việc tại thời điểm chạy **có thay đổi chưa commit**
(X06 đang được làm ở cây này — xem §4), nên mọi số liệu dưới đây là số của **đúng cây đó**.

### Phiên bản đã dùng cho mọi số liệu trong tài liệu này

| Thành phần | Phiên bản |
|---|---|
| Hệ điều hành | Windows 11 (10.0.26200), AMD64 |
| Python | 3.12.13 (CPython), `.venv\Scripts\python.exe` |
| App | `truyenaudio-studio-backend` 0.1.0 |
| pytest | 8.4.1 (`backend/pyproject.toml`) |
| Adapter Phase 2 | `backend/app/providers/{groq,nim,cerebras,cloudflare,huggingface}.py` |

> **Không có mục live nào trong tài liệu này.** Máy này không có account/key của Groq, NVIDIA NIM,
> Cerebras, Cloudflare Workers AI hay Hugging Face; không có consent/budget cloud; chưa review điều khoản
> free-tier; chưa xác nhận region/permission. Vì vậy **mọi cột live = NOT_RUN** và **mọi feature Phase 2
> đang bị tắt**. Không có dòng nào dưới đây được gán PASS cho live.

## 1. Ma trận task X01–X07 → trạng thái

| Task | Trạng thái | code complete | contract verified (offline) | live verified | Bằng chứng chính |
|---|---|---|---|---|---|
| X01 Adapter Groq | DONE (code) | ✅ | ✅ 49 test | ❌ NOT_RUN | `test_groq_contract.py`; commit `37a6381` |
| X02 Adapter NVIDIA NIM | DONE (code) | ✅ | ✅ 66 test | ❌ NOT_RUN | `test_nim_contract.py`; commit `37a6381` |
| X03 Adapter Cerebras | DONE (code) | ✅ | ✅ 45 test | ❌ NOT_RUN | `test_cerebras_contract.py`; commit `37a6381` |
| X04 Adapter Cloudflare Workers AI | DONE (code) | ✅ | ✅ 53 test | ❌ NOT_RUN | `test_cloudflare_contract.py`; commit `37a6381` |
| X05 Adapter Hugging Face | DONE (code) | ✅ | ✅ 67 test | ❌ NOT_RUN | `test_huggingface_contract.py`; commit `37a6381` |
| X06 Vector index tuỳ chọn | **DONE (code+offline)** | ✅ | ✅ 34 test | ❌ NOT_RUN | `test_memory_index.py`; §4 |
| X07 Nghiệm thu và bật từng mở rộng | **PARTIAL** (thiếu live) | ✅ | ✅ | ❌ NOT_RUN | tài liệu này + `test_phase2_extension_gate.py` |

**X07 chưa DONE.** Lý do: **không provider nào có live evidence** (§3) và X06 chưa có benchmark thật (§4). Phụ thuộc X06 đã được đáp ứng (34 test PASS).

## 2. Cách đọc bảng này — bốn mức trạng thái KHÁC NHAU

| Mức | Nghĩa | Điều kiện tối thiểu |
|---|---|---|
| **code complete** | Đã viết mã adapter/guard | File tồn tại, import được |
| **contract verified (offline)** | Đã kiểm bằng fixture HTTP giả, không mở socket | Có lệnh + exit code + số test thật; **không** chứng minh account/region/terms/chất lượng |
| **live verified** | Đã gọi provider thật bằng account được cấp quyền | Có credential/consent/budget + invocation thật + output thật + ledger |
| **NOT_RUN** | Chưa chạy được vì thiếu môi trường | Kèm lý do cụ thể; **không bao giờ** suy ra PASS từ fixture |

## 3. Bảng provider × mức kiểm chứng

Lệnh contract cho từng provider: `.venv\Scripts\python.exe -m pytest backend/tests/providers/test_<tên>_contract.py -q`
(chạy 2026-09-10 trong phiên này, exit 0 cho cả 5). Cột **guard** = số test khớp `-k "guard"`; cột **key-leak** = số test khớp
`-k "leak or credential or api_key or secret or repr"` (đúng biểu thức này, để tái lập lại được).

| Provider | Feature / flag | Contract suite | Guard (test) | Key-leak (test) | Offline | **live** | Lý do NOT_RUN |
|---|---|---|---|---|---|---|---|
| Groq (X01) | `provider_groq_live` | **PASS** · 49 test · exit 0 | PASS · 4 | PASS · 4 | ✅ | **NOT_RUN** | Không có Groq account/key; chưa review terms free-tier (plan X01: "enable theo account/terms") |
| NVIDIA NIM (X02) | `provider_nim_live` | **PASS** · 66 test · exit 0 | PASS · 4 | PASS · 3 | ✅ | **NOT_RUN** | Không có NVIDIA account/license; chưa chạy endpoint self-hosted nào (plan X02: "account/license là gate bật riêng") |
| Cerebras (X03) | `provider_cerebras_live` | **PASS** · 45 test · exit 0 | PASS · 5 | PASS · 1 | ✅ | **NOT_RUN** | Không có Cerebras key; quota free không được xác nhận (plan X03: "bật sau evidence riêng của provider") |
| Cloudflare Workers AI (X04) | `provider_cloudflare_live` | **PASS** · 53 test · exit 0 | PASS · 3 | PASS · 2 | ✅ | **NOT_RUN** | Không có Cloudflare account/API token; chưa xác nhận region/permission cho account-scoped endpoint |
| Hugging Face (X05) | `provider_huggingface_live` | **PASS** · 67 test · exit 0 | PASS · 4 | PASS · 1 | ✅ | **NOT_RUN** | Không có HF token; chưa review license từng model, chưa có evidence inference-provider routing/price |

Tổng 5 provider = **280 test** trong tổng **399 test** của `backend/tests/providers` sau khi thêm 12 test X07
(trước khi thêm: 387 test — xem baseline ở §5).

### 3.1 Groq (X01)

- Wire surface: `POST https://api.groq.com/openai/v1/chat/completions`, Bearer auth, body tối thiểu
  `{model, messages}` (không gửi field OpenAI tuỳ chọn khi chưa kiểm chứng).
- Guard (4 test): `test_groq_guard_receives_the_request_context_before_dispatch`,
  `test_groq_blocks_without_cloud_guard_before_http`, `test_groq_blocks_when_the_guard_denies_before_http`,
  `test_groq_requires_guard_context_before_http`.
- Key-leak (4 test): `test_groq_never_places_the_credential_outside_the_authorization_header`,
  `test_groq_never_leaks_the_credential_through_failure_paths`, `test_groq_repr_redacts_the_credential`,
  `test_groq_missing_credential_fails_before_http`.
- Khác đã kiểm: usage/model provider thật báo, `GROQ_BILLING_UNKNOWN` khi timeout sau khi đã gửi (không retry),
  **không** claim free/unlimited trong `capabilities()`, `live_verified: false`.
- **Còn NOT_RUN**: mọi thứ cần account thật (G-PROVIDER live smoke, quota, region, terms).

### 3.2 NVIDIA NIM (X02)

- Wire surface: endpoint allowlist + `ENDPOINT_UNSUPPORTED`; endpoint self-hosted https được giữ nguyên văn.
- Guard (4 test): `test_nim_guard_missing_blocks_before_any_http`, `test_nim_guard_context_missing_blocks_before_any_http`,
  `test_nim_denied_guard_blocks_before_any_http`, `test_nim_guard_precedes_source_validation`.
- Key-leak (3 test): `test_nim_never_places_or_returns_the_api_key`, `test_nim_empty_credential_fails_before_any_dispatch`,
  `test_nim_rejects_unsupported_endpoints_before_any_request[…user:secret@…]`.
- Khác đã kiểm: `capability_verified: false` (không discovery ⇒ capability chỉ là khai báo, fail-closed),
  usage/request id, error matrix.
- **Còn NOT_RUN**: account thật, self-hosted endpoint thật, licence.

### 3.3 Cerebras (X03)

- Guard (5 test): 4 test guard như trên + `test_cerebras_stream_guard_blocks_before_any_bytes`.
- Key-leak (1 test): `test_cerebras_never_places_or_returns_the_api_key`.
- Khác đã kiểm: payload chat native, **SSE split UTF-8/JSON, byte-by-byte, frame bị cắt**, finish/done, actual model
  (không silent substitution), billing state theo ma trận lỗi.
- **Còn NOT_RUN**: key/account thật, quota free thật, G-PROVIDER live smoke.

### 3.4 Cloudflare Workers AI (X04)

- Wire surface: account-scoped `/client/v4/accounts/{account_id}/ai/run/{model}`.
- Guard (3 test): `test_cloudflare_guard_is_required_before_http`, `test_cloudflare_denied_guard_blocks_before_http`,
  `test_cloudflare_guard_precedes_source_validation`.
- Key-leak (2 test): `test_cloudflare_never_places_credentials_in_the_payload_or_the_result`,
  `test_cloudflare_rejects_unsafe_model_or_account_identifiers[…api_key=…]`.
- Khác đã kiểm: envelope `success/errors` không bao giờ thành công giả, **từ chối ép model non-chat vào payload chat**,
  từ chối endpoint tuỳ ý trước khi gửi bearer.
- **Còn NOT_RUN**: account/token thật, region/permission matrix thật, model licence.

### 3.5 Hugging Face (X05)

- Wire surface: hosted `POST https://api-inference.huggingface.co/models/{model}`, task `text-generation` bắt buộc.
- Guard (4 test): `test_huggingface_guard_missing_blocks_before_any_http`, `…_guard_context_missing_…`,
  `…_denied_guard_…`, `…_guard_precedes_source_validation`.
- Key-leak (1 test): `test_huggingface_never_places_or_returns_the_api_key` (+ `…_missing_token_fails_closed_before_any_http`).
- Khác đã kiểm: chỉ task text-generation (task khác bị **từ chối**), hai shape response, routed model được báo thật,
  endpoint canonical, **module HTTP-only**: test `…_module_is_http_only_and_never_downloads_a_model` đọc chính module.
- **Còn NOT_RUN**: token thật, gated-model acceptance, license từng model, routing/price của inference provider.

## 4. X06 — vector index: code-complete và contract-verified offline

X06 đã hoàn thành trong cùng session này. Trạng thái:

- `backend/app/modules/translation/memory_index.py`: service nhập/rebuild/retrieve vector index theo đúng spec.
- `backend/migrations/versions/0019_memory_indexes.py`: migration additive, không chỉnh sửa migration đã phát hành.
- `backend/tests/translation/test_memory_index.py`: **34 test PASS** (exit 0), bao gồm:
  - Import valid artifact, hash ổn định, schema/dimension/non-finite/empty/duplicate rejection.
  - Deterministic top-8 retrieval, project isolation, stale/cross-project rejection.
  - Query mismatch fallback, missing embedding never auto-generated, disabled index.
  - Off-path identical selection, rebuild reproducibility, corrupt artifact detection.
  - API integration: import/status/rebuild/retrieve routes, named error codes.
- API routes: `POST /embedding-index/import`, `GET /embedding-index/status`,
  `POST /embedding-index/rebuild`, `POST /embedding-index/retrieve` (trong `api/memory.py`).
- `context_engine.py`: `select_context` nhận `hint()` từ vector retrieval làm đầu vào bias.
- Flag `vector_index` vẫn `unsafe`, `default=false`, gắn `vector_index_live` = **NOT_RUN**
  (chưa có embedding artifact thật, chưa có benchmark relevance/latency/RSS).

**Hệ quả:** X06 đã code-complete và contract-verified offline (34 test). X07 cần X06 có kết quả đã được đáp ứng.
Tuy vậy Phase 2 vẫn chưa complete vì **không provider nào có live evidence** (§3) và X06 chưa có benchmark thật.

## 5. Lệnh đã chạy (command · exit code · số test thật)

| # | Lệnh | Kết quả | Exit | Timestamp (UTC) |
|---|---|---|---|---|
| 1 | `.venv\Scripts\python.exe -m pytest backend/tests/providers -q` | **399 passed** · 2,50 s | 0 | 2026-09-10T18:05:53Z |
| 2 | `.venv\Scripts\python.exe -m pytest backend/tests/providers backend/tests/settings -q` | **423 passed** · 3,18 s | 0 | 2026-09-10T18:05:57Z |
| 3 | `.venv\Scripts\python.exe -m pytest backend/tests/providers backend/tests/execution backend/tests/compliance backend/tests/security backend/tests/db -q` (regression Phase 1) | **527 passed**, 1 warning (SQLAlchemy FK-cycle có sẵn) · 104,49 s | 0 | 2026-09-10T18:03:54Z |
| 4 | `.venv\Scripts\python.exe -m pytest backend/tests/providers/test_<provider>_contract.py -q` (5 provider) | 49 / 66 / 45 / 53 / 67 passed | 0 | 2026-09-10 (cùng phiên) |
| 5 | `.venv\Scripts\python.exe -m ruff check backend/tests/providers/test_phase2_extension_gate.py backend/app/modules/settings/feature_flags.py` | `All checks passed!` | 0 | 2026-09-10T18:05:59Z |
| 6 | script tự kiểm `evidenceRef` (đọc `release-manifest.json`, kiểm file + anchor GitHub-slug) | `features 21 bad []` | 0 | 2026-09-10 (cùng phiên) |
| 7 | `pytest backend/tests/db/test_migration_rehearsal.py -q -s` | **4 passed** · EVIDENCE clean-install revision=0019 migrations=19 tables=38 integrity=ok · legacy 0016→head giữ nguyên số hàng | 0 | session này |
| 8 | `pytest backend/tests/storage/test_backup_rollback_drill.py -q -s` | **2 passed** · EVIDENCE backup-rollback restored_revision=0016 then_reupgraded=0019 artifact_sha256_match=True · restore-guards used_target_rejected=True corrupted_backup_rejected=True | 0 | session này |
| 9 | `pytest backend/tests/translation/test_memory_index.py -v` | **34 passed** (X06) | 0 | session này |
| 10 | `pytest backend/tests/translation/test_memory_index.py backend/tests/providers/test_phase2_extension_gate.py -q` | **46 passed** (34 X06 + 12 X07) — chạy lại SAU bản sửa mypy vẫn 46 passed | 0 | session này |
| 11 | `pytest backend/tests/providers/test_<provider>_contract.py --collect-only -q` (5 provider) | 49 / 66 / 45 / 53 / 67 collected = **280**, khớp đúng số đã claim ở §1/§3 | 0 | session này |
| 12 | `pytest backend/tests --collect-only -q` | **1243 tests collected** | 0 | session này |
| 13 | `mypy app` (baseline toàn app) | **235 lỗi / 53 file** — pre-existing, KHÔNG do X06/X07; mypy vốn chưa xanh ở baseline | 1 | session này |
| 14 | `mypy app/modules/translation/memory_index.py app/api/memory.py` | trước bản sửa **28 lỗi**; sau khi thay `**located` bằng kwargs tường minh còn **1 lỗi** `attr-defined` cùng loại pattern baseline (`Mapped[object].isoformat()`, như projects.py/queries.py) — nguyên nhân gốc là annotation của MutableMixin, cố ý KHÔNG sửa vì cắt ngang ngoài scope X06 | 1 | session này |

**Baseline trước khi thêm test X07** (đo trong cùng phiên, cùng cây): `backend/tests/providers` = **387 passed**,
`providers + settings` = **411 passed**, cả hai exit 0. Chênh lệch 12 test chính là
`test_phase2_extension_gate.py`.

Lệnh 3 là phần "Phase 1 regression xanh" của tiêu chí nghiệm thu X07: vùng lõi
providers/execution/compliance/security/db **không hồi quy** sau khi thêm scope Phase 2.

Không chạy full suite `backend/tests`, không chạy frontend, **không gọi cloud**, **không tải model**.

## 6. Gate bật từng extension (fail-closed theo TỪNG flag)

Bằng chứng: `backend/tests/providers/test_phase2_extension_gate.py` — test chỉ **đọc** evidence record và flag guard,
không mở socket, không gọi provider.

| Điều phải chứng minh | Test | Cách chứng minh |
|---|---|---|
| Mỗi provider Phase 2 CHƯA có evidence PASS ⇒ flag tương ứng không bật được | `test_no_phase2_provider_flag_can_be_enabled_from_the_repository_manifest`, `test_settings_api_returns_400_for_every_phase2_provider_flag` | `effective_flags({flag: True})` ném `FEATURE_FLAG_EVIDENCE_NOT_PASS:<flag>`; `PUT /api/settings/feature-flags` trả **400** đúng mã đó cho **cả 5** flag |
| **Không bật toàn bộ vì một adapter pass** | `test_one_provider_with_pass_evidence_never_enables_the_others` | Manifest tạm: **chỉ Groq PASS**, 4 provider còn lại NOT_RUN ⇒ Groq bật được (200, `true`), **từng provider còn lại vẫn 400** với `FEATURE_FLAG_EVIDENCE_NOT_PASS` |
| Gate theo từng flag, không theo nhóm | `test_every_phase2_provider_flag_is_registered_and_defaults_off` | 5 flag ↔ 5 evidence feature **phân biệt** (`set(evidence_features) == set(PHASE2_FLAGS)`, không flag nào dùng chung feature) |
| Xin bật **cả nhóm** cũng bị chặn | `test_no_phase2_provider_flag_can_be_enabled_from_the_repository_manifest` (phần cuối) | `effective_flags({cả 5 flag: True})` vẫn ném `FEATURE_FLAG_EVIDENCE_NOT_PASS:provider_*` — không có công tắc nhóm |
| Manifest **thiếu** ⇒ fail-closed | `test_missing_manifest_keeps_every_phase2_provider_off` | Không có file manifest ⇒ cả 5 flag off và mọi yêu cầu bật đều bị từ chối |
| API công khai phơi đúng trạng thái | `test_flag_gate_report_marks_every_phase2_provider_as_blocked` | `GET /api/settings/feature-flags`: `enabled=false`, `evidence=NOT_RUN`, `disabledReason=FEATURE_FLAG_EVIDENCE_NOT_PASS:<flag>`, `evidenceRef` trỏ `docs/validation/phase2.md#…` |
| 5 adapter **chưa** đăng ký ⇒ không resolve được | `test_phase2_adapters_are_not_registered_in_the_curated_catalog` | `curated_provider_catalog()` chỉ có gemini/qwen/openrouter; `catalog.get(<provider Phase 2>)` ⇒ `PROVIDER_NOT_FOUND`; registry trên catalog đó ⇒ `MODEL_UNAVAILABLE` |
| Capability **unknown** fail-closed | `test_unknown_capability_fails_closed_before_any_factory_runs` | Model có `translation=UNKNOWN` ⇒ `CAPABILITY_UNKNOWN`, **factory không được gọi** (`dispatched == []`); cùng descriptor với `SUPPORTED` thì factory chạy (chứng minh nguyên nhân đúng là capability) |
| Local LLM vẫn deferred | `test_local_llm_stays_deferred`, `test_huggingface_extension_is_hosted_only_and_downloads_nothing` | Không flag/feature nào nhắc Ollama/LM Studio/llama.cpp/vLLM; không file nào trong `backend/app/providers/` nhắc tới runtime local; adapter HF **chỉ HTTP** (kiểm bằng AST import graph: không `transformers`/`torch`/`huggingface_hub`, không `from_pretrained`/`snapshot_download`) |
| Mọi `evidenceRef` trỏ tới file **và anchor** tồn tại | `test_every_manifest_evidence_ref_points_at_an_existing_file_and_anchor` | Kiểm 21 feature: file tồn tại + anchor sinh theo luật GitHub slug có trong heading của file đích |

Đổi duy nhất ngoài phạm vi "test + tài liệu": **thêm 5 flag** `provider_{groq,nim,cerebras,cloudflare,huggingface}_live`
vào `app/modules/settings/feature_flags.py` (`safe=True`, `default=False`, `evidence_feature` = chính nó — cùng khuôn với
`cloud_quality_mode`). Không có flag này thì "gate theo từng provider" chỉ là **tên trong manifest** mà API không biết,
nên yêu cầu "chứng minh bằng TEST rằng flag tương ứng không bật được" không thể chứng minh thật.
`registry.py` và `catalog.py` **không bị sửa**.

## 7. Danh sách feature ĐANG DISABLED

| # | Flag | Feature evidence | Evidence | Lý do / điều kiện bật |
|---|---|---|---|---|
| 1 | `provider_groq_live` | `provider_groq_live` | **NOT_RUN** | Cần Groq account/key + terms + invocation thật |
| 2 | `provider_nim_live` | `provider_nim_live` | **NOT_RUN** | Cần NVIDIA account/license + endpoint thật |
| 3 | `provider_cerebras_live` | `provider_cerebras_live` | **NOT_RUN** | Cần Cerebras key + xác nhận quota/terms |
| 4 | `provider_cloudflare_live` | `provider_cloudflare_live` | **NOT_RUN** | Cần account/token + region/permission |
| 5 | `provider_huggingface_live` | `provider_huggingface_live` | **NOT_RUN** | Cần HF token + license model + routing/price |
| 6 | `cloud_quality_mode` | `cloud_quality_live` | **NOT_RUN** | Giữ nguyên từ R01 (không đổi trong X07) |
| 7 | `vector_index` (unsafe) | `vector_index_live` | **NOT_RUN** | X06 code/offline ✅, chưa có benchmark/live |
| 8 | `auto_approve_translation` (unsafe) | `auto_approve_translation_live` | **NOT_RUN** | Giữ nguyên từ R01 |
| 9 | `public_export_bypass` (unsafe) | `public_export_bypass_live` | **BLOCKED** | Vi phạm F03 nếu bật |
| — | `vieneu_tts_live`, `quality_evaluation_corpus`, `ffmpeg_real_master`, `g_perf_long_task_typing`, `g_perf_sse_heap_30min` | (không có flag API) | **NOT_RUN** | Giữ nguyên từ Phase 1 |

## 8. KNOWN LIMITS — X07 KHÔNG chứng minh được gì

1. **Không có live evidence cho bất kỳ provider Phase 2 nào.** Fixture pass **không** chứng minh account/region/terms,
   không chứng minh chất lượng dịch, không chứng minh quota/giá. G-PROVIDER smoke có uỷ quyền (plan X01–X05) và
   **G-LIVE** đều **NOT_RUN**.
2. **X06 đã code-complete và contract-verified offline** (34 test) nhưng chưa có benchmark vector relevance/latency/RSS
   và chưa có per-provider index. Phần "vector relevance/latency/RSS" trong kiểm thử bắt buộc của X07 vẫn **NOT_RUN**.
3. **G-CORE không chạy lại trong task này.** Chỉ chạy các vùng lõi liệt kê ở §5 lệnh 3 (providers/execution/compliance/
   security/db). E2E/browser/frontend **không** chạy ở đây.
4. **`scripts/preflight.ps1` không bị sửa** (plan chỉ liệt kê "file/module dự kiến"). Kiểm soát gate Phase 2 nằm ở
   manifest + flag guard, đã có test. Thêm bước preflight đọc manifest là việc của task sau.
5. ~~`docs/validation/release-manifest.md` (bản người đọc) vẫn mô tả 16 feature~~ **ĐÃ ĐÓNG trong session này**:
   bản người đọc nay có đủ **21 feature** (thêm 5 dòng provider Phase 2), và các dòng schema được cập nhật theo
   migration 0019 (revision 0019 · 19 migration · **38 bảng**, đo thật bằng `EVIDENCE clean-install` ở lệnh 7).
6. **Cây làm việc có thay đổi đã sẵn sàng commit của X06+X07**: số test ở §5 là số của cây trước khi X06 hoàn tất.
   Sau khi thêm 34 test X06 và 12 test X07, tổng test backend là **1243** (1243 collected).
7. **Một provider được xác minh live KHÔNG mở đường cho provider khác**: gate là theo từng flag (§6), nhưng điều đó
   cũng có nghĩa X07 phải chạy lại cho **từng** provider khi có evidence.

## 9. Kết luận (chính xác theo evidence)

1. **Phase 2 CHƯA complete.** X07 phụ thuộc X01–X06; X06 đã có kết quả (34 test PASS, §4) nhưng **không provider
   nào có live evidence** (§3). Báo cáo này ghi nhận đúng trạng thái đó, không suy diễn.
2. **Năm adapter Phase 2 đã code-complete và contract-verified offline** (280 test, exit 0) nhưng **đang tắt**:
   cả 5 flag `provider_*_live` bị chặn bằng `FEATURE_FLAG_EVIDENCE_NOT_PASS:<flag>` (API trả 400), và cả 5 adapter
   **chưa được đăng ký** trong `catalog.py`/`registry.py` nên không thể resolve.
3. **Không bật toàn bộ vì một adapter pass**: đã chứng minh bằng test — một provider PASS chỉ bật đúng provider đó,
   4 provider còn lại vẫn 400; và xin bật cả nhóm cũng bị chặn.
4. **Local LLM vẫn deferred**: không flag/feature/mã nguồn nào bật Ollama/LM Studio/llama.cpp/vLLM; adapter Hugging Face
   chỉ là HTTP hosted, không import runtime local và không có đường tải model.
5. **Phase 1 regression xanh** trên vùng lõi đã chạy (§5 lệnh 3, exit 0) — scope Phase 2 **không** làm hồi quy vùng
   providers/execution/compliance/security/db. `registry.py`/`catalog.py` không bị sửa.
6. **Điều kiện để X07 hoàn tất**: (a) ~~X06 có kết quả nghiệm thu riêng~~ ✅ ĐÃ ĐẠT (34 test PASS); (b) mỗi provider
   có credential/consent/budget hợp lệ + invocation thật + ledger + output thật (G-LIVE), rồi cập nhật
   `release-manifest.json` **từng feature** sang PASS và bật **đúng flag đó**;
   (c) cập nhật bản người đọc `release-manifest.md` (16 → 21 feature).
