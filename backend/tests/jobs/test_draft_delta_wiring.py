"""J04 round 4: worker deltas land in the U04 draft store and are served live.

Coverage:

- ``append_draft_delta`` appends/dedupes/gaps and never bumps the revision on a
  no-op frame;
- the persisted draft **overlays** the run's approved segment text, so a partial
  delta is visible through ``/api/jobs/{id}/draft`` frames while the approved
  translation segment stays untouched;
- ``TranslationWorkflow`` consumes an adapter stream through the sink, so the
  draft is written by the worker process (own engine) and the API process reads
  it from its own session.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

from sqlalchemy import Engine, select

from app.contracts import (
    ChapterState,
    ImportKind,
    JobKind,
    JobStatus,
    RightsStatus,
    RunStatus,
    SourceType,
    TranslationRequest,
    TranslationResult,
)
from app.db.base import session_factory
from app.db.models import (
    Chapter,
    Job,
    Project,
    SourceRevision,
    SourceSegment,
    TranslationRun,
    TranslationSegment,
    WorkspaceDraft,
)
from app.modules.translation.draft_service import draft_frames, draft_snapshot
from app.modules.translation.draft_sink import DraftDeltaSinkFactory
from app.modules.translation.drafts import (
    append_draft_delta,
    restore_draft,
)
from app.modules.translation.workflow import TranslationWorkflow

PROJECT_ID = "018f0000-0000-7000-8000-000000000701"
CHAPTER_ID = "018f0000-0000-7000-8000-000000000702"
REVISION_ID = "018f0000-0000-7000-8000-000000000703"
RUN_ID = "018f0000-0000-7000-8000-000000000704"
JOB_ID = "018f0000-0000-7000-8000-000000000705"
SEGMENT_IDS = (
    "018f0000-0000-7000-8000-000000000711",
    "018f0000-0000-7000-8000-000000000712",
)


def _seed(session, *, run_status: str = RunStatus.REVIEW.value) -> None:
    project = Project(
        id=PROJECT_ID,
        title="T",
        slug="draft-delta",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        default_language="zh-CN",
        target_language="vi-VN",
    )
    session.add(project)
    session.flush()
    chapter = Chapter(
        id=CHAPTER_ID,
        project_id=project.id,
        ordinal=1,
        state=ChapterState.TRANSLATION_REVIEW.value,
    )
    session.add(chapter)
    session.flush()
    revision = SourceRevision(
        id=REVISION_ID,
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="一\n二",
        normalized_sha256=hashlib.sha256("一\n二".encode("utf-8")).hexdigest(),
        han_char_count=0,
        total_char_count=3,
        normalizer_version="nfc-v1",
    )
    session.add(revision)
    session.flush()
    chapter.active_source_revision_id = revision.id
    run = TranslationRun(
        id=RUN_ID,
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        prompt_version="translation-v1",
        status=run_status,
        translation_text_sha256="b" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    session.add(run)
    session.flush()
    for index, (source_text, target_text) in enumerate((("一", "Một"), ("二", "Hai"))):
        segment = SourceSegment(
            id=SEGMENT_IDS[index],
            source_revision_id=revision.id,
            segment_index=index,
            paragraph_start=index,
            paragraph_end=index,
            source_text=source_text,
            source_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            segment_kind="SOURCE",
        )
        session.add(segment)
        session.flush()
        session.add(
            TranslationSegment(
                id=f"018f0000-0000-7000-8000-00000000072{index}",
                translation_run_id=run.id,
                source_segment_id=segment.id,
                target_text=target_text,
                target_sha256=hashlib.sha256(target_text.encode("utf-8")).hexdigest(),
                was_cache_hit=False,
                manually_edited=False,
            )
        )
    session.add(
        Job(
            id=JOB_ID,
            kind=JobKind.TRANSLATE.value,
            status=JobStatus.RUNNING.value,
            project_id=project.id,
            chapter_id=chapter.id,
            idempotency_key="draft-delta",
            priority=100,
            progress_current=0,
            progress_total=2,
        )
    )
    session.commit()


def test_append_draft_delta_is_replay_safe(db_session) -> None:
    _seed(db_session)

    first = append_draft_delta(db_session, CHAPTER_ID, REVISION_ID, SEGMENT_IDS[0], "Một", offset=0)
    assert first.status == "applied"
    assert first.revision == 1
    assert first.offset == 3

    # A replayed frame (same offset) is a no-op and must not bump the revision,
    # so readers never observe a phantom change.
    replay = append_draft_delta(db_session, CHAPTER_ID, REVISION_ID, SEGMENT_IDS[0], "Một", offset=0)
    assert replay.status == "duplicate"
    assert replay.revision == 1
    assert restore_draft(db_session, CHAPTER_ID, REVISION_ID).revision == 1

    # A frame ahead of the stored offset is a gap: nothing is written.
    gap = append_draft_delta(db_session, CHAPTER_ID, REVISION_ID, SEGMENT_IDS[0], " x", offset=99)
    assert gap.status == "gap"
    assert gap.offset == 3

    # Empty deltas are no-ops too.
    empty = append_draft_delta(db_session, CHAPTER_ID, REVISION_ID, SEGMENT_IDS[0], "", offset=3)
    assert empty.status == "duplicate"

    second = append_draft_delta(db_session, CHAPTER_ID, REVISION_ID, SEGMENT_IDS[0], " hai", offset=3)
    assert second.status == "applied"
    assert second.revision == 2
    assert second.text == "Một hai"

    draft = restore_draft(db_session, CHAPTER_ID, REVISION_ID)
    assert draft.content == {SEGMENT_IDS[0]: "Một hai"}


def test_append_draft_delta_rejects_unknown_scope(db_session) -> None:
    _seed(db_session)

    try:
        append_draft_delta(db_session, CHAPTER_ID, REVISION_ID, "018f0000-0000-7000-8000-000000000799", "x")
    except ValueError as exc:
        assert str(exc) == "DRAFT_SEGMENT_UNKNOWN"
    else:  # pragma: no cover - defensive
        raise AssertionError("unknown segment must be rejected")

    try:
        append_draft_delta(db_session, "018f0000-0000-7000-8000-000000000798", REVISION_ID, SEGMENT_IDS[0], "x")
    except ValueError as exc:
        assert str(exc) == "CHAPTER_NOT_FOUND"
    else:  # pragma: no cover - defensive
        raise AssertionError("unknown chapter must be rejected")


def test_persisted_draft_overlays_approved_segments(db_session) -> None:
    _seed(db_session)
    append_draft_delta(db_session, CHAPTER_ID, REVISION_ID, SEGMENT_IDS[0], "Mộ", offset=0)

    frames = draft_frames(db_session, JOB_ID)
    snapshot = draft_snapshot(db_session, JOB_ID)

    # The partial delta is served live: segment 0 shows the draft text, while
    # the approved run segment keeps its own text.
    assert [frame["text"] for frame in frames] == ["Mộ", "Hai"]
    assert [frame["offset"] for frame in frames] == [2, 5]
    assert snapshot["text"] == "MộHai"
    assert snapshot["offset"] == 5
    assert snapshot["status"] == "FINISHED"
    assert snapshot["approvable"] is False
    assert snapshot["draftRevision"] == 1

    approved = db_session.scalars(
        select(TranslationSegment.target_text).where(TranslationSegment.translation_run_id == RUN_ID)
    ).all()
    assert list(approved) == ["Một", "Hai"]


def test_draft_snapshot_reports_running_run_and_no_draft(db_session) -> None:
    _seed(db_session, run_status=RunStatus.RUNNING.value)

    snapshot = draft_snapshot(db_session, JOB_ID)

    assert snapshot["status"] == "DRAFT"
    assert snapshot["draftRevision"] is None
    assert snapshot["text"] == "MộtHai"


class _StreamingFakeTranslator:
    """Adapter that streams a deterministic translation in two deltas."""

    def capabilities(self) -> dict[str, object]:
        return {"provider": "fake-stream", "model": "fake-stream-1", "provider_version": "v1"}

    def _full(self, request: TranslationRequest) -> str:
        return f"VI:{request.source_text}"

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        return self._result(self._full(request))

    async def stream_translate(self, request: TranslationRequest) -> AsyncIterator[str]:
        full = self._full(request)
        half = len(full) // 2 or len(full)
        yield full[:half]
        if full[half:]:
            yield full[half:]

    def stream_result(self, text: str) -> TranslationResult:
        return self._result(text)

    def _result(self, text: str) -> TranslationResult:
        return TranslationResult(
            target_text=text,
            provider="fake-stream",
            model="fake-stream-1",
            provider_version="v1",
            usage=(),
        )


def test_workflow_streams_deltas_into_draft_store(migrated_engine: Engine) -> None:
    db_path = migrated_engine.url.database
    writer = session_factory(migrated_engine)()
    api_session = session_factory(migrated_engine)()
    sink = DraftDeltaSinkFactory(db_path)
    try:
        _seed(writer)
        # Move the chapter back to NORMALIZED so enqueue_translation runs the
        # translation path.
        chapter = writer.get(Chapter, CHAPTER_ID)
        chapter.state = ChapterState.NORMALIZED.value
        writer.commit()

        workflow = TranslationWorkflow(
            writer,
            translator=_StreamingFakeTranslator(),
            draft_sink=sink,
        )
        workflow.enqueue_translation(
            CHAPTER_ID,
            cloud_consent_id="consent-1",
            budget_authorization_id="budget-1",
        )

        # The worker wrote provider deltas into the U04 draft store...
        drafts = writer.scalars(select(WorkspaceDraft)).all()
        assert len(drafts) == 1
        assert drafts[0].content_json == {
            SEGMENT_IDS[0]: "VI:一",
            SEGMENT_IDS[1]: "VI:二",
        }
        assert sink.applied == 4  # two deltas per segment, two segments
        assert sink.gaps == 0

        # ... and the API process serves the same live draft from its own
        # session without re-running the provider.
        api_session.expire_all()
        snapshot = draft_snapshot(api_session, JOB_ID)
        assert snapshot["text"] == "VI:一VI:二"
        assert snapshot["approvable"] is False
        assert snapshot["draftRevision"] == 4  # one revision bump per applied delta
        frames = draft_frames(api_session, JOB_ID)
        assert [frame["offset"] for frame in frames] == [4, 8]
        assert [frame["text"] for frame in frames] == ["VI:一", "VI:二"]

        # The approved run segments are the full streamed text, and the draft
        # remains a non-approvable view.
        latest_run = writer.scalars(
            select(TranslationRun)
            .where(TranslationRun.chapter_id == CHAPTER_ID)
            .order_by(TranslationRun.created_at.desc(), TranslationRun.id.desc())
            .limit(1)
        ).one()
        assert latest_run.id != RUN_ID
        assert latest_run.status == RunStatus.REVIEW.value
        segments = writer.scalars(
            select(TranslationSegment.target_text)
            .where(TranslationSegment.translation_run_id == latest_run.id)
            .order_by(TranslationSegment.source_segment_id)
        ).all()
        assert sorted(segments) == ["VI:一", "VI:二"]
    finally:
        sink.close()
        api_session.close()
        writer.close()
