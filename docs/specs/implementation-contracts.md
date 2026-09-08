# Hợp đồng triển khai bổ sung — baseline C/C/C/C

Ngày 08/09/2026. Đây là thiết kế mục tiêu, không phải mô tả API đã triển khai. Đọc cùng [master spec](master-spec.md), [ADR-0001](../architecture/adr/0001-locked-subdecisions.md) và [plan](../plans/implementation-plan.md). Các quy tắc dưới đây thay thế câu “chốt trong implementation” của bản cũ. Thay đổi boundary, schema công khai hoặc chính sách cần cập nhật tài liệu và fixture trước khi đổi mã.

## C01 — Quy ước kiểu, snapshot và tương thích

- JSON dùng camelCase; Python dùng snake_case qua alias Pydantic. ID là chuỗi opaque theo `new_id` hiện hữu, không đổi định dạng ID cũ. Thời gian UTC ISO-8601; revision là số nguyên dương; SHA-256 là 64 ký tự hex. Trường nullable phải khai báo rõ; thiếu trường bắt buộc trả 422.
- Snapshot có `schemaVersion: 1`, `id: ID`, `createdAt: timestamp`, `hash: SHA256`. Hash dùng JSON UTF-8 canonical: khóa sort, không whitespace, cấm NaN/Infinity, không biến đổi Unicode; loại `id`, `createdAt`, `hash` khỏi phần băm. Mảng giữ thứ tự, tiền dùng chuỗi decimal. Không băm secret/ref keyring vào payload công khai.
- Bản đã dispatch/approved bất biến. Sửa tạo revision mới; không cập nhật snapshot mà job đang dùng. Parser revision cũ có fixture tương thích; phiên bản chưa hỗ trợ trả `UNSUPPORTED_SCHEMA`, không diễn giải theo schema mới.
- Phân trang cursor opaque theo cặp sort-key/ID ổn định, `limit` mặc định 25, tối đa 100; response `{items: T[], nextCursor: string|null}`. Danh sách chỉ metadata, không toàn văn truyện. Cursor lỗi trả 422; cursor event hết retention trả 410 và yêu cầu tải snapshot.

## C02 — Contract provider và thực thi

| Kiểu | Trường bắt buộc ngoài envelope C01 |
|---|---|
| ModelSnapshot | `providerId: string`, `modelId: string`, `region: string|null`, `apiKind: chat|nativeMt`, `contextTokens: int|null`, `maxOutputTokens: int|null`, `languages: string[]`, `capabilities: {stream,structured,translation}` với mỗi giá trị `supported|unsupported|unknown`, `availability: available|unavailable|unknown`, `pricing: {class: free|paid|unknown,currency:string|null,inputPerMillion:decimal|null,outputPerMillion:decimal|null}`, `license: string|null`, `sourceUrl: string`, `fetchedAt: timestamp`, `expiresAt: timestamp`, `benchmarkRef: ID|null` |
| PromptEnvelope | `builderVersion: string`, `sourceLanguage: string`, `targetLanguage: string`, `styleRevisionId: ID`, `expectedSegmentIds: ID[]`, `segments: {id:ID,text:string}[]`, `systemPolicy: string`, `userInstruction: string`, `contextSnapshotId: ID`, `estimatedInputTokens: int`, `reservedOutputTokens: int` |
| ContextSnapshot | `projectId: ID`, `chapterId: ID`, `sourceRevisionId: ID`, `glossaryRevisionIds: ID[]`, `memoryRevisionIds: ID[]`, `characterRevisionIds: ID[]`, `tmRevisionIds: ID[]`, `selected: {id:ID,reason:string,tokens:int}[]`, `excluded: {id:ID,reason:string}[]`, `tokenBudget: int`, `indexRevisionId: ID|null` |
| ExecutionPlan | `projectId: ID`, `chapterId: ID`, `sourceRevisionId: ID`, `stage: analyze|translate|review|polish|repair|summarize|preview|synthesize|master|export`, `profileId: ID|null`, `profileRevision: int|null`, `modelSnapshotId: ID|null`, `promptEnvelopeId: ID|null`, `contextSnapshotId: ID|null`, `inputRevisionIds: ID[]`, `rightsGrantId: ID|null`, `consentId: ID|null`, `budgetAuthorizationId: ID|null`, `quoteId: ID|null`, `fallbackProfileIds: ID[]`, `idempotencyKey: string` |
| AttemptResult | `attemptId: ID`, `planId: ID`, `requestedModel: string|null`, `actualModel: string|null`, `providerRequestId: string|null`, `status: succeeded|failed|billingUnknown`, `finishReason: string|null`, `usage: {inputTokens:int|null,outputTokens:int|null,cost:decimal|null,currency:string|null,confidence: actual|estimated|unknown}`, `outputRevisionIds: ID[]`, `error: ProviderError|null` |
| ProviderError | `code: string`, `message: string`, `retryable: bool`, `billingState: notSent|known|unknown`, `retryAfterMs: int|null`, `correlationId: ID`, `details: object` đã lọc allowlist |

