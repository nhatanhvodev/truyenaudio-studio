from __future__ import annotations

from dataclasses import dataclass
import hashlib

from app.contracts import (
    ChapterState,
    ImportKind,
    JobKind,
    QaCategory,
    QaSeverity,
    QaStatus,
    RightsStatus,
    RunStatus,
    SourceType,
    TranslationResult,
    Usage,
    UsageUnit,
)
from app.db.models import (
    Chapter,
    Project,
    QaIssue,
    SourceRevision,
    SourceSegment,
    TranslationRun,
    TranslationSegment,
)
from app.modules.translation.repair import RepairService
from app.modules.translation.review import ReviewService


def test_reviewer_only_receives_risky_or_selected_segments(db_session) -> None:
    fixture = _review_fixture(db_session)
    jobs = RecordingJobRunner()
    review = ReviewService(db_session, job_runner=jobs, id_factory=_ids())

    queued = review.enqueue(fixture.chapter_id, selected_ids=(fixture.clean_segment_id,))

    assert {job.source_segment_id for job in queued} == {
        fixture.clean_segment_id,
        fixture.major_segment_id,
        fixture.critical_segment_id,
    }
    assert fixture.minor_segment_id not in {job.source_segment_id for job in queued}
    assert [job.kind for job in jobs.created] == [JobKind.REVIEW, JobKind.REVIEW, JobKind.REVIEW]


def test_repair_requires_explicit_accept(db_session) -> None:
    fixture = _review_fixture(db_session)
    budget = RecordingBudgetGuard()
    repair = RepairService(
        db_session,
        translator=SuffixTranslator("Lam Dong da sua."),
        budget_guard=budget,
        id_factory=_ids(),
    )

    proposal = repair.propose(fixture.chapter_id, (fixture.major_segment_id,), "qwen-mt-plus")

    assert repair.current_target(fixture.major_segment_id) == "林动 bi sai."
    assert proposal.replacements[0].target_text == "Lam Dong da sua."
    assert proposal.base_run_id == fixture.run_id
    assert proposal.estimated_cost_vnd == 123
    assert budget.categories == ["QA_REPAIR"]


def test_accept_repair_creates_new_run_and_keeps_unchanged_rows(db_session) -> None:
    fixture = _review_fixture(db_session)
    repair = RepairService(db_session, translator=SuffixTranslator("Lam Dong da sua."), id_factory=_ids())
    proposal = repair.propose(fixture.chapter_id, (fixture.major_segment_id,), "qwen-mt-plus")

    accepted = repair.accept_repair(proposal.id, proposal.hash)

    by_source = {segment.source_segment_id: segment.target_text for segment in accepted.segments}
    assert accepted.id != fixture.run_id
    assert by_source[fixture.major_segment_id] == "Lam Dong da sua."
    assert by_source[fixture.clean_segment_id] == "Ban dich sach."
    assert db_session.get(TranslationRun, fixture.run_id).status == RunStatus.SUPERSEDED.value


class RecordingJobRunner:
    def __init__(self) -> None:
        self.created = []

    def enqueue(self, kind: JobKind, project_id: str, chapter_id: str, idempotency_key: str, priority: int = 100):
        job = _QueuedJob(
            id=f"job-{len(self.created) + 1}",
            kind=kind,
            project_id=project_id,
            chapter_id=chapter_id,
            idempotency_key=idempotency_key,
            priority=priority,
        )
        self.created.append(job)
        return job


class RecordingBudgetGuard:
    def __init__(self) -> None:
        self.categories: list[str] = []

    def quote_usage(self, **kwargs):
        self.categories.append(str(kwargs["category"]))
        return _Quote(total_vnd=123)


class SuffixTranslator:
    def __init__(self, target_text: str) -> None:
        self.target_text = target_text
        self.last_request = None

    def capabilities(self) -> dict[str, object]:
        return {"provider": "qwen", "model": "qwen-mt-plus", "region": "frankfurt"}

    async def translate(self, request):
        self.last_request = request
        return TranslationResult(
            target_text=self.target_text,
            provider="qwen",
            model="qwen-mt-plus",
            provider_version="1",
            usage=(Usage(UsageUnit.INPUT_TOKEN.value, len(request.source_text)),),
        )


