# Research v2 — Hiệu năng và độ tin cậy cho truyện dài

Ngày nghiên cứu: 2026-09-07  
Phạm vi: chỉ nghiên cứu/spec. Không thay đổi source, schema hay runtime.

## 1. Phạm vi và cách đọc code

Đây là ghi chú bổ sung cho prompt nghiên cứu tổng thể. Prompt là yêu cầu công việc; các kết luận dưới đây là kết quả kiểm tra code và tài liệu kỹ thuật. Tôi không khóa quyết định sản phẩm thay cho chủ dự án.

Codebase hiện tại là ứng dụng FastAPI + React/Vite + SQLite WAL, chạy local loopback. README ghi worker concurrency cố định là `1` và dữ liệu mặc định ở `data`; `backend/app/settings/config.py` dùng `Literal[1]` cho concurrency. Dữ liệu văn bản, trạng thái và chỉ mục nằm trong SQLite; artifact/audio nằm trên filesystem.

Các bằng chứng chính đã đọc:

| Khu vực | Bằng chứng hiện tại | Hệ quả cần tính khi scale |
|---|---|---|
| Claim job | `runner.py:137-198` dùng `BEGIN IMMEDIATE`, chọn một job theo `status/next_run_at/priority`, rồi tạo attempt | Phù hợp single writer nhưng mỗi claim tranh chấp writer lock; cần đo p95 và `SQLITE_BUSY` |
| Lease | `runner.py:213-269`, hằng số `LEASE_SECONDS=60`, heartbeat mỗi 15 giây | Job dài vẫn an toàn nếu heartbeat chạy; mất worker được recovery sau ngưỡng 90 giây |
| Retry/fallback | `retry.py:7-44` tối đa 3 attempt, delay 2/10/30 giây; rate limit tối đa 600 giây | Có retry cơ bản; chưa có circuit breaker/provider health hoặc hàng đợi delay riêng |
| Cloud request safety | `runner.py:328-386`, attempt đã đánh dấu provider request sẽ chuyển `BILLING_UNKNOWN` | Đúng hướng an toàn chi phí; không tự retry khi kết quả thanh toán không rõ |
| Recovery/checkpoint | `recovery.py:247-320` kiểm tra cache, commit session trước network, đánh dấu provider sent, ghi artifact atomically rồi thêm manifest | Có thể resume theo segment khi handler dùng `RecoveryJobContext`; cần chứng minh crash ở từng điểm |
| Handler | `worker.py:192-198` mặc định gắn `_unconfigured_recovery_handler` cho mọi `JobKind`, hàm này ném `HandlerUnavailable` | Worker chạy được nhưng job thật sẽ fail closed nếu chưa wiring handler; đây là trạng thái triển khai, không phải benchmark throughput |
| Dịch | `workflow.py:124-207` tải tất cả source segments, gọi provider tuần tự từng segment, chỉ commit cuối workflow | Network call nằm trong một workflow/session dài; nếu transaction đã flush có thể giữ write lock trong lúc chờ provider |
| TTS | `speech/workflow.py:300-405` lặp tuần tự mọi speech segment, cache artifact theo hash, sau đó master và SRT mới commit | Resume tốt hơn nếu cache hit, nhưng HTTP render hiện tại không phải job checkpoint đầy đủ; lỗi cuối có thể để lại file đã ghi nhưng chưa có row |
| SSE | `events.py:30-44` mở session/engine cho stream; `events.py:49-57` gọi `_sync_event_log`, load toàn bộ `EventLog`, lọc cursor sau khi load | Chi phí kết nối và replay tăng theo toàn bộ lịch sử; cần query theo cursor và scope job/project |
| SSE payload | `events.py:59-84` mỗi lần sync quét toàn bộ Job/Audit/Usage; `events.py:86-128` gọi `session.get` theo từng event | Có nguy cơ N+1 và event log backfill lặp lại ở reconnect |
| Project list | `api/projects.py:66-107` đọc toàn bộ chapters của từng project rồi chỉ trả 30 chapter; `:110-139` GET một project đọc toàn bộ chapters | 1k/10k chapter vẫn tạo list Python lớn và query thừa; list screen không nên là API full snapshot |
| Chapter page | `projects/queries.py:57-78` có keyset cursor và limit 25–100, nhưng `:88-124` gọi `_chapter_summary` từng row; `:126-131` tải toàn bộ jobs của từng chapter | Pagination đúng hướng nhưng summary có N+1 và `_progress` giữ tất cả Job objects trong RAM |
| DB | `db/base.py:46-59` bật WAL, foreign keys và `busy_timeout=5000`; models có index job/artifact/chapter, nhưng `StoryMemoryEntry` và nhiều quan hệ segment chưa có index chuyên dụng | WAL giúp đọc/ghi đồng thời trên cùng host, nhưng chỉ có một writer và checkpoint vẫn là phần phải vận hành |
| Audio delivery | `api/audio.py` chỉ có configure/render/approve/status; không thấy route GET artifact audio có `Range` | Audio player/range playback cần contract riêng trước khi đánh giá seek/buffer |
| Frontend jobs | `JobProgress.tsx:38-81` lấy snapshot rồi mở EventSource; chỉ giữ 6 event, cursor nằm trong ref | UI không phình theo stream nhưng reconnect hiện dùng query `after`, chưa gửi/kiểm tra `Last-Event-ID` chủ động |
| Frontend batch | `BatchQueue.tsx:47-113` page 25, load-more cursor; `:115-127` giới hạn chọn 50 | Có pagination cơ bản; chưa virtualization, prefetch, bulk selection theo server-side filter |