Không cloud-dispatch khi các ID quyền/consent/budget/profile/model bắt buộc cho cloud bị null. Null phục vụ local/fake hoặc stage không gọi provider, không miễn guard. Actual model chưa được provider xác nhận phải null, không tự chép requested model thành actual.

Boundary logic (chữ ký thiết kế, không phải mã implementation):

| Interface | Input → output |
|---|---|
| CredentialStore | `set(profileId, secret) → configured`; `resolve(profileId) → SecretHandle`; `delete(profileId) → configured=false`; handle chỉ sống backend, cấm serialize |
| ProviderRegistry | `resolve(plan, authorization) → ProviderAdapter`; validate profile revision/model capability trước khi tạo adapter |
| Adapter.listModels | `(profileId, cursor?, etag?) → {items: ModelSnapshot[], nextCursor, etag, notModified}` |
| Adapter.validateCredential / healthCheck | `(profileId) → {state: ready|invalid|unavailable|unknown, checkedAt, error: ProviderError|null}` |
| Adapter.estimateCost | `(modelSnapshot, envelope) → {amount: decimal|null,currency,confidence}` |
| Adapter.translate | `(plan, envelope, cancellationToken) → {segments:{id,text}[], result:AttemptResult}` |
| Adapter.stream | cùng input → luồng `{segmentId,offset:int,text:string}` và đúng một terminal result; chỉ expose nếu capability supported |
| PromptBuilder / ContextEngine | `build(inputRevisionIds, styleRevisionId, modelSnapshot) → PromptEnvelope`; `select(projectId, chapterOrdinal, revisions, tokenBudget) → ContextSnapshot` |

Discovery TTL mặc định 24 giờ, refresh thủ công có cooldown 60 giây/profile; giữ snapshot tốt cuối khi lỗi, hiển thị stale; không gọi discovery trong translation request. Metadata curated phải có URL/ngày; không coi free tier, free model và quota tài khoản là một khái niệm. Capability unknown chặn thao tác yêu cầu capability đó.

## C03 — Lưu trữ và quan hệ

Tái sử dụng `Project`, `Chapter`, `SourceRevision`, `SourceSegment`, `TranslationRun`, `TranslationSegment`, `GlossaryEntry`, `StoryMemoryEntry`, `ProviderProfile`, `VoicePlan`, `VoiceRole`, `SpeechSegment`, `Artifact`, `Job`, `JobAttempt`, `BudgetAuthorization`, `UsageLedger`, `EventLog`, `Export`. Không tạo Story/TranslationJob/AudioArtifact trùng domain hiện hữu. Tên dưới là bảng mới dự kiến hoặc phần mở rộng, phải được ánh xạ trong migration additive.

