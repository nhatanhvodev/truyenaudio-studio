from __future__ import annotations

import pytest

from app.contracts import OperationContext, ReviewRequest, SynthesisRequest, TranslationRequest
from app.providers.fake import FakeAudioProcessor, FakeReviewer, FakeTranslator, FakeTts
from tests.providers.contract_suite import (
    assert_audio_processor_contract,
    assert_reviewer_contract,
    assert_translator_contract,
    assert_tts_contract,
)


@pytest.fixture
def operation_context() -> OperationContext:
    return OperationContext(
        operation_id="op-1",
        cache_key="cache-1",
        timeout_seconds=30,
        estimated_units=12,
        budget_authorization_id=None,
        cloud_consent_id=None,
    )


@pytest.fixture
def translation_request(operation_context: OperationContext) -> TranslationRequest:
    return TranslationRequest(
        context=operation_context,
        source_segment_id="segment-1",
        source_text="Xin chao",
        source_language="zh-CN",
        target_language="vi-VN",
        terms=(("道", "dao"),),
        tm_list=(("old", "cu"),),
        domain_instruction="plain",
        story_memory=("hero enters",),
    )


@pytest.fixture
def clean_review_request(operation_context: OperationContext) -> ReviewRequest:
    return ReviewRequest(
        context=operation_context,
        source_segment_id="segment-1",
        source_text="abc",
        target_text="ban dich sach",
    )


@pytest.fixture
def han_review_request(operation_context: OperationContext) -> ReviewRequest:
    return ReviewRequest(
        context=operation_context,
        source_segment_id="segment-2",
        source_text="abc",
        target_text="ban dich con 漢 tu",
    )


@pytest.fixture
def synthesis_request(operation_context: OperationContext) -> SynthesisRequest:
    return SynthesisRequest(
        context=operation_context,
        speech_segment_id="speech-1",
        narration_text="Xin chao ban doc",
        locale="vi-VN",
        voice_id="fake-vi-narrator",
        speed="1.0",
        pitch="0",
        style=None,
        sample_rate=44_100,
    )


@pytest.mark.asyncio
async def test_fake_translation_is_deterministic_and_local(translation_request: TranslationRequest, monkeypatch) -> None:
    await assert_translator_contract(FakeTranslator(), translation_request, monkeypatch)


@pytest.mark.asyncio
async def test_fake_review_flags_only_residual_han(
    clean_review_request: ReviewRequest,
    han_review_request: ReviewRequest,
    monkeypatch,
) -> None:
    await assert_reviewer_contract(FakeReviewer(), clean_review_request, han_review_request, monkeypatch)


@pytest.mark.asyncio
async def test_fake_tts_writes_deterministic_locked_wav(
    synthesis_request: SynthesisRequest,
    tmp_path,
    monkeypatch,
) -> None:
    await assert_tts_contract(FakeTts(), synthesis_request, tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_fake_audio_processor_streams_compatible_wavs(
    synthesis_request: SynthesisRequest,
    tmp_path,
    monkeypatch,
) -> None:
    await assert_audio_processor_contract(FakeAudioProcessor(), FakeTts(), synthesis_request, tmp_path, monkeypatch)
