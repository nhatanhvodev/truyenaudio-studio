# Nghiên cứu engine dịch truyện dài

Ngày: 07/09/2026. Trạng thái: **nghiên cứu và phương án, chưa khóa spec**. Không chạy benchmark hoặc gọi model. Baseline và evidence: [current-system-analysis](current-system-analysis.md).

## 1. Hệ hiện tại giải quyết được gì

`TranslationRequest` có terms, tm_list, domain_instruction, story_memory; workflow hash glossary/style/memory và tạo revision. Segmenter chia theo đoạn/câu và 1.200/2.000/2.500 ký tự Hán, không phải token budget. QA kiểm tra trống, tỷ lệ độ dài, chữ Hán sót, số/đơn vị, thuật ngữ khóa, lặp và meta text. Đây là nền để mở rộng, không phải engine trống cần viết lại toàn bộ.

Khoảng trống xác nhận trong source:

- `workflow.py:415` luôn truyền TM rỗng; context không được xếp hạng/budget token.
- `story_memory.py:29` chỉ đọc memory theo ordinal; không thấy đường production tạo và duyệt chapter summary trong các module đã khảo sát.
- `workflow.py:697` lặp lại truy vấn memory, trong khi `repair.py` dùng StoryMemoryService với cách hash khác.
- `gemini_mt.py:112–150` hard-code prompt Trung-Việt, temperature 0.3 và output 8.192; chỉ lấy 5 summary cuối theo thứ tự hiện có, không chắc là 5 chương gần nhất.
- Gemini xử lý chữ Hán sót bằng `convert_hanviet`; quality issue có thể bị che bởi biến đổi hậu kỳ không có diff cho người duyệt.
- Review/repair có khung chọn đoạn nhưng worker production chưa cấu hình, repair proposals nằm trong RAM.

## 2. Nghiên cứu từ nguồn chính thức