| Bảng/phần mở rộng | Khóa, dữ liệu và ràng buộc |
|---|---|
| `execution_snapshots` | PK id; kind `model|prompt|context|plan`; schema_version, hash, payload_json, created_at; UNIQUE(kind,hash); payload không chứa secret. Snapshot plan tham chiếu ID domain tồn tại trước enqueue |
| Job / JobAttempt / UsageLedger | Job thêm plan_id FK snapshot; UNIQUE(project_id,kind,idempotency_key); attempt lưu requested/actual model, request_id, billing_state; ledger UNIQUE(attempt_id,entry_kind) cho reservation/settlement/release, không nhân charge khi replay |
| ProviderProfile | revision int, enabled bool; secret_ref chỉ backend. Canonical keyring service `truyenaudio-studio`, username `provider-profile:{profileId}`; strip prefix `keyring:` khi resolve ref cũ. Config allowlist theo provider, cấm key/token trong config_json |
| `translation_styles` | id, project_id nullable, revision, name, source_language, target_language, genre, tone, user_instruction, prompt_template_version; UNIQUE(id,revision); global preset copy-on-write khi sửa theo project |
| `translation_memory` | id, project_id, source_hash, source_text, target_text, source_language, target_language, style_revision_id, glossary_hash, approved_translation_revision_id; exact reuse chỉ khi toàn fingerprint khớp và revision chưa stale |
| `characters` / `character_revisions` | character id/project_id; revision id/character_id/revision, canonical_name, aliases_json, entity_type, role nullable, gender nullable, evidence_source_revision_id, evidence_segment_ids, status candidate/approved/stale; UNIQUE(character_id,revision) |
| `character_relationships` | id, project_id, from_character_id, to_character_id, from_ordinal, to_ordinal nullable, addressing_json, evidence_revision_id, status; from_ordinal ≤ to_ordinal nếu có; FK cùng project, có hướng; không suy diễn giới tính thiếu dữ liệu |
| StoryMemoryEntry / GlossaryEntry | thêm revision/evidence/scope/approval nếu chưa có tương đương; glossary có locked, forbidden forms, description. Summary chỉ lấy chương ordinal nhỏ hơn chương đang dịch; sửa upstream đánh dấu phạm vi ảnh hưởng stale |
| `workspace_drafts` | UNIQUE(project_id,chapter_id,base_revision_id); content_json theo segmentId, revision, updated_at; compare-and-swap expectedRevision; dữ liệu draft không thay approved translation |
| `workspace_layouts` | UNIQUE(project_id); version=1, openChapterIds, activeChapterId, paneAssignments, splitRatio, inspectorTab; ID không còn tồn tại bị loại khi restore, không tự tải source text cho toàn bộ tab |
| `job_projections` | PK job_id FK Job; project_id/status/stage/done/total/last_sequence_id; rebuild từ EventLog; index(project_id,status,job_id) |
| EventLog | sequence tăng đơn điệu, index(project_id,sequence), index(job_id,sequence); event và projection commit cùng transaction; không ghi raw source/secret vào audit |
| `memory_indexes` | id, project_id, snapshot_hash, embedding_model_id, embedding_revision, dimension, license, status ready/stale/failed, artifact_id; checksum artifact; không index dữ liệu project khác |

FK dùng RESTRICT đối với dữ liệu audit/revision; không cascade-delete lịch sử. Migration backfill giá trị chưa biết thành null/unknown, không giả lập actual model/chi phí. Đánh index cho chapter(project_id,ordinal,id), segment(parent_revision_id,ordinal,id) bằng cột tương đương hiện có; không tạo index trùng. Recovery orphan artifact chỉ báo cáo/đưa vào danh sách dọn, không tự xóa.

## C04 — API mục tiêu và lỗi

Giữ API hiện hữu qua compatibility wrapper trong giai đoạn migration. `/api/cloud-profiles` hiện hữu là route chính; không tạo kho profile độc lập ở `/api/provider-profiles`. Adapter tương thích route cũ gọi cùng service/guard với route mới. Chỉ xóa wrapper sau milestone dọn dẹp.