## 2. Mô hình tải phải benchmark

Không quy đổi số chapter thành số request một cách đơn giản. Một truyện dài có ít nhất bốn trục tải:

1. **Quy mô thư mục:** 1.000 và 10.000 chapter; metadata, state, job và event log tăng theo chapter.
2. **Quy mô chapter:** 500+ source/speech segments/chapter; mỗi segment có thể gây một network call, artifact file, hash và event.
3. **Độ dài request:** provider/local model có latency và context khác nhau; network không được nằm trong transaction DB cần thiết.
4. **Độ dài phiên UI:** mở workspace hàng giờ, reconnect SSE, seek audio, chuyển chapter và lọc danh sách.

Fixture benchmark nên có các profile:

| Profile | Nội dung |
|---|---|
| S1 | 1 project, 1.000 chapter, 20 source segments/chapter |
| S2 | 1 project, 10.000 chapter, 20 source segments/chapter |
| C500 | 1 chapter, 500 source segments và 500 speech segments |
| C2K | 1 chapter, 2.000 segments để tìm ngưỡng suy giảm |
| J10K | 10.000 jobs, event log tương ứng; 10 client reconnect/replay |
| Audio | 500 audio segments, tổng master 1–4 giờ, file artifact trên SSD local |

Mỗi test phải chạy warm-cache và cold-cache, ghi median/p95/p99, RSS peak, số query SQL, bytes response, kích thước `studio.sqlite3`, `studio.sqlite3-wal`, `studio.sqlite3-shm`, artifact bytes và số request provider. Không gọi provider trả phí; dùng adapter fake/local fixture có latency phân phối được kiểm soát.

## 3. Phân tích reliability theo flow

### 3.1 Queue, lease, worker unavailable

`JobRunner.enqueue` dùng unique `idempotency_key` và `INSERT OR IGNORE` (`runner.py:93-130`). `claim` bắt đầu `BEGIN IMMEDIATE` (`runner.py:137-198`) để chọn đúng một job trong single-writer SQLite. Đây là invariant cần giữ: hai worker cạnh tranh không được tạo hai attempt cho cùng một lượt claim.

Worker chạy recovery trước claim (`worker.py:42-47`), heartbeat tách task (`worker.py:50-55`, `:120-129`) và hoàn tất/cancel/fail qua lease hiện tại. Handler không có sẽ tạo lỗi không retry (`worker.py:59-94`); mặc định mọi kind đều trỏ vào handler ném `HandlerUnavailable` (`worker.py:192-198`). Vì vậy health UI phải phân biệt ba trạng thái: worker process còn sống, handler chưa cấu hình, và handler đang xử lý.

