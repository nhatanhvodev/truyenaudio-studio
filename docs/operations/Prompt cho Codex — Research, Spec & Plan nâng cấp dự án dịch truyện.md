# Vai trò

Bạn đang đóng vai trò **Principal Software Architect + AI Engineer + Product/UX Architect**.

Model hiện tại là **GPT-6 Astra**, vì vậy trong task này:

> **CHỈ NGHIÊN CỨU, PHÂN TÍCH, THIẾT KẾ, VIẾT SPEC VÀ LẬP PLAN.**
>
> **KHÔNG IMPLEMENT CODE.**
>
> Việc coding/implementation sẽ được thực hiện sau bằng một model khác.

Mọi trao đổi, câu hỏi, báo cáo, spec và plan gửi cho tôi phải viết bằng **TIẾNG VIỆT**.

---

# Mục tiêu tổng thể

Tôi muốn nâng cấp toàn diện dự án hiện tại thành một ứng dụng dịch truyện hiện đại, có kiến trúc tốt, dễ mở rộng, hỗ trợ nhiều AI Provider/Model, có Text-to-Speech bằng VieNeu-TTS và được thiết kế lại toàn bộ UI/UX.

Bạn cần nghiên cứu codebase hiện tại thật kỹ trước khi đề xuất bất kỳ thay đổi nào.

Không được đưa ra solution dựa trên phỏng đoán khi chưa đọc codebase.

---

# TOOL / SKILL BẮT BUỘC SỬ DỤNG

Trong quá trình nghiên cứu, ưu tiên sử dụng các tool/skill sau nếu môi trường hiện tại cung cấp:

- **CodeGraph**
  - dùng để khám phá toàn bộ codebase;
  - dependency graph;
  - module relationship;
  - entry points;
  - data flow;
  - API flow;
  - service/provider abstraction;
  - frontend/backend boundary;
  - database/storage;
  - các phần có coupling cao.

- **Superpower**
  - dùng để brainstorming;
  - architecture exploration;
  - research;
  - requirement analysis;
  - tìm edge case;
  - đánh giá trade-off;
  - xây dựng spec;
  - xây dựng implementation plan.

- **TasteSkill**
  - dùng cho toàn bộ phần:
    - Product Design;
    - UI/UX;
    - Information Architecture;
    - Design System;
    - Interaction Design;
    - usability;
    - accessibility;
    - responsive design.

Nếu có nhiều skill liên quan, hãy chủ động sử dụng những skill phù hợp thay vì chỉ phân tích thủ công.

---

# QUY TẮC LÀM VIỆC

## 1. KHÔNG CODE

Trong task này tuyệt đối không:

- implement feature;
- sửa source code;
- refactor code;
- tạo component production;
- viết migration;
- thay API;
- cài dependency;
- chạy destructive command;
- tự động thay đổi project.

Có thể viết:

- pseudo-code;
- interface proposal;
- schema proposal;
- architecture diagram;
- Mermaid diagram;
- folder structure đề xuất;
- API contract;
- data model;
- implementation checklist.

Nhưng chỉ ở mức **SPEC / PLAN**.

Nếu phát hiện bug nghiêm trọng trong codebase, hãy ghi nhận vào report thay vì sửa trực tiếp.

---

## 2. KHÔNG VỘI ĐỀ XUẤT

Thứ tự làm việc bắt buộc:

**Codebase Discovery  
→ Current Architecture Analysis  
→ Problem Analysis  
→ Product Research  
→ Technical Research  
→ Options / Trade-offs  
→ Proposed Architecture  
→ UX/UI Spec  
→ Technical Spec  
→ Implementation Plan**

Không được bỏ qua Discovery và nhảy ngay vào solution.

---

# PHASE 1 — CODEBASE DISCOVERY

Sử dụng **CodeGraph** để đọc và hiểu toàn bộ project.

Cần xác định ít nhất:

### Project structure

