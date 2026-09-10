from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.review import create_review_router
from app.contracts import ChapterState, ImportKind, RightsStatus, RunStatus, SourceType
from app.db.base import create_engine_for, session_factory
from app.db.models import (
    Chapter,
    Project,
    SourceRevision,
    SourceSegment,
    TranslationRun,
    TranslationSegment,
)
from app.modules.translation.repair import RepairConflict, RepairService
from app.settings.config import Settings

PROPOSAL_KEYS = {
    "id",
    "baseRunId",
    "baseRunSha256",
    "providerModel",
    "storyMemoryRevisionHash",
    "hash",
    "estimatedCostVnd",
    "replacements",
}
REPLACEMENT_KEYS = {"sourceSegmentId", "sourceText", "currentTargetText", "targetText"}


@pytest.fixture(autouse=True)
def _clear_proposals() -> Iterator[None]:
    """Keep the in-memory proposal registry isolated between tests."""
    from app.modules.translation import repair as repair_module

    repair_module._PROPOSALS.clear()
    yield
    repair_module._PROPOSALS.clear()


def test_preview_returns_stable_immutable_proposal_hash(tmp_path) -> None:
    client, chapter_id = _client_with_fixture(tmp_path)
    body = {"selectedSegmentIds": [_fixture().major_segment_id]}

    first = client.post(f"/api/chapters/{chapter_id}/review/repair-preview", json=body)
    second = client.post(f"/api/chapters/{chapter_id}/review/repair-preview", json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    one, two = first.json(), second.json()
    # Same input => same proposal payload hash (immutability contract).
    assert one["hash"] == two["hash"]
    assert one["baseRunId"] == two["baseRunId"]
    assert one["replacements"] == two["replacements"]
    # Fresh ids distinguish the two stored proposals even when the hash repeats.
    assert one["id"] != two["id"]


def test_preview_matches_frontend_camel_case_contract(tmp_path) -> None:
    client, chapter_id = _client_with_fixture(tmp_path)

    response = client.post(
        f"/api/chapters/{chapter_id}/review/repair-preview",
        json={"selectedSegmentIds": [_fixture().major_segment_id]},
    )

    assert response.status_code == 200
    proposal = response.json()
    assert set(proposal) == PROPOSAL_KEYS
    assert len(proposal["replacements"]) == 1
    assert set(proposal["replacements"][0]) == REPLACEMENT_KEYS
    assert proposal["estimatedCostVnd"] == 0
    assert len(proposal["hash"]) == 64
    replacement = proposal["replacements"][0]
    assert replacement["sourceSegmentId"] == _fixture().major_segment_id
    assert replacement["currentTargetText"] != replacement["targetText"]


def test_preview_does_not_modify_any_run_or_segment(tmp_path) -> None:
    fixture = _fixture()
    client, chapter_id = _client_with_fixture(tmp_path)
    before = _count_rows(tmp_path, chapter_id)

    response = client.post(
        f"/api/chapters/{chapter_id}/review/repair-preview",
        json={"selectedSegmentIds": [fixture.major_segment_id, fixture.clean_segment_id]},
    )

    assert response.status_code == 200
    after = _count_rows(tmp_path, chapter_id)
    # Preview is read-only: no new runs/segments and no target text changed.
    assert before == after
    engine = create_engine_for(tmp_path / "studio.sqlite3")
    with session_factory(engine)() as session:
        targets = dict(
            session.execute(
                select(TranslationSegment.source_segment_id, TranslationSegment.target_text).where(
                    TranslationSegment.translation_run_id == fixture.run_id
                )
            ).all()
        )
    engine.dispose()
    assert targets[fixture.major_segment_id] == "林动 bi sai."
    assert targets[fixture.clean_segment_id] == "Ban dich sach."


def test_apply_with_correct_hash_creates_new_review_run(tmp_path) -> None:
    fixture = _fixture()
    client, chapter_id = _client_with_fixture(tmp_path)
    preview = client.post(
        f"/api/chapters/{chapter_id}/review/repair-preview",
        json={"selectedSegmentIds": [fixture.major_segment_id]},
    )
    proposal = preview.json()

    applied = client.post(
        f"/api/chapters/{chapter_id}/review/repair-apply",
        json={"proposalId": proposal["id"], "expectedProposalHash": proposal["hash"]},
    )

    assert applied.status_code == 200
    payload = applied.json()
    assert payload["runId"] != fixture.run_id
    assert len(payload["sha256"]) == 64
    assert payload["appliedCount"] == 1

    engine = create_engine_for(tmp_path / "studio.sqlite3")
    with session_factory(engine)() as session:
        old_run = session.get(TranslationRun, fixture.run_id)
        new_run = session.get(TranslationRun, payload["runId"])
        assert old_run.status == RunStatus.SUPERSEDED.value  # approved run never overwritten
        assert new_run.status == RunStatus.REVIEW.value
        targets = dict(
            session.execute(
                select(TranslationSegment.source_segment_id, TranslationSegment.target_text).where(
                    TranslationSegment.translation_run_id == new_run.id
                )
            ).all()
        )
    engine.dispose()
    assert targets[fixture.major_segment_id] == proposal["replacements"][0]["targetText"]
    assert targets[fixture.clean_segment_id] == "Ban dich sach."  # untouched segment copied verbatim


def test_apply_second_time_with_same_proposal_returns_409_consumed(tmp_path) -> None:
    fixture = _fixture()
    client, chapter_id = _client_with_fixture(tmp_path)
    preview = client.post(
        f"/api/chapters/{chapter_id}/review/repair-preview",
        json={"selectedSegmentIds": [fixture.major_segment_id]},
    )
    proposal = preview.json()
    first = client.post(
        f"/api/chapters/{chapter_id}/review/repair-apply",
        json={"proposalId": proposal["id"], "expectedProposalHash": proposal["hash"]},
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/chapters/{chapter_id}/review/repair-apply",
        json={"proposalId": proposal["id"], "expectedProposalHash": proposal["hash"]},
    )

    assert second.status_code == 409
    assert second.json()["detail"] == "REPAIR_PROPOSAL_CONSUMED"


def test_apply_with_wrong_hash_returns_409_conflict(tmp_path) -> None:
    fixture = _fixture()
    client, chapter_id = _client_with_fixture(tmp_path)
    preview = client.post(
        f"/api/chapters/{chapter_id}/review/repair-preview",
        json={"selectedSegmentIds": [fixture.major_segment_id]},
    )
    proposal = preview.json()
    wrong_hash = "f" * 64
    assert wrong_hash != proposal["hash"]

    response = client.post(
        f"/api/chapters/{chapter_id}/review/repair-apply",
        json={"proposalId": proposal["id"], "expectedProposalHash": wrong_hash},
    )

    assert response.status_code == 409
    assert "REPAIR_PROPOSAL_CONFLICT" in response.json()["detail"]
    engine = create_engine_for(tmp_path / "studio.sqlite3")
    with session_factory(engine)() as session:
        statuses = [
            row.status
            for row in session.scalars(
                select(TranslationRun).where(TranslationRun.chapter_id == _chapter_id(fixture))
            ).all()
        ]
    engine.dispose()
    assert RunStatus.SUPERSEDED.value not in statuses  # nothing was applied


def test_apply_with_unknown_proposal_id_returns_400(tmp_path) -> None:
    _fixture()
    client, chapter_id = _client_with_fixture(tmp_path)

    response = client.post(
        f"/api/chapters/{chapter_id}/review/repair-apply",
        json={"proposalId": "018f0000-0000-7000-8000-900000000009", "expectedProposalHash": "a" * 64},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "REPAIR_PROPOSAL_NOT_FOUND"


def test_preview_without_review_run_returns_409(tmp_path) -> None:
    client, _chapter_id = _client_with_fixture(tmp_path)
    other = _other_chapter_id()
    _seed_chapter_without_run(tmp_path, other)

    response = client.post(
        f"/api/chapters/{other}/review/repair-preview",
        json={"selectedSegmentIds": ["018f0000-0000-7000-8000-400000000401"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "TRANSLATION_RUN_NOT_FOUND"


def test_preview_with_empty_selection_returns_400(tmp_path) -> None:
    client, chapter_id = _client_with_fixture(tmp_path)

    response = client.post(
        f"/api/chapters/{chapter_id}/review/repair-preview",
        json={"selectedSegmentIds": []},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "REPAIR_SEGMENT_REQUIRED"


def test_preview_with_unknown_segment_returns_404(tmp_path) -> None:
    client, chapter_id = _client_with_fixture(tmp_path)

    response = client.post(
        f"/api/chapters/{chapter_id}/review/repair-preview",
        json={"selectedSegmentIds": ["seg-not-in-run"]},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "SOURCE_SEGMENT_NOT_IN_RUN"


def test_apply_with_segment_missing_from_run_returns_400(tmp_path) -> None:
    _migrate(tmp_path)

    # A proposal whose replacement references a segment that is not part of the
    # current run must fail loudly (C06 optimistic guard), never silently skip.
    from app.modules.translation import repair as repair_module
    from app.modules.translation.repair import RepairReplacement

    fixture = _fixture()
    _seed(tmp_path, fixture)
    service = _repair_service(tmp_path)
    proposal = service.propose(fixture.chapter_id, (fixture.major_segment_id,), "qwen-mt-plus")
    tampered = type(proposal)(
        id=proposal.id,
        base_run_id=proposal.base_run_id,
        base_run_sha256=proposal.base_run_sha256,
        chapter_id=proposal.chapter_id,
        provider_model=proposal.provider_model,
        story_memory_revision_hash=proposal.story_memory_revision_hash,
        replacements=(
            RepairReplacement(
                source_segment_id="018f0000-0000-7000-8000-400000000401",
                source_text="甲",
                current_target_text="Ban dich sach.",
                target_text="Ban dich moi.",
            ),
        ),
        estimated_cost_vnd=proposal.estimated_cost_vnd,
        hash=proposal.hash,
    )
    repair_module._PROPOSALS[tampered.id] = tampered
    try:
        service.accept_repair(tampered.id, tampered.hash)
    except ValueError as exc:
        assert str(exc) == "SOURCE_SEGMENT_NOT_IN_RUN"
    else:
        raise AssertionError("proposal with missing segment was accepted")
    finally:
        repair_module._PROPOSALS.pop(tampered.id, None)


def test_repair_service_rejects_non_hash_expected_hash(db_session) -> None:
    """C06 guard: an arbitrary string (or a foreign row id) can never satisfy the hash check."""
    from app.modules.translation import repair as repair_module

    fixture_rows = _minimal_rows(db_session)
    service = RepairService(db_session, translator=_RecordingTranslator(), id_factory=_ids())
    proposal = service.propose(fixture_rows.chapter_id, (fixture_rows.major_segment_id,), "qwen-mt-plus")

    try:
        service.accept_repair(proposal.id, "run-1")
    except RepairConflict as exc:
        assert str(exc) == "REPAIR_PROPOSAL_HASH_MISMATCH"
    else:
        raise AssertionError("non-hash expected value was accepted")

    repair_module._PROPOSALS.pop(proposal.id, None)


@dataclass(frozen=True)
class _Rows:
    chapter_id: str
    run_id: str
    major_segment_id: str
    clean_segment_id: str


def _fixture() -> _Rows:
    return _Rows(
        chapter_id="018f0000-0000-7000-8000-200000000301",
        run_id="018f0000-0000-7000-8000-500000000301",
        major_segment_id="018f0000-0000-7000-8000-400000000302",
        clean_segment_id="018f0000-0000-7000-8000-400000000301",
    )


def _other_chapter_id() -> str:
    return "018f0000-0000-7000-8000-200000000401"


def _chapter_id(fixture: _Rows) -> str:
    return fixture.chapter_id


def _client_with_fixture(tmp_path: Path) -> tuple[TestClient, str]:
    _seed(tmp_path, _fixture())
    app = FastAPI()
    app.include_router(create_review_router(Settings(data_root=tmp_path)))
    return TestClient(app), _fixture().chapter_id


def _repair_service(tmp_path: Path):
    from app.api.review import CleanRepairFakeTranslator

    engine = create_engine_for(tmp_path / "studio.sqlite3")
    factory = session_factory(engine)
    session = factory()
    return RepairService(session, translator=CleanRepairFakeTranslator())


def _count_rows(tmp_path: Path, chapter_id: str) -> dict[str, int]:
    engine = create_engine_for(tmp_path / "studio.sqlite3")
    with session_factory(engine)() as session:
        run_ids = [
            row.id
            for row in session.scalars(
                select(TranslationRun).where(TranslationRun.chapter_id == chapter_id)
            ).all()
        ]
        counts = {
            "runs": len(run_ids),
            "segments": len(
                session.scalars(
                    select(TranslationSegment.id).where(
                        TranslationSegment.translation_run_id.in_(run_ids)
                    )
                ).all()
            ),
            "issue_rows": int(
                session.scalar(select(func.count()).select_from(TranslationRun).where(TranslationRun.chapter_id == chapter_id))
                or 0
            ),
        }
    engine.dispose()
    return counts


def _seed_chapter_without_run(tmp_path: Path, chapter_id: str) -> None:
    _migrate(tmp_path)
    engine = create_engine_for(tmp_path / "studio.sqlite3")
    with session_factory(engine)() as session:
        project_id = session.scalar(select(Project.id).limit(1))
        chapter = Chapter(
            id=chapter_id,
            project_id=project_id,
            ordinal=9,
            state=ChapterState.TRANSLATION_REVIEW.value,
        )
        session.add(chapter)
        session.flush()
        revision = SourceRevision(
            id="018f0000-0000-7000-8000-300000000401",
            chapter_id=chapter_id,
            revision_no=1,
            import_kind=ImportKind.PASTE.value,
            normalized_text="甲",
            normalized_sha256=_sha("甲"),
            han_char_count=0,
            total_char_count=1,
            normalizer_version="nfc-v1",
        )
        session.add(revision)
        session.flush()
        chapter.active_source_revision_id = revision.id
        session.add(
            SourceSegment(
                id="018f0000-0000-7000-8000-400000000401",
                source_revision_id=revision.id,
                segment_index=0,
                paragraph_start=0,
                paragraph_end=0,
                source_text="甲",
                source_sha256=_sha("甲"),
                segment_kind="SOURCE",
            )
        )
        session.commit()
    engine.dispose()


def _migrate(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    backend_root = Path(__file__).parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{(tmp_path / 'studio.sqlite3').as_posix()}")
    command.upgrade(config, "head")


def _seed(tmp_path: Path, fixture: _Rows) -> None:
    _migrate(tmp_path)
    engine = create_engine_for(tmp_path / "studio.sqlite3")
    with session_factory(engine)() as session:
        project = Project(
            id="018f0000-0000-7000-8000-100000000301",
            title="Truyen",
            slug="repair-api",
            source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
            rights_status=RightsStatus.PRIVATE_ONLY.value,
            default_language="zh-CN",
            target_language="vi-VN",
        )
        session.add(project)
        session.flush()
        chapter = Chapter(
            id=fixture.chapter_id,
            project_id=project.id,
            ordinal=5,
            state=ChapterState.TRANSLATION_REVIEW.value,
        )
        session.add(chapter)
        session.flush()
        revision = SourceRevision(
            id="018f0000-0000-7000-8000-300000000301",
            chapter_id=chapter.id,
            revision_no=1,
            import_kind=ImportKind.PASTE.value,
            normalized_text="A\nB",
            normalized_sha256=_sha("A\nB"),
            han_char_count=0,
            total_char_count=3,
            normalizer_version="nfc-v1",
        )
        session.add(revision)
        session.flush()
        chapter.active_source_revision_id = revision.id
        clean = SourceSegment(
            id=fixture.clean_segment_id,
            source_revision_id=revision.id,
            segment_index=0,
            paragraph_start=0,
            paragraph_end=0,
            source_text="林动有金币。",
            source_sha256=_sha("林动有金币。"),
            segment_kind="SOURCE",
        )
        major = SourceSegment(
            id=fixture.major_segment_id,
            source_revision_id=revision.id,
            segment_index=1,
            paragraph_start=1,
            paragraph_end=1,
            source_text="林动有金币。",
            source_sha256=_sha("林动有金币。"),
            segment_kind="SOURCE",
        )
        session.add_all([clean, major])
        session.flush()
        run = TranslationRun(
            id=fixture.run_id,
            chapter_id=chapter.id,
            source_revision_id=revision.id,
            prompt_version="translation-v1",
            status=RunStatus.REVIEW.value,
            translation_text_sha256="a" * 64,
            estimated_cost_vnd=0,
            actual_cost_vnd=0,
        )
        session.add(run)
        session.flush()
        for segment, target in ((clean, "Ban dich sach."), (major, "林动 bi sai.")):
            session.add(
                TranslationSegment(
                    id=f"018f0000-0000-7000-8000-6000000003{segment.segment_index + 1}",
                    translation_run_id=run.id,
                    source_segment_id=segment.id,
                    target_text=target,
                    target_sha256=_sha(target),
                    was_cache_hit=False,
                    manually_edited=False,
                )
            )
        session.commit()
    engine.dispose()


def _minimal_rows(session) -> _Rows:
    project = Project(
        id="018f0000-0000-7000-8000-100000000901",
        title="Truyen",
        slug="repair-hash-guard",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        default_language="zh-CN",
        target_language="vi-VN",
    )
    session.add(project)
    session.flush()
    chapter = Chapter(
        id="018f0000-0000-7000-8000-200000000901",
        project_id=project.id,
        ordinal=7,
        state=ChapterState.TRANSLATION_REVIEW.value,
    )
    session.add(chapter)
    session.flush()
    revision = SourceRevision(
        id="018f0000-0000-7000-8000-300000000901",
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="A",
        normalized_sha256=_sha("A"),
        han_char_count=0,
        total_char_count=1,
        normalizer_version="nfc-v1",
    )
    session.add(revision)
    session.flush()
    chapter.active_source_revision_id = revision.id
    segment = SourceSegment(
        id="018f0000-0000-7000-8000-400000000902",
        source_revision_id=revision.id,
        segment_index=0,
        paragraph_start=0,
        paragraph_end=0,
        source_text="林动",
        source_sha256=_sha("林动"),
        segment_kind="SOURCE",
    )
    session.add(segment)
    session.flush()
    run = TranslationRun(
        id="018f0000-0000-7000-8000-500000000901",
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        prompt_version="translation-v1",
        status=RunStatus.REVIEW.value,
        translation_text_sha256="a" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    session.add(run)
    session.flush()
    session.add(
        TranslationSegment(
            id="018f0000-0000-7000-8000-600000000901",
            translation_run_id=run.id,
            source_segment_id=segment.id,
            target_text="林动 bi sai.",
            target_sha256=_sha("林动 bi sai."),
            was_cache_hit=False,
            manually_edited=False,
        )
    )
    session.commit()
    return _Rows(
        chapter_id=chapter.id,
        run_id=run.id,
        major_segment_id=segment.id,
        clean_segment_id=segment.id,
    )


class _RecordingTranslator:
    def capabilities(self) -> dict[str, object]:
        return {"provider": "fake", "model": "fake-hanviet-v2", "region": "local"}

    async def translate(self, request):
        from app.contracts import TranslationResult, Usage, UsageUnit
        from app.modules.translation.hanviet import convert_hanviet

        return TranslationResult(
            target_text=convert_hanviet(request.source_text, request.terms),
            provider="fake",
            model="fake-hanviet-v2",
            provider_version="2",
            usage=(Usage(UsageUnit.CHARACTER.value, len(request.source_text)),),
        )


_ID_COUNTER = 0


def _ids():
    global _ID_COUNTER

    def factory() -> str:
        global _ID_COUNTER
        _ID_COUNTER += 1
        return f"018f0000-0000-7000-8000-900200{_ID_COUNTER:06d}"

    return factory


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