Độ tin cậy hiện có một giới hạn: một call provider hoặc TTS không có cooperative `await`/timeout nội bộ sẽ làm heartbeat task không thể giải quyết việc provider bị treo trong thời gian dài. FastAPI ghi rõ async task chỉ bị cancel khi tới `await`, và điều này đặc biệt quan trọng với stream lớn [FastAPI Custom Response, mục StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/) (truy cập 2026-09-07). Spec tương lai cần timeout cứng của adapter, heartbeat watchdog và trạng thái `PROVIDER_TIMEOUT` rõ ràng.

### 3.2 Retry, fallback, cancellation và idempotency

Policy hiện tại chỉ retry `PROVIDER_NETWORK`, `PROVIDER_5XX`, `DB_BUSY`, rate limit có `retry_after`; attempt 1–3 lần lượt chờ 2/10/30 giây (`retry.py:7-44`). Khi request đã gửi, `fail` chuyển `BILLING_UNKNOWN` (`runner.py:338-363`) và recovery cũng làm vậy (`runner.py:463-483`). Đây là lựa chọn an toàn cho cloud: retry tự động chỉ được phép khi adapter chứng minh request chưa được provider nhận hoặc provider có idempotency key và trạng thái truy vấn được.

Cancel là cooperative: `request_cancel` chuyển queued thành `CANCELED`, running thành `CANCEL_REQUESTED` (`runner.py:271-296`); recovery context kiểm tra trước và sau provider/artifact write (`recovery.py:261-295`, `:322-327`). Một cancel trong lúc provider đang chờ không thể dừng request từ phía worker nếu adapter không hỗ trợ cancellation. Acceptance nên đo thời gian từ cancel đến terminal state theo từng stage, và kiểm tra artifact READY trước cancel vẫn còn nguyên.

Đề xuất contract retry/fallback ở mức spec:

- `ErrorClass`: `TRANSIENT_NETWORK`, `RATE_LIMIT`, `PROVIDER_5XX`, `TIMEOUT`, `CONTEXT_OVERFLOW`, `AUTH`, `POLICY`, `BILLING_UNKNOWN`, `LOCAL_RESOURCE`, `BUG`.
- `retry_allowed` phải phụ thuộc cả `provider_request_sent`, idempotency support và budget gate.
- Backoff có jitter, tôn trọng `Retry-After`, có deadline theo job; không retry vô hạn.
- Fallback chỉ chạy khi lỗi thuộc class cho phép, model fallback đã được user cấu hình và estimate/budget được kiểm tra lại.
- Circuit breaker là Option C; A/B chỉ cần cooldown theo provider và health snapshot để tránh thác retry.
- Mỗi attempt phải ghi `provider`, `model`, `request_id` đã redacted, latency, input/output units, error class và fallback hop.

### 3.3 Transaction và network coupling trong dịch

`enqueue_translation` tạo `TranslationRun`, `flush`, sau đó lặp toàn bộ source segment và gọi `asyncio.run(provider.adapter.translate(request))` (`workflow.py:145-201`, `:389-419`), chỉ commit run ở dòng 206. Nếu session đã phát sinh write transaction, network latency có thể kéo dài thời gian giữ writer lock. Ngoài việc làm request khác nhận `SQLITE_BUSY`, crash giữa segment làm transaction lớn rollback toàn bộ metadata dù provider calls đã thành công.

Khuyến nghị cho spec cân bằng: một job/attempt xử lý một segment hoặc micro-batch nhỏ; commit trạng thái/checkpoint sau mỗi segment; tuyệt đối không giữ transaction mở qua network. Mỗi segment có `input_hash + settings_hash + source_revision_id`; cache hit không gọi provider. Batch orchestration chỉ enqueue child jobs, không gom 10.000 chapter thành một transaction.

### 3.4 TTS và audio master