- framework;
- runtime;
- frontend;
- backend;
- database;
- ORM;
- authentication;
- API;
- state management;
- styling;
- build system;
- package manager;
- deployment architecture.

### Main modules

Xác định:

- entry point;
- translation workflow;
- story/chapter processing;
- AI/model integration;
- prompt management;
- streaming;
- retry;
- error handling;
- caching;
- persistence;
- audio/TTS nếu đã tồn tại;
- settings;
- user configuration;
- history;
- import/export.

### Data flow

Phân tích flow thực tế, ví dụ:

`Story Input → Chapter Parsing → Translation Request → Provider → Model → Translation Processing → Storage → UI`

Nếu flow khác, hãy mô tả chính xác theo codebase.

### Dependency graph

Tìm:

- tight coupling;
- duplicated logic;
- hard-coded provider;
- hard-coded model;
- hard-coded prompt;
- UI/business logic coupling;
- những module khó mở rộng.

### Technical Debt

Phân loại:

- Critical
- High
- Medium
- Low

Không chỉ liệt kê vấn đề.

Mỗi vấn đề cần ghi:

- vị trí/module;
- nguyên nhân;
- impact;
- recommendation;
- mức độ ưu tiên.

---

# PHASE 2 — NGHIÊN CỨU HỆ THỐNG MULTI-PROVIDER / MULTI-MODEL

Mục tiêu là biến hệ thống thành kiến trúc **Provider-Agnostic**.

Người dùng phải có khả năng chọn:

`Provider → Model → Translation Settings`

Ưu tiên đặc biệt các provider/model có:

- Free Tier;
- Free Model;
- Trial;
- chi phí thấp;
- OpenAI-compatible API.

Nghiên cứu tối thiểu các hướng:

- NVIDIA NIM
- OpenRouter
- Gemini
- Groq
- Cerebras
- Cloudflare Workers AI
- Hugging Face
- Ollama / local model
- LM Studio / local OpenAI-compatible server
- các provider/model miễn phí hoặc giá thấp khác nếu phù hợp.

Không mặc định tất cả provider trên đều phải được implement.

Hãy đánh giá trước rồi đề xuất.

---

# PROVIDER RESEARCH MATRIX

Với mỗi provider, phân tích:

| Tiêu chí | Nội dung |
|---|---|
| Provider | |
| API format | |
| OpenAI compatible | |
| Free tier | |
| Free models | |
| Rate limit | |
| Context length | |
| Streaming | |
| Structured output | |
| Vietnamese quality | |
| Chinese → Vietnamese | |
| Japanese → Vietnamese | |
| Korean → Vietnamese | |
| Long-context translation | |
| Latency | |
| Stability | |
| Commercial restriction | |
| API key requirement | |
| Integration complexity | |
| Recommendation | |

Đặc biệt ưu tiên use case:

> **Dịch tiểu thuyết / truyện dài sang tiếng Việt tự nhiên.**

Không chỉ benchmark kiến thức chung.

Cần quan tâm:

- giữ tên nhân vật;
- đại từ;
- xưng hô;
- terminology consistency;
- style consistency;
- context giữa chapter;
- tránh dịch literal;
- giữ formatting;
- dialogue;
- văn phong tiểu thuyết.

---

# MODEL DISCOVERY

Thiết kế khả năng:

- provider discovery;
- model discovery;
- model metadata;
- model capability;
- model context limit;
- pricing;
- free/paid status.

Nghiên cứu xem nên:

### Option A
Hard-code curated model list.

### Option B
Fetch `/models` từ provider.

### Option C
Hybrid:
- curated recommended models;
- dynamic provider models.

Đánh giá trade-off và chọn kiến trúc phù hợp.

---

# MODEL FILTER / UI

Nghiên cứu UX cho phép filter:

- Free
- Paid
- Recommended
- Fast
- High Quality
- Long Context
- Translation Optimized
- Local
- Cloud

Có thể hiển thị metadata như:

- context window;
- provider;
- input/output pricing;
- free;
- latency;
- capability;
- recommended use case.

---

# FALLBACK SYSTEM

Nghiên cứu kiến trúc fallback:

`Primary Model  
↓  
Fallback Model  
↓  
Fallback Provider`

Ví dụ khi:

- rate limit;
- provider down;
- model unavailable;
- timeout;
- context overflow;
- quota exhausted.

Đề xuất retry strategy:

- exponential backoff;
- provider-aware retry;
- error classification;
- circuit breaker nếu cần.

---

# PROVIDER ABSTRACTION

Nghiên cứu architecture dạng:

```text
LLMProvider
 ├── OpenRouterProvider
 ├── NvidiaNIMProvider
 ├── GeminiProvider
 ├── GroqProvider
 ├── OllamaProvider
 └── ...
```

Hoặc kiến trúc tốt hơn nếu codebase hiện tại cần cách khác.

Cần đề xuất interface cho các capability như:

```text
listModels()
translate()
stream()
validateApiKey()
getModelMetadata()
estimateCost()
healthCheck()
```

Đây chỉ là ví dụ.

Không bắt buộc sử dụng y hệt nếu có architecture tốt hơn.

---

# PHASE 3 — TRANSLATION ENGINE

Đây không chỉ là app gọi API.

Hãy nghiên cứu thiết kế một **Translation Engine** phù hợp cho truyện dài.

Các vấn đề cần phân tích:

### Context management

Làm thế nào giữ consistency giữa chapter?

Nghiên cứu:

- rolling context;
- chapter summary;
- previous chapter memory;
- entity memory;
- glossary;
- translation memory.

### Character memory

Lưu:

- tên;
- giới tính nếu biết;
- vai trò;
- cách xưng hô;
- relationship;
- aliases.

### Glossary

Ví dụ:

```text
Original Term
Preferred Translation
Forbidden Translation
Description
Scope
```

### Style Profile

Cho phép chọn:

- sát nghĩa;
- tự nhiên;
- văn học;
- web novel;
- light novel;
- cổ trang;
- hiện đại;
- fantasy;
- wuxia/xianxia nếu phù hợp.

### Translation quality pipeline

Nghiên cứu các pipeline:

#### Option A

`Source → Translate`

#### Option B

`Source → Translate → Review → Polish`

#### Option C

`Source → Analyze → Translate → Critique → Rewrite`

Đánh giá:

- quality;
- tokens;
- latency;
- cost.

Đề xuất nhiều profile:

- Economy
- Balanced
- Quality
- Maximum Quality

---

# PROMPT SYSTEM

Nghiên cứu cách quản lý prompt.

Không hard-code prompt rải rác.

Xem xét:

```text
System Prompt
Translation Prompt
Style Prompt
Glossary
Character Context
Previous Context
User Custom Prompt
```

Đề xuất Prompt Builder / Prompt Template architecture.

---

# PHASE 4 — VIENEU-TTS / AUDIO

Tích hợp **VieNeu-TTS** làm hệ thống đọc truyện.

Mục tiêu UX:

Người dùng có thể:

1. mở danh sách voice;
2. nghe sample;
3. so sánh voice;
4. chỉnh parameter;
5. preview audio;
6. sau đó mới chọn voice.

---

# VOICE PICKER

Thiết kế một Voice Picker hiện đại.

Thông tin có thể gồm:

- voice name;
- gender/style nếu metadata hỗ trợ;
- description;
- sample button;
- currently playing state;
- selected state.

Ví dụ:

```text
● Minh Anh
  Nữ · nhẹ nhàng · kể chuyện

  [▶ Nghe thử]

○ Quang Minh
  Nam · trầm · audiobook

  [▶ Nghe thử]
```

Chỉ là ví dụ UX, không bắt buộc copy.

---

# AUDIO PREVIEW

Research flow:

`Voice  
→ Sample Text  
→ Generate Preview  
→ Play  
→ Select Voice`