| Method/path | Request → response / điều kiện |
|---|---|
| GET/POST `/api/cloud-profiles` | GET `{profiles: ProfileView[]}`; POST `{providerKind,adapterName,displayName,model?,region?,config,enabled,secret?}` → 201 ProfileView. View gồm id/revision/providerKind/adapterName/displayName/model/region/enabled/secretConfigured/status, tuyệt đối không secret/ref |
| PATCH `/api/cloud-profiles/{id}` | `{expectedRevision,displayName?,model?,region?,config?,enabled?}` → ProfileView; conflict 409 |
| PUT/DELETE `/api/cloud-profiles/{id}/credential` | PUT `{secret}` → `{secretConfigured:true}`; DELETE → `{secretConfigured:false}`; rotate vô hiệu authorization chưa dispatch gắn revision cũ |
| POST `/api/cloud-profiles/{id}/validate` | body rỗng → credential health C02; tác vụ validation không được quảng cáo là miễn phí |
| GET `/api/models` | `profileId,cursor?,limit?,refresh?` → trang ModelSnapshot + `stale:bool`; filter capability/language/pricing bằng query; lỗi discovery không biến thành items rỗng thành công |
| POST `/api/translation/quote` | `{chapterId,sourceRevisionId,profileId,modelSnapshotId,styleRevisionId,stage,inputRevisionIds}` → `{quoteId,planHash,expiresAt,estimatedAmount:decimal|null,currency,ceilingRequired:true,blockers:string[]}` |
| POST `/api/translation/jobs` | `{quoteId,consentId,budgetAuthorizationId,rightsGrantId,idempotencyKey}` → 202 `{jobId,planId,status}`; cùng key/cùng payload trả job cũ, khác payload 409; quote hết hạn/plan đổi chặn dispatch |
| GET `/api/jobs/{id}` | → `{id,kind,status,stage,done,total,revision,billingState,error,resultRevisionIds}`; total chưa biết là null |
| POST `/api/jobs/{id}/cancel` | `{expectedRevision}` → 202 JobView; terminal thì trả trạng thái hiện tại; không promise hoàn tiền |
| GET `/api/events` | `after?,projectId?,jobId?` hoặc Last-Event-ID → SSE C05; header ưu tiên nếu có cả hai |
| GET/PUT `/api/chapters/{id}/draft` | GET DraftView; PUT `{baseRevisionId,expectedRevision,segments:{id,text}[]}` → `{revision,baseRevisionId,segments}`; 409 trả currentRevision, không đè draft |
| POST `/api/chapters/{id}/translation-approval` | `{translationRevisionId,expectedHash}` → `{approvedRevisionId}`; 409 khi stale, 422 khi critical QA chưa giải quyết |
| GET/PUT `/api/projects/{id}/workspace` | C03 Layout; PUT `{expectedRevision,layout}` → `{revision,layout}`; 409 giữ layout hiện tại |
| GET/POST `/api/projects/{id}/styles` | trang StyleView / tạo style C03 → 201 StyleView; PATCH `/{styleId}` với expectedRevision tạo revision mới |
| GET/POST `/api/projects/{id}/characters` | trang CharacterView / tạo candidate theo C03; PATCH `/{characterId}` với expectedRevision tạo revision; POST `/{characterId}/approve` với revisionId/evidence → approved |
| GET/POST `/api/projects/{id}/relationships` | trang relationship / tạo quan hệ C03; PATCH `/{relationshipId}` với expectedRevision; cùng project và scope hợp lệ |
| GET/POST `/api/projects/{id}/memory` | trang summary/fact / tạo candidate; POST `/{memoryId}/approve` với expectedRevision/evidence; không tự approve output LLM |
| GET `/api/voices` | → `{items: VoiceDescriptor[],manifestHash}`; descriptor id/name/locale/engineRevision/license/sampleArtifactId?/capabilities |
| POST `/api/voices/previews` | `{voiceId,text,settings,manifestHash,idempotencyKey}` → 202 `{jobId}`; text 1–420 ký tự Unicode; không hứa duration cố định |
| POST `/api/audio/jobs` | `{approvedTranslationRevisionId,voicePlanId,settings,expectedHash,idempotencyKey}` → 202 `{jobId}`; local TTS không cần cloud consent, cloud voice dùng guard C06 |
| GET/HEAD `/api/artifacts/{id}` | kiểm quyền local và root confinement; 200/206/416, Content-Length/Content-Range/ETag, Range bytes; không trả filesystem path |

CRUD glossary, rights/consent/budget, import/export và storage giữ route hiện hữu; sửa service theo invariant mới, không buộc đổi endpoint. Contract test phải khóa payload hiện có trước refactor. Không có secret trong response/error/telemetry. Lỗi công khai `{code,message,retryable,correlationId,details}`; 400 input không hợp lệ, 401/403 auth/quyền, 404 ID, 409 revision/idempotency, 410 cursor hết hạn, 422 schema/QA, 429 throttle, 503 dependency/keyring unavailable. Provider HTTP không được chuyển nguyên body ra browser.

## C05 — Job, streaming và revision

