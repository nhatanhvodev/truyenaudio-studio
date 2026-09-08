# Nghiên cứu security và observability v2

Ngày rà soát: 2026-09-07 (Asia/Ho_Chi_Minh)

Phạm vi: prompt nghiên cứu nâng cấp, `README.md`, source backend/frontend liên quan đến secrets, loopback, CSRF/origin, cloud consent/budget/rights, logging, audit/events, SQLite WAL và backup/restore, cùng các test liên quan. Báo cáo này chỉ nghiên cứu; không sửa source và không thực hiện cloud call có phí.

## Kết luận điều hành

Có hai lỗi cần chặn trước khi dùng cloud thật:

1. Luồng Gemini cho phép API key đi từ `localStorage` vào request body của backend, sau đó adapter đặt key trong URL query string. Đây là rò rỉ credential có khả năng dẫn đến mất quota/chi phí và phải xếp Critical.
2. Luồng keyring profile ghi với cặp `(service="truyenaudio-studio", username="provider-profile:<id>")` nhưng lưu tham chiếu `keyring:provider-profile:<id>`. Bộ đọc tách chuỗi đó thành `service="provider-profile:<id>"`, `username=""`, nên không đọc lại đúng secret. Cloud Qwen sẽ fail khi chạy profile được tạo qua API; đây là Critical về availability và cần test hồi quy trực tiếp.

Các guard Qwen và TTS có nhiều lớp kiểm tra consent, policy snapshot, rights, quota và budget. Tuy vậy route Gemini tạo adapter không có `CloudCallGuard`, mặc định ID giả cho consent/budget và vẫn gọi provider. Vì vậy guard hiện tại không bao phủ toàn bộ provider surface. Ứng dụng bind `127.0.0.1:8765`, nên đây là công cụ local single-user; loopback làm giảm remote exposure nhưng không phải authentication chống process khác trên cùng máy.

## Threat model và trust boundary hiện tại

| Boundary | Hiện trạng | Hệ quả |
|---|---|---|
| Remote network | `Settings` chỉ chấp nhận host `127.0.0.1`, port `8765`, worker concurrency `1` (`backend/app/settings/config.py:8-28`) | Giảm đáng kể remote attack surface; không giải quyết local malware, local user, browser extension hoặc process khác |
| Browser same-origin | Frontend gọi backend cùng origin; state-changing request thêm `Origin` và `X-CSRF-Token` (`frontend/src/shared/api.ts:45-76`) | Ngăn website khác trong browser gửi state-changing request bình thường; không phải auth cho local process |
| Local filesystem | SQLite, artifacts, logs và browser storage đều thuộc user profile; keyring chỉ giữ provider secret | Bất kỳ process có quyền user hoặc malware đọc profile có thể lấy dữ liệu local; dữ liệu chưa có mã hóa at rest riêng |
| External provider | Qwen có guard trong adapter; Gemini route tạo adapter riêng không gắn guard | Consent/budget/rights có thể bị bypass qua Gemini |
| Browser JavaScript | API key Gemini được giữ trong `localStorage` và gửi trực tiếp từ UI (`frontend/src/routes/router.tsx:278-348`, `535-570`) | XSS, extension hoặc mọi script cùng origin có thể đọc key; browser devtools/network history và request logs có thể chứa key |

Mô hình nên ghi rõ: “local-only” là giảm phạm vi đối tượng tấn công, không phải bằng chứng rằng API không cần authentication. Nếu sau này bỏ ràng buộc loopback, mọi endpoint đọc/ghi, SSE, diagnostics và profile phải có authentication/authorization riêng.

## Findings theo mức độ

### Critical

#### C-01 — Gemini API key nằm trong browser storage, request body và URL

Evidence:

- UI khởi tạo state từ `localStorage.getItem('gemini_api_key')` và ghi lại key ở dòng 339-348; input tiếp tục ghi mỗi lần thay đổi ở dòng 543-548 (`frontend/src/routes/router.tsx:278-348`, `535-570`).
- `GeminiTranslationRequest` nhận `apiKey` và route lấy từ body hoặc environment (`backend/app/api/translation.py:56-64`, `112-139`).
- Gemini adapter tạo endpoint `...?key={self.api_key}` cho từng model fallback và `list_models` cũng dùng `?key=` (`backend/app/providers/gemini_mt.py:34-52`, `154-218`).