Nghiên cứu:

- sample caching;
- audio cache;
- preview loading;
- generation queue;
- cancel generation;
- retry;
- progress;
- audio storage.

---

# TTS SETTINGS

Nếu VieNeu-TTS hỗ trợ, nghiên cứu UX cho:

- speed;
- pitch;
- energy;
- emotion/style;
- pause;
- paragraph spacing.

Không hiển thị setting mà engine không thực sự hỗ trợ.

---

# AUDIOBOOK GENERATION

Nghiên cứu architecture:

`Translated Chapter  
↓  
Chunking  
↓  
VieNeu-TTS  
↓  
Audio Segments  
↓  
Merge  
↓  
Chapter Audio`

Quan tâm:

- chapter dài;
- memory;
- queue;
- resumable jobs;
- failed chunk;
- caching;
- duplicate generation;
- progress;
- audio file naming;
- metadata.

---

# PHASE 5 — XÓA TOÀN BỘ DESIGN HIỆN TẠI VÀ THIẾT KẾ LẠI UI/UX

**Không giữ design hiện tại chỉ vì code đã tồn tại.**

Có thể giữ business logic/backend nếu tốt.

Nhưng về UI/UX:

> **Thiết kế lại từ đầu.**

Sử dụng **TasteSkill** để nghiên cứu và xây dựng một design system hoàn chỉnh.

---

# PRODUCT UX GOALS

UI mới phải:

- hiện đại;
- đơn giản;
- dễ học;
- ít clutter;
- responsive;
- desktop-first nhưng mobile usable;
- có dark/light mode nếu phù hợp;
- feedback rõ ràng;
- loading state rõ;
- empty state;
- error state;
- keyboard friendly;
- accessibility tốt.

Không làm UI kiểu:

- dashboard template generic;
- card everywhere;
- gradient quá mức;
- animation thừa;
- quá nhiều modal.

Ưu tiên **functional elegance**.

---

# INFORMATION ARCHITECTURE

Nghiên cứu navigation tốt nhất.

Một hướng tham khảo:

```text
Library
Projects
Translator
Glossary
Characters
Audio
Models
Settings
```

Nhưng phải phân tích workflow thực tế trước khi quyết định.

---

# CORE USER FLOW

Thiết kế flow tối ưu:

```text
Import Story
↓
Configure Translation
↓
Choose Provider
↓
Choose Model
↓
Configure Style
↓
Translate
↓
Review/Edit
↓
Generate Audio
↓
Export
```

Nếu cần cải thiện flow, hãy đề xuất flow mới.

---

# TRANSLATION WORKSPACE

Đây nên là màn hình quan trọng nhất.

Nghiên cứu layout như:

```text
Sidebar
        Chapters

Main Workspace
        Original
        Translation

Context Panel
        Model
        Glossary
        Characters
        Prompt
        Quality
```

Đánh giá xem:

- split editor;
- tabs;
- inspector;
- collapsible panel;

cái nào phù hợp nhất.

---

# SETTINGS UX

Không gom tất cả vào một trang dài.

Phân nhóm:

- AI Providers
- Models
- Translation
- TTS
- Storage
- Appearance
- Advanced

API key cần:

- masked;
- validate;
- connection status;
- clear provider status.

---

# DESIGN SYSTEM

TasteSkill cần đề xuất:

### Colors

- background;
- surface;
- border;
- text;
- accent;
- success;
- warning;
- danger.

### Typography

- heading;
- body;
- reading;
- monospace.

### Components

- Button;
- Input;
- Select;
- Combobox;
- Modal;
- Drawer;
- Tooltip;
- Toast;
- Tabs;
- Table;
- Audio Player;
- Model Picker;
- Provider Picker;
- Chapter Tree;
- Translation Editor;
- Progress component.

### States

- hover;
- active;
- focus;
- selected;
- disabled;
- loading;
- error.

---

# PHASE 6 — SYSTEM ARCHITECTURE

