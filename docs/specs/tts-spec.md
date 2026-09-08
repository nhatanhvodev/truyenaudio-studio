# VieNeu-TTS Spec (LOCKED BASELINE; Q03 C)

## Contract

Engine manifest pinned gồm package/model/voice revision, license, locale, native sample rate và capability probe. `listVoices`, `preview`, `synthesizeSegment`, `master`, `probe` là boundary; không quảng cáo tham số engine chưa chứng minh.

## Flow

Voice catalog → sample artifact → custom preview → select one narrator → segment jobs → master/probe → player/review → export. Multi-voice opt-in, role review thủ công.

## Reliability

Cache theo text/revision/voice/settings; idempotency per segment; resumable/cancel-safe; giữ master cũ; output checksum. Native sample rate lấy từ manifest/probe; resample 44,1 kHz tại master nếu app contract yêu cầu. Part-master/checkpoint, Range player, preview/A-B và voice-plan review theo [C08](implementation-contracts.md), task A01–A05. Paid/cloud và VieNeu thật giữ `NOT_RUN` trước invocation/probe/playback evidence; fake không chứng minh TTS quality.