Speech workflow dùng hash cache cho từng segment (`speech/workflow.py:333-389`), rồi master dùng danh sách path tuần tự (`:392-455`). Đây là tốt cho rerender chọn lọc, nhưng master render vẫn là barrier: muốn có MP3 cuối phải có toàn bộ segment. Với 500+ segment, nên lưu manifest thứ tự, duration và checksum sau từng segment; master job đọc manifest, kiểm tra tất cả READY, rồi tạo master vào file tạm atomically. Nếu FFmpeg master fail, các segment READY không bị mất.

Đừng tải bytes audio vào Python memory để ghép. Chỉ truyền path/stream cho FFmpeg, đo RSS của process Python riêng với RSS của FFmpeg/model. Nếu master rất dài, hỗ trợ hai tầng: segment/part masters và final master; đó là Option C khi seek hoặc resume master trở thành bottleneck.

## 4. SQLite WAL, checkpoint và backup

SQLite nói WAL cho phép reader không chặn writer và writer không chặn reader, nhưng vẫn chỉ có một writer tại một thời điểm; WAL yêu cầu các process cùng một host và không hoạt động trên network filesystem [SQLite WAL](https://www.sqlite.org/wal.html) (truy cập 2026-09-07). Điều này phù hợp loopback local nhưng loại trừ chia sẻ `data` trên NAS/network drive.

SQLite mặc định tự checkpoint khi WAL đạt khoảng 1.000 pages; checkpoint có thể chậm hơn commit và reader transaction dài có thể ngăn checkpoint hoàn tất. SQLite cũng cảnh báo WAL file là một phần của persistent state khi database còn mở; không được copy riêng file `.sqlite3` mà bỏ `.sqlite3-wal`/`.sqlite3-shm` [SQLite WAL, mục Checkpointing và WAL File](https://www.sqlite.org/wal.html) (truy cập 2026-09-07).

Code hiện bật WAL và `busy_timeout=5000` trên mọi connection (`db/base.py:46-59`). Backup hiện dùng Python `sqlite3.Connection.backup` (`modules/storage/backup.py:116-119`), integrity check, SHA-256 manifest và verify artifact pointers; test xác nhận journal là WAL (`tests/db/test_schema.py:569-577`) và backup/restore pointer (`tests/storage/test_backup_restore.py:14-38`). SQLite Online Backup API tạo snapshot nhất quán và có thể incremental, chỉ giữ source lock trong khoảng đọc từng phần để writer tiếp tục hoạt động [SQLite Online Backup API](https://www.sqlite.org/backup.html) (truy cập 2026-09-07). Vì artifact nằm ngoài DB, backup database không đủ để khôi phục audio; manifest phải ghi danh sách artifact cần copy và checksum.

Policy vận hành đề xuất:

- Không gọi `wal_checkpoint(TRUNCATE)` trong request user. Ghi metric WAL bytes/pages và chạy PASSIVE ở idle; FULL/RESTART chỉ trong maintenance window có timeout.
- Đảm bảo mọi DB read session đóng nhanh; không giữ transaction qua network hoặc stream SSE lâu.
- Backup DB bằng Online Backup API; copy artifact immutable theo manifest vào thư mục tạm, fsync/rename, rồi verify `integrity_check`, checksum và pointer count.
- Acceptance backup: tạo snapshot trong lúc có writer/job và khôi phục được DB + artifacts tới trạng thái nhất quán; thử crash trước/sau rename.
- Benchmark checkpoint với WAL 4 MB, 32 MB, 256 MB và reader giữ transaction 1/10/60 giây. Ghi p95 commit/read latency và bytes WAL sau 5 phút.
- Theo dõi version SQLite đi kèm Python. SQLite đã ghi nhận WAL-reset bug hiếm nhưng nghiêm trọng và khuyến nghị dùng bản đã sửa; không pin một runtime cũ mà không kiểm tra `sqlite3.sqlite_version` [SQLite WAL, mục WAL-reset bug](https://www.sqlite.org/wal.html) (truy cập 2026-09-07).

## 5. SSE cursor và event log

SSE có event `id`; browser dùng id này làm last event ID khi reconnect, `retry` là thời gian reconnect tính bằng milliseconds [MDN — Using server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events) (truy cập 2026-09-07). `sse-starlette` hỗ trợ ping keepalive, phát hiện disconnect, send timeout và khuyến nghị tạo DB session trong generator để scope session theo stream [sse-starlette README](https://github.com/sysid/sse-starlette/blob/main/README.md) (truy cập 2026-09-07).

Code hiện tạo session trong generator (`events.py:30-44`), nhưng `_events` gọi `_sync_event_log`, lấy mọi EventLog theo thứ tự rồi mới lọc `sequence_id > after` (`events.py:49-84`). Với 10.000–1.000.000 event, reconnect cursor vẫn đọc và dựng list cũ. Mỗi stream cũng chỉ snapshot một lần; không có vòng poll/live broker trong route này, nên tên “stream” hiện thực chất là replay rồi đóng.

Thiết kế cursor nên giữ numeric monotonic `sequence_id`, query `WHERE sequence_id > :cursor ORDER BY sequence_id LIMIT :page_size`, trả `nextCursor`/`hasMore`, rồi poll với backoff hoặc subscribe local channel. Cursor phải scoped theo project/job nếu có thể. Khi client nhận gap vì retention, server trả `cursor_expired` và client tải snapshot mới trước khi nối tiếp. Client phải dedupe theo sequence id; không append cùng event sau reconnect.

Event payload nên là delta nhỏ (`jobId`, status, current, total, error code), không nhúng source/translation/audio metadata. Coalesce progress events theo job mỗi 100–250 ms hoặc mỗi 1% tiến độ; giữ audit/usage event riêng vì chúng cần tính đầy đủ. Gửi ping định kỳ để tránh proxy idle timeout; cấu hình `send_timeout` và cleanup khi client disconnect.

## 6. Frontend virtualization, chapter list và audio range

`BatchQueue` đã phân trang server-side 25 item với cursor (`BatchQueue.tsx:47-113`) nhưng render tất cả page đã tải (`:210-226`). 10.000 chapter sẽ khiến DOM tăng theo số lần “Load more”, còn `/api/projects/{id}` hiện vẫn full-load chapters (`api/projects.py:110-139`). MDN giải thích `content-visibility:auto` cho phép browser bỏ qua layout/paint subtree ngoài viewport, kèm `contain-intrinsic-size` để tránh layout shift [MDN — content-visibility](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/content-visibility) (truy cập 2026-09-07). web.dev minh họa virtualized long list để tránh DOM quá lớn [web.dev — Virtualize large lists with react-window](https://web.dev/articles/virtualize-long-lists-react-window) (truy cập 2026-09-07).

Option B nên dùng keyset pagination + virtualized chapter tree, chỉ giữ window khoảng 30–100 row và buffer; selection lưu ID, không phụ thuộc row đang mount. Với row có chiều cao biến đổi, đo chiều cao hoặc dùng fixed-height summary; giữ focus/keyboard semantics khi row unmount. `content-visibility` có thể là fallback nhẹ cho trang review, nhưng không thay thế virtualization nếu hàng nghìn row đều nằm trong DOM.

Audio player cần endpoint artifact đã authorize, `Content-Type` đúng, `Accept-Ranges: bytes`, xử lý `Range: bytes=start-end` bằng `206 Partial Content` + `Content-Range`, trả `416` cho range không hợp lệ và hỗ trợ `HEAD`/`ETag`. MDN nêu range request hữu ích cho media player random access và pause/resume; `Accept-Ranges` cho biết server hỗ trợ partial response [MDN — HTTP range requests](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Range_requests) (truy cập 2026-09-07). `HTMLMediaElement.buffered`, `preload`, `readyState` và `playbackRate` là các tín hiệu cần đo trên client [MDN — HTMLMediaElement](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement) (truy cập 2026-09-07).

Không nên đưa toàn bộ MP3 vào JS `Blob` trước khi phát. Trỏ `<audio src>` tới endpoint range, `preload="metadata"` cho danh sách, và chỉ prefetch chapter kế tiếp khi người dùng đang nghe. Nếu cần phát khi audio còn đang render, Media Source Extensions là một hướng riêng có thể append segment vào `SourceBuffer`, nhưng tăng đáng kể complexity và codec/browser matrix; giữ cho Option C [W3C Media Source Extensions](https://www.w3.org/TR/media-source/) (truy cập 2026-09-07).

## 7. Performance budget đề xuất

Đây là budget khởi đầu để đo, không phải cam kết chất lượng provider/model. Tất cả p95 chạy trên máy mục tiêu 8 GB, SSD local, API/worker cùng máy, fake/local provider có latency cố định được ghi trong báo cáo.

| Hạng mục | Budget Option A | Budget Option B (cân bằng) | Budget Option C |
|---|---:|---:|---:|
| First chapter page 25 rows, cold DB | p95 ≤ 500 ms | p95 ≤ 250 ms | p95 ≤ 150 ms |
| Chapter page 25 rows, 10k-chapter project | p95 ≤ 500 ms | p95 ≤ 300 ms | p95 ≤ 200 ms |
| List project metadata, 1k/10k chapters | p95 ≤ 2 s / 5 s | p95 ≤ 400 ms / 600 ms | p95 ≤ 300 ms / 400 ms |
| Claim job dưới 10 client cạnh tranh | p95 ≤ 1 s, không duplicate | p95 ≤ 300 ms, busy rate ≤ 1% | p95 ≤ 150 ms, busy rate ≤ 0.1% |
| SSE replay 1k event sau cursor | p95 ≤ 2 s | p95 ≤ 500 ms | p95 ≤ 300 ms |
| Event payload | ≤ 32 KiB | ≤ 16 KiB | ≤ 16 KiB, coalesced |
| UI chapter list | ≤ 500 mounted rows | ≤ 100 mounted rows | ≤ 100 mounted rows + server filter |
| Cancel sau checkpoint | p95 ≤ 30 s | p95 ≤ 10 s | p95 ≤ 5 s, adapter cancellation |
| RSS app metadata khi C500 | ≤ +512 MiB | ≤ +256 MiB | ≤ +256 MiB; model đo riêng |
| TTS artifact resume | cache hit, không duplicate READY | cache hit, checkpoint từng segment | checkpoint + part-master |
| Backup snapshot DB | hoàn tất và verify | p95 downtime user = 0 | incremental/progress observable |

Không gộp latency provider vào budget API UI. Đối với dịch/TTS, báo cáo riêng `provider_latency_ms`, `queue_wait_ms`, `db_commit_ms`, `artifact_write_ms`, `ffmpeg_ms`; throughput chỉ có ý nghĩa khi fixture provider được định nghĩa.

## 8. Benchmark protocol

### 8.1 Harness và dataset

- Tạo database tạm bằng migration hiện hành, không dùng DB người dùng.
- Sinh deterministic IDs, text lengths và job statuses cho S1/S2/C500/C2K/J10K.
- Fake adapter hỗ trợ latency 0 ms, 100 ms và p95 2 s; các mode timeout, 429 + Retry-After, 5xx, malformed response và provider-sent-then-disconnect.
- Chạy mỗi case 5 warm-up + 20 measured iterations; ghi median/p95/p99. Test concurrency 1, 4, 10 client; worker concurrency thực tế hiện là 1 nên không giả vờ đo song song worker.
- Thu `EXPLAIN QUERY PLAN` cho list chapter, progress summary, event replay, artifact lookup; ghi query count qua SQLAlchemy event hook.

### 8.2 Reliability matrix

| Scenario | Inject tại đâu | Invariant cần kiểm |
|---|---|---|
| Hai worker claim cùng lúc | trước/giữa `BEGIN IMMEDIATE` | đúng một lease/attempt; job còn lại không mất |
| Kill worker sau claim | sau `claim` | recovery chỉ requeue khi chưa provider-sent |
| Kill sau provider request id | `mark_provider_sent` | terminal `BILLING_UNKNOWN`, không auto retry |
| Kill sau artifact rename trước DB commit | recovery writer | không có READY row trỏ file thiếu; file orphan được diagnostics thấy |
| Cancel trước segment | `raise_if_cancel_requested` | CANCELED, artifact READY trước đó giữ nguyên |
| Cancel trong provider wait | adapter sleep | terminal phụ thuộc timeout/cancel contract, không mất lease im lặng |
| DB busy/checkpoint | concurrent read + write | retry bounded, không lock vô hạn, WAL không tăng vô hạn |
| SSE reconnect | disconnect sau event N | replay `sequence_id > N`, không duplicate, cursor gap được báo |
| Backup trong lúc write | writer loop + `backup` | backup integrity ok và snapshot có transaction hợp lệ |
| 429/5xx/timeout | fake provider | retry delay đúng, fallback chỉ khi policy cho phép |

### 8.3 SLO và báo cáo

Mỗi report cần có commit SHA, Python/SQLite/Node/browser version, CPU/RAM/disk, dataset profile, environment variables, seed, kết quả từng percentile và failure sample. Nếu test timeout, ghi `INCOMPLETE` và không suy ra pass.

## 9. Ba option kiến trúc hiệu năng/độ tin cậy

| Criteria | A — Giữ đơn giản | B — Cân bằng | C — Scale dài hạn |
|---|---|---|---|
| Queue | SQLite queue, một job/chapter, retry hiện tại | SQLite durable queue, child segment jobs/checkpoint, bounded leases | queue service/DB riêng, nhiều worker và scheduler |
| Transaction | tách commit khỏi network ở các điểm lớn | commit per segment/micro-batch, event append trực tiếp | outbox/event bus, idempotent consumer |
| SSE | replay query có cursor, snapshot lại khi reconnect | cursor indexed + project/job scope + coalesce/poll | broker/fan-out và retention policy |
| Chapter UI | pagination hiện tại, giới hạn page | keyset + virtualization + server filter | indexed search, windowed server stream |
| Audio | file endpoint range + `<audio>` | range + cache headers + segment manifest | part-master/MSE khi có nhu cầu thật |
| Backup | Online Backup API + artifact manifest | incremental progress + verify/retention | snapshot/replication ngoài máy |
| Complexity | thấp | vừa | cao |
| Migration cost | thấp | vừa | cao |
| Maintainability | giữ nhiều coupling hiện tại | ranh giới rõ, dễ kiểm thử | phụ thuộc hạ tầng mới |
| Scalability | phù hợp nhỏ/vừa | phù hợp 10k chapter, 500 segment | phù hợp nhiều project/worker |
| Risk | lock/N+1 tiếp tục tồn tại | migration/query contract phải chuẩn | vận hành và failure mode tăng |
| Recommendation | chỉ dùng làm hotfix | **khuyến nghị cho phase nâng cấp** | chỉ sau khi B đo chứng minh không đủ |

Khuyến nghị B không khóa model/provider cụ thể. Bảo toàn local-first, SQLite/WAL, worker 1 trong phase đầu; tập trung tách network khỏi transaction, query theo cursor, checkpoint segment, range audio và virtualized UI. Chỉ chọn C sau khi benchmark B vượt budget hoặc workload thật yêu cầu nhiều worker.

## 10. Acceptance criteria cho coding phase

### Queue và recovery

- 10 client cạnh tranh claim trong 60 giây không tạo duplicate attempt; kết quả có p95 theo budget.
- Mỗi job có idempotency key ổn định; enqueue lại cùng revision trả job cũ.
- Handler unavailable hiển thị `HANDLER_NOT_CONFIGURED` và không bị retry vô hạn.
- Retry test chứng minh backoff, jitter/deadline, Retry-After và class không retry.
- Provider-sent/unknown billing không auto retry; UI hướng dẫn reconcile.
- Cancel/resume không tạo duplicate READY artifact và không xóa artifact checkpoint hợp lệ.
- Crash injection qua tất cả điểm reliability matrix đều để lại trạng thái hợp lệ hoặc diagnostics actionable.

### SQLite/WAL/backup

- `EXPLAIN QUERY PLAN` của job claim, chapter page, artifact lookup và SSE replay dùng index dự kiến; query page không load toàn bộ lịch sử.
- Không có network/provider call trong transaction DB giữ write lock; đo bằng trace transaction duration.
- WAL size, checkpoint duration, busy count và backup duration được log redacted/structured.
- Backup restore được database snapshot và artifact manifest; `integrity_check=ok`, hash và pointer đều pass.
- Backup/restore test bao gồm database có WAL đang hoạt động; không copy thủ công riêng `.sqlite3`.

### SSE/UI/audio

- Reconnect sau `Last-Event-ID=N` chỉ nhận event có sequence lớn hơn N; duplicate/gap được xử lý rõ.
- SSE disconnect không giữ session/connection; ping và send timeout có test.
- Event replay 1k event đạt p95 budget; payload job không chứa source text, API key hay secret.
- Chapter list 10k item không tạo 10k DOM row; selection/focus/keyboard vẫn đúng.
- Audio endpoint trả `HEAD`, full `200`, partial `206`, invalid `416`, ETag và checksum/authorization đúng.
- `<audio>` seek tới cuối file không tải toàn bộ MP3; đo request range và thời gian first playable.

## 11. Các rủi ro còn lại cần decision point

1. **SQLite hay queue ngoài process:** Option B giữ SQLite vì app local-first; chỉ mở rộng khi benchmark cho thấy writer contention hoặc nhiều worker thực sự cần.
2. **Event retention:** giữ audit/usage lâu dài hay compact progress event cần product decision; nên giữ audit/usage, coalesce/delete progress theo retention.
3. **Master audio:** file MP3 hoàn chỉnh đơn giản hơn; part-master/MSE chỉ đáng làm khi seek master nhiều giờ không đạt budget.
4. **Cancellation semantics:** provider nào hỗ trợ cancel/idempotency quyết định fallback an toàn; không giả định mọi HTTP timeout đồng nghĩa request chưa gửi.
5. **Độ chính xác budget:** provider/local model chưa benchmark trong task này; cần fixture và đo runtime thật trước khi khóa SLO.

## 12. Nguồn chính thức đã tham khảo

- SQLite, *Write-Ahead Logging*: https://www.sqlite.org/wal.html — WAL concurrency, one-writer, checkpoint, WAL file, retention và WAL-reset; truy cập 2026-09-07.
- SQLite, *SQLite Backup API*: https://www.sqlite.org/backup.html — Online Backup API, snapshot và incremental backup; truy cập 2026-09-07.
- FastAPI, *Custom Response — StreamingResponse/FileResponse*: https://fastapi.tiangolo.com/advanced/custom-response/ — streaming cancellation, file response headers; truy cập 2026-09-07.
- sse-starlette, *README*: https://github.com/sysid/sse-starlette/blob/main/README.md — EventSourceResponse, ping, disconnect, send timeout, DB streaming và shutdown; truy cập 2026-09-07.
- MDN, *Using server-sent events*: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events — event id, retry và EventSource reconnect; truy cập 2026-09-07.
- MDN, *HTTP range requests*: https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Range_requests — Accept-Ranges, partial response và media random access; truy cập 2026-09-07.
- MDN, *HTMLMediaElement*: https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement — buffered, preload, readyState, playbackRate; truy cập 2026-09-07.
- MDN, *content-visibility*: https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/content-visibility — bỏ qua render offscreen và intrinsic size/accessibility; truy cập 2026-09-07.
- web.dev, *Virtualize large lists with react-window*: https://web.dev/articles/virtualize-long-lists-react-window — DOM size và windowed list; truy cập 2026-09-07.
- W3C, *Media Source Extensions*: https://www.w3.org/TR/media-source/ — append media buffer bằng JavaScript và trade-off compatibility; truy cập 2026-09-07.