Sau khi hiểu codebase, đề xuất target architecture.

Phải phân biệt:

```text
Current Architecture
vs
Target Architecture
```

Đề xuất module boundaries rõ ràng.

Ví dụ:

```text
Core
 ├── Translation Engine
 ├── Prompt Engine
 ├── Context Engine
 └── Job Engine

AI
 ├── Provider Registry
 ├── Model Registry
 └── Provider Adapters

TTS
 ├── VieNeu Adapter
 ├── Voice Registry
 └── Audio Jobs

Domain
 ├── Story
 ├── Chapter
 ├── Character
 ├── Glossary
 └── Translation

UI
 ├── Library
 ├── Workspace
 ├── Models
 ├── Audio
 └── Settings
```

Đây chỉ là reference.

Architecture cuối cùng phải dựa trên codebase thật.

---

# PHASE 7 — DATA MODEL

Kiểm tra schema hiện tại và nghiên cứu những entity cần thiết.

Ví dụ:

```text
Project
Story
Chapter
Translation
TranslationVersion
Provider
Model
ProviderCredential
Glossary
GlossaryEntry
Character
CharacterAlias
TranslationProfile
Voice
AudioChapter
AudioSegment
Job
```

Không thêm table/entity nếu không cần.

Chỉ đề xuất khi có business justification.

---

# PHASE 8 — SECURITY

Phân tích:

- API key storage;
- secrets;
- local storage;
- backend storage;
- logging;
- credential leakage;
- frontend exposure;
- request proxy.

Đặc biệt:

> Không được để API key provider bị expose ngoài ý muốn.

Đề xuất security architecture phù hợp với loại app hiện tại.

---

# PHASE 9 — PERFORMANCE

Research:

- streaming;
- batching;
- concurrency;
- caching;
- request deduplication;
- TTS queue;
- translation queue;
- virtualization;
- large chapter;
- huge novel;
- thousands of chapters.

Đề xuất performance budget nếu cần.

---

# PHASE 10 — OBSERVABILITY

Nghiên cứu cần theo dõi:

- requests;
- provider;
- model;
- tokens;
- errors;
- latency;
- retries;
- fallback;
- translation jobs;
- TTS jobs.

Nếu phù hợp, đề xuất hệ thống log/debug cho người dùng.

---

# PHASE 11 — ĐƯA RA NHIỀU OPTION

Không chỉ đưa một architecture.

Với các quyết định lớn, hãy đưa ít nhất:

### Option A
Minimal change.

### Option B
Balanced / Recommended.

### Option C
Long-term scalable.

So sánh:

| Criteria | A | B | C |
|---|---:|---:|---:|
| Complexity | | | |
| Migration Cost | | | |
| Maintainability | | | |
| UX | | | |
| Scalability | | | |
| Time to implement | | | |
| Risk | | | |

Sau đó chọn:

> **Recommended Option**

và giải thích lý do.

---

# PHASE 12 — MIGRATION STRATEGY

Không mặc định rewrite toàn bộ project.

Phân tích:

- phần nào giữ;
- phần nào refactor;
- phần nào rewrite;
- phần nào remove.

Tạo bảng:

| Module | Current | Action | Reason |
|---|---|---|---|
| X | ... | Keep | ... |
| Y | ... | Refactor | ... |
| Z | ... | Rewrite | ... |
| W | ... | Remove | ... |

Riêng **UI/UX hiện tại được phép redesign/rewrite hoàn toàn**.

Backend/core chỉ rewrite khi có lý do kỹ thuật rõ ràng.

---

# PHASE 13 — TESTING STRATEGY

Lên plan testing cho implementation sau này:

- unit;
- integration;
- provider adapter;
- translation;
- prompt;
- glossary;
- context memory;
- TTS;
- UI;
- E2E.

Đặc biệt cần mock:

- LLM APIs;
- rate limit;
- timeout;
- malformed response;
- provider outage.

---

# PHASE 14 — SPEC