@dataclass(frozen=True)
class _QueuedJob:
    id: str
    kind: JobKind
    project_id: str
    chapter_id: str
    idempotency_key: str
    priority: int


@dataclass(frozen=True)
class _Quote:
    total_vnd: int


@dataclass(frozen=True)
class _Fixture:
    chapter_id: str
    run_id: str
    clean_segment_id: str
    major_segment_id: str
    critical_segment_id: str
    minor_segment_id: str


def _review_fixture(db_session) -> _Fixture:
    project = Project(
        id="018f0000-0000-7000-8000-100000000201",
        title="Truyen",
        slug="selective-review",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        default_language="zh-CN",
        target_language="vi-VN",
    )
    chapter = Chapter(
        id="018f0000-0000-7000-8000-200000000201",
        project_id=project.id,
        ordinal=5,
        state=ChapterState.TRANSLATION_REVIEW.value,
    )
    revision = SourceRevision(
        id="018f0000-0000-7000-8000-300000000201",
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="A\nB\nC\nD",
        normalized_sha256=_sha("A\nB\nC\nD"),
        han_char_count=0,
        total_char_count=7,
        normalizer_version="nfc-v1",
    )
    db_session.add(project)
    db_session.flush()
    db_session.add(chapter)
    db_session.flush()
    db_session.add(revision)
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    segments = (
        _segment(revision.id, "018f0000-0000-7000-8000-400000000201", 0, "clean"),
        _segment(revision.id, "018f0000-0000-7000-8000-400000000202", 1, "major"),
        _segment(revision.id, "018f0000-0000-7000-8000-400000000203", 2, "critical"),
        _segment(revision.id, "018f0000-0000-7000-8000-400000000204", 3, "minor"),
    )
    db_session.add_all(segments)
    run = TranslationRun(
        id="018f0000-0000-7000-8000-500000000201",
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        prompt_version="translation-v1",
        status=RunStatus.REVIEW.value,
        translation_text_sha256="a" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    db_session.add(run)
    db_session.flush()
    targets = ("Ban dich sach.", "林动 bi sai.", "Ban dich thieu.", "Ban dich lap lap lap lap.")
    for segment, target in zip(segments, targets, strict=True):
        db_session.add(
            TranslationSegment(
                id=f"018f0000-0000-7000-8000-60000000020{segment.segment_index + 1}",
                translation_run_id=run.id,
                source_segment_id=segment.id,
                target_text=target,
                target_sha256=_sha(target),
                was_cache_hit=False,
                manually_edited=False,
            )
        )
    _issue(db_session, chapter.id, run.id, segments[1].id, QaSeverity.MAJOR, "major", 1)
    _issue(db_session, chapter.id, run.id, segments[2].id, QaSeverity.CRITICAL, "critical", 2)
    _issue(db_session, chapter.id, run.id, segments[3].id, QaSeverity.MINOR, "minor", 3)
    db_session.commit()
    return _Fixture(chapter.id, run.id, segments[0].id, segments[1].id, segments[2].id, segments[3].id)


def _segment(revision_id: str, segment_id: str, index: int, text: str) -> SourceSegment:
    return SourceSegment(
        id=segment_id,
        source_revision_id=revision_id,
        segment_index=index,
        paragraph_start=index,
        paragraph_end=index,
        source_text=text,
        source_sha256=_sha(text),
        segment_kind="SOURCE",
    )


def _issue(
    db_session,
    chapter_id: str,
    run_id: str,
    source_segment_id: str,
    severity: QaSeverity,
    suffix: str,
    index: int,
) -> None:
    db_session.add(
        QaIssue(
            id=f"018f0000-0000-7000-8000-70000000020{index}",
            chapter_id=chapter_id,
            translation_run_id=run_id,
            category=QaCategory.RESIDUAL_HAN.value,
            severity=severity.value,
            status=QaStatus.OPEN.value,
            source_segment_id=source_segment_id,
            evidence=suffix,
            suggestion=f"Fix {suffix}.",
            rule_or_model="test",
        )
    )


_ID_COUNTER = 0


def _ids():
    def factory() -> str:
        global _ID_COUNTER
        _ID_COUNTER += 1
        return f"018f0000-0000-7000-8000-900100{_ID_COUNTER:06d}"

    return factory


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