OWASP khuyến cáo không lưu credential, token hoặc session identifier trong `localStorage` vì mọi JavaScript chạy cùng origin có thể đọc; một XSS có thể lấy toàn bộ dữ liệu trong storage ([OWASP HTML5 Security](https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html), truy cập 2026-09-07). Google Gemini API reference hiện yêu cầu gửi key trong header `x-goog-api-key`, và tài liệu Google AI Studio full-stack mô tả server-side secret để key không lộ trong browser ([Gemini API reference](https://ai.google.dev/api), [AI Studio full-stack](https://ai.google.dev/gemini-api/docs/aistudio-fullstack), truy cập 2026-09-07).

Impact: key có thể bị lấy bởi XSS/extension/process đọc browser profile; key trong query string có thể xuất hiện trong proxy/access log, URL instrumentation hoặc error telemetry. Gemini docs cũng thông báo việc chuyển sang authorization keys và việc từ chối standard key không hạn chế vào tháng 9/2026, nên implementation cần theo dõi migration này ([Using Gemini API keys](https://ai.google.dev/gemini-api/docs/api-key), truy cập 2026-09-07).

Recommendation:

- Xóa đường gửi `apiKey` từ UI và không ghi credential vào `localStorage`/`sessionStorage`; yêu cầu chọn `provider_profile_id`.
- Lưu Gemini key bằng cùng secret store server-side như Qwen; dùng header `x-goog-api-key` khi outbound call, không dùng query parameter.
- Khi migrate, xóa key cũ ở browser storage và yêu cầu rotate/revoke key đã từng nhập; thêm thông báo incident nếu key đã dùng trong môi trường chia sẻ.
- Dùng allowlist hostname cho provider endpoint; không cho `config.endpoint` tùy ý biến adapter thành SSRF proxy.

#### C-02 — Keyring reference mismatch làm profile tạo qua API không đọc được secret

Evidence:

- `_store_secret` gọi `keyring.set_password(KEYRING_SERVICE, secret_ref, value)` với service cố định `truyenaudio-studio`, username `provider-profile:<profile_id>`, rồi ghi DB là `keyring:provider-profile:<profile_id>` (`backend/app/api/cloud_profiles.py:16`, `80-90`).
- `Secret.from_ref` bỏ tiền tố `keyring:` rồi partition theo dấu `/`; với ref hiện tại, service trở thành `provider-profile:<id>` và username rỗng (`backend/app/providers/qwen_mt.py:26-43`).
- Keyring API định nghĩa `set_password(service, username, password)` và `get_password(service, username)` phải dùng cùng cặp service/username ([keyring README](https://github.com/jaraco/keyring/blob/main/README.rst), truy cập 2026-09-07).

Impact: profile trả `secretConfigured=true` nhưng Qwen runtime ném `QWEN_SECRET_MISSING`; UI có thể hiểu nhầm đã cấu hình thành công. Đây là lỗi availability và làm hỏng acceptance criterion “configure profile rồi gọi provider”.

Recommendation: chuẩn hóa một format có delimiter rõ ràng, ví dụ `keyring:truyenaudio-studio/provider-profile:<id>`, parse bằng `partition('/')`, và viết test round-trip với fake keyring. Không tự động fallback sang env khi profile đã chỉ rõ keyring ref; tránh gọi nhầm credential khác.

#### C-03 — Gemini translation bypass toàn bộ consent, rights và budget guard

Evidence:

- Route Gemini cho phép thiếu `cloudConsentId`/`budgetAuthorizationId` và tự tạo chuỗi `consent:gemini-user`, `budget:gemini-user` (`backend/app/api/translation.py:112-139`).
- `_gemini_workflow` tạo `GeminiMtAdapter` chỉ với API key/model/http client, không truyền `cloud_guard`, `project_id` hoặc `provider_profile_id` (`backend/app/api/translation.py:259-288`).
- Adapter chỉ đánh giá guard khi cả ba giá trị tồn tại (`if self.cloud_guard and self.project_id and self.provider_profile_id`), nên request mặc định không chạy `CloudCallGuard` (`backend/app/providers/gemini_mt.py:98-110`).
- Qwen guard mới thực hiện profile enabled, project, consent, policy snapshot/hash, rights, quota và budget (`backend/app/modules/compliance/cloud.py:46-119`).

Impact: caller có thể gửi nội dung lên Gemini mà không có consent evidence, rights grant third-party cloud hay budget authorization; fallback model và retry còn có thể nhân số request. Điều này vi phạm boundary local-first/cloud consent trong README và có rủi ro quyền tác giả lẫn chi phí.

Recommendation: tất cả cloud adapters đi qua một workflow/guard chung; Gemini phải dùng `ProviderProfile` + keyring, yêu cầu consent/budget ID thật, và fail closed khi thiếu. Xóa synthetic IDs. Acceptance phải chứng minh route Gemini bị 403/422 trước network call khi thiếu từng gate.

### High

#### H-01 — Consent nhận policy text từ client, không xác thực profile hoặc policy server-owned

`GrantCloudConsentRequest` nhận `provider_profile_id`, `policy_text`, hash và attestation trực tiếp (`backend/app/api/cloud_consents.py:20-24`). Service chỉ kiểm tra hash của text do caller gửi (`80-97`), lưu artifact và tạo consent (`99-110`); không kiểm tra provider profile tồn tại/đúng loại/enabled hay policy có phải snapshot được backend phát hành. `CloudCallGuard` chỉ yêu cầu artifact READY và so sánh hash nếu `profile.config_json.policy_sha256` có giá trị (`backend/app/modules/compliance/cloud.py:69-76`).

Impact: trong local single-user flow đây là self-attestation có chủ đích, nhưng không đủ để gọi là “user accepted current provider policy”. Nếu UI hoặc deployment chuyển sang nhiều user/remote, caller có thể tạo consent cho profile bất kỳ và đóng dấu policy tùy ý.

Recommendation: server phát hành policy snapshot theo provider/profile/version; grant chỉ nhận snapshot ID + acknowledgement, kiểm tra profile tồn tại, provider kind, enabled và project binding. Hash phải bắt buộc đối chiếu với policy registry server-side. Ghi actor, timestamp, client version và revoke reason vào audit.

#### H-02 — Public rights gate cho phép grant không có evidence

`CreateGrant.evidence_id` là optional và service chỉ kiểm tra project match khi ID được cung cấp (`backend/app/modules/compliance/evidence.py:31-40`, `141-177`). `RightsGate` cho export public kiểm tra `rights_status == CLEARED` và các scope đang active, nhưng không bắt grant có `evidence_id` (`backend/app/modules/compliance/rights.py:62-100`). API tạo project cũng nhận `rights_status` từ request (`backend/app/api/projects.py:25-35`, `129-155`).

Impact: project có thể được tạo với trạng thái CLEARED rồi tạo đủ scope bằng grant không đính kèm tài liệu. README mô tả public export phải có rights evidence/publication gate riêng; nếu đó là invariant bắt buộc, gate hiện tại cho phép tự khai quyền mà không có bằng chứng.

Recommendation: phân biệt `PRIVATE_ARCHIVE` với public; public gate yêu cầu evidence tồn tại, artifact READY, hash kiểm tra được, còn hạn và phù hợp scope/territory. Nếu product cố ý cho self-attestation, phải đổi tên trạng thái và hiển thị cảnh báo “unverified”, không xem là rights cleared.

#### H-03 — Translator budget có thể tự authorize khi gọi service trực tiếp

`CloudCallGuard` chỉ bắt buộc `budget_authorization_id` cho TTS (`backend/app/modules/compliance/cloud.py:81-83`), còn nếu không có ID thì tự gọi `BudgetGuard.authorize(quote)` (`104-107`). Route Qwen hiện ép ID ở API (`backend/app/api/translation.py:89-108`), nhưng adapter/service surface vẫn cho phép auto-authorize. README lại mô tả cloud call cần budget authorization.

Impact: một route mới hoặc code path nội bộ có thể biến quote thành chi tiêu thật mà UI chưa có bước người dùng duyệt. Hard cap giúp giảm blast radius nhưng không thay thế explicit authorization.

Recommendation: policy rõ theo category: free/local không cần authorization; mọi cloud billable operation phải nhận authorization được tạo trước bởi UI/service và bind operation, provider, model, region, rate card, TTL. Nếu vẫn giữ auto-authorize cho một class, đặt tên `AUTO_AUTHORIZED_LOCAL_POLICY` và audit rõ.

#### H-04 — Diagnostics scrubber chưa bao phủ loại key thực tế và query URL

`SECRET_PATTERNS` chỉ nhận dạng `sk-...`, một số chuỗi có từ khóa bearer/token/api-key/secret và JWT (`backend/app/modules/diagnostics/logging.py:28-32`). Gemini key dạng `AIza...`, Qwen key dạng provider-specific và query string không được đảm bảo bắt; hơn nữa outbound httpx URL có key (`backend/app/providers/gemini_mt.py:167`, `211-218`). OWASP yêu cầu access token, password, database connection string, encryption key và primary secret không xuất hiện trực tiếp trong log ([OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html), [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html), truy cập 2026-09-07).

Điểm tốt: logger dùng allowlist JSONL fields và không ghi full source/translation; test redaction xác nhận secret mẫu và full text không lọt (`backend/app/modules/diagnostics/logging.py:88-115`, `backend/tests/diagnostics/test_redaction.py:11-75`).

Recommendation: loại bỏ key khỏi URL trước tiên; tập trung outbound request logging qua wrapper có URL sanitizer, header sanitizer và structured fields. Thêm canary cho `AIza`, `sk-`, Qwen/provider patterns, `Authorization`, query `key=`, `api_key=`, và kiểm thử log cả stream logger lẫn zip export. Log không nên chỉ dựa vào regex.

### Medium

#### M-01 — Loopback/CSRF không phải local authentication

CSRF token sinh một lần khi process start (`backend/app/settings/csrf.py:14-20`). Bootstrap cho phép request có host chính xác và `Origin` không có hoặc loopback; state-changing yêu cầu exact origin và token (`22-32`). Frontend giữ token trong module memory và retry bootstrap khi 403 (`frontend/src/shared/api.ts:1-2`, `66-95`). Test hiện chứng minh evil Origin/missing token bị 403 và request hợp lệ đi đến validation 422 (`backend/tests/api/test_csrf.py:11-36`).

Thiết kế phù hợp với browser CSRF nhưng bất kỳ local process nào cũng có thể GET bootstrap không có Origin, lấy token rồi gửi Origin loopback. Đây là giới hạn trust model cần ghi rõ. Nếu sau này chạy remote hoặc máy có nhiều user, cần authentication per-installation, secret handshake hoặc OS-bound access control; không chỉ mở host.

#### M-02 — Events và diagnostics endpoints thiếu authorization/data scope

`/api/events` trả event log theo sequence, gồm audit actor/action/entity ID, provider/model/operation và chi phí (`backend/app/api/events.py:19-44`, `86-127`). Không có project scope hay authentication. Diagnostics export đóng gói health, tối đa 30 log files và sample do caller chọn (`backend/app/modules/diagnostics/service.py:61-71`). Local-only làm giảm rủi ro remote, nhưng nếu bind sai hoặc mở CORS, đây là dữ liệu nhạy cảm và có thể lộ tên model, vận hành, đường dẫn/health và usage.

Recommendation: thêm scope project/owner khi có identity; endpoint diagnostics phải require explicit local-owner action, `Cache-Control: no-store`, giới hạn kích thước sample/log, và ghi audit export. Giữ redacted-by-default khi chia sẻ support bundle.

#### M-03 — Audit/event schema đủ theo dõi cơ bản nhưng thiếu security event và stream tail

`_sync_event_log` tổng hợp job/audit/usage thành durable sequence (`backend/app/api/events.py:49-83`), và test chứng minh resume không bị reorder khi `updated_at` thay đổi (`backend/tests/api/test_sse_resume.py:23-109`). Tuy nhiên security failures như CSRF reject, keyring failure, policy mismatch, rights denial, provider auth failure chưa có event chuẩn ở middleware/service boundary; stream hiện phát các event có sẵn rồi kết thúc thay vì giữ kết nối chờ event mới (`30-44`).

Recommendation: chuẩn hóa event taxonomy: `security.csrf_denied`, `secret.read_failed`, `cloud.consent_denied`, `rights.denied`, `budget.blocked`, `provider.request`, `provider.retry`, `provider.fallback`, `tts.chunk_failed`; mỗi event có correlation ID, project/job scope, outcome, reason code và redacted metadata. Nếu cần realtime, dùng polling cursor hoặc SSE tail có heartbeat/reconnect contract.

#### M-04 — Budget/quota thiếu bảo vệ rõ cho concurrency và idempotency

Budget tính tổng từ authorization/ledger (`backend/app/modules/budgets/guard.py:120-161`, `234-273`) rồi insert authorization mới; quota đọc ledger theo provider/model/region và tháng (`backend/app/modules/budgets/quota.py:23-57`, `77-99`). Không thấy unique constraint hoặc transaction lock bảo đảm hai request đồng thời không cùng vượt cap. Worker concurrency bị khóa 1, nhưng API vẫn có thể nhận request chồng lấn.

Recommendation: bind `operation_id` với unique authorization/ledger semantics; transaction có lock hoặc atomic reservation; test concurrent quote/authorize, retry sau timeout và provider billing unknown. Không coi worker concurrency 1 là đủ nếu nhiều process/client cùng gọi API.

#### M-05 — Backup chỉ sao chép SQLite, không đóng gói artifacts/model/keyring/log retention

`BackupService.create` dùng SQLite backup API, integrity check, SHA-256 và manifest (`backend/app/modules/storage/backup.py:108-160`); restore xác minh artifact pointers tồn tại và đúng hash (`165-228`). Đây là nền tảng tốt. SQLite mô tả Online Backup API tạo snapshot nhất quán của database live và tránh giữ read lock suốt quá trình ([SQLite Backup API](https://www.sqlite.org/backup.html), truy cập 2026-09-07). Repo bật WAL và foreign keys (`backend/app/db/base.py:46-60`); SQLite cảnh báo WAL có thêm `-wal`/`-shm`, checkpoint và có thể lớn vô hạn nếu checkpoint starvation ([SQLite WAL](https://www.sqlite.org/wal.html), truy cập 2026-09-07).

Backup hiện không sao chép file artifact, voice model/license snapshot, diagnostics logs hoặc keyring entry. Restore chỉ xác minh pointer; nếu artifact ở máy đích thiếu thì restore fail, còn keyring secret không đi cùng backup. Test có pointer restore, integrity, retention 7 bản và path escape (`backend/tests/storage/test_backup_restore.py:16-130`) nhưng chưa có crash/power-loss, WAL growth/checkpoint, quyền file, artifact bundle, secret rebind hoặc restore cross-machine.

Recommendation: manifest phải khai báo rõ DB-only hay full bundle. Với full bundle, tạo snapshot artifacts theo manifest/hash rồi verify trước khi commit; không export secret plaintext. Khi restore máy khác, trạng thái provider secret phải `REAUTH_REQUIRED`, không `secretConfigured` giả. Có health metric cho DB/WAL size, checkpoint age, free disk, backup age và last verified hash.

### Low

#### L-01 — `errorSummary` được alias nhưng không nằm trong JSONL allowlist

`DiagnosticsLogger._record` có alias `error_summary -> errorSummary`, nhưng `JSONL_FIELDS` không chứa `errorSummary` (`backend/app/modules/diagnostics/logging.py:12-26`, `97-115`). `job_error` nhận exception nhưng chỉ lưu error code (`47-80`). Điều này an toàn về data minimization nhưng làm giảm khả năng điều tra lỗi; `summarize_error` tồn tại nhưng không được nối vào record (`121-125`).

Recommendation: quyết định rõ giữ minimal error code hay thêm summary đã scrub/capped. Nếu thêm, chỉ cho phép typed reason từ provider, cấm response body/prompt/source, và test newline/log injection.

#### L-02 — Log file append không có rotation/size cap cục bộ

Logger tạo JSONL theo ngày (`backend/app/modules/diagnostics/logging.py:88-95`), còn export chỉ lấy tối đa 30 file (`backend/app/modules/diagnostics/service.py:61-70`). Không thấy giới hạn byte mỗi file hoặc retention job trong logger; disk health có báo free bytes (`service.py:134-136`) nhưng không chặn log flood. OWASP cảnh báo attacker có thể flood log để chiếm disk ([OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html), truy cập 2026-09-07).

Recommendation: giới hạn bytes/events mỗi ngày, rotation atomic, backpressure/drop policy có metric, và retention job fail-safe không xóa audit cần giữ.

## Điểm mạnh đã xác nhận

- Host/port/concurrency bị giới hạn bằng type và validator (`backend/app/settings/config.py:8-28`).
- CSRF dùng token ngẫu nhiên, so sánh constant-time và exact Origin/Host (`backend/app/settings/csrf.py:14-32`); custom header là pattern OWASP khuyến nghị cho synchronizer token ([OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html), truy cập 2026-09-07).
- Qwen adapter fail closed khi thiếu cloud guard/context/consent/budget, giới hạn source 30.000 ký tự, và tách `Secret.__repr__` thành redacted (`backend/app/providers/qwen_mt.py:19-43`, `82-147`).
- Cloud guard kiểm tra profile enabled, project, consent granted đúng profile, policy artifact READY/hash, rights AI/cloud, quota và rate card; TTS yêu cầu budget authorization (`backend/app/modules/compliance/cloud.py:46-119`).
- Rights evidence giới hạn 10 MB, basename an toàn, MIME/signature kiểm tra và SHA-256 (`backend/app/modules/compliance/evidence.py:16-17`, `74-127`, `189-217`).
- Diagnostics dùng allowlist fields, scrub stream/file/export và không đưa source/translation vào record (`backend/app/modules/diagnostics/logging.py:12-26`, `88-115`; `backend/app/modules/diagnostics/service.py:61-71`, `173-196`).
- Backup dùng snapshot API, integrity check, SHA-256 manifest, path containment và restore lock API+worker (`backend/app/modules/storage/backup.py:108-228`).

## Đánh giá test hiện tại

Đã chạy targeted suite:

```text
.venv\\Scripts\\python -m pytest backend/tests/api/test_csrf.py backend/tests/compliance/test_cloud_guard.py backend/tests/compliance/test_cloud_translation_guard.py backend/tests/storage/test_backup_restore.py backend/tests/api/test_diagnostics_recovery.py backend/tests/api/test_storage_api.py -q
25 passed in 10.25s
```

Coverage có giá trị nhưng chưa đủ cho các critical findings:

| Area | Đã có | Cần bổ sung |
|---|---|---|
| CSRF | exact evil origin, missing token, valid request  (`backend/tests/api/test_csrf.py:11-36`) | bootstrap Host sai, Origin `null`, mixed scheme/port, token rotation/restart, local-process trust boundary |
| Keyring | Không có test route profile → `Secret.from_ref` round-trip | fake keyring set/get cùng service+username; missing entry; rotate/delete; Windows backend behavior |
| Gemini | Có provider unit tests ở nơi khác nhưng không thấy test route guard | thiếu consent/budget/rights phải fail trước network; key không nằm URL/log; profile-based server secret |
| Cloud guard | policy/consent/rights/budget/quota cases (`backend/tests/compliance/test_cloud_guard.py`) | consent profile mismatch, client policy forgery, authorization race/idempotency, expiry/revocation |
| Redaction | allowlist, full text và `sk-` mẫu bị loại (`backend/tests/diagnostics/test_redaction.py:11-75`) | `AIza`, provider key variants, URL query/header, newline injection, zip export, log rotation |
| SSE/audit | durable cursor, audit/usage no secret detail (`backend/tests/api/test_sse_resume.py`) | security-denial events, project scope, long-lived tail/heartbeat, deleted entity behavior |
| Backup | snapshot/integrity/pointer/path/retention/locks (`backend/tests/storage/test_backup_restore.py`) | WAL growth/checkpoint, crash between rename/manifest, full artifact bundle, missing artifact, cross-machine reauth |

## Options cho security architecture

### Option A — Vá Critical, giữ local single-user

- Xóa Gemini key khỏi browser storage/body; đưa Gemini vào provider profile/keyring và guard chung.
- Sửa keyring ref, thêm test round-trip.
- Giữ loopback + in-memory CSRF; bổ sung redaction canary và denial events.
- Giữ DB-only backup nhưng ghi rõ giới hạn.

Ưu điểm: ít thay đổi, nhanh đưa cloud flow về đúng boundary. Nhược điểm: local process vẫn có thể lấy CSRF token; rights/consent vẫn là self-attestation nếu không nâng policy registry.

### Option B — Balanced / khuyến nghị có điều kiện

Option A cộng với server-owned policy snapshot, provider/profile validation, public rights evidence bắt buộc, explicit budget reservation cho mọi billable cloud call, event taxonomy/correlation ID, backup manifest phân biệt DB/full bundle và trạng thái `REAUTH_REQUIRED` cho secret.

Khuyến nghị cho mục tiêu hiện tại nếu app vẫn local single-user. Điều kiện: chấp nhận giữ OS user là trust anchor, chưa cần multi-user auth; trước khi bật public export hoặc cloud paid phải đạt toàn bộ C/H acceptance criteria.

### Option C — Long-term hardened local service

Option B cộng với per-installation authentication bound to OS credential/DPAPI hoặc local IPC, ACL filesystem, encrypted support bundle, signed policy/rate-card snapshots, append-only audit, atomic budget reservation, full artifact/model backup và formal threat-model review khi có remote access.

Ưu điểm: chịu được nhiều process/user và mở rộng deployment. Nhược điểm: migration/operational complexity lớn, có thể quá mức cho tool cá nhân.

| Tiêu chí | A | B (khuyến nghị có điều kiện) | C |
|---|---:|---:|---:|
| Complexity | Thấp | Vừa | Cao |
| Migration cost | Thấp | Vừa | Cao |
| Secret safety | Cao sau khi vá C-01/C-02 | Cao | Rất cao |
| Rights/budget assurance | Vừa | Cao | Rất cao |
| Local usability | Cao | Cao | Vừa |
| Remote/multi-user readiness | Thấp | Thấp-vừa | Cao |
| Time to implement | Ngắn | Trung bình | Dài |

## Security acceptance criteria đề xuất

### Secrets

- Không có provider API key trong `localStorage`, `sessionStorage`, URL, request body frontend hoặc error payload.
- Tạo profile rồi đọc lại secret bằng cùng service/username; test rotate, delete, missing backend và `KEYRING_UNAVAILABLE`.
- Outbound Gemini dùng `x-goog-api-key`; URL sanitizer và log canary chứng minh không có key trong JSONL, stream logger, diagnostics zip hay exception string.
- UI chỉ nhận `providerProfileId`, hiển thị masked `secretConfigured`; secret không bao giờ trả về API.

### Cloud consent / rights / budget

- Gemini, Qwen và TTS đều fail closed trước network nếu thiếu provider profile, consent, current policy, rights grant hoặc budget authorization tương ứng.
- Consent chỉ bind policy snapshot server-owned, profile đúng loại/enabled và project; revoke làm request kế tiếp bị từ chối.
- Public export có evidence READY/hash/expiry phù hợp từng scope; grant thiếu evidence không thể làm `RIGHTS_CLEARED`.
- Hai request đồng thời cho cùng `operation_id` không tạo double reservation/ledger; retry sau billing-unknown có trạng thái cần reconcile.

### Loopback / CSRF

- Sai Host, sai scheme/port, Origin `null`, Origin khác và token sai đều bị 403; GET bootstrap không làm thay đổi state.
- Tài liệu và health report nêu rõ loopback không bảo vệ trước process cùng OS user. Nếu bật remote, app phải từ chối startup thay vì chỉ dựa vào CSRF.

### Observability

- Mọi denial/retry/fallback/provider request có correlation ID, operation/job/project scope, reason code, latency và outcome; không có prompt/source/translation/API key.
- Diagnostics export mặc định redacted, `no-store`, giới hạn kích thước; test zip không chứa secrets và không có log injection.
- Health snapshot báo DB integrity, journal mode, WAL size/age, free disk, worker heartbeat, last verified backup và artifact pointer status.

### Backup / recovery

- Backup verify được SHA-256 + `PRAGMA integrity_check`; restore giữ pre-restore snapshot và yêu cầu API/worker locks.
- Full restore hoặc DB-only được ghi rõ trong manifest; DB-only không được báo là đã khôi phục artifacts/models/secrets.
- Missing artifact, checksum mismatch, malformed manifest và path traversal đều fail closed; cross-machine restore đặt provider credentials ở `REAUTH_REQUIRED`.

## Gợi ý thứ tự xử lý cho coding phase

1. C-01/C-02/C-03: loại bỏ browser secret, sửa keyring ref, đưa Gemini vào guarded provider profile.
2. H-04 và test canary: cấm key trong URL, mở rộng sanitizer và kiểm tra diagnostics export.
3. H-01/H-02/H-03: khóa policy/rights/budget invariants, thêm audit denial và idempotency.
4. M-01/M-02/M-03: ghi rõ local trust boundary, scope events/diagnostics và taxonomy observability.
5. M-05/L-02: hoàn thiện WAL/backup health, artifact bundle hoặc tuyên bố DB-only, retention/rotation.

Không nên khóa target architecture ở báo cáo này ngoài điều kiện: mọi provider cloud phải qua một guard contract duy nhất và mọi credential phải server-side. Việc chọn A/B/C là product/operational decision; B phù hợp nhất nếu tiếp tục local single-user và chỉ bật cloud sau khi đạt acceptance criteria.