Sau khi hoàn thành research, tạo một **MASTER SPEC**.

Spec phải đủ rõ để một coding model khác có thể implementation mà không phải tự thiết kế lại kiến trúc.

MASTER SPEC cần chứa:

1. Product Overview
2. Goals
3. Non-goals
4. Current System Analysis
5. Problems
6. User Personas
7. User Flows
8. Functional Requirements
9. Non-functional Requirements
10. Translation Engine
11. Provider Architecture
12. Model Registry
13. Prompt System
14. Context/Memory
15. Glossary
16. Character System
17. VieNeu-TTS
18. Audio Pipeline
19. UI Architecture
20. UX Specification
21. Design System
22. Data Model
23. API Contract
24. Error Model
25. Retry/Fallback
26. Security
27. Performance
28. Observability
29. Testing
30. Migration
31. Risks
32. Open Questions
33. Acceptance Criteria

---

# PHASE 15 — IMPLEMENTATION PLAN

Sau MASTER SPEC, tạo implementation plan cho coding model.

Chia theo milestone.

Ví dụ:

```text
Milestone 0 — Foundation

Milestone 1 — Provider Architecture

Milestone 2 — Translation Engine

Milestone 3 — Context / Glossary

Milestone 4 — New Design System

Milestone 5 — Translation Workspace

Milestone 6 — VieNeu-TTS

Milestone 7 — Audio Workflow

Milestone 8 — QA / Performance

Milestone 9 — Migration / Cleanup
```

Nhưng milestone cuối cùng phải dựa trên project thực tế.

---

# TASK BREAKDOWN

Mỗi task phải có:

```text
Task ID

Goal

Files / Modules likely affected

Dependencies

Detailed changes

Technical notes

Acceptance criteria

Tests required

Risk
```

Task đủ nhỏ để coding model có thể làm tuần tự.

Không tạo task kiểu mơ hồ như:

> "Implement provider system."

Thay vào đó chia nhỏ rõ ràng.

---

# DEPENDENCY GRAPH

Tạo dependency giữa các task.

Ví dụ:

```text
P01 Provider Interface
       ↓
P02 Provider Registry
       ↓
P03 OpenRouter Adapter
       ↓
P04 NIM Adapter
       ↓
P05 Model Picker UI
```

Dùng Mermaid nếu hữu ích.

---

# IMPLEMENTATION ORDER

Phải chỉ rõ:

### Có thể làm song song

Ví dụ:

```text
Provider research
TTS architecture
Design System
```

### Phải làm tuần tự

Ví dụ:

```text
Provider Interface
↓
Registry
↓
Adapters
↓
UI
```

Mục tiêu là để model coding sau này không phá architecture.

---

# ACCEPTANCE CRITERIA

Định nghĩa acceptance criteria rõ cho toàn project.

Ví dụ:

### Multi-provider

- có thể configure nhiều provider;
- validate API key;
- load model;
- chọn model;
- xác định free model;
- provider error không crash app.

### Translation

- translation chapter;
- preserve glossary;
- preserve character naming;
- context giữa chapter.

### VieNeu-TTS

- list voice;
- preview;
- play;
- select;
- generate chapter audio.

### UI

- responsive;
- keyboard usable;
- loading/error state;
- không phụ thuộc design cũ.

Acceptance criteria phải cụ thể hơn sau khi hiểu codebase.

---

# RISK ANALYSIS

Tìm ít nhất các risk liên quan:

- breaking migration;
- API compatibility;
- free model instability;
- rate limits;
- provider changes;
- long context;
- cost explosion;
- translation inconsistency;
- TTS performance;
- audio storage;
- UI rewrite regression.

Đưa mitigation.

---

# OPEN QUESTIONS

Nếu có vấn đề cần tôi quyết định:

Không tự đoán.

Hãy đưa theo format:

```text
QUESTION Q01

Context:
...

Options:
A. ...
B. ...
C. ...

Recommended:
B

Reason:
...

Impact:
...
```