- Trạng thái công khai: queued → running → succeeded/failed/cancelRequested; cancelRequested → cancelled hoặc succeeded nếu đã commit kết quả trước cancel. Retry an toàn đưa về queued có nextRunAt; recovery giữ cùng jobId và sinh attempt mới. Billing unknown là thuộc tính attempt/job, không coi là failed-retryable.
- Commit snapshot/claim/attempt trước network, đóng transaction; ghi terminal/usage/artifact bằng transaction ngắn sau network. Heartbeat 5 giây, lease 30 giây; expired lease có attempt đã gửi mà chưa settle → reconciliation/billingUnknown, không dispatch lại tự động. Check cancel trước mỗi request, sau response và trước publish artifact.
- Event envelope `{sequenceId,projectId,jobId,type,createdAt,payload}`. Type: job.transition, job.progress, translation.delta, translation.segmentReady, artifact.ready, revision.stale. Payload delta `{attemptId,segmentId,offset,text}`; UI apply theo attempt/offset, bỏ duplicate, gap thì fetch draft snapshot. Delta draft checkpoint tối đa mỗi 1 giây; restart có thể mất tối đa đoạn draft chưa checkpoint, không mất approved output.
- SSE chỉ gửi draft từ provider có streaming; provider không hỗ trợ dùng progress/segmentReady, không giả delta. Không ghi từng token vào audit; coalesce tối đa 4 frame/giây/job, payload ≤16 KiB. UI tối đa 1.000 event metadata trong RAM, nội dung editor chỉ cho tab đang active. Audit giữ transition/provenance; draft stream retention 24 giờ sau terminal, metadata 30 ngày trong feed, audit bền vững theo storage policy. Cursor cũ tải snapshot; không rerun provider để nối stream.
- Edited/approved revision làm audio/export liên quan stale; output cũ vẫn đọc/nghe được với nhãn stale nhưng không approve/export mới như hiện hành. Critical QA phải giải quyết hoặc chuyển policy bằng ADR; không có nút bypass mặc định.

## C06 — Guard, chi phí và retry

Credential chỉ được nhập qua request cấu hình loopback được bảo vệ origin/CSRF; clear input sau submit, không persist browser và không log body. Request dịch chỉ gửi profile ID, không secret. Do đó network provisioning có thể chứa secret transient; tiêu chí “không có key trong mọi network request” của bản cũ không áp dụng cho chính thao tác nhập key.

Mỗi cloud stage kiểm theo thứ tự: revision → profile/credential → endpoint/region/capability → rights/cloud consent → quote/budget reservation. Quote TTL 10 phút. Hard ceiling mặc định kế thừa 500.000 VND trong ADR, không tự sinh authorization; người dùng chọn ceiling từng stage trong giới hạn còn lại. Giá unknown phải có reservation tính được bằng rate card thủ công có nguồn/ngày hoặc dispatch bị chặn; không coi unknown bằng 0. Không mở review/polish khi chỉ approve translate. Mặc định Balanced = translate + deterministic QA; Quality/Maximum chỉ bật theo chapter/batch và quote từng stage. Bốn profile được định nghĩa ở bảng dưới; deterministic QA luôn chạy, kể cả Economy. Candidate analysis/summary không tự trở thành approved fact.

Retry tối đa 3 attempt tổng cho lỗi chắc chắn chưa tính tiền/được provider xác nhận an toàn, delay base 1 giây exponential + full jitter, tôn trọng Retry-After; nếu vượt deadline job thì dừng chờ thao tác người dùng. Không retry/fallback timeout sau gửi khi chưa reconcile billing. Circuit breaker persist theo provider/profile/model: 5 lỗi transient liên tiếp mở 60 giây, sau đó 1 half-open probe; auth/consent/budget lỗi chặn profile/dispatch, không tính là transient. Fallback explicit allowlist phải resolve snapshot và quote/guard lại, không chuyển free sang paid tự động. Không hứa exactly-once network; bảo đảm không tự gửi lại attempt bất định và không nhân kết quả/ledger đã commit.

| Profile | Các stage model và điều kiện chuyển |
|---|---|
| Economy | Translate với glossary bắt buộc và context tối thiểu; QA deterministic; review chỉ người dùng |
| Balanced (mặc định) | Translate với approved context/TM đầy đủ trong token budget; QA deterministic; không có review/polish model tự động |
| Quality (opt-in) | Translate → Review → Polish các đoạn người dùng chọn; quote/approve riêng mỗi stage, có thể dừng sau review |
| Maximum (opt-in) | Analyze nguồn thành candidate constraints → Translate → Review (critique) → Polish (rewrite có chọn lọc); mỗi bước snapshot/quote/approve; rewrite tạo proposal so với bản trước, người dùng có thể bỏ nếu không cải thiện |

Stage summarize tạo candidate memory từ chương đã approved, có quote/consent riêng nếu dùng cloud. Source style presets gồm sát nghĩa, tự nhiên, văn học, web novel, light novel, cổ trang, hiện đại, fantasy và wuxia/xianxia; preset là cấu hình versioned, không phải badge chất lượng. Genre/tone tùy chỉnh vẫn phải giữ output schema và glossary locked.

## C07 — UI và design system mục tiêu

