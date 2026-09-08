# Truyen Audio Studio

Local web studio để nhập truyện/tiểu thuyết tiếng Trung, dịch sang tiếng Việt, dựng giọng đọc, kiểm tra chất lượng audio và xuất gói nội dung để upload thủ công lên web app `truyenaudio`.

Tool này chạy cá nhân trên máy local, mặc định chỉ bind `127.0.0.1:8765`. Không thiết kế để public internet.

## Tool này làm gì?

- Tạo project truyện và nhập chương trực tiếp từ **Wenku (QQ Reading)** qua URL/ID hoặc Bảng xếp hạng.
- Xem trước thông tin truyện (bìa, tác giả, số chữ, danh mục chương) và chọn phạm vi chương cần cào.
- Preview nội dung các chương đã cào trước khi xác nhận nhập vào dự án.
- Dịch thử bằng fake local hoặc dịch cloud qua Qwen khi đã cấu hình consent/budget/key.
- Duyệt bản dịch theo revision hash để tránh sửa nhầm bản cũ.
- Chọn giọng đọc local, render audio, tạo master MP3 và SRT.
- Hỗ trợ nền tảng assisted multi-voice: narrator + tối đa 3 role phụ.
- Có cloud TTS adapters có guard cho Google, Gemini và ElevenLabs.
- Có optional ASR backcheck để gắn issue phát âm/mất số/lặp nội dung.
- Có batch queue, checkpoint recovery, diagnostics và export bundle.

## Nguyên tắc an toàn và bản quyền

Tool có tích hợp crawler Wenku để lấy nội dung chương công khai (miễn phí). Chương VIP/locked sẽ được đánh dấu cảnh báo và chỉ hiển thị nội dung preview. Tool không bypass CAPTCHA/DRM/login, không lấy cookie hộ và không tự upload lên web app chính.

Bạn chỉ nên import nội dung bạn có quyền xử lý. Export public cần rights evidence/publication gate riêng; consent cloud chỉ cho phép gửi dữ liệu đi provider, không thay thế quyền xuất bản.

## Kiến trúc repo

```text
backend/   FastAPI, SQLite, worker, domain workflow, provider adapters
frontend/  Vite + React UI
scripts/   preflight, migrate, smoke/poc scripts
data/      database, artifacts, audio, reports local; bị git ignore
```

App backend cũng serve frontend build ở `/`, nên cách chạy ổn định nhất là build frontend trước rồi chạy `run-studio.bat`.

## Yêu cầu máy

- Windows PowerShell.
- Python `3.12`.
- Node.js `24.x`.
- FFmpeg và FFprobe trong `PATH`.
- Git.
- GitHub CLI chỉ cần nếu muốn push repo.

Kiểm tra nhanh:

```powershell
py -3.12 --version
node --version
ffmpeg -version
ffprobe -version
```

## Cài đặt lần đầu

Chạy trong thư mục repo:

```powershell
cd D:\truyenaudio-studio

py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e "backend[dev,poc]"

cd frontend
npm install
```

## Chạy app local

Build frontend:

```powershell
cd D:\truyenaudio-studio\frontend
npm run build
```

Chạy studio:

```powershell
cd D:\truyenaudio-studio
.\run-studio.bat
```

Script sẽ:

- chạy preflight;
- chạy database migration;
- start API tại `http://127.0.0.1:8765`;
- start worker nền;
- mở browser vào studio.

## Chạy demo/test không tốn phí

Nếu muốn test flow audio mà chưa có model TTS local thật, bật fake audio trước khi build/run:

```powershell
cd D:\truyenaudio-studio
$env:STUDIO_FAKE_AUDIO = "1"
$env:VITE_STUDIO_FAKE_AUDIO = "1"

cd frontend
npm run build

cd ..
.\run-studio.bat
```

Fake audio chỉ dùng để demo/test. Không dùng để xuất bản production.

## Workflow sử dụng cơ bản

1. Vào `Dự án` để tạo project.
2. Nhập nội dung bằng paste, folder, EPUB hoặc DOCX.
3. Chạy dịch fake để test nhanh, hoặc Qwen nếu đã cấu hình cloud.
4. Duyệt bản dịch.
5. Chọn giọng đọc và render audio.
6. Duyệt audio.
7. Export private/public bundle.
8. Upload file audio thủ công lên web app `truyenaudio`.

## Multi-voice và cloud demo

Route demo hiện có tại:

```text
http://127.0.0.1:8765/multivoice-cloud-demo
```

Demo này dùng để kiểm tra UX/logic: consent guard, so sánh giọng, role assignment và selective rerender.

Đường production chính vẫn cần wiring sâu hơn để biến toàn bộ cloud multi-voice thành một flow một-click trong UI.

## Có cần config key gì không?

Chạy local cơ bản: không cần API key.

Bạn chỉ cần key khi dùng provider thật:

| Tính năng | Có cần key? | Ghi chú |
| --- | --- | --- |
| Paste/import/project | Không | Chạy hoàn toàn local |
| Fake translation/audio | Không | Dùng demo/test |
| Local Piper/VieNeu TTS | Không cần API key | Cần model file, license snapshot và manifest hợp lệ |
| Qwen translation | Có | Cần provider profile, secret, cloud consent, rate card, budget authorization |
| Google/Gemini/ElevenLabs TTS | Có | Adapter đã có guard/contract; paid smoke thật chưa wired fully |
| ASR backcheck fake | Không | Advisory-only |
| ASR cloud thật | Có, sau này | Chưa chọn provider ASR production |