Ưu tiên gom các câu hỏi quan trọng thành một batch thay vì hỏi liên tục từng câu.

---

# BẮT BUỘC RESEARCH TRƯỚC KHI HỎI

Trước khi hỏi tôi một vấn đề:

1. kiểm tra codebase;
2. dùng CodeGraph;
3. dùng Superpower;
4. kiểm tra config/docs;
5. xem có thể suy ra từ project hay không.

Chỉ hỏi khi thực sự là:

> product decision / preference / business decision.

Không hỏi những thứ có thể tự xác định bằng code.

---

# OUTPUT DOCUMENTS

Sau khi nghiên cứu hoàn tất, tạo hoặc đề xuất bộ tài liệu:

```text
docs/
  research/
    current-system-analysis.md
    provider-research.md
    model-research.md
    translation-engine-research.md
    vieneu-tts-research.md
    ui-ux-research.md

  specs/
    master-spec.md
    ui-ux-spec.md
    provider-spec.md
    translation-spec.md
    tts-spec.md

  architecture/
    current-architecture.md
    target-architecture.md

  plans/
    migration-plan.md
    implementation-plan.md
    testing-plan.md
```

Có thể điều chỉnh structure nếu project đã có convention riêng.

---

# FINAL REPORT

Cuối quá trình nghiên cứu, gửi cho tôi một executive report bằng tiếng Việt gồm:

## 1. Tôi đã hiểu project như thế nào

Mô tả ngắn architecture hiện tại.

## 2. 10 vấn đề lớn nhất

Sắp xếp theo impact.

## 3. Những phần nên giữ lại

Không rewrite vô lý.

## 4. Những phần nên refactor

Giải thích lý do.

## 5. Những phần nên rewrite

Đặc biệt UI/UX.

## 6. Architecture đề xuất

Current → Target.

## 7. Multi-provider strategy

Provider nào:

- Phase 1;
- Phase 2;
- optional.

## 8. Free-model strategy

Đề xuất model/provider tốt cho:

- Free;
- Fast;
- Balanced;
- Quality;
- Local.

## 9. Translation Engine

Cách đảm bảo consistency cho truyện dài.

## 10. VieNeu-TTS

Voice preview + generation architecture.

## 11. UI/UX

Navigation + workspace + design system.

## 12. Migration

Thứ tự nâng cấp.

## 13. Implementation Milestones

Danh sách milestone.

## 14. Risks

Critical risks.

## 15. Questions for me

Chỉ những product decision thực sự cần tôi quyết định.

---

# NGUYÊN TẮC QUAN TRỌNG NHẤT

Đừng cố giữ code/design hiện tại chỉ vì nó đã tồn tại.

Nhưng cũng đừng rewrite chỉ vì muốn architecture đẹp hơn.

Mỗi quyết định phải cân bằng:

```text
User Experience
+
Translation Quality
+
Maintainability
+
Extensibility
+
Performance
+
Cost
+
Implementation Complexity
```

Ưu tiên:

> **đơn giản ở bề mặt người dùng, mạnh và mở rộng tốt ở bên trong.**

---

# LƯU Ý CUỐI CÙNG

Bạn đang sử dụng **GPT-6 Astra**.

Trong session này:

**KHÔNG IMPLEMENT.**

Nhiệm vụ của bạn là tận dụng khả năng reasoning/research mạnh của model để:

> **hiểu project sâu → nghiên cứu → challenge kiến trúc hiện tại → đưa options → thiết kế target system → viết spec → lập implementation plan cực kỳ chi tiết.**

Sau khi tôi duyệt SPEC và PLAN, tôi sẽ chuyển sang model coding khác để thực hiện.

Nếu trong quá trình nghiên cứu phát hiện một quyết định quan trọng có nhiều hướng đi, hãy **dừng tại decision point đó, trình bày options + recommendation bằng tiếng Việt** để tôi quyết định trước khi khóa kiến trúc.