- Colors light: background #F7F8FA, surface #FFFFFF, border #6B7280, text #172033, textMuted #4B5563, accent #1D4ED8, success #166534, warning #92400E, danger #B91C1C, focus #1D4ED8. Dark: background #111827, surface #1F2937, border #9CA3AF, text #F9FAFB, textMuted #D1D5DB, accent/focus #93C5FD, success #86EFAC, warning #FCD34D, danger #FCA5A5. Đây là token thiết kế cần kiểm contrast, không phải bằng chứng đã render đạt WCAG.
- Font UI Segoe UI/system sans 14px, heading 20/24px semibold; reading 18px/line-height 1.7 với fallback Noto Sans CJK SC/Microsoft YaHei/sans-serif; mono Consolas 13px. Không tự download font. Spacing 4/8/12/16/24/32px; focus ring 2px + offset 2px; reduced-motion bỏ animation không cần thiết.
- Button/Input/Select/Combobox/Modal/Drawer/Tooltip/Toast/Tabs/Table/Tree/Progress/AudioPlayer có default/hover/focus/disabled/loading/error khi phù hợp. Button destructive phải nêu đối tượng; combobox phân biệt focus/selection; modal trap và trả focus; toast không là nơi duy nhất giữ lỗi; table/list paging phía server; tree chỉ dùng hierarchy thật.
- Desktop ≥1024px: navigator 240px, inspector 320px có thể thu gọn, editor phần còn lại; split ratio clamp 25–75%. Tối đa 8 tab đang mở mặc định; tab thứ 9 yêu cầu lưu và chọn tab đóng, hủy thì không mở thêm. Tab inactive unmount editor, giữ draft trên server; network save lỗi không tự evict draft bẩn. Đóng/reorder/dock/undock/reset layout đều có thao tác keyboard tương đương. Dưới 1024px chuyển source/target tabs, inspector drawer; kiểm cả 320px.
- Settings gồm AI Providers (credential/status), Models (catalog/filter), Translation (ngôn ngữ/genre/style/custom prompt/quality), TTS (engine/voice/preview/settings), Storage (dung lượng/backup/restore/retention), Appearance (theme/font/density), Advanced (diagnostics/feature flags). Storage delete/restore hiển thị đích và xác nhận riêng, không tự xóa khi đổi retention.

## C08 — Audio, export và vector

VieNeu manifest khóa package/model/voice revision/license/locale/native sample rate; native rate lấy từ probe, không ép giả định mọi model đều 48 kHz. Master resample 44,1 kHz nếu contract app đích yêu cầu. Synth cache = hash(text,translationRevision,voiceRevision,engineRevision,settings); write tạm → probe/checksum → atomic ready. Master chỉ đọc segment ready theo ordinal; chunk master theo tối đa 50 segment hoặc 10 phút (điều kiện đến trước), manifest part giữ checksum/duration/input hashes, final concat từ parts. Rerender chỉ invalidates part chứa segment đổi. SRT dựa duration thật; không suy ra từ số ký tự.

Một narrator mặc định; multi-voice phải review mapping role/character thủ công. Một player toàn app; playing khác selected; A/B preview dùng cùng text/settings hỗ trợ và giữ mốc nghe. Speed/pitch/style chỉ hiện nếu manifest probe hỗ trợ, playback speed phải ghi rõ chỉ ảnh hưởng nghe. Export cần translation/audio approval hiện hành, metadata review, checksum/provenance/rights scope; archive riêng không được gắn quyền public. Chỉ xuất file để upload thủ công.

Vector Phase 2 là index tùy chọn, không chặn release Phase 1. Baseline dùng artifact embedding đã được người dùng nhập, không tự chạy local LLM hay cloud embedding. Format `{schemaVersion:1,projectId,modelId,modelRevision,dimension,license,sourceSnapshotHash,items:[{memoryRevisionId,vector:number[]}]}`; dimension 1–4096, vector finite/nonzero, cùng dimension, tối đa 10.000 item và file 200 MiB. Query embedding phải cùng model/revision/dimension và kèm queryHash đúng câu truy vấn; thiếu/sai thì structured fallback. SQLite lưu metadata; artifact float32 immutable, cosine exact top-8 bằng adapter bounded, tie-break memoryRevisionId. Không thêm vector DB ở baseline. Không có query embedding hợp lệ thì không bật retrieval; UI phải nói rõ chưa cấu hình, không tự phát sinh mạng. Thay đổi cách tạo embedding cần ADR riêng. Stale hoặc evidence chưa approved bị loại trước retrieval; vector không override glossary/fact.