## Biến môi trường quan trọng

Các biến app đều dùng prefix `STUDIO_`.

| Biến | Mặc định | Mục đích |
| --- | --- | --- |
| `STUDIO_DATA_ROOT` | `D:\truyenaudio-studio\data` | Nơi lưu SQLite, artifacts, audio, reports |
| `STUDIO_FAKE_AUDIO` | unset | Set `1` để backend dùng fake TTS/audio |
| `VITE_STUDIO_FAKE_AUDIO` | unset | Set `1` trước khi build frontend để UI chọn fake preset |
| `STUDIO_ENABLE_ASR_BACKCHECK` | `false` | Bật optional ASR advisory |

Các setting này bị khóa để an toàn local:

- `STUDIO_HOST` chỉ chấp nhận `127.0.0.1`.
- `STUDIO_PORT` chỉ chấp nhận `8765`.
- `STUDIO_WORKER_CONCURRENCY` chỉ chấp nhận `1`.

## Cấu hình provider cloud

Provider profile được tạo qua API `/api/cloud-profiles`. Secret được lưu vào Windows keyring bằng service `truyenaudio-studio`; API không trả lại secret.

Ví dụ payload cho Qwen:

```json
{
  "providerKind": "TRANSLATOR",
  "adapterName": "qwen",
  "displayName": "Qwen MT Flash",
  "model": "qwen-mt-flash",
  "region": "intl",
  "config": {
    "endpoint": "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
  },
  "enabled": true,
  "secret": "PASTE_API_KEY_HERE"
}
```

Sau provider profile, cloud call còn cần:

- cloud consent đã grant cho project;
- policy snapshot hash đúng;
- rights grant không cấm third-party cloud;
- rate card đúng provider/model/region/unit;
- quota config;
- budget authorization còn hạn.

Nếu thiếu một trong các gate trên, cloud call sẽ fail closed.

## Local voice model manifest

Voice catalog đọc manifest tại:

```text
data\models\voices\voice-presets.manifest.json
```

Manifest cần khai báo model path, license snapshot path, hash, provider, locale, sample rate và trạng thái verification.

Nếu chưa có manifest/model hợp lệ, UI sẽ báo chưa có giọng local đã verify. Khi đó dùng `STUDIO_FAKE_AUDIO=1` để test flow hoặc cấu hình model Piper/VieNeu thật.

## Smoke/Poc scripts

Fake POC:

```powershell
cd D:\truyenaudio-studio
.\scripts\run-poc.ps1 -Provider fake
```

Qwen POC có phí, chỉ chạy khi đã có consent và authorization:

```powershell
$env:STUDIO_QWEN_API_KEY = "..."
.\scripts\run-poc.ps1 -Provider qwen -AllowPaidProvider -AuthorizationId "<uuid>" -CloudConsentId "<uuid>"
```

Cloud TTS smoke hiện chỉ là preflight `NOT_RUN`, chưa phải provider PASS:

```powershell
.\scripts\smoke-cloud-tts.ps1 -AllowPaid -Provider google -AuthorizationId "<uuid>" -CloudConsentId "<uuid>" -ExpectedMaxVnd 5000
```

Không paste hoặc commit API key vào repo.

## Lệnh kiểm tra

## Baseline và phục hồi bản sao

Mỗi backup storage được tạo bằng SQLite Backup API để lấy snapshot nhất quán khi database dùng WAL. Bản backup gồm:

- file SQLite có `PRAGMA integrity_check = ok`;
- manifest có SHA-256 và byte size của database;
- bản sao các artifact còn được tham chiếu, kiểm lại bằng SHA-256 trong database.

Core storage có thể phục hồi vào một `STUDIO_DATA_ROOT` mới, chưa tồn tại và không overlap data root nguồn. Quá trình dựng database và artifact trong thư mục staging, kiểm `integrity_check` cùng artifact pointers, rồi mới publish data root đích; data root nguồn không bị thay đổi. Đây là đường phục hồi bản sao đã được fixture kiểm chứng. API restore hiện hữu vẫn là restore tại chỗ và chưa nhận data root đích từ giao diện; phần chọn/confirm đích thuộc Storage UI của các task sau.

Kết quả baseline và lệnh đã chạy được ghi tại [docs/validation/baseline.md](docs/validation/baseline.md). Không coi fake audio, preflight cloud hoặc package audit là bằng chứng provider/audio cloud đã hoạt động thật.

Backend:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m pytest backend/tests -q
```

Frontend:

```powershell
cd D:\truyenaudio-studio\frontend
npm test -- --run
npm run build
npm exec playwright test e2e/batch-recovery.spec.ts e2e/multivoice-cloud.spec.ts e2e/single-voice.spec.ts
```

## Trạng thái hiện tại

Đã merge vào `main` và push lên GitHub private:

```text
https://github.com/nhatanhvodev/truyenaudio-studio
```

Verification gần nhất trên `main`:

- Backend: `377 passed, 1 warning`.
- Frontend Vitest: `19 passed`.
- Build: pass.
- Playwright E2E: `3 passed`.

## Giới hạn cần nhớ

- Chưa có crawler hợp pháp cho Wenku/QQ.
- Chưa auto-upload sang web app chính.
- Paid cloud TTS smoke thật chưa wired fully.
- Multi-voice cloud production UI chưa phải one-click end-to-end.
- App ưu tiên an toàn chi phí, audit và local control hơn tốc độ tự động hóa.