Nghiên cứu WMT 2023 trên dịch văn học cho thấy ngữ cảnh đoạn có thể cải thiện so với dịch từng câu; lỗi nghiêm trọng và bỏ sót vẫn tồn tại, cần đánh giá con người. Đây là bằng chứng cho thiết kế giữ ngữ cảnh, **không phải chứng minh Gemini/Qwen hiện tại tốt cho Trung–Việt**. [Karpinska & Iyyer, WMT 2023](https://aclanthology.org/2023.wmt-1.41/)

Benchmark ACL 2024 ghi nhận hạn chế của LLM trong dịch văn bản dài và suy giảm khi tài liệu dài hơn. Context window lớn không đủ để gắn nhãn “dịch nguyên tiểu thuyết tốt”. [Wang et al., ACL 2024](https://aclanthology.org/2024.findings-acl.428/)

Nghiên cứu chapter-to-chapter xem consistency liên chương là bài toán riêng, có benchmark chuyên biệt. Đề xuất của studio là đánh giá chuỗi chương, ngoài bộ câu rời. [Jin et al., 2024](https://arxiv.org/abs/2407.08978)

OmegaT tách translation memory (cặp nguồn–đích đã dịch) khỏi glossary (thuật ngữ), có fuzzy matching. Studio nên giữ phân biệt này; synopsis của truyện không phải TM. [Kho chính thức OmegaT](https://github.com/omegat-org/omegat)

Các thiết kế bên dưới là **suy luận kiến trúc cho codebase này**, không phải kết quả thực nghiệm đã đạt.

## 3. Ba pipeline và bốn profile

| Phương án | Pipeline | Ưu điểm | Nhược điểm |
|---|---|---|---|
| A | Source → Translate → deterministic QA | Ít lượt, dễ dự toán | Lỗi đại từ/văn phong còn cần người sửa |
| B | Source → Translate → Review → Polish có chọn đoạn | Có thể tăng chất lượng ở chỗ rủi ro, tiết kiệm so với rewrite hết | Cần revision/diff, reviewer vẫn có thể sai |
| C | Source → Analyze → Translate → Critique → Rewrite | Nhiều cơ hội phân tích nhân vật/giọng văn | Tốn token và độ trễ, rewrite có thể làm lệch nghĩa; không bảo đảm tốt hơn |

| Profile đề xuất | Cấu hình | Giới hạn và cách duyệt |
|---|---|---|
| Economy | A + glossary/context tối thiểu | Một lần dịch/chunk, QA xác định; lỗi vẫn chặn approval theo policy |
| Balanced | A + review đoạn QA có lỗi hoặc người dùng chọn; polish chỉ nếu được yêu cầu | Mặc định đề xuất cho cá nhân; giữ output ban đầu, hiển thị diff |
| Quality | Review mọi chunk; polish những chunk được đánh dấu | Quote bao gồm cả review và polish tối đa một vòng |
| Maximum Quality | C, một vòng critique/rewrite | Opt-in từng chương/batch, hard budget; dừng nếu không cải thiện hoặc QA mới tăng |

Không dùng hệ số “gấp 2/3/4 lần” làm giá cố định. Với mỗi stage s: chi phí = input_tokens_s × giá_input_s + output_tokens_s × giá_output_s + phí riêng provider. Input review có cả source + target + context; thinking/cached-token pricing nếu provider dùng phải tính riêng. Độ trễ = queue + các stage tuần tự + retry. So sánh bằng token/latency thật trên cùng corpus sau duyệt budget; giá chưa biết không coi là 0.

## 4. Context và consistency

Đề xuất snapshot bất biến cho mỗi run:

1. Khóa source revision, profile version, model metadata, prompt version, glossary revision và context revision.
2. Lấy glossary khớp source; tên/thuật ngữ người dùng khóa có ưu tiên cao. Nếu rule mâu thuẫn, báo conflict trước network.
3. Lấy entity liên quan trong đoạn, aliases và quan hệ có evidence; giới tính chưa biết lưu `unknown`, không tự đoán thành fact.
4. Lấy summary **đã duyệt** của các chương trước; không lấy chương tương lai. Tóm tắt do AI tạo chỉ là candidate cho tới khi được chấp nhận.
5. Lấy vài đoạn dịch đã duyệt lân cận làm continuity; không đưa full history vô hạn.
6. TM exact match được tái dùng chỉ khi source + project/scope + glossary/style/version phù hợp. Fuzzy match chỉ là gợi ý, không ghi đè bản dịch tự động.
7. Ghi selection trace: source ids, revision hashes, token estimate, các mục bị cắt.

Token rule đề xuất: `prompt + source + context + reserved_output + safety_margin <= model_context_limit`. Giá trị limit/output lấy metadata của model thật; native MT có giới hạn ký tự riêng phải thỏa cả hai. Nếu overflow: bỏ context ít liên quan, rút summary, chia nhỏ ở biên đoạn/câu; **không truncate nguồn**. Mức dự phòng khởi đầu 10% và tỉ lệ output phải hiệu chỉnh bằng corpus, không coi là thông số đã đo.

Job chương N có dependency vào context snapshot cần dùng. Batch liên chương mặc định tuần tự; batch độc lập có thể dùng snapshot cố định và báo người dùng consistency bị giới hạn. Sửa chương N không tự dịch lại toàn bộ truyện: đánh dấu context/audio downstream stale và cho xem phạm vi ảnh hưởng.

## 5. Glossary, nhân vật và style

GlossaryEntry hiện có source_term/target_term/reading/category/gender/addressing_notes/is_locked/revision. Đề xuất bổ sung forbidden translations, description, scope theo project/chapter interval, provenance; tránh thêm một entity Glossary container không có nghiệp vụ riêng.

Character có canonical name, aliases, gender nullable/unknown, role, note và evidence. Addressing là quan hệ **có hướng** giữa speaker → addressee và có hiệu lực theo chương; không thể một `pronoun` duy nhất cho cả nhân vật. TTS VoiceRole là vai giọng, không đồng nhất Character: nhiều nhân vật có thể dùng cùng voice, narrator không nhất thiết là nhân vật truyện.

Style profile tách hai chiều: độ trung thành (sát nghĩa/tự nhiên/văn học) và thể loại (web novel/light novel/cổ trang/hiện đại/fantasy/wuxia/xianxia). Một preset là tổ hợp, không 9 nút loại trừ nhau. Custom instructions có preview phần thực gửi đi và version; glossary khóa thắng style nhưng người dùng có thể sửa rule bằng revision rõ ràng.

Cân nhắc 3 cách lưu Character: A tận dụng notes/glossary (nhanh, quan hệ khó); B bảng character + alias/relationship khi schema đã chốt (đề xuất); C knowledge graph/vector store (chưa có lợi ích đủ cho app local). Không tự index embedding hoặc thêm vector DB trong bước này.

## 6. Prompt Builder

Builder thuộc translation engine; adapter chỉ chuyển payload protocol. Proposal contract (không phải code production):

```text
buildPrompt(sourceRevision, segmentIds, profileSnapshot, contextSnapshot,
            glossarySnapshot, stage, capabilities)
  -> PromptEnvelope { version, system, instructions, untrustedSource,
                      expectedSegmentIds, outputContract, hash, tokenEstimate }
```

Thứ tự: system policy → translation/style instructions → glossary/character/context có phân vùng → user custom instructions → source như dữ liệu. Nội dung truyện có câu “bỏ qua hướng dẫn” vẫn là văn bản cần dịch; không có tool execution trong translation call. Native MT như Qwen-MT cần builder tương ứng domain/terms/TM của API, không ép mọi provider nhận chat system prompt.

Output phải map đủ segment id, không thêm/mất/đổi thứ tự. Structured JSON chỉ dùng khi capability đã kiểm chứng; text-only dùng một segment/request hoặc delimiter protocol có parser kiểm tra. `finish_reason=length`, empty/refusal/malformed không được coi là bản dịch thành công. Streaming là bản nháp hiển thị; chỉ commit output hoàn chỉnh đã kiểm tra.

## 7. Chất lượng và benchmark dự kiến

Corpus do người dùng có quyền xử lý: 30 đoạn Trung–Việt chia đều thoại, đại từ ẩn, tên/aliases, số/đơn vị, cổ trang, hiện đại; thêm 3 chuỗi × 3 chương liên tiếp. Nhật/Hàn là nhánh nghiên cứu riêng, không gắn nhãn hỗ trợ trước khi kiểm thử corpus tương ứng.

Chấm blind theo cặp model/profile: đúng nghĩa 35%, không bỏ/thêm 20%, thuật ngữ/tên 20%, xưng hô 15%, văn phong/format 10%. Trọng số là đề xuất sản phẩm. Lỗi critical (đảo nghĩa, bỏ đoạn, sai người thực hiện, mất số quan trọng) là gate riêng, không được che bởi điểm trung bình. Người duyệt bilingual đánh dấu span/evidence và thời gian sửa; LLM judge chỉ advisory.

Lưu mỗi lần chạy: provider/model resolved + timestamp + prompt/context/glossary hash + sampling config + usage/cost + first token/total latency + retry/fallback + raw-output artifact có chính sách riêng. Chạy lại ứng viên để đo biến thiên; báo median/p95 với cỡ mẫu. Nhãn Recommended/High Quality/Translation Optimized chỉ được bật sau kết quả benchmark có provenance, hoặc ghi rõ “ứng viên chưa đánh giá”.

Acceptance dự kiến: round-trip source segmentation không mất ký tự; 100% segment id đủ; glossary khóa xuất hiện đúng phạm vi; không tự sửa số để vượt QA; không ghi đè revision được duyệt; lỗi context không đưa nguồn bị cắt lên provider; resume không tạo hai kết quả active cho cùng logical segment. Đây là tiêu chí để viết tests sau Q01, chưa phải kết quả đã đạt.

## 8. Điểm quyết định còn mở

Q01 ở [upgrade-decision](../architecture/upgrade-decision.md) quyết định độ sâu engine/job/UI cho đợt nâng cấp. Các cấu hình profile trên là đề xuất. Không cần hỏi lại stack/ngôn ngữ mặc định vì source đã xác nhận Trung–Việt và app local; việc hỗ trợ Nhật/Hàn production là phạm vi bổ sung cần duyệt nếu muốn đưa vào release đầu.
